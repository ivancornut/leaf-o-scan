"""Re-assembly of a leaf cut in two: synthetic leaf, both halves scanned at
arbitrary positions/orientations, compared with the uncut leaf."""
import numpy as np
import pytest

from leafscan.analysis import AnalysisParams, analyse
from leafscan.stitch import stitch_two
from test_analysis import disk, ellipse
from test_petiole_damage import BROWN, GREEN, PETIOLE, TIP, rect, render_layers

# Uncut leaf (y = 30..185 mm): ellipse blade with petiole, drip tip, two holes, one necrotic patch
BLADE = ellipse(75, 110, 60, 25, np.pi / 2)
PET = rect(73.5, 76.5, 30, 55)
HOLE_A, HOLE_B = disk(70, 80, 1.5), disk(82, 140, 2.0)
PATCH = disk(88, 120, 5.0)


def leaf_layers(tf=None, keep=None):
    """Layers of the leaf, optionally clipped to a half-plane and moved by a rigid transform.

    tf: (angle, dx, dy) applied to the leaf; keep: function(x, y) in leaf coordinates.
    """
    def wrap(fn):
        def g(x, y):
            if tf is not None:
                a, dx, dy = tf
                c, s = np.cos(-a), np.sin(-a)
                xr, yr = x - dx, y - dy
                x, y = c * xr - s * yr + 75, s * xr + c * yr + 110   # inverse rotation about leaf centre
            m = fn(x, y)
            return m & keep(x, y) if keep is not None else m
        return g
    leaf = lambda x, y: (BLADE(x, y) | PET(x, y) | TIP(x, y)) & ~HOLE_A(x, y) & ~HOLE_B(x, y)
    return [(wrap(leaf), GREEN), (wrap(lambda x, y: PET(x, y) & ~BLADE(x, y)), PETIOLE),
            (wrap(lambda x, y: PATCH(x, y) & BLADE(x, y)), BROWN)]


# Oblique scissor cut through y = 105 + 0.15 (x - 75)
above = lambda x, y: y <= 105 + 0.15 * (x - 75)
below = lambda x, y: y > 105 + 0.15 * (x - 75)


@pytest.mark.parametrize("dpi", [150, 300])
def test_stitched_equals_whole(dpi):
    p = AnalysisParams(dpi=dpi)
    whole = analyse(render_layers(dpi, [(f, c) for f, c in leaf_layers((0, 75, 110))], size_mm=(220, 150)), p).leaves.iloc[0]
    part1 = render_layers(dpi, leaf_layers((0.6, 70, 130), above), size_mm=(220, 150))
    part2 = render_layers(dpi, leaf_layers((-2.3, 80, 100), below), size_mm=(220, 150))
    st = stitch_two(part1, part2, p, "t")
    L = st.combined.iloc[0]
    assert L.cut_mismatch_mm < 0.5
    for k, rel in [("area_cm2", 0.003), ("area_outline_cm2", 0.003), ("area_with_petiole_cm2", 0.003),
                   ("damage_area_cm2", 0.02), ("holes_area_mm2", 0.05)]:
        assert L[k] == pytest.approx(whole[k], rel=rel), k
    assert L.n_holes == whole.n_holes == 2
    assert L.length_cm == pytest.approx(whole.length_cm, abs=0.05)
    assert L.width_cm == pytest.approx(whole.width_cm, abs=0.05)
    assert L.perimeter_cm == pytest.approx(whole.perimeter_cm, rel=0.02)
    assert L.petiole_length_cm == pytest.approx(whole.petiole_length_cm, abs=0.05)
