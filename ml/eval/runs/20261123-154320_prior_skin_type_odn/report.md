# Eval run 20261123-154320_prior_skin_type_odn

- reader: **prior** v1
- dataset: `skin_type_odn` split `test` (n=490)
- manifest sha256: `aef6e76ed3e987fc…`
- git: `4b8fb77bac65745276bcb2514865859fb2902fad+dirty` (dirty)

## Skin type (cosmetic appearance)

- strict accuracy (abstain = wrong): **0.404** (95% CI 0.361–0.449)
- coverage: 1.000; accuracy on answered: 0.404
- macro-F1: 0.192

### Per-class

| class | P | R | F1 | support |
|---|---|---|---|---|
| dry | 0.000 | 0.000 | 0.000 | 127 |
| normal | 0.404 | 1.000 | 0.576 | 198 |
| oily | 0.000 | 0.000 | 0.000 | 165 |

### Confusion (rows = truth, cols = predicted)

| truth \ pred | oily | dry | normal | combination | sensitive | abstain |
|---|---|---|---|---|---|---|
| dry | 0 | 0 | 127 | 0 | 0 | 0 |
| normal | 0 | 0 | 198 | 0 | 0 | 0 |
| oily | 0 | 0 | 165 | 0 | 0 | 0 |

> Truth is 3-class (oily/dry/normal); predicted combination/sensitive always counts as wrong. Abstain counts as wrong.

## Fairness by estimated tone (always reported)

- lighter−darker accuracy gap: **-0.013** (95% CI -0.100…+0.075; n=303/181)

### per tone_group2

| group | n | accuracy | flag |
|---|---|---|---|
| darker | 181 | 0.409 |  |
| lighter | 303 | 0.396 |  |

### per ita_group6

| group | n | accuracy | flag |
|---|---|---|---|
| brown | 88 | 0.443 |  |
| dark | 27 | 0.444 | insufficient n |
| intermediate | 81 | 0.457 |  |
| light | 96 | 0.479 |  |
| tan | 66 | 0.348 |  |
| very_light | 126 | 0.294 |  |

## Concerns (threshold 0.5, only where the concern is 'known')

Not covered by this reader: breakouts, redness, dark_spots, pores, fine_lines

_Cosmetic appearance evaluation only; not a medical assessment._
