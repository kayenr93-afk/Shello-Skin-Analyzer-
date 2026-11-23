"""Unit tests for Phase 2B training primitives (synthetic tensors only)."""

import numpy as np
import pytest
import torch

from ml.train.train import bag_max, masked_bce, select_tau, select_thresholds


def test_bag_max_pooling():
    logits = torch.tensor([
        [1.0, -2.0],
        [3.0, 0.5],
        [-1.0, 4.0],
        [0.0, -1.0],
    ])
    bag_idx = torch.tensor([0, 0, 1, 1])
    out = bag_max(logits, bag_idx, 2)
    assert torch.allclose(out, torch.tensor([[3.0, 0.5], [0.0, 4.0]]))


def test_bag_max_single_patch():
    logits = torch.tensor([[2.0, 1.0, 0.0]])
    out = bag_max(logits, torch.tensor([0]), 1)
    assert torch.equal(out, logits)


def test_masked_bce_ignores_unknown():
    logits = torch.zeros(1, 5)
    y = torch.zeros(1, 5)
    mask_known = torch.tensor([[1.0, 0.0, 0.0, 0.0, 0.0]])
    loss_known = masked_bce(logits, y, mask_known)
    # BCE at logit 0 = ln(2) only over the one known entry.
    assert torch.isclose(loss_known, torch.tensor(0.6931, dtype=torch.float32), atol=1e-3)
    # Unknown entries contribute nothing even if their labels differ.
    y2 = torch.ones(1, 5)
    loss2 = masked_bce(logits, y2, mask_known)
    assert torch.isclose(loss2, torch.tensor(0.6931, dtype=torch.float32), atol=1e-3)


def test_masked_bce_zero_mask_is_finite():
    loss = masked_bce(torch.zeros(2, 5), torch.zeros(2, 5), torch.zeros(2, 5))
    assert loss.item() == 0.0 and torch.isfinite(loss)


def test_select_thresholds_picks_f1_optimum():
    rng = np.random.RandomState(0)
    n = 200
    p = np.zeros((n, 5))
    y = np.zeros((n, 5))
    m = np.ones((n, 5))
    # Concern 0: clean signal, best threshold near 0.5.
    y[:, 0] = (rng.rand(n) < 0.4).astype(float)
    p[:, 0] = y[:, 0] * 0.6 + rng.rand(n) * 0.3
    ths = select_thresholds(y, m, p)
    assert 0.3 <= ths["breakouts"] <= 0.75
    # Concern 1: only one class -> default 0.5.
    assert ths["redness"] == 0.5


def test_select_tau_threshold_and_curve():
    rng = np.random.RandomState(1)
    n = 400
    y = rng.randint(0, 3, n)
    prob = rng.dirichlet([1.0, 1.0, 1.0], size=n)
    # Inject confident-correct predictions for a subset.
    idx = rng.choice(n, size=200, replace=False)
    for i in idx:
        prob[i] = 0.01
        prob[i, y[i]] = 0.98
    tau, curve = select_tau(y, prob, min_acc=0.85, min_cov=0.40)
    assert tau is not None
    accepted = prob.max(1) >= tau
    assert accepted.mean() >= 0.40
    assert (prob.argmax(1)[accepted] == y[accepted]).mean() >= 0.85
    assert len(curve) > 0


def test_select_tau_returns_none_when_unreachable():
    # Pure noise: accepted accuracy never reaches 0.85.
    rng = np.random.RandomState(2)
    y = rng.randint(0, 3, 300)
    prob = rng.dirichlet([1.0, 1.0, 1.0], size=300)
    tau, curve = select_tau(y, prob, min_acc=0.85, min_cov=0.40)
    assert tau is None
    assert curve  # curve still recorded
