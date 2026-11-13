"""Accuracy tests on synthetic 'scans' with analytically known geometry.

Shapes are rasterised with 4x4 supersampling and anti-aliased like a real
scanner (grey edge pixels), then measured by the full pipeline.
"""
import numpy as np
import pytest

from leafscan.analysis import AnalysisParams, analyse

BG = np.array([222, 221, 218.0])
LEAF = np.array([60, 72, 55.0])
MM = 25.4


def render(dpi, shapes, holes=(), size_mm=(200, 150), ss=4):
    """shapes/holes: callables f(x_mm, y_mm) -> bool. Returns uint8 RGB."""
    h = int(size_mm[0] / MM * dpi); w = int(size_mm[1] / MM * dpi)
    px = MM / dpi
    cov = np.empty((h, w), np.float32)
    x = ((np.arange(w * ss) + 0.5) / ss * px)[None, :]
    step = 64  # render in strips of output rows to bound memory
    for r0 in range(0, h, step):
        r1 = min(h, r0 + step)
        y = ((np.arange(r0 * ss, r1 * ss) + 0.5) / ss * px)[:, None]
        m = np.zeros((y.shape[0], x.shape[1]), bool)
        for s in shapes: m |= s(x, y)
        for s in holes: m &= ~s(x, y)
        cov[r0:r1] = m.reshape(r1 - r0, ss, w, ss).mean((1, 3))
    rgb = cov[..., None] * LEAF + (1 - cov[..., None]) * BG
    rng = np.random.default_rng(0)
    rgb += rng.normal(0, 2, rgb.shape)  # scanner noise
    return np.clip(rgb, 0, 255).astype(np.uint8)


def ellipse(cx, cy, a, b, theta=0.0):
    c, s = np.cos(theta), np.sin(theta)
    return lambda x, y: (((x - cx) * c + (y - cy) * s) / a) ** 2 + ((-(x - cx) * s + (y - cy) * c) / b) ** 2 <= 1


def disk(cx, cy, r):
    return ellipse(cx, cy, r, r)


@pytest.mark.parametrize("dpi", [150, 300, 600])
@pytest.mark.parametrize("theta", [0.0, 0.5, 1.2])
def test_ellipse_with_holes(dpi, theta):
    a, b = 80.0, 30.0  # mm semi-axes -> length 16 cm, width 6 cm
    holes = [disk(100, 90, 2.0), disk(110, 95, 1.0), disk(95, 110, 4.0)]
    hole_area = np.pi * (2.0 ** 2 + 1.0 ** 2 + 4.0 ** 2)          # mm2
    img = render(dpi, [ellipse(100, 100, a, b, theta)], holes, size_mm=(200, 200))
    r = analyse(img, AnalysisParams(dpi=dpi))
    assert len(r.leaves) == 1
    L = r.leaves.iloc[0]
    assert not L.touches_border
    true_outline = np.pi * a * b / 100                               # cm2
    assert L.area_outline_cm2 == pytest.approx(true_outline, rel=0.005)
    assert L.area_cm2 == pytest.approx(true_outline - hole_area / 100, rel=0.005)
    assert L.length_cm == pytest.approx(2 * a / 10, abs=2 * MM / dpi / 10 + 0.01)
    assert L.width_cm == pytest.approx(2 * b / 10, abs=2 * MM / dpi / 10 + 0.01)
    assert L.n_holes == 3
    assert L.holes_area_mm2 == pytest.approx(hole_area, rel=0.08 if dpi == 150 else 0.04)
    # perimeter of the ellipse (Ramanujan II)
    h = ((a - b) / (a + b)) ** 2
    P = np.pi * (a + b) * (1 + 3 * h / (10 + np.sqrt(4 - 3 * h))) / 10
    assert L.perimeter_cm == pytest.approx(P, rel=0.015)
    assert L.circularity == pytest.approx(4 * np.pi * true_outline / P ** 2, rel=0.03)


def test_rectangle_exact_extents():
    dpi = 300
    rect = lambda x, y: (x >= 20) & (x <= 120) & (y >= 30) & (y <= 70)  # 10 x 4 cm
    r = analyse(render(dpi, [rect], size_mm=(120, 150)), AnalysisParams(dpi=dpi))
    L = r.leaves.iloc[0]
    assert L.area_cm2 == pytest.approx(40.0, rel=0.005)
    assert L.length_cm == pytest.approx(np.hypot(10, 4), abs=0.02)   # max Feret = diagonal
    assert L.solidity == pytest.approx(1.0, abs=0.01)


def test_multiple_leaves_and_dust():
    dpi = 150
    shapes = [ellipse(40, 50, 30, 12), ellipse(40, 130, 25, 10), ellipse(110, 90, 35, 15, 0.7),
              disk(120, 20, 0.6)]  # dust 1.1 mm2 < 0.5 cm2
    r = analyse(render(dpi, shapes), AnalysisParams(dpi=dpi))
    assert len(r.leaves) == 3
    areas = sorted(r.leaves.area_cm2)
    assert areas == pytest.approx(sorted([np.pi * 30 * 12 / 100, np.pi * 25 * 10 / 100, np.pi * 35 * 15 / 100]), rel=0.01)


def test_hole_size_distribution():
    dpi = 600
    radii = [0.3, 0.5, 0.8, 1.2, 2.0, 3.0, 5.0]
    holes = [disk(40 + 12 * i, 100, rr) for i, rr in enumerate(radii)]
    r = analyse(render(dpi, [ellipse(75, 100, 70, 25)], holes), AnalysisParams(dpi=dpi))
    got = np.sort(r.hole_table.area_mm2.values)
    exp = np.sort(np.pi * np.array(radii) ** 2)
    assert len(got) == len(exp)
    assert got == pytest.approx(exp, rel=0.10, abs=0.02)


def test_notch_at_margin_is_not_a_hole():
    dpi = 300
    r = analyse(render(dpi, [ellipse(75, 100, 70, 25)], [disk(75, 125, 4)]), AnalysisParams(dpi=dpi))
    assert r.leaves.iloc[0].n_holes == 0


def test_too_small_holes_ignored():
    dpi = 150
    r = analyse(render(dpi, [ellipse(75, 100, 70, 25)], [disk(75, 100, 0.1)]), AnalysisParams(dpi=dpi))
    assert r.leaves.iloc[0].n_holes == 0
