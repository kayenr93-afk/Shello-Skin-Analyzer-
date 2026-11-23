"""Quiz-only prior reader.

A stand-in for "what the quiz alone would say". If the record carries
quiz_skin_type / quiz_concerns fields (a future Shello labelled study), those
are used. Otherwise this reader predicts the majority skin type of the
manifest's train split and no concerns.

IMPORTANT: on Kaggle images this is a no-image prior — it never looks at the
photo and it is NOT a stand-in for real quiz answers. It exists so the harness
has a non-trivial baseline and so plumbing can be exercised end to end.
"""

from __future__ import annotations

from ml.eval.readers.base import Reader

READER_VERSION = "1"


class PriorReader(Reader):
    name = "prior"
    supports_concurrency = True  # trivially cheap; concurrency is harmless

    def __init__(self, majority_skin_type: str | None = None):
        self.majority_skin_type = majority_skin_type

    def version(self) -> str:
        return READER_VERSION

    def predict(self, record: dict, image_path: str) -> dict:
        del image_path  # the prior never reads the photo
        skin_type = record.get("quiz_skin_type") or self.majority_skin_type
        quiz_concerns = record.get("quiz_concerns") or []
        concerns = {c: 1.0 for c in quiz_concerns}
        return {
            "skin_type": skin_type,
            "skin_type_probs": None,
            "concerns": concerns,
            "covered_concerns": sorted(concerns),
            "confidence": None,
            "meta": {
                "provider": "prior",
                "model": "majority" if not record.get("quiz_skin_type") else "quiz",
                "prompt_version": None,
                "latency_ms": None,
                "server_latency_ms": None,
            },
        }
