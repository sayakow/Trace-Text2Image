import argparse
import logging
import os
import time

import torch
import torch.nn.functional as F
from torch.nn.parallel import DistributedDataParallel
from torch.nn.utils import clip_grad_norm_
from transformers.modeling_bert import BertConfig
from transformers.optimization import AdamW, WarmupCosineSchedule

from config import _C as config
from dataset import COCOCaptionDataset, collate_fn_train
from modeling import Generator, LabelSmoothingLoss
from utils import get_rank, mkdir, synchronize
from utils.checkpointer import Checkpointer
from utils.dataloader import make_data_loader
from utils.logger import setup_logger
from utils.tokenizer import EOS, MASK, PAD, num_tokens


def train(generator, optimizer, data_loader, scheduler, checkpointer,
          device, log_time, checkpoint_time, arguments):
    logger = logging.getLogger("train")
    logger.info("Start training")

    max_iter = len(data_loader)
    print("------------------------------max_iter = ",max_iter)
    start_iter = arguments['iteration']
    print("------------------------------generator.train")
    generator.train()

    if config.loss.balance_weight != 1.0:
        balance_weight = torch.ones(
            num_tokens, dtype=torch.float32, device=device)
        balance_weight[EOS] = config.loss.balance_weight
    else:
        balance_weight = None

    print("------------------------------criterion")
    criterion = LabelSmoothingLoss(
        num_tokens, balance_weight, config.loss.label_smoothing)

    print("------------------------------end time")
    end = time.time()

    print("------------------------------for文")
    #print("data_loader : ",data_loader,"--","start_iter : ",start_iter)
    #print(enumerate(data_loader, start_iter))
    
    for iteration, batch in enumerate(data_loader, start_iter):
        #print(iteration, batch)
        #print("------------------------------iteration")
        iteration = iteration + 1
        arguments['iteration'] = iteration

        #print("------------------------------batch")
        token_type_ids = batch[0].to(device)  # (N, L), long
        input_token_ids = batch[1].to(device)  # (N, L), long
        masked_token_ids = batch[2].to(device)  # (N, L), long
        region_features = batch[3].to(device)  # (N, 100, 2048), float
        region_class = batch[4].to(device)  # (N, 100, 1601), float
        region_spatial = batch[5].to(device)  # (N, 100, 6), float

        #print("------------------------------size")
        num_img_tokens = region_spatial.size(1)
        seq_length = input_token_ids.size(1)
        batch_size = input_token_ids.size(0)

        #print("------------------------------region")
        region_spatial[:, :, [0, 2]] /= region_spatial[:, :, [2]] + 1e-5
        region_spatial[:, :, [1, 3]] /= region_spatial[:, :, [3]] + 1e-5
        rel_area = (region_spatial[:, :, [3]] - region_spatial[:, :, [1]]) * \
                   (region_spatial[:, :, [2]] - region_spatial[:, :, [0]])
        region_spatial = torch.cat((region_spatial[:, :, :4],
            rel_area.clamp_(0), region_spatial[:, :, 5:]), dim=-1)
        position_features = torch.cat((F.layer_norm(region_spatial, [6]),
            F.layer_norm(region_class, [1601])), dim=-1)

        #print("------------------------------position")
        position_ids = torch.arange(seq_length, dtype=torch.long, device=device)
        position_ids = position_ids.unsqueeze(0).expand_as(input_token_ids)

        #print("------------------------------region type")
        region_type = position_ids.new_full(
            region_features.shape[:2], len(config.boundaries) + 1)
        token_type_ids = torch.cat((region_type, token_type_ids), dim=1)

        #print("------------------------------attention mask")
        attention_mask = (masked_token_ids != PAD).float()
        _attention_mask = attention_mask.new_ones((batch_size, num_img_tokens))
        attention_mask = torch.cat((_attention_mask, attention_mask), dim=1)

        #print("------------------------------mask pojition")
        mask_position = (masked_token_ids == MASK).to(torch.long).view(-1)
        mask_position = mask_position.nonzero().squeeze()

        #print("------------------------------まとめ")
        pred_scores = generator(
            region_features, position_features,
            masked_token_ids, token_type_ids,
            position_ids, attention_mask)

        #print("------------------------------score")
        pred_scores = pred_scores[:, num_img_tokens:, :]
        pred_scores = pred_scores.contiguous().view(-1, num_tokens)
        pred_scores = pred_scores[mask_position]

        #print("------------------------------準備")
        gt_token_ids = input_token_ids.view(-1)[mask_position]
        loss = criterion(pred_scores, gt_token_ids)

        optimizer.zero_grad()
        loss.backward()
        clip_grad_norm_(generator.parameters(), config.solver.grad_clip)
        optimizer.step()
        scheduler.step()
        batch_time = time.time() - end
        end = time.time()

        #print("------------------------------print")
        if iteration % log_time == 0 or iteration == max_iter:
            logger.info(
                '  '.join([
                    "iter: {iter}", "time: {time:.4f}", "mem: {mem:.2f}",
                    "lr: {lr:.8f}", "loss: {loss:.4f}"
                ]).format(
                    iter=iteration, time=batch_time, loss=loss,
                    lr=optimizer.param_groups[0]["lr"],
                    mem=torch.cuda.max_memory_allocated() / 1024.0 ** 3,
                ))
        if iteration % checkpoint_time == 0 or iteration == max_iter:
            checkpointer.save("211205_model_{:07d}".format(iteration), **arguments)


