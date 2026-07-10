"""Adaptive-Thresholding Loss (ATLoss) from ATLOP (Zhou et al., AAAI 2021).

The threshold is a learned class (index 0). Training pushes every positive relation's
logit above the TH logit and the TH logit above every negative's. Inference predicts a
relation iff its logit exceeds the per-pair TH logit -- a per-instance, per-pair
threshold rather than a single global cutoff. Faithful port of the reference `losses.py`.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class ATLoss(nn.Module):
    def forward(self, logits, labels):
        th_label = torch.zeros_like(labels, dtype=torch.float)
        th_label[:, 0] = 1.0
        labels = labels.clone()
        labels[:, 0] = 0.0

        p_mask = labels + th_label
        n_mask = 1 - labels

        # positive term: among {positive classes, TH}, the positives must dominate
        logit1 = logits - (1 - p_mask) * 1e30
        loss1 = -(F.log_softmax(logit1, dim=-1) * labels).sum(1)

        # negative term: among {negative classes, TH}, TH must dominate
        logit2 = logits - (1 - n_mask) * 1e30
        loss2 = -(F.log_softmax(logit2, dim=-1) * th_label).sum(1)

        return (loss1 + loss2).mean()

    def get_label(self, logits, num_labels=-1):
        th_logit = logits[:, 0].unsqueeze(1)
        output = torch.zeros_like(logits).to(logits)
        mask = logits > th_logit
        if num_labels > 0:
            top_v, _ = torch.topk(logits, num_labels, dim=1)
            top_v = top_v[:, -1]
            mask = (logits >= top_v.unsqueeze(1)) & mask
        output[mask] = 1.0
        output[:, 0] = (output.sum(1) == 0.0).to(logits)
        return output
