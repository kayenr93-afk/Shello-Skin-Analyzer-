"""Evaluation runner.

    python -m ml.eval.run --reader {prior,gemini_http}
        --manifest ml/data/manifests/<key>.jsonl --split test
        [--max-per-class N] [--seed 0] [--endpoint URL] [--concurrency 2]

Writes predictions.jsonl, metrics.json and report.md under
ml/eval/runs/<YYYYMMDD-HHMMSS>_<reader>_<key>/ and appends a row to
ml/eval/runs/registry.csv.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from ml.eval import metrics as M

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = REPO_ROOT / "ml" / "eval" / "runs"


def load_manifest(path: Path) -> list[dict]:
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def manifest_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_reader(name: str, args, train_majority: str | None):
    if name == "prior":
        from ml.eval.readers.prior import PriorReader

        return PriorReader(majority_skin_type=train_majority)
    if name == "gemini_http":
        from ml.eval.readers.gemini_http import GeminiHttpReader

        return GeminiHttpReader(endpoint=args.endpoint)
    if name == "onnx":
        from ml.eval.readers.onnx_reader import OnnxReader

        return OnnxReader(model_dir=args.model)
    raise SystemExit(f"unknown reader {name!r}")


def merge_landmark_tone(records: list[dict], manifests_dir: Path, dataset_key: str):
    """Prefer landmark-mask tone fields (tone_group2_lm / ita_lm_group6 from the
    face sidecar) over centre-crop ones. Returns the tone source used."""
    sidecar = manifests_dir / f"{dataset_key}.face.jsonl"
    if not sidecar.is_file():
        return "center_crop"
    info = {}
    for line in sidecar.read_text("utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            info[r["image_id"]] = r
    used_lm = 0
    for r in records:
        fi = info.get(r["image_id"], {})
        if fi.get("tone_group2_lm"):
            r["tone_group2"] = fi["tone_group2_lm"]
            used_lm += 1
        if fi.get("ita_lm_group6"):
            r["ita_group6"] = fi["ita_lm_group6"]
    return "landmark" if used_lm else "center_crop"


def train_majority_skin_type(records: list[dict]) -> str | None:
    counts: dict[str, int] = {}
    for r in records:
        if r.get("split") == "train" and r.get("skin_type"):
            counts[r["skin_type"]] = counts.get(r["skin_type"], 0) + 1
    if not counts:
        return None
    return max(sorted(counts), key=lambda k: counts[k])


def subsample_per_class(records: list[dict], max_per_class: int | None, seed: int) -> list[dict]:
    if not max_per_class:
        return records
    rng = np.random.default_rng(seed)
    by_label: dict[str, list[dict]] = {}
    for r in records:
        label = r.get("skin_type") or r.get("tone_label") or "+".join(r.get("concerns_pos") or []) or "unlabelled"
        by_label.setdefault(label, []).append(r)
    out = []
    for label in sorted(by_label):
        rows = by_label[label]
        if len(rows) > max_per_class:
            idx = rng.choice(len(rows), size=max_per_class, replace=False)
            rows = [rows[i] for i in sorted(idx)]
        out.extend(rows)
    return out


def git_state() -> tuple[str | None, bool]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT, timeout=10
        ).stdout.strip() or None
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=REPO_ROOT, timeout=10
            ).stdout.strip()
        )
        return sha, dirty
    except Exception:
        return None, False


def render_report(run: dict) -> str:
    m = run["metrics"]
    lines = [
        f"# Eval run {run['run_id']}",
        "",
        f"- reader: **{run['reader']}** v{run['reader_version']}",
        f"- dataset: `{run['dataset_key']}` split `{run['split']}` (n={m.get('n_total')})",
        f"- manifest sha256: `{run['manifest_sha256'][:16]}…`",
        f"- git: `{run.get('git_sha')}`{' (dirty)' if run.get('git_dirty') else ''}",
        f"- tone source: `{run.get('tone_source', 'center_crop')}`",
        "",
    ]
    st = m.get("skin_type") or {}
    if st.get("n"):
        lines += [
            "## Skin type (cosmetic appearance)",
            "",
            f"- strict accuracy (abstain = wrong): **{st['accuracy']:.3f}** "
            f"(95% CI {st['accuracy_ci95'][0]:.3f}–{st['accuracy_ci95'][1]:.3f})",
            f"- coverage: {st['coverage']:.3f}; accuracy on answered: "
            + (f"{st['accuracy_on_answered']:.3f}" if st["accuracy_on_answered"] is not None else "n/a"),
            f"- macro-F1: {st['macro_f1']:.3f}",
            "",
            "### Per-class",
            "",
            "| class | P | R | F1 | support |",
            "|---|---|---|---|---|",
        ]
        for cls, s in st["per_class"].items():
            lines.append(f"| {cls} | {s['precision']:.3f} | {s['recall']:.3f} | {s['f1']:.3f} | {s['support']} |")
        lines += ["", "### Confusion (rows = truth, cols = predicted)", ""]
        cols = st["confusion"]["cols_predicted"]
        lines.append("| truth \\ pred | " + " | ".join(cols) + " |")
        lines.append("|---|" + "---|" * len(cols))
        for row_label, row in zip(st["confusion"]["rows_truth"], st["confusion"]["matrix"]):
            lines.append(f"| {row_label} | " + " | ".join(str(v) for v in row) + " |")
        lines += ["", f"> {st['confusion']['note']}", ""]

        tone = m.get("tone") or {}
        lines += ["## Fairness by estimated tone (always reported)", ""]
        gap = tone.get("gap_lighter_minus_darker")
        if gap:
            flag = " (insufficient n)" if gap["insufficient_n"] else ""
            lines.append(
                f"- lighter−darker accuracy gap: **{gap['gap']:+.3f}** "
                f"(95% CI {gap['ci95'][0]:+.3f}…{gap['ci95'][1]:+.3f}; "
                f"n={gap['n_lighter']}/{gap['n_darker']}){flag}"
            )
        for title, table in (("tone_group2", tone.get("per_tone_group2")), ("ita_group6", tone.get("per_ita_group6"))):
            if not table:
                continue
            lines += ["", f"### per {title}", "", "| group | n | accuracy | flag |", "|---|---|---|---|"]
            for g, s in table.items():
                flag = "insufficient n" if s.get("insufficient_n") else ""
                lines.append(f"| {g} | {s['n']} | {s['accuracy']:.3f} | {flag} |")
        lines.append("")

    cm = m.get("concerns") or {}
    if cm.get("per_concern") or cm.get("uncovered"):
        lines += ["## Concerns (threshold 0.5, only where the concern is 'known')", ""]
        if cm.get("per_concern"):
            lines += ["| concern | n | prevalence | P | R | F1 |", "|---|---|---|---|---|---|"]
            for c, s in cm["per_concern"].items():
                if not s.get("n"):
                    lines.append(f"| {c} | 0 | - | - | - | - |")
                    continue
                lines.append(
                    f"| {c} | {s['n']} | {s['prevalence']:.3f} | {s['precision']:.3f} | {s['recall']:.3f} | {s['f1']:.3f} |"
                )
            lines.append("")
        if cm.get("uncovered"):
            lines.append(f"Not covered by this reader: {', '.join(cm['uncovered'])}")
            lines.append("")

    if m.get("tone_only"):
        lines += ["## Tone-only dataset: abstain + predicted mix", "", "| tone label | n | abstain rate | predicted mix |", "|---|---|---|---|"]
        for g, s in m["tone_only"].items():
            dist = ", ".join(f"{k}:{v}" for k, v in s["predicted_class_distribution"].items() if v)
            lines.append(f"| {g} | {s['n']} | {s['abstain_rate']:.3f} | {dist} |")
        lines.append("")

    lat = m.get("latency") or {}
    if lat.get("client_ms") or lat.get("server_ms"):
        lines += ["## Latency (ms)", "", "| source | n | p50 | p95 | p99 |", "|---|---|---|---|---|"]
        for name, s in (("client", lat.get("client_ms")), ("server", lat.get("server_ms"))):
            if s:
                lines.append(f"| {name} | {s['n']} | {s['p50']:.0f} | {s['p95']:.0f} | {s['p99']:.0f} |")
        lines.append("")

    lines += ["_Cosmetic appearance evaluation only; not a medical assessment._", ""]
    return "\n".join(lines)


def append_registry(row: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    reg = RUNS_DIR / "registry.csv"
    fields = [
        "run_id",
        "date",
        "git_sha",
        "reader",
        "reader_version",
        "models",
        "prompt_version",
        "dataset_key",
        "manifest_sha256",
        "split",
        "n",
        "accuracy",
        "macro_f1",
        "tone_gap",
        "p95_latency_ms",
    ]
    write_header = not reg.exists()
    with reg.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k) for k in fields})


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run a reader over a manifest split.")
    ap.add_argument("--reader", required=True, choices=["prior", "gemini_http", "onnx"])
    ap.add_argument("--model", default=None, help="Model dir for --reader onnx.")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--split", default="test", choices=["train", "val", "test", "all"])
    ap.add_argument("--max-per-class", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--endpoint", default="http://127.0.0.1:8765/api/analyze")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--data-root", default=None, help="Resolve image paths relative to this root instead of the repo.")
    args = ap.parse_args(argv)

    manifest_path = Path(args.manifest)
    records = load_manifest(manifest_path)
    dataset_key = records[0]["dataset"] if records else manifest_path.stem
    train_majority = train_majority_skin_type(records)

    eval_records = [r for r in records if args.split == "all" or r.get("split") == args.split]
    eval_records = subsample_per_class(eval_records, args.max_per_class, args.seed)
    if not eval_records:
        raise SystemExit(f"no records for split {args.split!r} in {manifest_path}")

    tone_source = merge_landmark_tone(
        eval_records, manifest_path.parent, dataset_key)

    image_root = Path(args.data_root).resolve() if args.data_root else REPO_ROOT
    reader = make_reader(args.reader, args, train_majority)

    def _predict(rec):
        return reader.predict(rec, str(image_root / rec["path"]))

    if reader.supports_concurrency and args.concurrency > 1:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            preds = list(pool.map(_predict, eval_records))
    else:
        preds = [_predict(r) for r in eval_records]

    task = eval_records[0]["task"]
    results = {
        "n_total": len(eval_records),
        "skin_type": M.skin_type_metrics(eval_records, preds, seed=args.seed),
        "tone": M.tone_fairness(eval_records, preds, seed=args.seed),
        "concerns": M.concern_metrics(eval_records, preds),
        "latency": M.latency_metrics(preds),
    }
    if task == "tone":
        results["tone_only"] = M.tone_only_report(eval_records, preds)

    ts = time.strftime("%Y%m%d-%H%M%S")
    run_id = f"{ts}_{args.reader}_{dataset_key}"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    git_sha, git_dirty = git_state()
    models = sorted({(p.get("meta") or {}).get("model") for p in preds if (p.get("meta") or {}).get("model")})
    prompt_versions = sorted(
        {(p.get("meta") or {}).get("prompt_version") for p in preds if (p.get("meta") or {}).get("prompt_version")}
    )

    with (run_dir / "predictions.jsonl").open("w", encoding="utf-8") as fh:
        for rec, pred in zip(eval_records, preds):
            fh.write(json.dumps({"image_id": rec["image_id"], "prediction": pred}, sort_keys=True) + "\n")

    run = {
        "run_id": run_id,
        "date": ts,
        "reader": args.reader,
        "reader_version": reader.version(),
        "dataset_key": dataset_key,
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_sha256(manifest_path),
        "split": args.split,
        "seed": args.seed,
        "git_sha": (git_sha or "unknown") + ("+dirty" if git_dirty else ""),
        "git_dirty": git_dirty,
        "models_seen": models,
        "prompt_versions": prompt_versions,
        "metrics": results,
        "tone_source": tone_source,
    }
    (run_dir / "metrics.json").write_text(json.dumps(run, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "report.md").write_text(render_report(run), encoding="utf-8")

    st = results["skin_type"]
    lat = (results["latency"] or {}).get("client_ms") or {}
    gap = (results["tone"] or {}).get("gap_lighter_minus_darker") or {}
    append_registry(
        {
            "run_id": run_id,
            "date": ts,
            "git_sha": run["git_sha"],
            "reader": args.reader,
            "reader_version": reader.version(),
            "models": ";".join(models),
            "prompt_version": ";".join(prompt_versions),
            "dataset_key": dataset_key,
            "manifest_sha256": run["manifest_sha256"],
            "split": args.split,
            "n": results["n_total"],
            "accuracy": st.get("accuracy"),
            "macro_f1": st.get("macro_f1"),
            "tone_gap": gap.get("gap"),
            "p95_latency_ms": lat.get("p95"),
        }
    )
    print(f"wrote {run_dir}")
    print(json.dumps({"accuracy": st.get("accuracy"), "macro_f1": st.get("macro_f1"), "n": results["n_total"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
