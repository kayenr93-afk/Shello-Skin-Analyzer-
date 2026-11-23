"""Individual Typology Angle (ITA) from an RGB image.

Cosmetic appearance grouping only. Not a medical measurement.

ITA = atan2(L* - 50, b*) in degrees, using the median L* and b* of pixels
that pass a YCbCr skin mask, or a caller-supplied mask of the same shape.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

# Chardon-style bins. A value sitting on a boundary belongs to the darker bin:
# ITA 28 is tan / "darker", not intermediate / "lighter".
_GROUP6 = (
    (55.0, "very_light"),
    (41.0, "light"),
    (28.0, "intermediate"),
    (10.0, "tan"),
    (-30.0, "brown"),
)


def ita_group6(ita: float | None) -> str | None:
    if ita is None:
        return None
    for bound, name in _GROUP6:
        if ita > bound:
            return name
    return "dark"


def tone_group2(ita: float | None) -> str | None:
    if ita is None:
        return None
    return "lighter" if ita > 28.0 else "darker"


def _srgb_to_lab(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return L* and b* arrays (D65) for an HxWx3 uint8 RGB image."""
    c = rgb.astype(np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    matrix = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ],
        dtype=np.float64,
    )
    xyz = lin @ matrix.T
    xn, yn, zn = 0.95047, 1.0, 1.08883
    t = np.stack((xyz[..., 0] / xn, xyz[..., 1] / yn, xyz[..., 2] / zn), axis=-1)
    delta = 6.0 / 29.0
    f = np.where(t > delta ** 3, np.cbrt(t), t / (3.0 * delta ** 2) + 4.0 / 29.0)
    light = 116.0 * f[..., 1] - 16.0
    yellow = 200.0 * (f[..., 1] - f[..., 2])
    return light, yellow


def _ycbcr_skin_mask(rgb: np.ndarray) -> np.ndarray:
    """Broad YCbCr skin gate.

    Accepts the light fixture (235, 200, 170) and the dark fixture (95, 60, 45).
    Rejects pure blue (0, 0, 255), which has no skin-range Cr.
    """
    r = rgb[..., 0].astype(np.float64)
    g = rgb[..., 1].astype(np.float64)
    b = rgb[..., 2].astype(np.float64)
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cr = (r - y) * 0.713 + 128.0
    cb = (b - y) * 0.564 + 128.0
    return (y > 40.0) & (cb > 77.0) & (cb < 135.0) & (cr > 130.0) & (cr < 180.0)


def estimate_ita(image, skin_mask_fn=None) -> float | None:
    """Median ITA over masked pixels, or None when the mask is empty."""
    if not isinstance(image, Image.Image):
        image = Image.fromarray(np.asarray(image))
    rgb = np.asarray(image.convert("RGB"))
    if skin_mask_fn is None:
        mask = _ycbcr_skin_mask(rgb)
    else:
        mask = np.asarray(skin_mask_fn(image), dtype=bool)
        if mask.shape != rgb.shape[:2]:
            raise ValueError(
                f"skin mask shape {mask.shape} does not match image {rgb.shape[:2]}"
            )
    if mask.size == 0 or not bool(mask.any()):
        return None
    light, yellow = _srgb_to_lab(rgb)
    return float(math.degrees(math.atan2(float(np.median(light[mask])) - 50.0, float(np.median(yellow[mask])))))
