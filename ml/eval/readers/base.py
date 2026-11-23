"""Reader interface.

A reader turns one manifest record + image file into a prediction dict:

    {
        "skin_type": str | None,           # None = abstain
        "skin_type_probs": dict | None,
        "concerns": {canon_concern: 0..1}, # only concerns the reader covers
        "covered_concerns": [str],
        "confidence": str | float | None,
        "meta": {
            "provider": str | None,
            "model": str | None,
            "prompt_version": str | None,
            "latency_ms": float | None,        # client-observed
            "server_latency_ms": float | None, # reported by the API
        },
    }
"""

from __future__ import annotations


class Reader:
    name = "base"
    supports_concurrency = False

    def version(self) -> str:
        return "0"

    def predict(self, record: dict, image_path: str) -> dict:
        raise NotImplementedError
