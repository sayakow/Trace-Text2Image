import torch
from torch import nn
from transformers import modeling_bert
import numpy

class VLBertEmbeddings(modeling_bert.BertEmbeddings):
    def __init__(self, config):
        super(VLBertEmbeddings, self).__init__(config)

        self.region_embed = nn.Sequential(
            nn.Linear(2048, 2048),
            nn.ReLU(inplace=True),
            nn.Linear(2048, config.hidden_size),
            nn.Dropout(config.hidden_dropout_prob))

        self.region_position_embed = nn.Sequential(
            nn.Linear(6 + 1601, config.hidden_size),
            nn.ReLU(inplace=True),
            nn.Dropout(config.hidden_dropout_prob))

    def forward(self, region_features, position_features,
                input_token_ids, token_type_ids, position_ids):


        
        r_f = region_features
        #print(r_f.dtype)



        #print("modeling.py region_embedのembedding前")
        region_features = self.region_embed(r_f)
        #print("modeling.py region_embedのembedding完了")
        position_features = self.region_position_embed(position_features)
        words_embeddings = self.word_embeddings(input_token_ids)
        position_embeddings = self.position_embeddings(position_ids)
        token_type_embeddings = self.token_type_embeddings(token_type_ids)

        words_embeddings = torch.cat((region_features, words_embeddings), dim=1)
        position_embeddings = torch.cat((position_features, position_embeddings), dim=1)


        """
        print("embeddings-------------------------------------------------")
        print(words_embeddings.size(),words_embeddings.size(),words_embeddings.size())

        #print(words_embeddings.size()[1])
        if(words_embeddings.size()[1] != 120):
            device = torch.device('cpu')
            words_embeddings= words_embeddings.to(device)
            words_embeddings = words_embeddings.detach().clone().numpy()

            
            #print("num : ",type(words_embeddings))
            words_embeddings_numpy = numpy.zeros((36,120,768))
            for a in range(len(words_embeddings_numpy)):
                words_embeddings_numpy[a] = words_embeddings[a][:120]
                #print("num : ",type(words_embeddings_numpy))

                words_embeddings = torch.from_numpy(words_embeddings_numpy)
                #print("ten : ",type(words_embeddings))


        print(words_embeddings.size(),words_embeddings.size(),words_embeddings.size())
        """
        
        embeddings = words_embeddings + position_embeddings + token_type_embeddings
        return self.dropout(self.LayerNorm(embeddings))


class Generator(modeling_bert.BertPreTrainedModel):
    def __init__(self, config):
        super(Generator, self).__init__(config)

        self.encoder = modeling_bert.BertEncoder(config)
        self.classifier = modeling_bert.BertLMPredictionHead(config)
        self.embedding_layer = VLBertEmbeddings(config)
        self.head_mask = [None] * config.num_hidden_layers

        self.apply(self._init_weights)

    def load_weights(self, path):
        state_dict = torch.load(path, map_location=torch.device("cpu"))
        if 'model' in state_dict.keys():
            state_dict = state_dict['model']
        self.load_state_dict(state_dict, strict=False)
        del state_dict

    def forward(self, region_features, position_features,
                masked_token_ids, token_type_ids, position_ids,
                attention_mask):
        embeddings = self.embedding_layer(
            region_features, position_features,
            masked_token_ids, token_type_ids, position_ids)

        attention_mask = attention_mask.unsqueeze(1).unsqueeze(2)
        attention_mask = (1.0 - attention_mask) * -10000.0

        hidden_states = self.encoder(embeddings, attention_mask, self.head_mask)[0]
        return self.classifier(hidden_states)


class LabelSmoothingLoss(nn.Module):
    def __init__(self, classes, weight=None, smoothing=0.0):
        super(LabelSmoothingLoss, self).__init__()
        self.confidence = 1.0 - smoothing
        self.smoothing = smoothing
        self.classes = classes
        self.weight = weight

    def forward(self, pred, target):
        """
        Args:
            pred: (N, C), float
            target: (N,), long, values in [0, C-1]
        """
        if self.weight is None:
            #self.weight = torch.ones(self.classes, dtype=torch.float32,
            #                         device=target.device)
            self.weight = torch.ones(self.classes, dtype=torch.double,device=target.device)
        pred = pred.log_softmax(dim=-1)
        with torch.no_grad():
            true_dist = torch.zeros_like(pred)
            true_dist.fill_(self.smoothing / (self.classes - 1))
            true_dist.scatter_(1, target.data.unsqueeze(1), self.confidence)

        weight = self.weight[target]
        weighted_loss = torch.sum(-true_dist * pred, dim=-1) * weight

        return torch.mean(weighted_loss) * weight.numel() / weight.sum()
