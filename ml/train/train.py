"""Phase 2B trainer: shared backbone, skin-type head + masked multi-label
concern head, calibrated on val.

    python -m ml.train.train --variant shippable --epochs 20
        [--out ml/artifacts/<run_id>] [--limit N] [--seed 0]

Each step consumes one class-balanced batch of 32 face crops and one batch of
16 concern bags (patches forwarded together, logits scatter-maxed per bag).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import transforms

from ml.train.data import (CONCERNS, SKIN_TYPES, bag_labels, center_square_crop,
                           concern_samples, load_conflicts, load_face_info,
                           load_manifest_rows, skin_samples)
from ml.train.model import SkinReader

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = REPO_ROOT / "ml" / "data" / "manifests"
PROCESSED = REPO_ROOT / "data" / "processed" / "v1"
DATASETS_JSON = REPO_ROOT / "ml" / "datasets.json"

MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)

FACE_AUG = transforms.Compose([
    transforms.RandomResizedCrop(224, scale=(0.7, 1.0)),
    transforms.RandomHorizontalFlip(),
    transforms.RandomRotation(10),
    transforms.ColorJitter(brightness=0.15, contrast=0.15, saturation=0.05, hue=0),
    transforms.ToTensor(),
    transforms.Normalize(MEAN, STD),
])
PATCH_AUG = transforms.Compose([
    transforms.RandomResizedCrop(224, scale=(0.5, 1.0)),
    transforms.RandomHorizontalFlip(),
    transforms.RandomRotation(10),
    transforms.ColorJitter(brightness=0.15, contrast=0.15, saturation=0.05, hue=0),
    transforms.ToTensor(),
    transforms.Normalize(MEAN, STD),
])
EVAL_TF = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(MEAN, STD),
])


def git_sha():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=10,
        )
        sha = out.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except Exception:
        return "unknown"


class FaceDataset(Dataset):
    def __init__(self, samples, tf):
        self.samples, self.tf = samples, tf

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, i):
        s = self.samples[i]
        img = Image.open(s["path"]).convert("RGB")
        return self.tf(img), s["label"]


class BagDataset(Dataset):
    """Yields (patches_tensor[K,3,224,224], y[5], known[5]). K varies."""
    def __init__(self, bags, tf):
        self.bags, self.tf = bags, tf

    def __len__(self):
        return len(self.bags)

    def __getitem__(self, i):
        b = self.bags[i]
        if b["patches"]:
            imgs = [Image.open(p).convert("RGB") for p in b["patches"]]
        else:
            imgs = [center_square_crop(Image.open(REPO_ROOT / b["raw_path"]).convert("RGB"))]
        xs = torch.stack([self.tf(im) for im in imgs])
        y, m = bag_labels(b)
        return xs, torch.tensor(y), torch.tensor(m)


def bag_collate(items):
    """Concat patches; keep per-image bag index for scatter-max."""
    xs, ys, ms, idx = [], [], [], []
    for i, (x, y, m) in enumerate(items):
        xs.append(x)
        idx += [i] * x.shape[0]
        ys.append(y)
        ms.append(m)
    return torch.cat(xs), torch.stack(ys), torch.stack(ms), torch.tensor(idx)


def bag_max(logits, bag_idx, n_bags):
    """Scatter-max pooling of per-patch concern logits -> [n_bags, 5]."""
    logits = logits.float()
    out = torch.full((n_bags, logits.shape[1]), -1e9, device=logits.device)
    out.scatter_reduce_(0, bag_idx.unsqueeze(1).expand(-1, logits.shape[1]),
                        logits, reduce="amax")
    return out


def masked_bce(logits, y, mask):
    """BCE only over concerns that are 'known' for the bag."""
    loss = F.binary_cross_entropy_with_logits(logits, y, reduction="none")
    m = mask.sum()
    if m == 0:
        return logits.sum() * 0.0
    return (loss * mask).sum() / m


def balanced_sampler(samples):
    counts = np.bincount([s["label"] for s in samples], minlength=3)
    weights = [1.0 / counts[s["label"]] for s in samples]
    return WeightedRandomSampler(weights, num_samples=len(samples), replacement=True)


def forward_face_batch(model, x, device):
    with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=device.type == "cuda"):
        return model(x.to(device))


def eval_pass(model, face_samples, bag_samples, device, bs=64):
    """-> predictions dict on the given split."""
    model.eval()
    skin_true, skin_prob = [], []
    with torch.no_grad():
        ds = FaceDataset(face_samples, EVAL_TF)
        for xb, yb in DataLoader(ds, batch_size=bs, num_workers=0):
            s_log, _ = model(xb.to(device))
            skin_prob.append(torch.softmax(s_log.float(), -1).cpu())
            skin_true.append(yb)
    skin_true = torch.cat(skin_true).numpy() if skin_true else np.array([])
    skin_prob = torch.cat(skin_prob).numpy() if skin_prob else np.zeros((0, 3))

    bag_y, bag_m, bag_prob = [], [], []
    with torch.no_grad():
        for bag in bag_samples:
            if bag["patches"]:
                imgs = [Image.open(p).convert("RGB") for p in bag["patches"]]
            else:
                imgs = [center_square_crop(
                    Image.open(REPO_ROOT / bag["raw_path"]).convert("RGB"))]
            xs = torch.stack([EVAL_TF(im) for im in imgs]).to(device)
            _, c_log = model(xs)
            bag_prob.append(torch.sigmoid(c_log.float().max(0).values).cpu().numpy())
            y, m = bag_labels(bag)
            bag_y.append(y)
            bag_m.append(m)
    return {
        "skin_true": skin_true, "skin_prob": skin_prob,
        "bag_y": np.array(bag_y), "bag_m": np.array(bag_m),
        "bag_prob": np.array(bag_prob),
    }


def skin_macro_f1(y_true, y_prob):
    from sklearn.metrics import f1_score
    if len(y_true) == 0:
        return 0.0
    pred = y_prob.argmax(1)
    return float(f1_score(y_true, pred, labels=[0, 1, 2], average="macro", zero_division=0))


def mean_auroc(y, m, p):
    from sklearn.metrics import roc_auc_score
    vals = []
    for c in range(len(CONCERNS)):
        keep = m[:, c] == 1
        if keep.sum() and len(np.unique(y[keep, c])) > 1:
            vals.append(float(roc_auc_score(y[keep, c], p[keep, c])))
    return float(np.mean(vals)) if vals else 0.0


def select_thresholds(y, m, p):
    """Per-concern threshold maximising F1 on val."""
    from sklearn.metrics import f1_score
    ths = {}
    for c, name in enumerate(CONCERNS):
        keep = m[:, c] == 1
        best_t, best_f1 = 0.5, -1.0
        if keep.sum() and len(np.unique(y[keep, c])) > 1:
            for t in np.arange(0.05, 0.95, 0.02):
                f1 = f1_score(y[keep, c], (p[keep, c] >= t).astype(int), zero_division=0)
                if f1 > best_f1:
                    best_f1, best_t = f1, float(t)
        ths[name] = best_t
    return ths


def select_tau(y_true, y_prob, min_acc=0.85, min_cov=0.40):
    """Smallest max-softmax threshold with accepted-accuracy >= min_acc and
    coverage >= min_cov. Returns (tau|None, curve rows)."""
    if len(y_true) == 0:
        return None, []
    conf = y_prob.max(1)
    pred = y_prob.argmax(1)
    curve = []
    tau = None
    for t in np.arange(0.30, 0.99, 0.01):
        acc_mask = conf >= t
        cov = float(acc_mask.mean())
        acc = float((pred[acc_mask] == y_true[acc_mask]).mean()) if acc_mask.any() else 0.0
        curve.append({"tau": round(float(t), 2), "coverage": round(cov, 4),
                      "accuracy": round(acc, 4)})
        if acc >= min_acc and cov >= min_cov and tau is None:
            tau = float(t)
    return tau, curve


def evaluate_val(model, val_faces, val_bags, device):
    ev = eval_pass(model, val_faces, val_bags, device)
    return {
        "skin_macro_f1": skin_macro_f1(ev["skin_true"], ev["skin_prob"]),
        "concern_auroc": mean_auroc(ev["bag_y"], ev["bag_m"], ev["bag_prob"]),
        "_ev": ev,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["shippable", "research"], required=True)
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0, help="Cap train samples per task.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--skin-batch", type=int, default=32)
    ap.add_argument("--bag-batch", type=int, default=16)
    args = ap.parse_args(argv)

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    run_id = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}_{args.variant}"
    out_dir = Path(args.out) if args.out else REPO_ROOT / "ml" / "artifacts" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = json.loads(DATASETS_JSON.read_text("utf-8"))
    rows = load_manifest_rows(MANIFESTS, args.variant, cfg)
    face_info = load_face_info(MANIFESTS, PROCESSED)
    conflicts = load_conflicts(MANIFESTS)
    skin, excl = skin_samples(rows, face_info, PROCESSED, conflicts)
    bags = concern_samples(rows, face_info, PROCESSED)
    print("skin exclusion counts:", excl)
    print(f"samples: skin={len(skin)} bags={len(bags)}")

    train_faces = [s for s in skin if s["split"] == "train"]
    val_faces = [s for s in skin if s["split"] == "val"]
    train_bags = [b for b in bags if b["split"] == "train"]
    val_bags = [b for b in bags if b["split"] == "val"]
    test_bags = [b for b in bags if b["split"] == "test"]
    if args.limit:
        train_faces = train_faces[: args.limit]
        train_bags = train_bags[: args.limit]
        val_faces = val_faces[: args.limit]
        val_bags = val_bags[: args.limit]
    print(f"train faces={len(train_faces)} val faces={len(val_faces)} "
          f"train bags={len(train_bags)} val bags={len(val_bags)} test bags={len(test_bags)}")

    face_loader = iter(DataLoader(
        FaceDataset(train_faces, FACE_AUG), batch_size=args.skin_batch,
        sampler=balanced_sampler(train_faces) if train_faces else None,
        num_workers=0, drop_last=True))
    bag_loader = DataLoader(
        BagDataset(train_bags, PATCH_AUG), batch_size=args.bag_batch,
        shuffle=True, num_workers=0, collate_fn=bag_collate, drop_last=False)

    model = SkinReader().to(device)
    head_params = list(model.skin_head.parameters()) + list(model.concern_head.parameters())
    opt = torch.optim.AdamW([
        {"params": model.backbone.parameters(), "lr": 3e-4},
        {"params": head_params, "lr": 1e-3},
    ], weight_decay=0.05)
    scaler = torch.GradScaler("cuda", enabled=device.type == "cuda")

    steps_per_epoch = max(len(bag_loader), 1)
    warmup_steps = steps_per_epoch
    total_steps = steps_per_epoch * args.epochs

    def lr_at(step):
        if step < warmup_steps:
            return step / max(1, warmup_steps)
        t = (step - warmup_steps) / max(1, total_steps - warmup_steps)
        return 0.5 * (1 + math.cos(math.pi * t))

    log_path = out_dir / "train_log.csv"
    log_fh = log_path.open("w", newline="", encoding="utf-8")
    logw = csv.writer(log_fh)
    logw.writerow(["epoch", "loss_ce", "loss_bce", "lr_scale", "epoch_s",
                   "val_skin_macro_f1", "val_concern_auroc", "val_score"])

    (out_dir / "config.json").write_text(json.dumps({
        "run_id": run_id, "variant": args.variant, "epochs": args.epochs,
        "git": git_sha(), "device": str(device),
        "skin_exclusions": excl,
        "n": {"train_faces": len(train_faces), "val_faces": len(val_faces),
              "train_bags": len(train_bags), "val_bags": len(val_bags),
              "test_bags": len(test_bags)},
    }, indent=2))

    best = {"score": -1}
    step = 0
    for epoch in range(args.epochs):
        model.train()
        t0 = time.time()
        ce_sum = bce_sum = nb = 0
        bag_iter = iter(bag_loader)
        for _ in range(steps_per_epoch):
            scale = lr_at(step)
            for g, base in zip(opt.param_groups, (3e-4, 1e-3)):
                g["lr"] = base * scale
            step += 1
            try:
                xb, yb = next(face_loader)
            except StopIteration:
                face_loader = iter(DataLoader(
                    FaceDataset(train_faces, FACE_AUG), batch_size=args.skin_batch,
                    sampler=balanced_sampler(train_faces), num_workers=0, drop_last=True))
                xb, yb = next(face_loader)
            try:
                px, by, bm, bidx = next(bag_iter)
            except StopIteration:
                bag_iter = iter(bag_loader)
                px, by, bm, bidx = next(bag_iter)

            opt.zero_grad(set_to_none=True)
            with torch.autocast(device_type=device.type, dtype=torch.float16,
                                enabled=device.type == "cuda"):
                s_log, c_patch = model(
                    torch.cat([xb, px], 0).to(device))
                s_log = s_log[: len(xb)]
                c_log = bag_max(c_patch[len(xb):], bidx.to(device), by.shape[0])
                loss_ce = F.cross_entropy(s_log.float(), yb.to(device), label_smoothing=0.05)
                loss_bce = masked_bce(c_log, by.to(device), bm.to(device))
                loss = loss_ce + loss_bce
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            ce_sum += loss_ce.item()
            bce_sum += loss_bce.item()
            nb += 1

        ev = evaluate_val(model, val_faces, val_bags, device)
        score = 0.5 * ev["skin_macro_f1"] + 0.5 * ev["concern_auroc"]
        dt = time.time() - t0
        print(f"epoch {epoch + 1}/{args.epochs} {dt:.0f}s "
              f"ce={ce_sum / max(1, nb):.4f} bce={bce_sum / max(1, nb):.4f} "
              f"val f1={ev['skin_macro_f1']:.3f} auroc={ev['concern_auroc']:.3f} score={score:.3f}",
              flush=True)
        logw.writerow([epoch + 1, round(ce_sum / max(1, nb), 5), round(bce_sum / max(1, nb), 5),
                       round(scale, 4), round(dt, 1), round(ev["skin_macro_f1"], 4),
                       round(ev["concern_auroc"], 4), round(score, 4)])
        log_fh.flush()
        if score > best["score"]:
            best = {"score": score, "epoch": epoch + 1}
            torch.save({"model": model.state_dict(), "epoch": epoch + 1,
                        "variant": args.variant}, out_dir / "best.pt")
    log_fh.close()

    # ----- calibration on val (use test bags too? spec: calibrate on val) -----
    ev = eval_pass(model, val_faces, val_bags, device)
    thresholds = select_thresholds(ev["bag_y"], ev["bag_m"], ev["bag_prob"])
    tau, curve = select_tau(ev["skin_true"], ev["skin_prob"])
    cal = {"thresholds": thresholds, "tau": tau, "coverage_curve": curve}
    (out_dir / "calibration.json").write_text(json.dumps(cal, indent=2))
    print("calibration:", thresholds, "tau:", tau)
    print(f"run dir: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
