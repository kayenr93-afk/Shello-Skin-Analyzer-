"""ITA estimator tests on solid-colour patches with known properties."""

import io

import numpy as np
from PIL import Image

from ml.data import tone


def _solid(rgb, size=(64, 64)):
    arr = np.full((size[1], size[0], 3), rgb, dtype=np.uint8)
    return Image.fromarray(arr)


def test_known_light_and_dark_patches():
    # Warm light skin-like RGB -> high ITA ("lighter" / very_light or light).
    light_ita = tone.estimate_ita(_solid((235, 200, 170)))
    dark_ita = tone.estimate_ita(_solid((95, 60, 45)))
    assert light_ita is not None and dark_ita is not None
    assert light_ita > dark_ita
    assert tone.tone_group2(light_ita) == "lighter"
    assert tone.tone_group2(dark_ita) == "darker"


def test_group6_boundaries():
    assert tone.ita_group6(60) == "very_light"
    assert tone.ita_group6(50) == "light"
    assert tone.ita_group6(35) == "intermediate"
    assert tone.ita_group6(20) == "tan"
    assert tone.ita_group6(0) == "brown"
    assert tone.ita_group6(-40) == "dark"
    assert tone.ita_group6(None) is None
    assert tone.tone_group2(28.0) == "darker"
    assert tone.tone_group2(28.1) == "lighter"


def test_no_skin_pixels_returns_none():
    # Pure blue: nothing passes the YCrCb skin mask.
    assert tone.estimate_ita(_solid((0, 0, 255))) is None


def test_pluggable_mask():
    img = _solid((235, 200, 170))
    full = lambda im: np.ones((im.size[1], im.size[0]), dtype=bool)
    ita_full = tone.estimate_ita(img, skin_mask_fn=full)
    ita_default = tone.estimate_ita(img)
    assert ita_full is not None and ita_default is not None
    assert abs(ita_full - ita_default) < 1e-6  # solid colour: same median
