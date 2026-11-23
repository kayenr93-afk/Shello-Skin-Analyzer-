"""Metrics tests on hand-computed toy predictions."""

import numpy as np

from ml.eval import metrics as M


def _rec(skin_type=None, pos=(), known=(), tone2=None, tone6=None, tone_label=None):
    return {
        "image_id": "x",
        "skin_type": skin_type,
        "concerns_pos": list(pos),
        "concerns_known": list(known),
        "tone_group2": tone2,
        "ita_group6": tone6,
        "tone_label": tone_label,
        "task": "skin_type" if skin_type else "tone",
    }


def _pred(skin_type=None, concerns=None, covered=(), latency=None):
    return {
        "skin_type": skin_type,
        "skin_type_probs": None,
        "concerns": concerns or {},
        "covered_concerns": list(covered),
        "confidence": None,
        "meta": {"provider": "t", "model": "m", "prompt_version": "v",
                 "latency_ms": latency, "server_latency_ms": None},
    }


def test_skin_type_accuracy_and_confusion():
    records = [
        _rec("oily", tone2="lighter", tone6="light"),
        _rec("oily", tone2="lighter", tone6="light"),
        _rec("dry", tone2="darker", tone6="brown"),
        _rec("normal", tone2="darker", tone6="tan"),
    ]
    preds = [
        _pred("oily"),
        _pred("dry"),      # wrong
        _pred("dry"),
        _pred(None),       # abstain -> wrong
    ]
    m = M.skin_type_metrics(records, preds, seed=0)
    assert m["n"] == 4
    assert m["accuracy"] == 0.5
    assert m["coverage"] == 0.75
    assert abs(m["accuracy_on_answered"] - 2 / 3) < 1e-9
    lo, hi = m["accuracy_ci95"]
    assert lo <= 0.5 <= hi and lo < hi
    cols = m["confusion"]["cols_predicted"]
    rows = m["confusion"]["rows_truth"]
    mat = m["confusion"]["matrix"]
    assert cols == ["oily", "dry", "normal", "combination", "sensitive", "abstain"]
    # truth 'oily' row: one oily, one dry
    oily_row = mat[rows.index("oily")]
    assert oily_row[cols.index("oily")] == 1 and oily_row[cols.index("dry")] == 1
    # truth 'normal' row: single abstain
    normal_row = mat[rows.index("normal")]
    assert normal_row[cols.index("abstain")] == 1


def test_tone_fairness_groups_and_gap():
    records = [_rec("oily", tone2="lighter", tone6="light")] * 4 + [
        _rec("oily", tone2="darker", tone6="brown")
    ] * 4
    preds = [_pred("oily")] * 4 + [_pred("dry")] * 4
    t = M.tone_fairness(records, preds, seed=0)
    assert t["per_tone_group2"]["lighter"]["accuracy"] == 1.0
    assert t["per_tone_group2"]["darker"]["accuracy"] == 0.0
    assert t["per_tone_group2"]["lighter"]["insufficient_n"] is True  # n<30
    gap = t["gap_lighter_minus_darker"]
    assert gap["gap"] == 1.0
    assert gap["insufficient_n"] is True


def test_concern_masking_and_uncovered():
    # breakouts covered by reader; pores not covered -> listed as uncovered.
    records = [
        _rec(pos=["breakouts"], known=["breakouts", "pores"]),
        _rec(pos=[], known=["breakouts"]),
        _rec(pos=["pores"], known=["pores"]),          # breakouts unknown here
    ]
    preds = [
        _pred(concerns={"breakouts": 0.9}, covered=["breakouts"]),
        _pred(concerns={"breakouts": 0.1}, covered=["breakouts"]),
        _pred(concerns={"breakouts": 0.9}, covered=["breakouts"]),
    ]
    cm = M.concern_metrics(records, preds)
    assert cm["covered"] == ["breakouts"]
    assert "pores" in cm["uncovered"] and "redness" in cm["uncovered"]
    b = cm["per_concern"]["breakouts"]
    assert b["n"] == 2          # third record excluded: breakouts not 'known'
    assert b["precision"] == 1.0 and b["recall"] == 1.0 and b["f1"] == 1.0


def test_tone_only_report():
    records = [_rec(tone_label="light"), _rec(tone_label="light"), _rec(tone_label="dark")]
    preds = [_pred("oily"), _pred(None), _pred("sensitive")]
    r = M.tone_only_report(records, preds)
    assert r["light"]["n"] == 2
    assert r["light"]["abstain_rate"] == 0.5
    assert r["dark"]["predicted_class_distribution"]["sensitive"] == 1


def test_latency_metrics():
    preds = [_pred(latency=v) for v in (10, 20, 30, 40)]
    lat = M.latency_metrics(preds)
    assert lat["client_ms"]["n"] == 4
    assert lat["client_ms"]["p50"] == np.percentile([10, 20, 30, 40], 50)
    assert lat["server_ms"] is None
