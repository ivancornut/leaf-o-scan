"""Combine a leaf that was cut in two and scanned in two separate scans.

Two complementary results are produced:

* **Additive traits** (areas, holes, damage, petiole) are summed over the
  parts. They do not depend on how the parts are put back together.
* **Shape traits** (length, width, perimeter, circularity, ...) need the
  whole outline. The two parts are re-assembled digitally: in each part
  the straight cut edge is detected, part 2 is rotated and translated so
  that its cut edge lies on the cut edge of part 1 (with the parts on
  opposite sides), and the re-assembled image is analysed like a normal scan.

See REPORT.md, section 3d.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import ndimage as ndi
from skimage import measure, transform

from .analysis import AnalysisParams, LeafResult, analyse

ADDITIVE = ["area_cm2", "area_outline_cm2", "area_with_petiole_cm2", "n_holes", "holes_area_mm2",
            "damage_area_cm2", "n_damage_patches", "petiole_area_cm2"]


@dataclass
class CutEdge:
    p1: np.ndarray        # (x, y) end points of the fitted cut segment, interior on the left of p1->p2
    p2: np.ndarray
    length_px: float
    rms_px: float         # RMS distance of the edge points to the fitted line (straightness)
    n_points: int


def _main_outline(res: LeafResult) -> np.ndarray:
    """Outline of the largest object of a part scan."""
    if res.labels.max() == 0:
        raise ValueError("No leaf found in this scan.")
    sizes = np.bincount(res.labels.ravel())[1:]
    return res.labels == (np.argmax(sizes) + 1)


def _contour_xy(outline: np.ndarray) -> np.ndarray:
    c = max(measure.find_contours(np.pad(outline, 1).astype(float), 0.5), key=len)[:-1] - 1
    return c[:, ::-1]  # (x, y)


def line_candidates(outline: np.ndarray, px_mm: float, tol_mm: float = 0.8, min_len_mm: float = 15.0,
                    n_trials: int = 3000, max_cand: int = 6, seed: int = 0):
    """Distinct straight runs of the contour (RANSAC + non-maximum suppression).

    Random pairs of contour points define lines; for each, the contiguous run
    of contour points within ``tol_mm`` of the line (gaps <= 1 mm bridged)
    is measured. Runs are sorted by length and near-duplicates (angle < 10 deg
    and offset < 3 mm) are suppressed. Returns a list of (point, unit vector, run length px).
    """
    pts = _contour_xy(outline)
    n = len(pts)
    rng = np.random.default_rng(seed)
    tol, min_sep = tol_mm / px_mm, min_len_mm / px_mm
    gap_max = max(2, int(1.0 / px_mm))
    runs = []
    for _ in range(n_trials):
        i, j = rng.integers(0, n, 2)
        d = pts[j] - pts[i]
        L = np.hypot(*d)
        if L < min_sep:
            continue
        u = d / L
        dist = np.abs((pts - pts[i]) @ np.array([-u[1], u[0]]))
        inl = dist <= tol
        sel = _run_around(inl, i, gap_max)
        P = pts[sel]
        m = P.mean(0)
        uu = np.linalg.svd(P - m)[2][0]
        span = np.ptp((P - m) @ uu)
        if span >= min_sep:
            runs.append((span, m, uu))
    runs.sort(key=lambda r: -r[0])
    out = []
    for span, m, u in runs:
        dup = False
        for _, m2, u2 in out:
            ang = np.degrees(np.arccos(min(1.0, abs(u @ u2))))
            off = abs((m - m2) @ np.array([-u2[1], u2[0]]))
            if ang < 10 and off < 3 / px_mm:
                dup = True
                break
        if not dup:
            out.append((span, m, u))
        if len(out) >= max_cand:
            break
    return [(m, u, span) for span, m, u in out]


def edge_from_line(outline: np.ndarray, m: np.ndarray, u: np.ndarray, px_mm: float) -> CutEdge:
    """Cut segment for a candidate line.

    The line is refitted (PCA) on all contour points within 2 mm of it, and
    its extent is the chord of the leaf in a strip 0.5-2.5 mm inside the
    line. Using this inner chord rather than corner detection makes the
    result insensitive to jogs in the cut and to where the cut meets the
    margin; both parts of one cut give the same chord.
    """
    pts = _contour_xy(outline)
    ys, xs = np.nonzero(outline)
    XY = np.stack([xs, ys], 1).astype(float)

    def chord(m, u):
        nrm = np.array([-u[1], u[0]])
        if not _inside(outline, m + nrm * (1.5 / px_mm)):
            u, nrm = -u, -nrm                       # interior on the left (+nrm side)
        rel = XY - m
        inn = rel @ nrm
        strip = (inn >= 0.5 / px_mm) & (inn <= 2.5 / px_mm)
        if not strip.any():
            return u, nrm, 0.0, 0.0
        t = rel[strip] @ u
        return u, nrm, t.min() - 0.5, t.max() + 0.5

    u, nrm, t0, t1 = chord(m, u)
    rc = pts - m
    near = (np.abs(rc @ nrm) <= 2.0 / px_mm) & ((rc @ u) >= t0) & ((rc @ u) <= t1)
    rms = float(np.sqrt(np.mean((rc[near] @ nrm) ** 2))) if near.any() else np.inf
    if near.sum() >= 10:
        Q = pts[near]
        m2 = Q.mean(0)
        u2 = np.linalg.svd(Q - m2)[2][0]
        if np.degrees(np.arccos(min(1.0, abs(u2 @ u)))) < 10:
            m = m2
            u, nrm, t0, t1 = chord(m2, u2 if u2 @ u > 0 else -u2)
            rms = float(np.sqrt(np.mean(((Q - m) @ nrm) ** 2)))
    return CutEdge(m + u * t0, m + u * t1, float(t1 - t0), rms, int(near.sum()))


def _major_axis(outline: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(outline)
    P = np.stack([xs, ys], 1).astype(float)
    return np.linalg.svd(P - P.mean(0), full_matrices=False)[2][0]


def find_cut_pair(o1: np.ndarray, o2: np.ndarray, px_mm: float):
    """Choose the cut edge in each part.

    Straight leaf margins can be as straight as a scissor cut, so the cut is
    chosen jointly over both parts: among candidate straight edges, the pair
    minimising
        cost = |c1 - c2| / max(c1, c2) + 0.5 (|cos a1| + |cos a2|)
    is selected, where c = inner chord (both sides of one cut have the same
    width) and a = angle between the edge and the major axis of its part
    (a cut runs across the leaf, a margin along it).
    """
    cands = []
    for o in (o1, o2):
        ax = _major_axis(o)
        lst = []
        for m, u, _ in line_candidates(o, px_mm):
            e = edge_from_line(o, m, u, px_mm)
            if e.length_px <= 0:
                continue
            d = (e.p2 - e.p1) / e.length_px
            lst.append((e, abs(d @ ax)))
        if not lst:
            raise ValueError("No straight cut edge found in one of the parts.")
        cands.append(lst)
    best = None
    for e1, c1 in cands[0]:
        for e2, c2 in cands[1]:
            cost = abs(e1.length_px - e2.length_px) / max(e1.length_px, e2.length_px) + 0.5 * (c1 + c2)
            if best is None or cost < best[0]:
                best = (cost, e1, e2)
    return best[1], best[2]


def find_cut_edge(outline: np.ndarray, px_mm: float) -> CutEdge:
    """Single-part fallback: the candidate most perpendicular to the major axis among the long ones."""
    ax = _major_axis(outline)
    best = None
    for m, u, span in line_candidates(outline, px_mm):
        e = edge_from_line(outline, m, u, px_mm)
        cost = abs(((e.p2 - e.p1) / max(e.length_px, 1)) @ ax)
        if best is None or cost < best[0]:
            best = (cost, e)
    return best[1]


def _cut_fraction(outline: np.ndarray, e: CutEdge, px_mm: float, band_mm: float = 1.5) -> float:
    """Fraction of the contour length lying on the cut (within band_mm of the cut segment)."""
    c = _contour_xy(outline)
    seg = np.hypot(*np.diff(np.vstack([c, c[:1]]), axis=0).T)
    u = (e.p2 - e.p1) / e.length_px
    rel = c - e.p1
    along = rel @ u
    across = np.abs(rel @ np.array([-u[1], u[0]]))
    on = (across <= band_mm / px_mm) & (along >= -band_mm / px_mm) & (along <= e.length_px + band_mm / px_mm)
    return float(seg[on].sum() / seg.sum())


def _inside(mask, xy):
    x, y = int(round(xy[0])), int(round(xy[1]))
    return 0 <= y < mask.shape[0] and 0 <= x < mask.shape[1] and bool(mask[y, x])


def _run_around(inl: np.ndarray, i: int, gap_max: int):
    """Indices of the contiguous (circular) run of True around i, bridging gaps <= gap_max."""
    n = len(inl)
    out = [i]
    for step in (1, -1):
        k, gap = i, 0
        for _ in range(n):
            k = (k + step) % n
            if k == i:
                break
            if inl[k]:
                gap = 0
                out.append(k)
            else:
                gap += 1
                if gap > gap_max:
                    break
    return np.unique(out)


def _rigid(src_a, src_b, dst_a, dst_b):
    """Rigid transform (rotation + translation) mapping segment src onto dst (midpoints, directions)."""
    ds, dd = src_b - src_a, dst_b - dst_a
    ang = np.arctan2(dd[1], dd[0]) - np.arctan2(ds[1], ds[0])
    t = transform.EuclideanTransform(rotation=ang)
    ms, md = (src_a + src_b) / 2, (dst_a + dst_b) / 2
    shift = md - t(ms[None])[0]
    return transform.EuclideanTransform(rotation=ang, translation=shift)


@dataclass
class StitchResult:
    combined: pd.DataFrame          # one row: the reconstructed leaf
    composite_rgb: np.ndarray       # re-assembled image
    composite_result: LeafResult    # analysis of the re-assembled image
    parts: list                     # LeafResult of each part
    edges: list                     # CutEdge of each part
    seam: tuple                     # seam end points in composite coordinates


def stitch_two(rgb1: np.ndarray, rgb2: np.ndarray, p: AnalysisParams, image_id: str = "") -> StitchResult:
    px_mm = 25.4 / p.dpi
    r1, r2 = analyse(rgb1, p, image_id + "_part1"), analyse(rgb2, p, image_id + "_part2")
    o1, o2 = _main_outline(r1), _main_outline(r2)
    e1, e2 = find_cut_pair(o1, o2, px_mm)

    # Part 2 goes on the other side of the cut: its edge direction is reversed
    tf = _rigid(e2.p1, e2.p2, e1.p2, e1.p1)

    # canvas holding part 1 and transformed part 2, with a margin
    pad = int(10 / px_mm)
    ys1, xs1 = np.nonzero(o1)
    ys2, xs2 = np.nonzero(o2)
    c2 = tf(np.stack([xs2, ys2], 1).astype(float))
    xmin = int(min(xs1.min(), c2[:, 0].min())) - pad
    ymin = int(min(ys1.min(), c2[:, 1].min())) - pad
    xmax = int(max(xs1.max(), c2[:, 0].max())) + pad
    ymax = int(max(ys1.max(), c2[:, 1].max())) + pad
    H, W = ymax - ymin + 1, xmax - xmin + 1
    off = transform.EuclideanTransform(translation=(-xmin, -ymin))

    bg = np.median(rgb1[~ndi.binary_dilation(r1.labels > 0, iterations=pad)], axis=0)
    canvas = np.empty((H, W, 3), float)
    canvas[:] = bg

    # part 1: integer translation only (no resampling)
    m1 = np.zeros((H, W), bool)
    m1[ys1 - ymin, xs1 - xmin] = True
    canvas[m1] = rgb1[ys1, xs1]

    # part 2: resampled (bilinear for colour, nearest for the mask)
    full = tf + off                               # part-2 pixel -> canvas (skimage: tf applied first)
    inv = full.inverse
    m2 = transform.warp(o2.astype(float), inv, output_shape=(H, W), order=0) > 0.5
    col2 = np.stack([transform.warp(rgb2[..., k].astype(float), inv, output_shape=(H, W), order=1,
                                    preserve_range=True) for k in range(3)], -1)
    canvas[m2 & ~m1] = col2[m2 & ~m1]

    # close the seam (≤ 1 mm gaps along the cut line) and fill its colour from the nearest leaf pixel
    s1, s2 = off(e1.p1[None])[0], off(e1.p2[None])[0]
    yy, xx = np.mgrid[0:H, 0:W]
    u = (s2 - s1) / np.hypot(*(s2 - s1))
    rel = np.stack([xx - s1[0], yy - s1[1]], -1)
    along = rel @ u
    across = np.abs(rel @ np.array([-u[1], u[0]]))
    band = (across <= 2.0 / px_mm) & (along >= -1) & (along <= np.hypot(*(s2 - s1)) + 1)
    union = m1 | m2
    closed = ndi.binary_closing(union, structure=np.ones((3, 3)), iterations=max(1, int(1.0 / px_mm)))
    gap = closed & ~union & band
    if gap.any():
        _, (iy, ix) = ndi.distance_transform_edt(~union, return_indices=True)
        canvas[gap] = canvas[iy[gap], ix[gap]]
    comp = np.clip(canvas, 0, 255).astype(np.uint8)

    cres = analyse(comp, p, image_id)
    if len(cres.leaves) == 0:
        raise ValueError("Re-assembly failed: no leaf in the composite image.")
    # keep the largest object of the composite (the re-assembled leaf)
    row = cres.leaves.loc[cres.leaves.area_outline_cm2.idxmax()].copy()

    parts = pd.concat([r1.leaves.loc[[r1.leaves.area_outline_cm2.idxmax()]],
                       r2.leaves.loc[[r2.leaves.area_outline_cm2.idxmax()]]], ignore_index=True)
    for k in ADDITIVE:
        row[k] = parts[k].sum()
    row["holes_pct"] = 100 * row["holes_area_mm2"] / 100 / row["area_outline_cm2"]
    row["damage_pct"] = 100 * row["damage_area_cm2"] / row["area_cm2"] if row["area_cm2"] else 0.0
    # Perimeter: outer contour of each part minus the stretch lying on its cut (within 1.5 mm
    # of the cut segment), so that the seam does not contribute. The pixel-contour fraction is
    # applied to each part's sub-pixel perimeter.
    perim = 0.0
    for o, e, pr in ((o1, e1, parts.iloc[0]), (o2, e2, parts.iloc[1])):
        frac = _cut_fraction(o, e, px_mm)
        perim += pr.perimeter_cm * (1 - frac)
    row["perimeter_cm"] = perim
    row["circularity"] = 4 * np.pi * row["area_outline_cm2"] / perim ** 2
    pet = parts[parts.petiole_length_cm > 0]
    if len(pet):
        row["petiole_length_cm"] = pet.petiole_length_cm.max()
        row["petiole_width_mm"] = pet.loc[pet.petiole_length_cm.idxmax(), "petiole_width_mm"]
    row["n_parts"] = 2
    row["cut_length_part1_cm"] = e1.length_px * px_mm / 10
    row["cut_length_part2_cm"] = e2.length_px * px_mm / 10
    row["cut_mismatch_mm"] = abs(e1.length_px - e2.length_px) * px_mm
    row["cut_straightness_rms_mm"] = max(e1.rms_px, e2.rms_px) * px_mm
    row["touches_border"] = bool(parts.touches_border.any())
    combined = pd.DataFrame([row])
    combined["image"] = image_id
    return StitchResult(combined, comp, cres, [r1, r2], [e1, e2], (s1, s2))


def display_result(st: StitchResult) -> LeafResult:
    """LeafResult of the re-assembled image, carrying the combined row and the parts' holes."""
    cr = st.composite_result
    lid = int(cr.leaves.loc[cr.leaves.area_outline_cm2.idxmax(), "leaf"])
    row = st.combined.copy()
    row["leaf"] = lid
    holes = []
    for k, r in enumerate(st.parts, start=1):
        h = r.hole_table.copy()
        h.insert(2, "part", k)
        h["leaf"] = lid
        holes.append(h)
    ht = pd.concat(holes, ignore_index=True)
    ht["image"] = row["image"].iloc[0]
    ht["hole"] = np.arange(1, len(ht) + 1)
    res = LeafResult(labels=cr.labels, tissue=cr.tissue, holes=cr.holes, leaves=row, hole_table=ht,
                     threshold=cr.threshold, geometry={lid: cr.geometry[lid]},
                     petiole=cr.petiole, damage=cr.damage)
    res.seam = st.seam
    return res
