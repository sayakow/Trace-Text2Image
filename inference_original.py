import numpy as np
import argparse
import json
import logging
import os
import re
import time

import torch
import torch.nn.functional as F
from tqdm import tqdm
from transformers.modeling_bert import BertConfig

from config import _C as config
from dataset import COCOCaptionDataset
from modeling import Generator
from utils import mkdir
from utils.checkpointer import Checkpointer
from utils.dataloader import make_data_loader
from utils.logger import setup_logger
from utils.tokenizer import EOS, MASK, tokenizer





def inference(generator, data_loader, device):
    logger = logging.getLogger("inference")
    logger.info("Start inferencing")
    generator.eval()

    pred_dict = dict()
    eos_penalizers = list()
    for l, (low, high) in enumerate(config.boundaries):
        pred_dict[str(l + 1)] = dict()

        eos_penalizer = torch.ones((1, high - low + 1), dtype=torch.float, device=device)
        eos_penalizer *= config.infer.eos_decay[l]
        eos_penalizer = eos_penalizer.cumprod(dim=-1).flip(-1)
        eos_penalizers.append(eos_penalizer)

    end = time.time()
    for iteration, batch in tqdm(enumerate(data_loader, 0), total=len(data_loader)):
        print("--------------------- iteration : ",iteration,"---------------------")
        iteration = iteration + 1

        region_features = batch[0].to(device)  # (N, 100, 2048), float
        region_class = batch[1].to(device)   # (N, 100, 1601), float
        region_spatial = batch[2].to(device)   # (N, 100, 6), float

        B = region_class.size(0)
        num_regions = region_class.size(1)
        pred_list = list()

        with torch.no_grad():
            batch_id = torch.arange(0, B, 1, device=device).unsqueeze(1)
            #xmin,xmax
            region_spatial[:, :, [0, 2]] /= region_spatial[:, :, [2]] + 1e-5
            #ymin,ymax
            region_spatial[:, :, [1, 3]] /= region_spatial[:, :, [3]] + 1e-5
            #BOXの座標
            rel_area = (region_spatial[:, :, [3]] - region_spatial[:, :, [1]]) * \
                       (region_spatial[:, :, [2]] - region_spatial[:, :, [0]])
            region_spatial = torch.cat((region_spatial[:, :, :4],
                rel_area.clamp_(0), region_spatial[:, :, 5:]), dim=-1)
            position_features = torch.cat((F.layer_norm(region_spatial, [6]),
                F.layer_norm(region_class, [1601])), dim=-1)
            region_type = torch.full((B, num_regions), len(config.boundaries) + 1)
            region_type = region_type.to(torch.long).to(device)

            for l, (low, high) in enumerate(config.boundaries, 1):
                print(low,"words ~ ",high,"words")
                print(l)
                """
                1 7 9
                2 10 14
                3 15 19
                4 20 25
                """



                token_type_ids = region_class.new_full((B, high), l, dtype=torch.long)
                print(np.shape(token_type_ids))
                masked_token_ids = token_type_ids.new_full((B, high), MASK)
                print(np.shape(masked_token_ids))
                attention_mask = rel_area.new_ones((B, high + num_regions))
                print(np.shape(attention_mask))
                position_ids = torch.arange(high, dtype=torch.long, device=device)
                position_ids = position_ids.unsqueeze(0).expand_as(masked_token_ids)
                token_type_ids = torch.cat((region_type, token_type_ids), dim=1)

                """
                -------------token_type_ids-------------- torch.Size([36, 125])
                -------------masked_token_ids-------------- torch.Size([36, 25])
                -------------attention_mask-------------- torch.Size([36, 125])
                -------------position_ids-------------- torch.Size([36, 25])
                -------------region_features-------------- torch.Size([36, 100, 2048])
                -------------position_features-------------- torch.Size([36, 100, 1607])
                """


                print("------------------Generator")
                pred_scores = generator(region_features, position_features,masked_token_ids, token_type_ids,position_ids, attention_mask)

                #print("----- pred_scores-----",np.shape(pred_scores))
                """
                1 7 9
                ----- pred_scores----- torch.Size([36, 109, 30522])
                2 10 14
                ----- pred_scores----- torch.Size([36, 114, 30522])
                3 15 19
                ----- pred_scores----- torch.Size([36, 119, 30522])
                4 20 25
                ----- pred_scores----- torch.Size([36, 125, 30522])
                """


                pred_probs = F.softmax(pred_scores[:, num_regions:, :], dim=-1)
                pred_probs[:, low - 1:, EOS] *= eos_penalizers[l - 1]
                pred_token_probs, pred_token_ids = pred_probs.max(dim=-1)

                total_steps = config.infer.steps[l - 1]
                print(config.infer.steps)
                for step in range(1, total_steps):
                    print("----step---",step)
                    num_mask = max(1, int(high * (1.0 - step / total_steps)))

                    mask_id = pred_token_probs.topk(num_mask, -1, False, False)[1]
                    mask_id = (mask_id + batch_id * high).view(-1)

                    """
                    288 - 36
                    468 - 36
                    648 - 36
                    828 - 36
                    """


                    #最初に決まった文字から始めるやつ
                    pred_token_ids.view(-1)[mask_id] = MASK

                    print("入力文章",tokenizer.decode(pred_token_ids[0].cpu().numpy()))


                    pred_scores = generator(
                        region_features, position_features,
                        pred_token_ids, token_type_ids,
                        position_ids, attention_mask)

                    pred_probs = F.softmax(pred_scores[:, num_regions:, :], dim=-1)
                    pred_probs[:, low - 1:, EOS] *= eos_penalizers[l - 1]
                    new_token_probs, new_token_ids = pred_probs.max(dim=-1)

                    pred_token_ids.view(-1)[mask_id] = new_token_ids.view(-1)[mask_id]
                    pred_token_probs.view(-1)[mask_id] = new_token_probs.view(-1)[mask_id]
                    pred_token_probs = (pred_token_probs + new_token_probs) / 2



                    """
                    36次元の中には全て同じものがはいっている
                    for i in range(len(list(pred_token_ids))):
                        if(list(pred_token_ids[i]) != list(pred_token_ids[i])):
                            print("not same")
                    """



                    """
                    出力単語
                    """
                    print("出力文章",tokenizer.decode(pred_token_ids[0].cpu().numpy()))



                pred_list.append(pred_token_ids.cpu().numpy())  # 5 * (N, L)

        image_ids = list(batch[3].cpu().numpy())
        print("----画像ファイル名----",image_ids[0])
        for level, preds_per_level in enumerate(pred_list, 1):
            for batch_id, image_id in enumerate(image_ids):
                pred_per_level = tokenizer.decode(preds_per_level[batch_id], end_flags=[EOS])
                pred_per_level = re.sub(r'\b(\w+)( \1\b)+', r'\1', pred_per_level)
                pred_dict[str(level)][str(image_id)] = [{'caption': pred_per_level}]

    logger.info('batch_time: {time:.4f} batch_memory: {memory:.2f}'.format(
        time=(time.time() - end) / iteration,
        memory=torch.cuda.max_memory_allocated() / 1024.0 ** 3))

    return pred_dict


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="inference")
    parser.add_argument("opts", default=None, nargs=argparse.REMAINDER)
    args = parser.parse_args()

    config.merge_from_list(args.opts)
    config.freeze()

    save_dir = os.path.join(config.save_dir)
    mkdir(save_dir)
    logger = setup_logger("inference", save_dir, 0)
    logger.info("Running with config:\n{}".format(config))

    device = torch.device(config.device)
    num_types = len(config.boundaries) + 2

    print("--------------inference.py main generator")
    generator = Generator(BertConfig(type_vocab_size=num_types))
    generator = generator.to(device)
    g_checkpointer = Checkpointer(model=generator, logger=logger)
    g_checkpointer.load(config.model_path, True)



    print("--------------inference.py main dataset")
    dataset = COCOCaptionDataset(
        root=config.data_dir,
        split='test',
        boundaries=config.boundaries
    )


    print("--------------inference.py main data loader")
    data_loader = make_data_loader(
        dataset=dataset,
        batch_size=config.samples_per_gpu,
        num_workers=config.num_workers,
        split='test'
    )

    #print("-------------generator---------------",generator)

    print("--------------inference.py main pred dict")
    pred_dict = inference(generator, data_loader, device)
    logger.info(f"Saving results to {save_dir}/caption_results.json")
    with open(os.path.join(save_dir, 'caption_results.json'), 'w') as f:
        json.dump(pred_dict, f)
