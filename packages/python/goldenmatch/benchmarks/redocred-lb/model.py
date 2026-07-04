"""DocREModel -- the ATLOP relation-extraction head (Zhou et al., AAAI 2021).

Encoder -> per-entity embedding (log-sum-exp pool over the "*" mention markers) ->
**localized context pooling** (per entity-pair context vector from the product of the
head/tail attention distributions) -> grouped bilinear classifier -> adaptive-threshold
loss. Faithful port of wzhouad/ATLOP's `model.py`, modern-transformers clean (no apex).
Torch-only (runs on GPU under Modal)."""
from __future__ import annotations

import torch
import torch.nn as nn
from long_input import process_long_input
from losses import ATLoss
from opt_einsum import contract


class DocREModel(nn.Module):
    def __init__(self, config, encoder, emb_size=768, block_size=64, num_labels=-1):
        super().__init__()
        self.config = config
        self.model = encoder
        self.loss_fnt = ATLoss()
        self.head_extractor = nn.Linear(2 * config.hidden_size, emb_size)
        self.tail_extractor = nn.Linear(2 * config.hidden_size, emb_size)
        self.bilinear = nn.Linear(emb_size * block_size, config.num_labels)
        self.emb_size = emb_size
        self.block_size = block_size
        self.num_labels = num_labels  # top-k cap on predicted relations per pair (-1 = off)

    def encode(self, input_ids, attention_mask):
        cfg = self.config
        if cfg.transformer_type == "roberta":
            start_tokens = [cfg.cls_token_id]
            end_tokens = [cfg.sep_token_id, cfg.sep_token_id]
        else:  # bert / deberta
            start_tokens = [cfg.cls_token_id]
            end_tokens = [cfg.sep_token_id]
        return process_long_input(self.model, input_ids, attention_mask, start_tokens, end_tokens)

    def get_hrt(self, sequence_output, attention, entity_pos, hts):
        offset = 1  # leading special token ([CLS]/<s>)
        _, h, _, c = attention.size()
        hss, tss, rss = [], [], []
        for i in range(len(entity_pos)):
            entity_embs, entity_atts = [], []
            for e in entity_pos[i]:
                if len(e) > 1:
                    e_emb, e_att = [], []
                    for start, _end in e:
                        if start + offset < c:
                            e_emb.append(sequence_output[i, start + offset])
                            e_att.append(attention[i, :, start + offset])
                    if e_emb:
                        e_emb = torch.logsumexp(torch.stack(e_emb, dim=0), dim=0)
                        e_att = torch.stack(e_att, dim=0).mean(0)
                    else:
                        e_emb = torch.zeros(self.config.hidden_size).to(sequence_output)
                        e_att = torch.zeros(h, c).to(attention)
                else:
                    start, _end = e[0]
                    if start + offset < c:
                        e_emb = sequence_output[i, start + offset]
                        e_att = attention[i, :, start + offset]
                    else:
                        e_emb = torch.zeros(self.config.hidden_size).to(sequence_output)
                        e_att = torch.zeros(h, c).to(attention)
                entity_embs.append(e_emb)
                entity_atts.append(e_att)

            entity_embs = torch.stack(entity_embs, dim=0)  # [n_e, d]
            entity_atts = torch.stack(entity_atts, dim=0)  # [n_e, h, c]

            ht_i = torch.LongTensor(hts[i]).to(sequence_output.device)
            hs = torch.index_select(entity_embs, 0, ht_i[:, 0])
            ts = torch.index_select(entity_embs, 0, ht_i[:, 1])

            h_att = torch.index_select(entity_atts, 0, ht_i[:, 0])
            t_att = torch.index_select(entity_atts, 0, ht_i[:, 1])
            ht_att = (h_att * t_att).mean(1)  # [n_pairs, c]
            ht_att = ht_att / (ht_att.sum(1, keepdim=True) + 1e-30)
            rs = contract("ld,rl->rd", sequence_output[i], ht_att)  # [n_pairs, d]

            hss.append(hs)
            tss.append(ts)
            rss.append(rs)
        return torch.cat(hss, 0), torch.cat(rss, 0), torch.cat(tss, 0)

    def forward(self, input_ids, attention_mask, entity_pos, hts, labels=None):
        sequence_output, attention = self.encode(input_ids, attention_mask)
        hs, rs, ts = self.get_hrt(sequence_output, attention, entity_pos, hts)

        hs = torch.tanh(self.head_extractor(torch.cat([hs, rs], dim=1)))
        ts = torch.tanh(self.tail_extractor(torch.cat([ts, rs], dim=1)))
        b1 = hs.view(-1, self.emb_size // self.block_size, self.block_size)
        b2 = ts.view(-1, self.emb_size // self.block_size, self.block_size)
        bl = (b1.unsqueeze(3) * b2.unsqueeze(2)).view(-1, self.emb_size * self.block_size)
        logits = self.bilinear(bl)

        preds = self.loss_fnt.get_label(logits, num_labels=self.num_labels)
        if labels is not None:
            loss = self.loss_fnt(logits.float(), labels.float())
            return loss, preds
        return (preds,)


def make_collate(pad_token_id: int):
    """Collate a batch of prepro features -> (input_ids, mask, labels, entity_pos, hts).
    `labels` is the pairs of ALL docs concatenated into one [total_pairs, 97] tensor
    (aligned with the flattened `hts`); `entity_pos`/`hts` stay per-doc lists."""
    import torch as _t

    def collate(batch):
        max_len = max(len(f["input_ids"]) for f in batch)
        input_ids, mask = [], []
        for f in batch:
            ids = f["input_ids"]
            pad = max_len - len(ids)
            input_ids.append(ids + [pad_token_id] * pad)
            mask.append([1.0] * len(ids) + [0.0] * pad)
        input_ids = _t.tensor(input_ids, dtype=_t.long)
        mask = _t.tensor(mask, dtype=_t.float)
        labels = _t.tensor([lab for f in batch for lab in f["labels"]], dtype=_t.float)
        entity_pos = [f["entity_pos"] for f in batch]
        hts = [f["hts"] for f in batch]
        return input_ids, mask, labels, entity_pos, hts

    return collate


def decode_preds(pred_matrix, hts_per_doc, id2rel):
    """Map the model's [total_pairs, 97] binary prediction matrix back to per-doc
    `(h_idx, t_idx, relation_Pid)` triples, sliced by each doc's pair count. Class 0 (TH)
    is skipped -- an all-zero-relation row means 'no relation'."""
    preds_per_doc = []
    cursor = 0
    for hts in hts_per_doc:
        doc_preds = []
        rows = pred_matrix[cursor: cursor + len(hts)]
        for (h, t), row in zip(hts, rows):
            for cls in range(1, row.shape[0]):
                if row[cls] > 0:
                    doc_preds.append((int(h), int(t), id2rel[cls]))
        preds_per_doc.append(doc_preds)
        cursor += len(hts)
    return preds_per_doc
