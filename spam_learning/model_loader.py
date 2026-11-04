"""按 manifest 类型安全加载本地建议模型。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .binary_model import BinaryPredictor, MODEL_KIND as BINARY_KIND
from .schemas import SpamSample
from .seed_model import MODEL_KIND as SIMILARITY_KIND, PredictionResult, SimilarityPredictor


class LocalPredictor:
    def __init__(self, predictor: SimilarityPredictor | BinaryPredictor, kind: str) -> None:
        self._predictor = predictor
        self.kind = kind

    @classmethod
    def from_path(cls, path: str | os.PathLike[str]) -> "LocalPredictor":
        kind = SIMILARITY_KIND
        try:
            manifest_path = Path(path) / "manifest.json"
            if manifest_path.stat().st_size > 1024 * 1024:
                raise OSError("manifest 过大")
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("kind") == BINARY_KIND:
                kind = BINARY_KIND
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        predictor = BinaryPredictor.from_path(path) if kind == BINARY_KIND else SimilarityPredictor.from_path(path)
        return cls(predictor, kind)

    @property
    def available(self) -> bool:
        return self._predictor.available

    @property
    def model_version(self) -> str | None:
        return self._predictor.model_version

    def predict(self, sample: SpamSample) -> PredictionResult:
        return self._predictor.predict(sample)

    def predict_text(self, text: str) -> PredictionResult:
        return self._predictor.predict_text(text)
