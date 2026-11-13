"""Leaf segmentation and measurement.

All measurements are computed from a binary mask obtained by colour
distance to the scanner background (CIE L*a*b*, Otsu threshold), in the
spirit of the MATLAB reference (le Maire, CIRAD 2020, segmentImageLeaf.m),
but rewritten for Python and extended with hole (perforation) analysis.

Units: lengths in cm, leaf areas in cm^2, hole areas in mm^2.
See REPORT.md for the full description of equations and validation.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd
from scipy import ndimage as ndi
from scipy.spatial import ConvexHull
from skimage import color, filters, graph, measure

MM_PER_INCH = 25.4


@dataclass
class AnalysisParams:
    dpi: float = 150.0
    min_leaf_area_cm2: float = 0.5      # objects smaller than this are dust (MATLAB: surf_threshold)
    min_hole_area_mm2: float = 0.1      # smaller background islands are treated as noise/tissue
    min_hole_pixels: int = 4            # hard lower bound whatever the dpi
    threshold_offset: float = 0.0       # added to the automatic (Otsu) colour-distance threshold
    manual_threshold: float | None = None  # if set, overrides Otsu (Delta E units)
    border_strip_mm: float = 3.0        # max depth of scanner-edge artefact stripping
    edge_artefact_max_thickness_mm: float = 2.0  # objects touching the scan edge that are never
                                                 # thicker than this are edge lines, not leaves
    petiole_width_mm: float = 3.5       # MATLAB threshold_petiole (0.35 cm)
    hole_grow_delta: float = 10.0       # hysteresis: holes grow from seeds into pixels with
                                        # Delta E <= threshold + hole_grow_delta (0 = no growth)
    # --- petiole separation
    separate_petiole: bool = True
    petiole_max_width_mm: float = 6.0   # opening diameter: parts narrower than this are 'thin'
    petiole_min_length_mm: float = 4.0  # thin parts shorter than this are ignored
    petiole_min_taper: float = 0.65     # distal/proximal width ratio; a tapering tip is ~0.3-0.5
    # --- brown damage (necrosis)
    damage_hue_max_deg: float = 80.0    # CIELAB hue angle h_ab below which tissue is 'brown'
    damage_min_chroma: float = 2.0      # pixels greyer than this have no reliable hue
    damage_min_width_mm: float = 1.5    # brown structures narrower than this are ignored ...
    damage_spot_min_area_mm2: float = 0.2   # ... unless they are compact spots at least this large
    damage_spot_max_elongation: float = 3.0 # spots: major/minor axis ratio (veins are elongated)
    damage_spot_max_dL: float = 0.0     # spots must be darker (L*) than the 3-px ring around them
    damage_smooth_mm: float = 0.1       # Gaussian smoothing of a*, b* (sigma, mm) before hue


@dataclass
class LeafResult:
    labels: np.ndarray              # int label image (filled leaves, 0 = background)
    tissue: np.ndarray              # bool, leaf tissue (holes excluded)
    holes: np.ndarray               # int label image of retained holes
    leaves: pd.DataFrame
    hole_table: pd.DataFrame
    threshold: float
    geometry: dict = field(default_factory=dict)  # per-leaf drawing info for overlays
    petiole: np.ndarray | None = None   # bool mask of petioles
    damage: np.ndarray | None = None    # bool mask of brown (damaged) tissue


# ----------------------------------------------------------------------------
# Segmentation
# ----------------------------------------------------------------------------

def _background_reference(lab: np.ndarray) -> np.ndarray:
    """Median Lab colour of the bright (background) pixels.

    The background of a flatbed scan (white lid) is the brightest large
    population; we take pixels with L* above the Otsu threshold of L*.
    """
    L = lab[..., 0]
    t = filters.threshold_otsu(L[::4, ::4])
    sel = L > t
    return np.median(lab[sel], axis=0)


def _strip_border_artefacts(fg: np.ndarray, max_px: int) -> np.ndarray:
    """Remove dark lines along the scan edges (lid shadow / calibration strip).

    A row/column at the edge is cleared while more than half of it is
    foreground, up to ``max_px`` lines deep (cf. MATLAB 'seuil_long_bord').
    A leaf crossing the edge never covers half of the scan width, so it is
    kept.
    """
    fg = fg.copy()
    h, w = fg.shape
    for i in range(min(max_px, h)):
        if fg[i].mean() > 0.5: fg[i] = False
        else: break
    for i in range(min(max_px, h)):
        if fg[h - 1 - i].mean() > 0.5: fg[h - 1 - i] = False
        else: break
    for j in range(min(max_px, w)):
        if fg[:, j].mean() > 0.5: fg[:, j] = False
        else: break
    for j in range(min(max_px, w)):
        if fg[:, w - 1 - j].mean() > 0.5: fg[:, w - 1 - j] = False
        else: break
    return fg


def segment(rgb: np.ndarray, p: AnalysisParams):
    """Return (foreground bool mask, threshold used, Delta E image)."""
    rgb = rgb[..., :3]
    lab = color.rgb2lab(rgb)
    ref = _background_reference(lab)
    dE = np.sqrt(((lab - ref) ** 2).sum(axis=2))  # CIE76 colour difference
    if p.manual_threshold is not None:
        thr = float(p.manual_threshold)
    else:
        thr = float(filters.threshold_otsu(dE[::2, ::2])) + p.threshold_offset
    fg = dE > thr
    px_mm = MM_PER_INCH / p.dpi
    fg = _strip_border_artefacts(fg, max(1, int(round(p.border_strip_mm / px_mm))))
    return fg, thr, dE


# ----------------------------------------------------------------------------
# Geometry helpers
# ----------------------------------------------------------------------------

def _pixel_corners(mask: np.ndarray) -> np.ndarray:
    """(x, y) coordinates of the 4 corners of every boundary pixel.

    Using pixel corners (not centres) makes extents exact for the pixel
    footprint: a 10-pixel-wide bar measures exactly 10 px.
    """
    er = ndi.binary_erosion(mask, border_value=0)
    yy, xx = np.nonzero(mask & ~er)
    pts = np.concatenate([
        np.stack([xx - 0.5, yy - 0.5], 1), np.stack([xx + 0.5, yy - 0.5], 1),
        np.stack([xx - 0.5, yy + 0.5], 1), np.stack([xx + 0.5, yy + 0.5], 1)])
    return np.unique(pts, axis=0)


def _max_feret(pts: np.ndarray):
    """Maximum Feret (caliper) diameter and its two end points."""
    hull = pts[ConvexHull(pts).vertices]
    d2 = ((hull[:, None, :] - hull[None, :, :]) ** 2).sum(-1)
    i, j = np.unravel_index(np.argmax(d2), d2.shape)
    return float(np.sqrt(d2[i, j])), hull[i], hull[j], hull


def _width_profile(mask: np.ndarray, u: np.ndarray, origin: np.ndarray):
    """Width of the leaf perpendicular to the unit axis ``u``.

    Pixels are binned in 1-px slices along the axis; within each slice the
    width is the extent (max-min, +1 px) of the perpendicular coordinate.
    Rotation is applied to coordinates, so no image resampling is involved.
    Returns slice positions s (px, from the origin) and widths (px).
    """
    yy, xx = np.nonzero(mask)
    v = np.array([-u[1], u[0]])
    rel = np.stack([xx, yy], 1) - origin
    s = rel @ u
    t = rel @ v
    fb = np.floor(s).astype(int)
    f0 = fb.min()
    b = fb - f0
    n = b.max() + 1
    tmin = np.full(n, np.inf); tmax = np.full(n, -np.inf)
    np.minimum.at(tmin, b, t); np.maximum.at(tmax, b, t)
    ok = np.isfinite(tmin)
    w = np.where(ok, tmax - tmin + 1.0, 0.0)
    return w, f0, v, tmin, tmax


def _detect_holes(outline: np.ndarray, fg: np.ndarray, dE: np.ndarray, grow_thr: float):
    """Background-coloured regions enclosed by leaf tissue, by hysteresis.

    Seeds are pixels inside the leaf outline classified as background
    (Delta E <= leaf threshold). Holes seen through a leaf that is not
    perfectly flat appear greyer (shadowed lid), so each seed region is
    grown into connected pixels with Delta E <= ``grow_thr``. Grown pixels
    connected to the outside of the leaf (pale, out-of-focus margins) are
    not added. Regions without a seed are never holes, which prevents vein
    highlights from being counted.
    """
    seeds = outline & ~fg
    grow = (outline & (dE <= grow_thr)) | seeds
    g = grow | ~outline
    lg, _ = ndi.label(g)                               # 4-connectivity
    outside = np.unique(lg[~outline])
    with_seed = np.unique(lg[seeds])
    ok = np.setdiff1d(with_seed, outside)
    ok = ok[ok > 0]
    return seeds | (np.isin(lg, ok) & outline)


def _subpixel_perimeter(outline: np.ndarray, dE: np.ndarray, thr: float, exclude=None) -> float:
    """Length (px) of the outer leaf contour, located with sub-pixel accuracy.

    Marching squares (Lorensen & Cline 1987, skimage.measure.find_contours)
    on the colour-distance image at the segmentation threshold, which uses
    the partial-coverage (grey) edge pixels. The interior is saturated so
    that holes and veins do not produce contours, and other objects are
    removed. Pixel-counting perimeters overestimate smooth outlines by
    ~4-8 % (staircase effect); this estimator is within ~1 % on test shapes.
    """
    ys, xs = np.nonzero(outline)
    y0, y1 = max(ys.min() - 3, 0), min(ys.max() + 4, outline.shape[0])
    x0, x1 = max(xs.min() - 3, 0), min(xs.max() + 4, outline.shape[1])
    o = outline[y0:y1, x0:x1]
    near = ndi.binary_dilation(o, iterations=2)
    if exclude is not None:   # e.g. the petiole: the lamina contour closes across its base
        near &= ~(exclude[y0:y1, x0:x1] & ~o)
    f = np.where(near, dE[y0:y1, x0:x1], 0.0)
    f[ndi.binary_erosion(o, iterations=2)] = dE.max() + 1
    f = np.pad(f, 1)
    cs = measure.find_contours(f, thr)
    if not cs:
        return float(measure.perimeter(outline))
    c = max(cs, key=len)
    return float(np.hypot(*np.diff(c, axis=0).T).sum())


def _opening(mask: np.ndarray, r: float) -> np.ndarray:
    """Exact Euclidean morphological opening with a disk of radius r (px).

    Implemented with two Euclidean distance transforms (erosion = points
    farther than r from the background; dilation = points within r of the
    eroded set), which is O(N) and independent of r (Danielsson 1980;
    scipy.ndimage.distance_transform_edt).
    """
    core = ndi.distance_transform_edt(mask) > r
    if not core.any():
        return core
    return (ndi.distance_transform_edt(~core) <= r) & mask


def _crop(mask: np.ndarray, pad: int):
    ys, xs = np.nonzero(mask)
    y0, x0 = max(ys.min() - pad, 0), max(xs.min() - pad, 0)
    y1, x1 = min(ys.max() + pad + 1, mask.shape[0]), min(xs.max() + pad + 1, mask.shape[1])
    return np.s_[y0:y1, x0:x1]


def find_petiole(outline: np.ndarray, px_mm: float, p: AnalysisParams):
    """Separate the petiole from the lamina.

    1. Opening of the leaf outline by a disk of diameter ``petiole_max_width_mm``
       removes every part narrower than that disk: the petiole, and often the
       acuminate tip (drip tip) and small marginal teeth.
    2. Each removed ('thin') component is characterised by its distance d to
       the opened lamina. Its extent D = max d must exceed
       ``petiole_min_length_mm``.
    3. Taper test: ratio = n(d >= D/2) / n(d < D/2). For a strip of constant
       width (petiole) the ratio is ~1; for a triangle tapering to a point
       (leaf tip) it is ~1/3. Components with ratio >= ``petiole_min_taper``
       are petiole candidates; the one with the largest area is retained
       (a leaf has a single petiole).
    Returns (mask, geodesic length in px, mean width in px) or (None, 0, 0).
    """
    r = p.petiole_max_width_mm / 2 / px_mm
    blade = _opening(outline, r)
    if not blade.any():
        return None, 0.0, 0.0
    thin = outline & ~blade
    lab, n = ndi.label(thin, structure=np.ones((3, 3)))
    if n == 0:
        return None, 0.0, 0.0
    dist = ndi.distance_transform_edt(~blade)
    best, best_area = 0, 0
    for i in range(1, n + 1):
        c = lab == i
        d = dist[c]
        D = d.max()
        if D * px_mm < p.petiole_min_length_mm:
            continue
        ratio = (d >= D / 2).sum() / max(1, (d < D / 2).sum())
        if ratio >= p.petiole_min_taper and c.sum() > best_area:
            best, best_area = i, c.sum()
    if not best:
        return None, 0.0, 0.0
    pet = lab == best
    # geodesic length inside the petiole from its junction with the lamina
    start = pet & ndi.binary_dilation(blade, structure=np.ones((3, 3)))
    if not start.any():
        start = pet & (dist <= dist[pet].min() + 1)
    # Length along the centreline. The petiole is sliced into 1-mm bins of Euclidean
    # distance d to the lamina; the centroids of the slices form the centreline, whose
    # polyline length (plus half a bin at the junction and the remainder up to max d at the
    # tip) is the petiole length. Distances and centroids are rotation-invariant, and the
    # centreline follows a curved petiole (for a straight one the length equals max d).
    yy, xx = np.nonzero(pet)
    d = dist[yy, xx]
    bw = max(1.0, 1.0 / px_mm)
    k = np.floor(d / bw).astype(int)
    nb = k.max() + 1
    cnt = np.bincount(k, minlength=nb)
    ok = cnt > 0
    cx = np.bincount(k, xx, nb)[ok] / cnt[ok]
    cy = np.bincount(k, yy, nb)[ok] / cnt[ok]
    centres = (np.nonzero(ok)[0] + 0.5) * bw
    D = float(d.max())
    length = (centres[0]                                   # junction -> first centroid
              + float(np.hypot(np.diff(cx), np.diff(cy)).sum())
              + max(0.0, D - centres[-1]))                 # last centroid -> tip
    return pet, length, pet.sum() / length


def find_damage(rgb: np.ndarray, tissue: np.ndarray, px_mm: float, p: AnalysisParams):
    """Brown (necrotic) tissue within ``tissue``.

    Colour criterion: CIELAB hue angle h_ab = atan2(b*, a*) (CIE 15:2004).
    Healthy and chlorotic (yellowing) cocoa tissue has h_ab ~ 100-135 deg
    (a* < 0, green side of the red-green opponent axis); brown necrotic
    tissue ~ 50-65 deg (a* > 0). Tissue with h_ab < ``damage_hue_max_deg``
    and chroma C*_ab >= ``damage_min_chroma`` is classified brown. a* and b*
    are first smoothed with a Gaussian of fixed physical size
    (``damage_smooth_mm``) so that noise suppression does not depend on dpi.
    Shape criterion (cocoa veins are reddish-brown):
      * patches: brown regions wider than ``damage_min_width_mm`` (Euclidean
        opening), restored to their full extent within the brown mask;
      * spots: remaining brown components that are compact (major/minor axis
        ratio <= ``damage_spot_max_elongation``), larger than
        ``damage_spot_min_area_mm2`` and darker than the surrounding tissue
        (mean L* minus mean L* of a 3-px ring <= ``damage_spot_max_dL``) --
        necrotic flecks. Vein fragments are lighter than their surroundings;
      * everything else (thin, elongated: veins, midrib) is discarded.
    """
    if not tissue.any():
        return np.zeros(tissue.shape, bool)
    sl = _crop(tissue, 2)
    t = tissue[sl]
    lab = color.rgb2lab(rgb[sl][..., :3])
    Lst = lab[..., 0]
    sig = p.damage_smooth_mm / px_mm
    a, b = lab[..., 1], lab[..., 2]
    if sig >= 0.3:
        a, b = ndi.gaussian_filter(a, sig), ndi.gaussian_filter(b, sig)
    h = np.degrees(np.arctan2(b, a))
    C = np.hypot(a, b)
    brown = t & (h < p.damage_hue_max_deg) & (h > -90) & (C >= p.damage_min_chroma)
    r = p.damage_min_width_mm / 2 / px_mm
    keep = np.zeros_like(brown)
    if r >= 0.5:
        core = _opening(brown, r)
        if core.any():
            keep = brown & (ndi.distance_transform_edt(~core) <= r + 1.0)
        # compact spots among the rest
        rest_lab, n = ndi.label(brown & ~keep, structure=np.ones((3, 3)))
        min_px = p.damage_spot_min_area_mm2 / px_mm ** 2
        for pr in measure.regionprops(rest_lab):
            if pr.area < min_px:
                continue
            if pr.axis_major_length > p.damage_spot_max_elongation * max(pr.axis_minor_length, 1.0):
                continue
            y0, x0, y1, x1 = pr.bbox
            win = np.s_[max(y0 - 4, 0):y1 + 4, max(x0 - 4, 0):x1 + 4]
            m = rest_lab[win] == pr.label
            ring = ndi.binary_dilation(m, iterations=3) & ~m & t[win] & ~brown[win]
            if ring.any() and Lst[win][m].mean() - Lst[win][ring].mean() > p.damage_spot_max_dL:
                continue
            keep[pr.slice][pr.image] = True
    else:
        keep = brown
    # drop slivers left by the patch restoration (smaller than the minimum spot)
    kl, kn = ndi.label(keep, structure=np.ones((3, 3)))
    if kn:
        sz = np.bincount(kl.ravel())
        small = sz < p.damage_spot_min_area_mm2 / px_mm ** 2
        small[0] = False
        keep[small[kl]] = False
    out = np.zeros(tissue.shape, bool)
    out[sl] = keep
    return out


# ----------------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------------

def analyse(rgb: np.ndarray, p: AnalysisParams, image_id: str = "") -> LeafResult:
    px_mm = MM_PER_INCH / p.dpi             # pixel side (mm)
    px_cm = px_mm / 10.0
    pa_mm2 = px_mm ** 2                     # pixel area (mm^2)
    pa_cm2 = pa_mm2 / 100.0

    fg, thr, dE = segment(rgb, p)

    # Leaves = 8-connected objects of the hole-filled mask, larger than min area
    filled = ndi.binary_fill_holes(fg)
    lab_all, n_all = ndi.label(filled, structure=np.ones((3, 3)))
    sizes = ndi.sum(np.ones_like(lab_all), lab_all, index=np.arange(1, n_all + 1))
    min_leaf_px = p.min_leaf_area_cm2 / pa_cm2
    keep = np.nonzero(sizes >= min_leaf_px)[0] + 1
    # Remove scanner-edge lines: objects touching the image border whose maximal
    # thickness (2 x max of the Euclidean distance transform) is below the limit
    if len(keep):
        edge_ids = np.unique(np.concatenate([lab_all[0], lab_all[-1], lab_all[:, 0], lab_all[:, -1]]))
        edge_ids = np.intersect1d(edge_ids[edge_ids > 0], keep)
        if len(edge_ids):
            dt = ndi.distance_transform_edt(filled)
            thick = 2 * np.asarray(ndi.maximum(dt, lab_all, edge_ids)) * px_cm * 10
            keep = np.setdiff1d(keep, edge_ids[thick < p.edge_artefact_max_thickness_mm])
    labels = np.zeros_like(lab_all)
    # Renumber leaves top-to-bottom, then left-to-right (reading order of centroids)
    if len(keep):
        cents = ndi.center_of_mass(filled, lab_all, keep)
        order = sorted(range(len(keep)), key=lambda k: (round(cents[k][0] / (2 / px_cm)), cents[k][1]))
        for new, k in enumerate(order, start=1):
            labels[lab_all == keep[k]] = new

    # Holes = background inside a leaf outline (4-connected, dual of 8-conn foreground)
    hole_cand = _detect_holes(labels > 0, fg, dE, thr + p.hole_grow_delta)
    hl, nh = ndi.label(hole_cand)
    min_hole_px = max(p.min_hole_pixels, p.min_hole_area_mm2 / pa_mm2)
    hsz = ndi.sum(np.ones_like(hl), hl, index=np.arange(1, nh + 1)) if nh else np.array([])
    keep_h = np.nonzero(hsz >= min_hole_px)[0] + 1
    holes = np.zeros_like(hl)
    hole_rows = []
    if len(keep_h):
        lut = np.zeros(nh + 1, dtype=hl.dtype); lut[keep_h] = np.arange(1, len(keep_h) + 1)
        holes = lut[hl]
        props = measure.regionprops(holes)
        for pr in props:
            r, c = pr.coords[0]
            leaf_id = int(labels[r, c])
            a_mm2 = pr.area * pa_mm2
            hole_rows.append(dict(
                image=image_id, leaf=leaf_id, hole=pr.label,
                area_mm2=a_mm2,
                eq_diameter_mm=2 * np.sqrt(a_mm2 / np.pi),
                centroid_x_px=pr.centroid[1], centroid_y_px=pr.centroid[0]))
    hole_df = pd.DataFrame(hole_rows, columns=[
        "image", "leaf", "hole", "area_mm2", "eq_diameter_mm", "centroid_x_px", "centroid_y_px"])
    # renumber holes within each leaf
    if len(hole_df):
        hole_df = hole_df.sort_values(["leaf", "centroid_y_px", "centroid_x_px"]).reset_index(drop=True)
        hole_df["hole"] = hole_df.groupby("leaf").cumcount() + 1

    tissue = (labels > 0) & ~(holes > 0)
    rgbf = rgb[..., :3].astype(float)
    h, w = labels.shape
    petiole_all = np.zeros(labels.shape, bool)
    damage_all = np.zeros(labels.shape, bool)

    rows, geom = [], {}
    for lid in range(1, int(labels.max()) + 1):
        outline = labels == lid
        sl = _crop(outline, int(p.petiole_max_width_mm / px_mm) + 3)
        pet_len_cm = pet_area_cm2 = pet_w_mm = 0.0
        if p.separate_petiole:
            pet_c, pl, pw = find_petiole(outline[sl], px_mm, p)
            if pet_c is not None:
                petiole_all[sl] |= pet_c
                pet_len_cm, pet_w_mm = pl * px_cm, pw * px_mm
                pet_area_cm2 = (pet_c & tissue[sl]).sum() * pa_cm2
        lamina = outline & ~petiole_all
        tis = tissue & lamina
        dmg = find_damage(rgb, tis, px_mm, p)
        damage_all |= dmg
        dmg_lab, n_dmg = ndi.label(dmg, structure=np.ones((3, 3)))
        dmg_sizes = np.bincount(dmg_lab.ravel())[1:] * pa_mm2 if n_dmg else np.array([0.0])
        # Total length including the petiole (kept as an extra column)
        len_with_pet_cm = _max_feret(_pixel_corners(outline))[0] * px_cm
        ys, xs = np.nonzero(outline)
        touches = bool(ys.min() == 0 or xs.min() == 0 or ys.max() == h - 1 or xs.max() == w - 1)

        # All size and shape traits below are measured on the LAMINA (petiole excluded)
        lh = hole_df[hole_df.leaf == lid]
        hole_area_cm2 = lh.area_mm2.sum() / 100.0
        area_outline = lamina.sum() * pa_cm2          # lamina outline (holes included)
        area_net = area_outline - hole_area_cm2       # lamina tissue
        area_with_pet = outline.sum() * pa_cm2 - hole_area_cm2

        perim_cm = _subpixel_perimeter(lamina, dE, thr, exclude=petiole_all) * px_cm

        pts = _pixel_corners(lamina)
        feret, p1, p2, hull = _max_feret(pts)
        # Orient the length axis from the end nearer the image top
        if p1[1] > p2[1]:
            p1, p2 = p2, p1
        u = (p2 - p1) / np.linalg.norm(p2 - p1)
        widths, f0, v, tmin, tmax = _width_profile(lamina, u, p1)
        iw = int(np.argmax(widths))
        width_cm = widths[iw] * px_cm
        # Caliper width perpendicular to the length axis (bounding-box width)
        tc = (pts - p1) @ v
        caliper_w_cm = (tc.max() - tc.min()) * px_cm
        # Length of slices wider than 3.5 mm (MATLAB 'length without petiole nor apex tip')
        blade_px = (widths > p.petiole_width_mm / px_mm).sum()
        w35_len_cm = blade_px * px_cm
        nz = np.nonzero(widths > 0)[0]
        rel_pos = (iw - nz.min() + 0.5) / (nz.max() - nz.min() + 1)  # 0 = top end, 1 = bottom end

        hull_area_cm2 = ConvexHull(pts).volume * pa_cm2
        R, G, B = (rgbf[..., k][tis].mean() for k in range(3))

        rows.append(dict(
            image=image_id, leaf=lid,
            area_cm2=area_net,
            area_outline_cm2=area_outline,
            perimeter_cm=perim_cm,
            length_cm=feret * px_cm,
            width_cm=width_cm,
            caliper_width_cm=caliper_w_cm,
            area_with_petiole_cm2=area_with_pet,
            length_with_petiole_cm=len_with_pet_cm,
            petiole_length_cm=pet_len_cm,
            petiole_area_cm2=pet_area_cm2,
            petiole_width_mm=pet_w_mm,
            damage_area_cm2=dmg.sum() * pa_cm2,
            damage_pct=100 * dmg.sum() / max(1, tis.sum()),
            n_damage_patches=int(n_dmg),
            damage_max_patch_cm2=float(dmg_sizes.max()) / 100,
            length_w35_cm=w35_len_cm,
            aspect_ratio=(feret * px_cm) / width_cm if width_cm else np.nan,
            max_width_rel_pos=rel_pos,
            circularity=4 * np.pi * area_outline / perim_cm ** 2,
            solidity=area_outline / hull_area_cm2,
            lw_factor=area_outline / ((feret * px_cm) * width_cm),
            n_holes=len(lh),
            holes_area_mm2=lh.area_mm2.sum(),
            holes_pct=100 * hole_area_cm2 / area_outline,
            hole_mean_mm2=lh.area_mm2.mean() if len(lh) else 0.0,
            hole_median_mm2=lh.area_mm2.median() if len(lh) else 0.0,
            hole_sd_mm2=lh.area_mm2.std(ddof=1) if len(lh) > 1 else 0.0,
            hole_max_mm2=lh.area_mm2.max() if len(lh) else 0.0,
            mean_R=R, mean_G=G, mean_B=B,
            greenness=(G - R) / (G + R),
            touches_border=touches,
            dpi=p.dpi,
        ))
        # geometry for drawing (image pixel coords)
        sc = f0 + iw + 0.5
        geom[lid] = dict(
            length=(tuple(p1), tuple(p2)),
            width=(tuple(p1 + u * sc + v * (tmin[iw] - 0.5)),
                   tuple(p1 + u * sc + v * (tmax[iw] + 0.5))),
            centroid=(xs.mean(), ys.mean()),
        )

    leaves = pd.DataFrame(rows)
    return LeafResult(labels=labels, tissue=tissue, holes=holes, leaves=leaves,
                      hole_table=hole_df, threshold=thr, geometry=geom,
                      petiole=petiole_all, damage=damage_all)


def params_dict(p: AnalysisParams) -> dict:
    return asdict(p)