if __name__ == "__main__":
    print("------------------------------引数")
    # 引数
    parser = argparse.ArgumentParser(description="train")
    # parser.add_argumentで受け取る引数を追加していく
    parser.add_argument("--local_rank", type=int, default=0)
    parser.add_argument("opts", default=None, nargs=argparse.REMAINDER)
    # 引数を解析
    args = parser.parse_args()

    print("------------------------------config")
    if config.distributed:
        torch.cuda.set_device(args.local_rank)
        torch.distributed.init_process_group("nccl", init_method="env://")
        synchronize()

    config.merge_from_list(args.opts)
    config.freeze()

    print("------------------------------ディレクトリ")
    save_dir = os.path.join(config.save_dir, f'train')
    mkdir(save_dir)
    logger = setup_logger("train", save_dir, get_rank())
    logger.info("Running with config:\n{}".format(config))

    arguments = {'iteration': 0}
    device = torch.device(config.device)

    print("------------------------------BERT")
    bert_config = BertConfig(type_vocab_size=len(config.boundaries) + 2)
    print("------------------------------generator")
    generator = Generator(bert_config)
    generator = generator.to(device)

    print("------------------------------optimizer")
    optimizer = AdamW(
        params=generator.parameters(),
        lr=config.solver.lr,
        weight_decay=config.solver.weight_decay,
        betas=config.solver.betas
    )

    print("------------------------------scheduler")
    scheduler = WarmupCosineSchedule(
        optimizer=optimizer,
        warmup_steps=config.scheduler.warmup_steps,
        t_total=config.scheduler.max_steps
    )

    print("------------------------------chheckpointer")
    checkpointer = Checkpointer(
        model=generator,
        optimizer=optimizer,
        scheduler=scheduler,
        save_dir=save_dir,
        save_to_disk=get_rank() == 0,
        logger=logger
    )

    if config.model_path == '':
        generator.load_weights(config.pretrained_bert)
    else:
        extra_checkpoint_data = checkpointer.load(config.model_path)
        arguments.update(extra_checkpoint_data)

    print("------------------------------データセット")
    dataset = COCOCaptionDataset(
        root=config.data_dir,
        split='trainrestval',
        boundaries=config.boundaries,
    )

    print("------------------------------データのロード")
    data_loader = make_data_loader(
        dataset=dataset,
        collate_fn=collate_fn_train,
        batch_size=config.samples_per_gpu,
        num_workers=config.num_workers,
        max_iter=config.scheduler.max_steps,
        split='trainrestval',
        is_distributed=config.distributed,
        start_iter=arguments['iteration'],
    )

    if config.distributed:
        generator = DistributedDataParallel(
            module=generator,
            device_ids=[args.local_rank],
            output_device=args.local_rank,
        )

    print("------------------------------train")
    train(generator=generator,
          optimizer=optimizer,
          data_loader=data_loader,
          scheduler=scheduler,
          checkpointer=checkpointer,
          device=device,
          log_time=config.log_time,
          checkpoint_time=config.checkpoint_time,
          arguments=arguments)



    
