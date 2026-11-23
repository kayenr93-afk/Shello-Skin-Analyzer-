# Eval run 20261123-191615_onnx_skin_type_odn

- reader: **onnx** vsr-smoke
- dataset: `skin_type_odn` split `test` (n=30)
- manifest sha256: `5150ed46502a9b99…`
- git: `4b8fb77bac65745276bcb2514865859fb2902fad+dirty` (dirty)
- tone source: `center_crop`

## Skin type (cosmetic appearance)

- strict accuracy (abstain = wrong): **0.267** (95% CI 0.100–0.433)
- coverage: 0.800; accuracy on answered: 0.333
- macro-F1: 0.283

### Per-class

| class | P | R | F1 | support |
|---|---|---|---|---|
| dry | 0.333 | 0.100 | 0.154 | 10 |
| normal | 0.250 | 0.300 | 0.273 | 10 |
| oily | 0.444 | 0.400 | 0.421 | 10 |

### Confusion (rows = truth, cols = predicted)

| truth \ pred | oily | dry | normal | combination | sensitive | abstain |
|---|---|---|---|---|---|---|
| dry | 1 | 1 | 5 | 0 | 0 | 3 |
| normal | 4 | 0 | 3 | 0 | 0 | 3 |
| oily | 4 | 2 | 4 | 0 | 0 | 0 |

> Truth is 3-class (oily/dry/normal); predicted combination/sensitive always counts as wrong. Abstain counts as wrong.

## Fairness by estimated tone (always reported)

- lighter−darker accuracy gap: **-0.072** (95% CI -0.403…+0.258; n=17/13) (insufficient n)

### per tone_group2

| group | n | accuracy | flag |
|---|---|---|---|
| darker | 13 | 0.308 | insufficient n |
| lighter | 17 | 0.235 | insufficient n |

### per ita_group6

| group | n | accuracy | flag |
|---|---|---|---|
| brown | 7 | 0.429 | insufficient n |
| dark | 1 | 0.000 | insufficient n |
| intermediate | 5 | 0.000 | insufficient n |
| light | 8 | 0.375 | insufficient n |
| tan | 5 | 0.200 | insufficient n |
| very_light | 4 | 0.250 | insufficient n |

## Concerns (threshold 0.5, only where the concern is 'known')

| concern | n | prevalence | P | R | F1 |
|---|---|---|---|---|---|
| breakouts | 0 | - | - | - | - |
| redness | 0 | - | - | - | - |
| dark_spots | 0 | - | - | - | - |
| pores | 0 | - | - | - | - |
| fine_lines | 0 | - | - | - | - |

## Latency (ms)

| source | n | p50 | p95 | p99 |
|---|---|---|---|---|
| client | 30 | 5783 | 16744 | 54000 |

_Cosmetic appearance evaluation only; not a medical assessment._
