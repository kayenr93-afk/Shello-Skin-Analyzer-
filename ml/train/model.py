"""Shared MobileNetV3 backbone + two heads (skin type x3, concerns x5)."""

from __future__ import annotations

import timm
import torch
import torch.nn as nn


class SkinReader(nn.Module):
    def __init__(self, backbone_name: str = "mobilenetv3_large_100", pretrained: bool = True):
        super().__init__()
        self.backbone = timm.create_model(
            backbone_name, pretrained=pretrained, num_classes=0, global_pool="avg"
        )
        d = getattr(self.backbone, "head_hidden_size", None) or self.backbone.num_features
        self.skin_head = nn.Linear(d, 3)
        self.concern_head = nn.Linear(d, 5)

    def forward(self, x):
        feat = self.backbone(x)
        return self.skin_head(feat), self.concern_head(feat)
