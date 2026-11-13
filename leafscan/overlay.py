"""Render a diagnostic overlay (leaf outlines, holes, damage, petiole, length/width lines).

The colours of every annotated class are defined once in CLASS_COLORS and are
also used by the GUI for the corresponding values in the text summary.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi

from .analysis import LeafResult

CLASS_COLORS = {
    "length": (225, 0, 0),       # red line: lamina length (max Feret)
    "width": (0, 175, 0),        # green line: maximum width
    "holes": (255, 135, 0),      # orange: holes
    "damage": (230, 0, 230),     # magenta: brown (damaged) tissue
    "petiole": (0, 185, 225),    # cyan: petiole
    "seam": (255, 255, 255),     # white dashed: cut between two parts
}
# Leaf outline colours (leaf 1, 2, ...): chosen to differ from the class colours above
LEAF_COLORS = [(40, 90, 255), (140, 60, 230), (190, 160, 0), (90, 90, 90), (0, 120, 140), (150, 75, 30)]


def hex_color(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def leaf_color(lid: int):
    return LEAF_COLORS[(lid - 1) % len(LEAF_COLORS)]


def render_overlay(rgb: np.ndarray, res: LeafResult, show_holes=True, show_axes=True,
                   show_outline=True, show_damage=True, show_petiole=True) -> np.ndarray:
    img = rgb[..., :3].copy()
    lab = res.labels
    if show_damage and res.damage is not None and res.damage.any():
        m = res.damage
        img[m] = (0.45 * img[m] + 0.55 * np.array(CLASS_COLORS["damage"])).astype(np.uint8)
    if show_petiole and res.petiole is not None and res.petiole.any():
        m = res.petiole
        img[m] = (0.35 * img[m] + 0.65 * np.array(CLASS_COLORS["petiole"])).astype(np.uint8)
    if show_outline:
        edge = (lab > 0) & ~ndi.binary_erosion(lab > 0, iterations=max(1, img.shape[0] // 1200))
        for lid in range(1, int(lab.max()) + 1):
            img[edge & (lab == lid)] = leaf_color(lid)
    if show_holes and res.holes.max() > 0:
        hm = res.holes > 0
        grow = ndi.binary_dilation(hm, iterations=max(1, img.shape[0] // 1500))
        img[grow] = CLASS_COLORS["holes"]
    pil = Image.fromarray(img)
    d = ImageDraw.Draw(pil)
    lw = max(2, img.shape[0] // 500)
    try:
        font = ImageFont.load_default(size=max(14, img.shape[0] // 50))
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    seam = getattr(res, "seam", None)
    if seam is not None:  # re-assembled leaf: dashed line on the cut
        (x0, y0), (x1, y1) = seam
        n = max(2, int(np.hypot(x1 - x0, y1 - y0) / (3 * lw)))
        for i in range(0, n, 2):
            a0, a1 = i / n, min(1.0, (i + 1) / n)
            d.line([(x0 + a0 * (x1 - x0), y0 + a0 * (y1 - y0)), (x0 + a1 * (x1 - x0), y0 + a1 * (y1 - y0))],
                   fill=CLASS_COLORS["seam"], width=lw)
    for lid, g in res.geometry.items():
        if show_axes:
            d.line([g["length"][0], g["length"][1]], fill=CLASS_COLORS["length"], width=lw)
            if g["width"] is not None:
                d.line([g["width"][0], g["width"][1]], fill=CLASS_COLORS["width"], width=lw)
        cx, cy = g["centroid"]
        d.text((cx, cy), str(lid), fill=leaf_color(lid), font=font, stroke_width=3, stroke_fill=(255, 255, 255))
    return np.asarray(pil)
