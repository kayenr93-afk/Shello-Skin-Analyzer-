"""Metric computation for evaluation runs.

All "concerns" use the canonical cosmetic vocabulary: breakouts, redness,
dark_spots, pores, fine_lines.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import f1_score, precision_recall_fscore_support

PRED_CLASSES = ["oily", "dry", "normal", "combination", "sensitive", "abstain"]
CANON_CONCERNS = ["breakouts", "redness", "dark_spots", "pores", "fine_lines"]
MIN_TONE_N = 30


def _percentiles(values: list[float]) -> dict | None:
    if not values:
        return None
    arr = np.asarray(values, dtype=float)
    return {
        "n": int(arr.size),
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
    }


def bootstrap_ci(values: np.ndarray, stat_fn, n_boot: int = 1000, seed: int = 0):
    """95% percentile bootstrap CI for stat_fn(resampled values)."""
    rng = np.random.default_rng(seed)
    n = len(values)
    if n == 0:
        return [None, None]
    stats = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        stats.append(stat_fn(values[idx]))
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return [float(lo), float(hi)]


def _accuracy_block(y_true: np.ndarray, correct: np.ndarray, seed: int) -> dict:
    n = int(len(y_true))
    acc = float(correct.mean()) if n else 0.0
    ci = bootstrap_ci(correct.astype(float), lambda v: float(v.mean()), seed=seed)
    return {"n": n, "accuracy": acc, "accuracy_ci95": ci}


def skin_type_metrics(records: list[dict], preds: list[dict], seed: int = 0) -> dict:
    pairs = [(r, p) for r, p in zip(records, preds) if r.get("skin_type")]
    if not pairs:
        return {"n": 0}
    y_true = np.array([r["skin_type"] for r, _ in pairs])
    y_pred_raw = [p.get("skin_type") for _, p in pairs]
    answered = np.array([p is not None for p in y_pred_raw])
    y_pred = np.array([p if p is not None else "abstain" for p in y_pred_raw])
    correct = y_true == y_pred
    n = len(y_true)

    truth_labels = sorted(set(y_true))
    labels_for_f1 = sorted(set(truth_labels) | {c for c in y_pred if c != "abstain"})
    prec, rec, f1, sup = precision_recall_fscore_support(
        y_true, y_pred, labels=labels_for_f1, zero_division=0
    )
    per_class = {
        lab: {
            "precision": float(prec[i]),
            "recall": float(rec[i]),
            "f1": float(f1[i]),
            "support": int(sup[i]),
        }
        for i, lab in enumerate(labels_for_f1)
    }
    macro_f1 = float(f1_score(y_true, y_pred, labels=labels_for_f1, average="macro", zero_division=0))
    # Rows/cols are built manually to keep the fixed predicted-class column
    # order (oily, dry, normal, combination, sensitive, abstain).
    col_labels = PRED_CLASSES
    row_labels = list(truth_labels)
    col_index = {c: j for j, c in enumerate(col_labels)}
    matrix = np.zeros((len(row_labels), len(col_labels)), dtype=int)
    row_index = {c: i for i, c in enumerate(row_labels)}
    for t, p in zip(y_true, y_pred):
        matrix[row_index[t], col_index.get(p, col_labels.index("abstain"))] += 1

    answered_acc = float(correct[answered].mean()) if answered.any() else None

    out = {
        "n": n,
        "accuracy": float(correct.mean()),
        "coverage": float(answered.mean()),
        "accuracy_on_answered": answered_acc,
        "macro_f1": macro_f1,
        "per_class": per_class,
        "confusion": {
            "rows_truth": row_labels,
            "cols_predicted": col_labels,
            "matrix": matrix.tolist(),
            "note": (
                "Truth is 3-class (oily/dry/normal); predicted combination/"
                "sensitive always counts as wrong. Abstain counts as wrong."
            ),
        },
    }
    out.update(_accuracy_block(y_true, correct, seed))
    return out


def tone_fairness(records: list[dict], preds: list[dict], seed: int = 0) -> dict:
    """Accuracy per tone group; always reported, never hidden."""
    pairs = [
        (r, p)
        for r, p in zip(records, preds)
        if r.get("skin_type") and r.get("tone_group2")
    ]
    result = {"per_tone_group2": {}, "per_ita_group6": {}, "gap_lighter_minus_darker": None}
    if not pairs:
        return result

    def _group_stats(key):
        groups = {}
        for r, p in pairs:
            g = r.get(key)
            if not g:
                continue
            groups.setdefault(g, []).append(r["skin_type"] == (p.get("skin_type") or "abstain"))
        return {
            g: {
                "n": len(v),
                "accuracy": float(np.mean(v)),
                "insufficient_n": len(v) < MIN_TONE_N,
            }
            for g, v in sorted(groups.items())
        }

    result["per_tone_group2"] = _group_stats("tone_group2")
    result["per_ita_group6"] = _group_stats("ita_group6")

    lighter = np.array(
        [r["skin_type"] == (p.get("skin_type") or "abstain") for r, p in pairs if r.get("tone_group2") == "lighter"],
        dtype=float,
    )
    darker = np.array(
        [r["skin_type"] == (p.get("skin_type") or "abstain") for r, p in pairs if r.get("tone_group2") == "darker"],
        dtype=float,
    )
    if len(lighter) and len(darker):
        gap = float(lighter.mean() - darker.mean())
        rng = np.random.default_rng(seed)
        boots = []
        for _ in range(1000):
            li = rng.integers(0, len(lighter), len(lighter))
            di = rng.integers(0, len(darker), len(darker))
            boots.append(float(lighter[li].mean() - darker[di].mean()))
        lo, hi = np.percentile(boots, [2.5, 97.5])
        result["gap_lighter_minus_darker"] = {
            "gap": gap,
            "ci95": [float(lo), float(hi)],
            "n_lighter": int(len(lighter)),
            "n_darker": int(len(darker)),
            "insufficient_n": len(lighter) < MIN_TONE_N or len(darker) < MIN_TONE_N,
        }
    return result


def tone_only_report(records: list[dict], preds: list[dict]) -> dict:
    """For tone-task datasets: abstain rate + predicted class mix per group."""
    groups: dict[str, list] = {}
    for r, p in zip(records, preds):
        g = r.get("tone_label") or "unlabelled"
        groups.setdefault(g, []).append(p)
    out = {}
    for g, ps in sorted(groups.items()):
        dist = {c: 0 for c in PRED_CLASSES}
        abst = 0
        for p in ps:
            pred = p.get("skin_type") or "abstain"
            if pred == "abstain":
                abst += 1
            dist[pred if pred in dist else "abstain"] += 1
        out[g] = {
            "n": len(ps),
            "abstain_rate": abst / len(ps),
            "predicted_class_distribution": dist,
        }
    return out


def concern_metrics(records: list[dict], preds: list[dict]) -> dict:
    covered = sorted({c for p in preds for c in (p.get("covered_concerns") or [])})
    per_concern = {}
    for concern in CANON_CONCERNS:
        if concern not in covered:
            continue
        y_true, y_pred = [], []
        for r, p in zip(records, preds):
            if concern not in (r.get("concerns_known") or []):
                continue
            if concern not in (p.get("concerns") or {}):
                continue
            y_true.append(1 if concern in (r.get("concerns_pos") or []) else 0)
            y_pred.append(1 if (p["concerns"].get(concern) or 0.0) >= 0.5 else 0)
        if not y_true:
            per_concern[concern] = {"n": 0}
            continue
        prec, rec, f1, _ = precision_recall_fscore_support(
            y_true, y_pred, labels=[1], zero_division=0
        )
        per_concern[concern] = {
            "n": len(y_true),
            "prevalence": float(np.mean(y_true)),
            "precision": float(prec[0]),
            "recall": float(rec[0]),
            "f1": float(f1[0]),
        }
    return {
        "covered": covered,
        "uncovered": [c for c in CANON_CONCERNS if c not in covered],
        "per_concern": per_concern,
    }


def latency_metrics(preds: list[dict]) -> dict:
    client = [p["meta"]["latency_ms"] for p in preds if (p.get("meta") or {}).get("latency_ms") is not None]
    server = [
        p["meta"]["server_latency_ms"] for p in preds if (p.get("meta") or {}).get("server_latency_ms") is not None
    ]
    return {"client_ms": _percentiles(client), "server_ms": _percentiles(server)}
