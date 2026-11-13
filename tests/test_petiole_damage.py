"""Petiole separation and brown-damage tests on synthetic multi-colour scans."""
import numpy as np
import pytest

from leafscan.analysis import AnalysisParams, analyse
from test_analysis import BG, MM, ellipse, disk

GREEN = np.array([60, 72, 55.0])      # healthy tissue (h_ab ~ 125 deg)
BROWN = np.array([98, 80, 72.0])      # necrotic patch, sampled on a real cocoa leaf (h_ab ~ 54 deg)
DARK_SPOT = np.array([63, 47, 39.0])  # dark necrotic fleck (h_ab ~ 55 deg)
VEIN = np.array([95, 85, 72.0])       # reddish-brown vein (h_ab ~ 70 deg), ~8 L* lighter than tissue,
                                      # as measured on real cocoa midribs
PETIOLE = np.array([117, 95, 83.0])


def render_layers(dpi, layers, size_mm=(200, 150), ss=4):
    """layers: list of (shape_fn, rgb) painted in order over the background."""
    h = int(size_mm[0] / MM * dpi); w = int(size_mm[1] / MM * dpi)
    px = MM / dpi
    out = np.empty((h, w, 3), np.float32)
    x = ((np.arange(w * ss) + 0.5) / ss * px)[None, :]
    for r0 in range(0, h, 64):
        r1 = min(h, r0 + 64)
        y = ((np.arange(r0 * ss, r1 * ss) + 0.5) / ss * px)[:, None]
        acc = np.broadcast_to(BG, (y.shape[0], x.shape[1], 3)).astype(np.float32).copy()
        for fn, col in layers:
            m = fn(x, y)
            acc[m] = col
        out[r0:r1] = acc.reshape(r1 - r0, ss, w, ss, 3).mean((1, 3))
    rng = np.random.default_rng(1)
    out += rng.normal(0, 2, out.shape)
    return np.clip(out, 0, 255).astype(np.uint8)


def rect(x0, x1, y0, y1):
    return lambda x, y: (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)


def tri_tip(cx, ytop, half_w, ybot):
    """Tapering tip: triangle with base at ytop and apex at ybot."""
    return lambda x, y: (y >= ytop) & (y <= ybot) & (np.abs(x - cx) <= half_w * (ybot - y) / (ybot - ytop))


LEAF = ellipse(75, 110, 60, 25, np.pi / 2)        # vertical blade: y 50..170, width 5 cm
PET = rect(73.5, 76.5, 30, 55)                   # 3 mm wide, 20 mm protruding above the blade
TIP = tri_tip(75, 168, 3.0, 185)                 # drip tip 15 mm beyond the blade


@pytest.mark.parametrize("dpi", [150, 600])
def test_petiole_separated_tip_kept(dpi):
    img = render_layers(dpi, [(LEAF, GREEN), (PET, PETIOLE), (TIP, GREEN)])
    L = analyse(img, AnalysisParams(dpi=dpi)).leaves.iloc[0]
    assert L.petiole_length_cm == pytest.approx(2.0, abs=0.15)
    assert L.petiole_width_mm == pytest.approx(3.0, abs=0.35)
    assert L.petiole_area_cm2 == pytest.approx(0.60, rel=0.12)
    # length is measured on the lamina = blade + tip: from y = 50 to 185 mm
    assert L.length_cm == pytest.approx(13.5, abs=0.1)
    assert L.length_with_petiole_cm == pytest.approx(15.5, abs=0.1)
    # lamina area excludes the petiole; the petiole is in the "with petiole" area
    assert L.area_with_petiole_cm2 - L.area_cm2 == pytest.approx(L.petiole_area_cm2, rel=0.02)


def test_no_petiole_on_plain_ellipse():
    img = render_layers(300, [(LEAF, GREEN), (TIP, GREEN)])
    L = analyse(img, AnalysisParams(dpi=300)).leaves.iloc[0]
    assert L.petiole_length_cm == 0.0


@pytest.mark.parametrize("dpi", [150, 300, 600])
def test_damage_area_veins_excluded(dpi):
    patch = disk(75, 130, 8.0)                       # 201.06 mm2 necrotic patch
    vein = rect(74.7, 75.3, 60, 160)                 # 0.6 mm wide midrib, reddish-brown
    spot = disk(62, 90, 0.8)                         # 2.01 mm2 dark fleck
    img = render_layers(dpi, [(LEAF, GREEN), (vein, VEIN), (patch, BROWN), (spot, DARK_SPOT)])
    L = analyse(img, AnalysisParams(dpi=dpi)).leaves.iloc[0]
    true_mm2 = np.pi * 8.0 ** 2 + np.pi * 0.8 ** 2
    assert L.damage_area_cm2 * 100 == pytest.approx(true_mm2, rel=0.04)
    assert L.n_damage_patches == 2


def test_healthy_leaf_no_damage():
    vein = rect(74.7, 75.3, 60, 160)
    img = render_layers(300, [(LEAF, GREEN), (vein, VEIN)])
    L = analyse(img, AnalysisParams(dpi=300)).leaves.iloc[0]
    assert L.damage_pct == pytest.approx(0.0, abs=0.01)
