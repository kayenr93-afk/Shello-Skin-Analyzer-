"""Validate estimated ITA tone groups against a labelled tone dataset.

    python -m ml.eval.tone_check --manifest ml/data/manifests/skintone.jsonl

Prints a confusion table (dataset tone label x estimated ita_group6) and the
Spearman rank correlation between the ordinal dataset label and ITA, and writes
a tone_check.md report next to the manifest.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from ml.data.build_manifests import DEFAULT_CONFIG, load_config
from ml.eval.run import load_manifest


def spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    """Spearman rho via Pearson on average ranks (handles ties)."""
    if len(x) < 2 or np.all(x == x[0]) or np.all(y == y[0]):
        return None
    rx = _rankdata(np.asarray(x, dtype=float))
    ry = _rankdata(np.asarray(y, dtype=float))
    return float(np.corrcoef(rx, ry)[0, 1])


def _rankdata(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    sorted_a = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = ap.parse_args(argv)

    records = load_manifest(Path(args.manifest))
    key = records[0]["dataset"] if records else "skintone"
    cfg = load_config(Path(args.config)).get(key, {})
    order = cfg.get("tone_label_order") or []
    labels = sorted({r["tone_label"] for r in records if r.get("tone_label")})
    label_order = [l for l in order if l in labels] + [l for l in labels if l not in order]
    label_ordinal = {l: i for i, l in enumerate(label_order)}

    groups = ["very_light", "light", "intermediate", "tan", "brown", "dark", "none"]
    table = {l: {g: 0 for g in groups} for l in label_order}
    itas, ords = [], []
    for r in records:
        if not r.get("tone_label"):
            continue
        g = r.get("ita_group6") or "none"
        table[r["tone_label"]][g] += 1
        if r.get("ita") is not None:
            itas.append(r["ita"])
            ords.append(label_ordinal[r["tone_label"]])
    rho = spearman(np.asarray(ords, dtype=float), np.asarray(itas, dtype=float)) if itas else None

    lines = [
        f"# Tone check: {key}",
        "",
        "Dataset tone label vs estimated ITA group.",
        "",
        "| tone_label | " + " | ".join(groups) + " |",
        "|---|" + "---|" * len(groups),
    ]
    for l in label_order:
        lines.append(f"| {l} | " + " | ".join(str(table[l][g]) for g in groups) + " |")
    lines += [
        "",
        f"Label order used for ordinality: {label_order}",
        f"Spearman rho (ordinal label vs ITA, n={len(itas)}): "
        + (f"{rho:.3f}" if rho is not None else "n/a"),
        "",
    ]
    report = "\n".join(lines)
    print(report)
    out = Path(args.manifest).parent / f"{key}.tone_check.md"
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
