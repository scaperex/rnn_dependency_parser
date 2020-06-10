from chu_liu_edmonds import decode_mst
from utils.DataPreprocessing import *
from MLP import *
from contextlib import nullcontext
import random


class KiperwasserDependencyParser(nn.Module):
    # TODO lstm_out_dim use
    def __init__(self, word_dict, tag_dict, word_list, tag_list,
                 tag_embedding_dim=25, word_embedding_dim=100,
                 lstm_out_dim=None, word_embeddings=None, hidden_dim=None,
                 hidden_dim_mlp=100, bilstm_layers=2, dropout=True, dropout_alpha=0.25):
        super(KiperwasserDependencyParser, self).__init__()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.dropout = dropout
        self.word_dict = word_dict
        self.tag_dict = tag_dict
        self.tag_list = tag_list
        self.word_list = word_list
        self.unknown_word_idx = 1 # TODO
        self.unknown_tag_idx = 1
        self.root_idx = 0
        self.alpha = dropout_alpha


        if word_embeddings:
            self.word_embedder = nn.Embedding.from_pretrained(word_embeddings, freeze=False)
        else:
            self.word_embedder = nn.Embedding(len(self.word_dict), word_embedding_dim)

        self.tag_embedder = nn.Embedding(len(self.tag_dict), tag_embedding_dim)

        self.emb_dim = self.word_embedder.embedding_dim + self.tag_embedder.embedding_dim

        self.lstm_out_dim = lstm_out_dim if lstm_out_dim else self.emb_dim

        self.hidden_dim = hidden_dim if hidden_dim else self.emb_dim

        self.encoder = nn.LSTM(input_size=self.emb_dim,
                               hidden_size=self.hidden_dim,
                               num_layers=bilstm_layers,
                               bidirectional=True,
                               batch_first=True)

        # input samples dim for MLP is lstm_out_dim*NUM_DIRECTION
        self.edge_scorer = MLP(self.lstm_out_dim*2, hidden_dim_mlp)

        self.decoder = decode_mst  # This is used to produce the maximum spannning tree during inference

        self.log_soft_max = nn.LogSoftmax(dim=0)

    def forward(self, sentence):
        loss, predicted_tree = self.infer(sentence)

        return loss, predicted_tree

    def infer(self, sentence, is_comp=False):
        cm = torch.no_grad() if is_comp else nullcontext()
        with cm:
            word_idx_tensor, tag_idx_tensor, true_tree_heads = sentence

            if self.dropout:
                for i, word in enumerate(word_idx_tensor[0]):
                    actual_word_idx = word.item()
                    if actual_word_idx != self.unknown_word_idx and actual_word_idx != self.root_idx:
                        freq_of_word = self.word_dict[self.word_list[actual_word_idx]]
                        prob_word = float(self.alpha) / (self.alpha + freq_of_word)
                        if random.random() < prob_word:
                            word_idx_tensor[0, i] = self.unknown_word_idx
                            tag_idx_tensor[0, i] = self.unknown_tag_idx


            # Pass word_idx and tag_idx through their embedding layers
            tag_embbedings = self.tag_embedder(tag_idx_tensor.to(self.device))
            word_embbedings = self.word_embedder(word_idx_tensor.to(self.device))

            # Concat both embedding outputs
            input_embeddings = torch.cat((word_embbedings, tag_embbedings), dim=2)

            # Get Bi-LSTM hidden representation for each word+tag in sentence
            lstm_output, _ = self.encoder(input_embeddings.view(1, input_embeddings.shape[1], -1))

            # Get score for each possible edge in the parsing graph, construct score matrix
            scores = self.edge_scorer(lstm_output)

            # Use Chu-Liu-Edmonds to get the predicted parse tree T' given the calculated score matrix
            seq_len = lstm_output.size(1)
            predicted_tree_heads, _ = self.decoder(scores.data.cpu().numpy(), seq_len, False)

            if not is_comp:
                true_tree_heads = true_tree_heads.squeeze(0)
                # Calculate the negative log likelihood loss described above
                probs_logged = self.log_soft_max(scores)
                loss = KiperwasserDependencyParser.nll_loss(probs_logged, true_tree_heads, self.device)
                return loss, torch.from_numpy(predicted_tree_heads)

            else:
                return torch.from_numpy(predicted_tree_heads)

    @staticmethod
    def nll_loss(probs_logged, tree, device):
        loss = torch.tensor(0, dtype=torch.float).to(device)
        tree_length = tree.size(0)
        for m, h in enumerate(tree):
            loss -= probs_logged[h, m]
        return loss / tree_length


