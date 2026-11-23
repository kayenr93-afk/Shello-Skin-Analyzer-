# Shello Skin Analyzer

A standalone, mobile-first web app that recommends a 2–3 step Shello Cosmetics routine from the public store catalog. It does not change the shop or CMS.

## Rebuild the catalog

```bash
python3 build_catalog.py
```

This reads the public WooCommerce Store API. If that fails, it can use read-only WooCommerce REST credentials from `WC_CONSUMER_KEY` and `WC_CONSUMER_SECRET`. It writes `catalog.json` and `catalog.csv`. Sizes are taken only from names, attributes, or the published weight field.

## Run locally

```bash
python3 server.py          # http://127.0.0.1:8765
```

Quiz matching runs in the browser and does not need an AI key. Optional selfie analysis uses `GEMINI_API_KEY` (preferred) or `OPENAI_API_KEY`. For local tests, the key may be placed in `.env.gemini` (chmod 600). Photos are not stored.

```bash
python3 tests/run_personas.py
```

## Branding

Colours, type and the logo are isolated in `css/styles.css` (`:root` tokens) and `assets/logo.png`. Official green `#01652F`, red `#EE1C25` and ink `#231F20` were sampled from `assets/brand/shello-logo-original.jpg`. Regenerating web assets:

```bash
python3 assets/brand/prepare_logo.py
```

This writes the transparent lockup, the header logo, favicon, apple-touch icon and the 1200×630 share image.

## Vercel

This repo is a static site plus `api/analyze.js`. Set `GEMINI_API_KEY` (or `OPENAI_API_KEY`) in the project environment. Do not commit `.env*` files.

## Daily catalog update

Weekday price and stock updates reuse the shared shop file `/workspace/shello/price-list.json` (refreshed at 8:00 AM Asia/Colombo). They do not crawl the shop again and they never add new products.

```bash
/workspace/skin-analyzer/refresh_from_shared.sh
/workspace/skin-analyzer/refresh_from_shared.sh --dry-run
```

Schedule this after the 8:00 AM shared refresh, for example 8:15 AM Asia/Colombo. On Sunday, or when the shared list has product ids that are not yet in `catalog.json`, the wrapper runs a full `refresh_and_deploy.sh` rebuild instead so new products can be classified and given images.

`apply_shared_prices.py` exit codes match `build_catalog.py`: 0 unchanged, 2 changed, 1 error.
