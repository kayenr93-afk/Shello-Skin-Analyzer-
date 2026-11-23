import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image  # noqa: E402


def make_image(path: Path, color=(200, 160, 120), size=(48, 48), noise_seed=None):
    """Write a JPEG. With noise_seed, adds deterministic pixel noise so files
    differ in bytes while looking the same."""
    import numpy as np

    arr = np.full((size[1], size[0], 3), color, dtype=np.uint8)
    if noise_seed is not None:
        rng = np.random.default_rng(noise_seed)
        arr = np.clip(arr.astype(int) + rng.integers(-8, 9, arr.shape), 0, 255).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path, "JPEG", quality=85)
    return path


@pytest.fixture
def img_factory():
    return make_image
