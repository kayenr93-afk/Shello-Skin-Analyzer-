# Shello Skin Analyzer — agent notes

Static HTML/CSS/JS app (`index.html`, `js/`, `css/`) + `/api/*` serverless functions
(Node on Vercel, mirrored by `server.py` locally). ML/eval code lives in `ml/` with its own
venv at `ml/.venv` (Windows paths below).

## Run locally
- `ml\.venv\Scripts\python server.py --port 8765` → http://127.0.0.1:8765
- `?reader=device` forces the on-device skin reader; `?reader=cloud` disables it.

## Verify before committing
- `npm run test:privacy` — proves no photo reaches disk/logs/DB (run every release)
- `npm run test:events` — event validation, storage, delete, dashboard auth
- `ml\.venv\Scripts\python tests\e2e_phase1.py` — Playwright: quality gate, confirm/correct, events, si/ta, CSP (set `PYTHONIOENCODING=utf-8`)
- `ml\.venv\Scripts\python tests\screens_revamp.py` — phone-width screenshots of every step → `screenshots/revamp-*.png`
- `ml\.venv\Scripts\python tests\parity_device_reader.py` — browser vs Python ONNX parity (needs `models/skin_reader/active.json`)
- `ml\.venv\Scripts\python -m pytest ml/tests -q` — ML unit tests
- `node --check js/*.js`

## Conventions
- Every new UI string needs en/si/ta in `js/i18n.js`; si/ta need native-speaker proofreading.
- The event schema (`api/_lib/event_schema.json`) is shared by Node and Python validators and
  must never contain image-ish words (`selfie`, `photo`, `image`, `base64`…) — the privacy test enforces it.
- Cosmetic language only (no diagnosis/treatment wording), including code and labels.
- `skin_issues` dataset licence is Unknown: never ship a model trained on it.
- Machine has 8 GB RAM: run `ml.face.preprocess` with `--workers 3` or it thrashes.
