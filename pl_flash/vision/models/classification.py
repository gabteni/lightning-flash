import os
from typing import Callable, List, Mapping, Sequence, Type, Union

import pandas as pd
import pytorch_lightning as pl
import torch
import torchvision
from pytorch_lightning.metrics import Accuracy
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader

from pl_flash.model import ClassificationLightningTask
from pl_flash.vision.data.classification import _pil_loader, FlashDatasetFolder

_resnet_backbone = lambda model: nn.Sequential(*list(model.children())[:-2])  # noqa: E731
_resnet_feats = lambda model: model.fc.in_features  # noqa: E731

_backbones = {
    "resnet18": (torchvision.models.resnet18, _resnet_backbone, _resnet_feats),
    "resnet34": (torchvision.models.resnet34, _resnet_backbone, _resnet_feats),
    "resnet50": (torchvision.models.resnet50, _resnet_backbone, _resnet_feats),
    "resnet101": (torchvision.models.resnet101, _resnet_backbone, _resnet_feats),
    "resnet152": (torchvision.models.resnet152, _resnet_backbone, _resnet_feats),
}


class ImageClassifier(ClassificationLightningTask):
    """LightningTask that classifies images.

    Args:
        num_classes: Number of classes to classify.
        backbone: A model to use to compute image features.
        pretrained: Use a pretrained backbone.
        loss_fn: Loss function for training, defaults to cross entropy.
        optimizer: Optimizer to use for training, defaults to `torch.optim.SGD`.
        metrics: Metrics to compute for training and evaluation.
        learning_rate: Learning rate to use for training, defaults to `1e-3`
    """

    def __init__(
        self,
        num_classes,
        backbone="resnet18",
        pretrained=True,
        loss_fn: Callable = F.cross_entropy,
        optimizer: Type[torch.optim.Optimizer] = torch.optim.SGD,
        metrics: Union[Callable, Mapping, Sequence, None] = [Accuracy()],
        learning_rate: float = 1e-3,
    ):
        super().__init__(
            model=None,
            loss_fn=loss_fn,
            optimizer=optimizer,
            metrics=metrics,
            learning_rate=learning_rate,
        )

        self._predict = False

        if backbone not in _backbones:
            raise NotImplementedError(f"Backbone {backbone} is not yet supported")

        backbone_fn, split, num_feats = _backbones[backbone]
        backbone = backbone_fn(pretrained=pretrained)
        self.backbone = split(backbone)
        num_features = num_feats(backbone)

        self.head = nn.Sequential(
            nn.AdaptiveAvgPool2d((1, 1)),
            nn.Flatten(),
            nn.Linear(num_features, num_classes),
        )

    def forward(self, x):
        x = self.backbone(x)
        return self.head(x)

    def freeze(self):
        """
        Freeze the backbone parameters.
        """
        for p in self.backbone.parameters():
            p.requires_grad = False

    def unfreeze(self):
        """
        Unfreeze the backbone parameters.
        """
        for p in self.backbone.parameters():
            p.requires_grad = True

    def _check_path_exists(self, img_paths: List[str]):
        for p in img_paths:
            assert os.path.exists(p)

    def predict(
        self,
        img_paths: List[str],
        loader: Callable = _pil_loader,
        transform=None,
        batch_size: int = 2,
        num_workers: int = 0,
        **kwargs
    ):

        self._predict = True
        self._check_path_exists(img_paths)
        assert transform is not None

        test_dataloaders = [
            DataLoader(
                FlashDatasetFolder(None, loader, transform=transform, predict=True, img_paths=img_paths),
                batch_size=batch_size,
                num_workers=num_workers,
            )
        ]

        trainer = pl.Trainer(**kwargs)

        results = trainer.test(self, test_dataloaders=test_dataloaders)
        outputs = []
        if "predictions" in results[0]:
            for r in results:
                for pred in r["predictions"]:
                    pred["id"] = img_paths[pred["id"]]
                outputs.append(pd.json_normalize(r["predictions"], sep='_'))
        else:
            results = outputs
        return outputs
