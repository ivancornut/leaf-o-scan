# LeafScan: methods, equations and validation report

*Version 0.3.0, 13 November 2026. v0.2 added brown-damage measurement (§3b) and automatic petiole separation (§3c). v0.3 added leaves cut in two parts (§3d) and a rotation-invariant petiole length (§3c, step 4). v0.4 measures every size and shape trait on the **lamina (petiole excluded)**, so that leaves scanned with and without their petiole are comparable (§4). It also colour-matches the GUI text to the overlay.*

This report documents exactly how LeafScan computes each output, how the computations were checked, and how the results compare with the reference MATLAB code and with the cocoa literature. All numbers below come from `validation/validate.py` (raw output in `validation/results.md`) and from the test suite `tests/test_analysis.py` (14 tests, all passing).

---

## 1. Image acquisition

| Item | Value |
|---|---|
| Scanner | Canon CanoScan LiDE 400 (CIS sensor), A4 platen of 216 × 297 mm |
| Linux/macOS | SANE `scanimage`, `pixma` backend, `--mode Color --resolution <dpi> --format=tiff` |
| Windows | WIA 2.0 Automation: intent = colour (property 6146), X/Y resolution (6147/6148), full extents (6151/6152), BMP transfer |
| Quick mode | 150 dpi by default (75/300 selectable), 0.169 mm pixel, about 16 s per A4 scan |
| HD mode | 600 dpi by default (300/1200 selectable), 0.042 mm pixel, about 33 s per A4 scan |

Spatial calibration uses the nominal scanner resolution:
pixel side *s* = 25.4 / dpi (mm), pixel area *a* = *s*² (mm²).
The platen check (1276 × 1754 px at 150 dpi = 216.07 × 297.01 mm, which matches the SANE-reported platen size) confirms that the image dimensions are consistent with the nominal dpi. It is **not** an independent physical calibration (see §7, recommendation 1).

---

## 2. Segmentation of leaves and background

The reference is `segmentImageLeaf.m` (G. le Maire, CIRAD, 2020). That code flood-fills from the brightest pixel in CIE L\*a\*b\* space with a fixed tolerance. LeafScan keeps the L\*a\*b\* colour-distance idea but makes the threshold data-driven.

1. **Colour space.** sRGB (D65) is converted to CIE L\*a\*b\* (`skimage.color.rgb2lab`, same definition as MATLAB `rgb2lab`).
2. **Background reference colour** **b** is the per-channel median L\*a\*b\* of pixels whose L\* exceeds the Otsu threshold of L\*. This takes the bright population (the scanner lid). A median is used instead of the single brightest pixel because it is robust to specular or saturated pixels.
3. **Colour difference** (CIE76): ΔE(x) = ‖Lab(x) − **b**‖₂.
4. **Threshold.** *T* = Otsu(ΔE) + offset (Otsu 1979). Otsu maximises the between-class variance σ²_B(T) = ω₀ω₁(μ₀ − μ₁)². A pixel is foreground (leaf) if ΔE > *T*. The offset defaults to 0 and is adjustable in the GUI.
5. **Scan-edge artefacts.** Following the MATLAB `seuil_long_bord` logic, rows and columns at each image edge are cleared while more than 50 % of their pixels are foreground, up to 3 mm deep. This removes the dark calibration and lid-shadow line that the LiDE 400 produces on its right edge. Edge lines that cover less than half of an edge are caught by a second rule (added in v0.2): an object that touches the scan edge is discarded if its maximum thickness, 2 × the maximum of its Euclidean distance transform, is below 2 mm. Leaves are far thicker. This rule removed a 14.8 cm × 1.2 mm dark line on the right edge of a real scan, which v0.1 had counted as a 0.66 cm² 'leaf'.
6. **Leaf objects.** Holes are filled (`binary_fill_holes`) and 8-connected components are labelled. Components smaller than 0.5 cm² are discarded as dust; this is the same value as MATLAB `surf_threshold`. Leaves are numbered in reading order (top to bottom in 2 cm bands, then left to right).

**Why a midpoint threshold?** The threshold sits halfway between the leaf and background colour classes, so a pixel that is half covered by leaf is counted with a probability of about 0.5. Partial-coverage errors along the margin therefore cancel, and the area estimator is unbiased (verified in §6.1). The MATLAB tolerance (0.15 of the normalised squared distance to the brightest pixel) corresponds here to a ΔE threshold about 2–3 units closer to the background. That counts more of the blurred and shadowed margin as leaf (see §6.3).

---

## 3. Holes (perforations)

A hole is a background-coloured region **completely enclosed by leaf tissue**.

1. **Seeds**: pixels inside the leaf outline with ΔE ≤ *T*.
2. **Hysteresis growth** (as in Canny 1986). When the leaf does not lie perfectly flat, the lid seen through a hole is in shadow and looks grey. Seed regions therefore grow into connected pixels with ΔE ≤ *T* + δ, where δ is *Hole growth* and defaults to 10 ΔE. Regions without a seed are never holes, so pale veins and highlights are not counted. Grown regions that connect to the exterior of the leaf (the pale out-of-focus margin) are not added.
3. **Connectivity**: holes use 4-connectivity, the topological dual of the 8-connected foreground (Rosenfeld 1970). A notch in the leaf margin is therefore never a hole (tested).
4. **Size filter**: holes smaller than max(0.1 mm², 4 px) are ignored as noise. At 150 dpi the limit is 0.115 mm², an equivalent diameter of 0.38 mm. At 600 dpi it is 0.1 mm², 0.36 mm.

**Per hole:** area *A_h* = *n*·*a* (mm²), equivalent diameter *d* = 2√(*A_h*/π), and the centroid.
**Per leaf:** number of holes, Σ*A_h*, % holes = 100·Σ*A_h* / outline area, and the mean, median, standard deviation (n − 1) and maximum of *A_h*. The GUI histogram uses logarithmic bins (4 per decade), because hole sizes span several orders of magnitude.

---

## 3b. Brown (necrotic) damage

**Where:** lamina tissue only, which excludes holes and the petiole (the petiole of cocoa is brown and would otherwise count as damage).

**Colour criterion.** CIELAB hue angle *h*_ab = atan2(*b*\*, *a*\*) in degrees, and chroma *C*\*_ab = √(*a*\*² + *b*\*²) (CIE 15:2004). Colours measured on the LiDE 400 scans:

| tissue | *a*\* | *b*\* | *h*_ab (°) | G/R |
|---|---:|---:|---:|---:|
| healthy green | −3 to −10 | 11–12 | 105–134 | 0.99–1.26 |
| chlorotic (yellow-green) | −5 to −12 | 18–26 | 101–124 | 0.95–1.19 |
| brown necrotic patch | +4 to +8 | 5–12 | 52–65 | 0.77–0.85 |
| dark necrotic fleck | +5.6 | 8.0 | 55 | 0.75 |

A pixel is classified brown if *h*_ab < 80° (*a*\* > 0 means the red side of the red–green axis) **and** *C*\* ≥ 2. Pixels with lower chroma are grey, and their hue is undefined. Before the hue is computed, *a*\* and *b*\* are smoothed with a Gaussian of σ = 0.1 mm. Because σ is fixed in millimetres rather than pixels, noise suppression is the same at every dpi. A first version used a 3 × 3-pixel median filter instead; it fragmented 600 dpi results into 432 patches and was **rejected**.

The MATLAB symptom index (G/R < 0.95) was not reused. Chlorotic tissue has G/R ≈ 0.95–0.97, right at that threshold, so the index counts yellowing as damage. On the test leaves it gives 23–24 % instead of 14.5 %, and 5–6 % instead of 0.4–0.5 % on the nearly healthy leaf (§6.4). The hue angle separates the classes by more than 35°.

**Shape criterion (veins).** Cocoa midribs and veins are reddish-brown (*h*_ab ≈ 60–75°), so colour alone flags them. Brown pixels are kept only if one of two conditions holds:

* **Patches:** they belong to a region wider than 1.5 mm. This uses a Euclidean opening with a disk of diameter 1.5 mm; the surviving cores are then dilated by the same radius within the brown mask, which restores the patch to its exact outline.
* **Spots:** they belong to a compact component that meets all of the following:
  * area ≥ 0.2 mm²;
  * major/minor axis ratio ≤ 3, from second-order moments;
  * darker than its surroundings, i.e. mean *L*\* minus mean *L*\* of a 3-px ring ≤ 0.

  On the real scans, necrotic flecks had a median Δ*L*\* of −4.1 (darker), while vein fragments had a median of +2.4 (lighter).

Components smaller than 0.2 mm² are then removed.

**Outputs:**
* damaged area (cm²);
* % damaged = 100 · damaged area / lamina tissue area;
* number of damage patches (8-connected);
* largest patch (cm²).

Holes are reported separately (§3) and are not part of the damaged area.

---

## 3c. Petiole separation

1. **Opening.** The leaf outline is opened with a disk of diameter *D*_p = 6 mm. The opening is exact Euclidean, computed with two distance transforms (Danielsson 1980), so it is fast at any dpi. This removes every part narrower than 6 mm. On cocoa that means the petiole (measured at 3.3–4.0 mm) and usually the acuminate drip tip.
2. **Candidates.** Each removed ("thin") component gets the Euclidean distance *d* of each of its pixels to the opened lamina. Its extent is *E* = max *d*, and *E* must be ≥ 4 mm.
3. **Taper test, which separates the petiole from the drip tip.**
   * ratio = *n*(*d* ≥ *E*/2) / *n*(*d* < *E*/2), the pixel count in the distal half divided by the count in the proximal half.
   * A strip of constant width gives a ratio ≈ 1. A triangle tapering to a point gives (1/4)/(3/4) = 1/3.
   * A component is a petiole if the ratio is ≥ 0.65. If several qualify, the largest is kept, since a leaf has one petiole.
   * Measured values:
     * petioles: 0.93 on the damaged leaf, 0.77 on the cut stub of the first leaf;
     * drip tips: 0.51 and 0.40.
4. **Petiole length** is measured along the centreline.
   * The petiole pixels are sliced into 1 mm bins of their Euclidean distance *d* to the lamina. The centroids of the slices form the centreline.
   * Length = (centre of the first bin) + (length of the polyline joining the centroids) + (max *d* − centre of the last bin).
   * For a straight petiole this equals max *d*. For a curved one it follows the curve: a quarter-circle test petiole with a 2.356 cm centreline measures 2.38 cm, whereas the straight chord is 2.12 cm.
   * **Version history (method rejected after testing):** v0.2 used the 8-neighbour geodesic path (`MCP_Geometric`). When I rotated the same synthetic petiole to 6 orientations, that path gave 1.99–2.13 cm (±3.5 %). Shortest 8-neighbour paths are not unique, and the traced one can be L-shaped. The slice-centroid method gives 1.965–1.972 cm at 600 dpi and 1.955–1.981 cm at 150 dpi over the same 6 orientations. As a result, petiole lengths reported by v0.2 on real leaves were up to about 5 % high; the damaged leaf, for example, now measures 1.47 cm instead of 1.54 cm.
5. **Petiole width** = petiole area / petiole length (mean width).
6. **Lamina** = outline − petiole. Since v0.4, all size and shape traits are measured on the lamina (§4). The whole-leaf values are kept as `area_with_petiole_cm2` and `length_with_petiole_cm`. If no petiole is found, or petiole separation is switched off, lamina = whole leaf.

Pérez-Harguindeguy et al. (2013) leave the inclusion of the petiole to the study's objectives and ask that it be reported, so both versions are exported.

---

## 3d. Leaves cut in two parts

Large cocoa leaves can be longer than the 29.7 cm platen. The leaf is then cut once with scissors, and the two parts are scanned separately at any position and angle. The GUI guides this: tick *Leaf cut in 2 parts*, then Quick or HD scan prompts for part 1 and then part 2. *Combine two part files…* does the same for parts that were already scanned.

**Additive traits** are summed over the parts. They do not depend on re-assembly:
* outline, net and lamina area;
* number and area of holes;
* damaged area and number of patches;
* petiole area (taken from the part that has the petiole).

% holes and % damaged are recomputed from the sums.

**Finding the cut edge.** A scissor cut is a long straight edge, but so can be the margin of an oblong cocoa leaf. In the test scans, part 1's margin was a longer straight run than its cut, so the rule "longest straight edge" picked the wrong one. The cut is therefore found jointly in both parts:
1. In each part, up to 6 distinct straight runs of the outline are found with RANSAC: random point pairs, contiguous inliers within 0.8 mm, gaps ≤ 1 mm bridged, near-duplicates suppressed.
2. Each candidate line is refitted (total least squares) on the contour points within 2 mm of it.
3. Each candidate's extent is the **inner chord** *c*: the width of the leaf in a strip 0.5–2.5 mm inside the line. This is insensitive to jogs in the cut and to how the cut meets the margin. The two sides of one cut have the same chord.
4. The pair of candidates minimising *cost* = |*c*₁ − *c*₂| / max(*c*₁, *c*₂) + 0.5·(|cos α₁| + |cos α₂|) is chosen, where α is the angle between the edge and the major axis of its part. A cut runs across the leaf; a margin runs along it.

**Re-assembly.** Part 2 is moved by a rigid transform (rotation and translation, no scaling or mirroring):
* the midpoint of its cut chord goes onto the midpoint of part 1's;
* its cut direction is reversed, so that the parts lie on opposite sides of the cut.

Part 1 is copied without resampling. Part 2 is resampled bilinearly for colour and with nearest-neighbour for the mask. Seam gaps up to 2 mm wide, within 2 mm of the cut, are closed and filled with the nearest leaf colour. The re-assembled image is analysed like a normal scan, which gives the shape traits: length, lamina length, width, maximum-width position and solidity.

**Perimeter** = Σ over parts of [sub-pixel perimeter × (1 − fraction of the contour lying within 1.5 mm of that part's cut)]. Circularity is recomputed from this perimeter and the summed outline area. This avoids counting small steps at the ends of the seam.

**Quality indicators exported:**
* `cut_length_part1_cm` and `cut_length_part2_cm`;
* `cut_mismatch_mm`: the difference between the two cut lengths. A value above 2 mm triggers a warning in the GUI;
* `cut_straightness_rms_mm`;
* `n_parts` = 2.

The re-assembled image is saved as `scans/<id>_stitched.png`. The seam is drawn as a dashed white line in the overlay.

---

## 4. Shape and size

**Since v0.4, every size and shape trait is measured on the lamina, i.e. with the petiole excluded (§3c).** The lamina outline *O* is the filled leaf object minus the petiole, and the tissue is *O* minus the holes. This makes leaves scanned with and without their petiole directly comparable. Petiole length differs between leaves (2.1 cm on the leaf used in §6.5, a 0.5 cm stub on the first leaf) and would otherwise add to the length and shift the length axis. The two values that include the petiole are still exported. The sub-pixel perimeter closes across the petiole base: petiole pixels are excluded from the iso-contour field.

| Output | Definition |
|---|---|
| `area_outline_cm2` | *n*(*O*)·*a* / 100: lamina, holes included |
| `area_cm2` (net lamina area) | lamina outline area − Σ hole areas |
| `area_with_petiole_cm2` | net area of the whole leaf, petiole included |
| `length_with_petiole_cm` | maximum Feret diameter of the whole leaf, petiole included |
| `perimeter_cm` | Length of the outer iso-contour ΔE = *T*, found by marching squares (Lorensen & Cline 1987; `skimage.measure.find_contours`) with sub-pixel interpolation, interior saturated so that holes are excluded |
| `length_cm` | Maximum Feret (caliper) diameter: the largest distance between two points of the convex hull of the corners of the boundary pixels (Qhull, Barber et al. 1996) |
| `width_cm` | The pixels are projected onto the length axis **u** and cut into 1-px slices. Width is the maximum over slices of the extent along the perpendicular axis **v** (+1 px). This is the largest leaf width perpendicular to the length axis, as in the MATLAB code, but rotating coordinates instead of resampling the image (no interpolation error) |
| `caliper_width_cm` | Total extent of the leaf perpendicular to **u** (bounding-box width). It is larger than `width_cm` for curved or asymmetric leaves |
| `length_w35_cm` | Number of slices wider than 3.5 mm × *s*: the MATLAB-style length without petiole and thin tip (`threshold_petiole` = 0.35 cm). Kept for comparison with the MATLAB code |
| `petiole_length_cm`, `petiole_width_mm`, `petiole_area_cm2` | See §3c |
| `damage_area_cm2`, `damage_pct`, `n_damage_patches`, `damage_max_patch_cm2` | See §3b |
| `max_width_rel_pos` | Position of the widest slice along the length, from 0 at the end nearer the top of the image to 1 at the far end |
| `aspect_ratio` | *L*/*W* |
| `circularity` | 4π·*A_outline*/*P*² |
| `solidity` | *A_outline* / convex hull area |
| `lw_factor` | *A_outline* / (*L*·*W*), the shape coefficient *k* of the classic *A* = *k*·*L*·*W* model |
| `mean_R/G/B`, `greenness` | Means over tissue pixels; greenness = (G − R)/(G + R), as in the MATLAB code |
| `touches_border` | True if the outline touches the image edge. **The leaf may be truncated, so check its length** |

**Why pixel corners?** Feret diameters computed from pixel centres are biased low by about one pixel. Using the four corners of every boundary pixel gives the extent of the pixel footprint: a rectangle of 10 cm × 4 cm measures a diagonal of 10.77 cm (tested to ±0.02 cm).

**Why a sub-pixel perimeter?** A perimeter traced on pixel boundaries over-estimates smooth outlines because of the staircase effect. On the synthetic ellipse the bias was +3 % to +6 % for 4- and 8-connected traversals and +30 % for skimage's `neighborhood=8` estimator, which was used in the first draft and **rejected after testing**. The sub-pixel iso-contour gives +0.45 %.

**Caveat on perimeter.** A real leaf margin is irregular at all scales, so its measured perimeter grows with resolution: the same leaf measures 59.4 cm at 150 dpi and 63.5 cm at 600 dpi. Compare perimeters (and circularity) only between scans made at the same dpi.

---

## 5. Software

Python 3.12, NumPy, SciPy (`ndimage`, `spatial.ConvexHull`), scikit-image (van der Walt et al. 2014), pandas, Pillow, PySide6 (Qt 6), matplotlib. The code is pure Python with no compiled parts of its own, so it runs on Linux, macOS and Windows. Only the scanner backend is OS-specific.

Code layout: `leafscan/analysis.py` (all measurement code), `leafscan/scanner.py` (SANE/WIA), `leafscan/overlay.py` (diagnostic figure), `leafscan/gui.py` (interface).

---

## 6. Validation

### 6.1 Synthetic leaves with known geometry

Synthetic scans were rasterised with 4 × 4 super-sampling, so edge pixels are partially covered and grey, as on a real scanner. Leaf and background colours were taken from a real LiDE 400 scan, and Gaussian noise (σ = 2 grey levels) was added. The test leaf is an ellipse with *a* = 80 mm and *b* = 30 mm, with holes of radius 1, 2 and 4 mm. It was tested at 3 resolutions × 3 rotations. Reference perimeter: Ramanujan's second approximation.

Relative error, 100·(measured − true)/true, in %:

| dpi | rotation (rad) | outline area | net area | length | width | perimeter | hole area |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 150 | 0.0 | −0.05 | −0.06 | +0.02 | −0.08 | +0.45 | +1.31 |
| 150 | 0.5 | −0.03 | −0.04 | +0.10 | +0.09 | +0.45 | +1.22 |
| 150 | 1.2 | −0.03 | −0.05 | +0.12 | +0.18 | +0.47 | +1.22 |
| 300 | 0.0 | −0.02 | −0.03 | +0.02 | −0.09 | +0.44 | +1.01 |
| 300 | 0.5 | −0.02 | −0.02 | +0.06 | +0.11 | +0.44 | +0.81 |
| 300 | 1.2 | −0.02 | −0.03 | +0.06 | +0.12 | +0.46 | +1.00 |
| 600 | 0.0 | −0.01 | −0.02 | −0.01 | −0.02 | +0.43 | +0.45 |
| 600 | 0.5 | −0.01 | −0.01 | +0.03 | +0.04 | +0.44 | +0.43 |
| 600 | 1.2 | −0.01 | −0.01 | +0.03 | +0.04 | +0.46 | +0.43 |

All three holes were found in every case.

**Hole-size distribution** (600 dpi, 7 circular holes):

| true (mm²) | 0.283 | 0.785 | 2.011 | 4.524 | 12.57 | 28.27 | 78.54 |
|---|---|---|---|---|---|---|---|
| measured (mm²) | 0.294 | 0.808 | 2.048 | 4.572 | 12.65 | 28.41 | 78.75 |
| error (%) | +3.9 | +2.9 | +1.9 | +1.1 | +0.7 | +0.5 | +0.3 |

Hole areas are slightly over-estimated (by about half a pixel ring). The relative error shrinks with hole size, as expected for a boundary effect. If precise sizes of holes below 1 mm² matter, use HD (600 dpi) scans.

**Other tests:**
* Rectangle: area 40.00 cm² within 0.5 %, and Feret length equal to the diagonal.
* Three leaves plus a dust speck of 1.1 mm²: 3 leaves found, areas within 1 %.
* A margin notch is not counted as a hole.
* A 0.03 mm² speck is ignored.

**Edge-mixing check.** A scanner mixes partially covered pixels in linear light, not in gamma-encoded sRGB. Re-rendering the test with linear-light mixing changed the area error to −0.14 % at 150 dpi, −0.07 % at 300 dpi and −0.03 % at 600 dpi, which is negligible.

### 6.2 Same real cocoa leaf at 150 and 600 dpi (LiDE 400)

| dpi | net area (cm²) | outline (cm²) | length (cm) | width (cm) | perimeter (cm) | holes (n) | holes (mm²) | *k* = A/(LW) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 150 | 151.39 | 151.54 | 26.32 | 8.28 | 58.41 | 11 | 14.7 | 0.695 |
| 600 | 151.31 | 151.50 | 26.30 | 8.28 | 62.08 | 18 | 19.4 | 0.695 |

(v0.4 values: lamina, petiole stub excluded.) Area, length and width agree within 0.1 % between resolutions. **Repeatability:** three separate 150 dpi scans of the same leaf, run through the GUI, gave net areas of 151.58, 151.54 and 151.56 cm² (range 0.03 %), lengths of 26.70–26.71 cm and a width of 8.27 cm each time. The hole count varied between 10 and 11, because the smallest holes are near the detection limit at 150 dpi. Holes differ: the 600 dpi scan resolves more small and grey holes (see §7). The leaf's petiole stub touches the top edge of the scan (`touches_border` = True). It is excluded from the lamina values, but `length_with_petiole_cm` is truncated. The repeatability figures above are from v0.1, when length still included the stub. The diagnostic overlay is in `validation/real_scans/cocoa_leaf_600dpi_overlay.jpg`.

### 6.3 Comparison with the MATLAB reference (Dataverse example: Eucalyptus leaves)

Leaves were matched one-to-one with the Hungarian algorithm.

| scan | dpi | area MATLAB → Python (cm²) | Δ area | length | width | blade length |
|---|---:|---|---:|---|---|---|
| A3FM0003 (6 leaves) | 300 | 22.6–39.6 | −1.7 to −2.2 % | ±0.1 cm (one leaf −0.5 cm) | −0.04 to −0.07 cm | −0.1 to −0.2 cm |
| B2S3A4FS-6 (4 leaves) | 150 | 38.8–58.6 | −0.1 to −0.5 % | ±0.03 cm | −0.01 to −0.06 cm | 0 to −0.1 cm |

Mean absolute differences: area 0.41 cm², length 0.08 cm, width 0.04 cm, blade length 0.09 cm, perimeter 0.35 cm (full table in `validation/results.md`).

**Explanation of the area difference.** The MATLAB segmentation was reimplemented in Python. That reimplementation reproduces the MATLAB areas to ±0.01 cm² (for example 33.19, 31.22, 22.58, 26.21, 39.61 and 26.17 cm²). So the gap comes **only** from the threshold position, not from a computational error. The MATLAB tolerance corresponds to ΔE ≈ 36.0 from the brightest pixel, which counts more of the grey, blurred and shadowed margin as leaf. LeafScan's midpoint threshold (ΔE ≈ 33.3 from the median background) was shown in §6.1 to be unbiased for blurred edges.

The gap is larger for A3FM0003, a 300 dpi JPEG with a visible shadow halo, than for B2S3A4FS-6 (0.3 %). This is consistent with the halo explanation, but it cannot be settled without an object of known area (recommendation 1).

The length difference on one leaf (−0.5 cm) comes from the definitions. MATLAB measures length along the inertia (PCA) axis of the whole leaf, including a curved petiole. LeafScan uses the maximum Feret diameter.

### 6.4 Damage and petiole

**Synthetic tests** (`tests/test_petiole_damage.py`; colours taken from the real scans):

| test | result |
|---|---|
| Blade + 3 mm × 20 mm petiole + 15 mm tapering drip tip, 150 and 600 dpi | petiole length 2.0 ± 0.15 cm, width 3.0 ± 0.35 mm, area within 12 %; tip kept in lamina (lamina length 13.5 ± 0.1 cm) ✔ |
| Same leaf without petiole | no petiole detected (the drip tip is not mistaken for one) ✔ |
| Brown disk 201.1 mm² + dark fleck 2.0 mm² + 0.6 mm reddish-brown midrib, at 150, 300 and 600 dpi | damaged area within 4 %, exactly 2 patches, midrib not counted ✔ |
| Green leaf with reddish-brown midrib only | damage 0.00 % ✔ |

**Real cocoa leaves (LiDE 400):**

| scan | dpi | damage % | damage (cm²) | patches | MATLAB G/R<0.95 % | petiole length (cm) | petiole width (mm) | lamina length (cm) | total length (cm) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| damaged leaf | 150 | 14.52 | 15.50 | 62 | 23.09 | 1.47 | 3.45 | 21.90 | 23.26 |
| damaged leaf | 600 | 14.59 | 15.58 | 61 | 23.97 | 1.47 | 3.45 | 21.90 | 23.26 |
| first leaf (few lesions) | 150 | 0.42 | 0.64 | 24 | 5.28 | 0.52 * | 3.58 | 26.32 | 26.71 |
| first leaf | 600 | 0.50 | 0.75 | 24 | 6.30 | 0.53 * | 3.61 | 26.30 | 26.71 |

\* This petiole is a cut stub that runs off the top of the scan, so its length is truncated.

* **Repeatability:** two separate 150 dpi scans of the damaged leaf gave 14.52 % and 14.70 % damaged, with petiole lengths of 1.471 cm and 1.475 cm and lamina areas of 106.76 cm² and 106.75 cm².
* **Resolution consistency:** damage agrees within 0.07–0.08 percentage points between 150 and 600 dpi, and petiole length within 0.01–0.05 cm.
* **Visual check:** the overlays are in `validation/real_scans/*_overlay.jpg`, with damage in magenta and petiole in cyan.
  * The large necrotic margins, the necrotic tip and the dark flecks are captured.
  * The midrib, veins and yellow chlorotic areas are not.
  * Two residual false positives remain, each a few mm²: where a lateral vein joins the midrib, and at the petiole–blade junction.

**Sensitivity to the hue threshold** (150 dpi, % damaged):

| *h*_max (°) | 60 | 70 | 75 | **80** | 85 | 90 | 95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| damaged leaf | 10.44 | 13.58 | 14.02 | **14.52** | 15.18 | 16.20 | 17.74 |
| first leaf | 0.07 | 0.14 | 0.24 | **0.42** | 0.64 | 0.85 | 1.23 |

The result changes only slowly between 70° and 85° (13.6–15.2 %): the threshold lies in the valley between the brown (≈55°) and green (≈120°) hue modes. Most of the residual sensitivity comes from the transition zone at the edge of the lesions. Below 70°, the paler brown tissue is lost.

**Literature.** Scanner-based lesion measurement with hue thresholds is an established approach. Examples are a soybean scanner study that thresholded brown lesions in HSB space, and the tomato *Botrytis* tool Colour-analyzer, which uses CIELAB *a*\*/*b*\* for lesion area. No published hue threshold exists for cocoa necrosis, which is why the threshold is user-adjustable and its sensitivity is reported above.

### 6.5 Leaf cut in two parts

**Synthetic test** (`tests/test_stitch.py`). A leaf with petiole, drip tip, 2 holes and a necrotic patch was cut along an oblique line. The parts were rendered at different positions and rotations (+0.6 rad and −2.3 rad), then stitched and compared with the uncut leaf at 150 and 300 dpi. Result: areas within 0.3 %, length and width within 0.05 cm, perimeter within 2 %, petiole length within 0.05 cm, both holes found, and a cut mismatch under 0.5 mm ✔

**Real cocoa leaf.** I scanned it whole, then you cut it with scissors and placed each half on the glass at an arbitrary position. The cut had a visible jog at the midrib.

| trait | 150 dpi whole | 150 dpi 2 parts | diff | 600 dpi whole | 600 dpi 2 parts | diff |
|---|---:|---:|---:|---:|---:|---:|
| area, lamina (cm²) | 151.67 | 153.17 | +0.99 % | 151.60 | 153.13 | +1.01 % |
| area incl. petiole (cm²) | 152.22 | 153.71 | +0.98 % | 152.15 | 153.68 | +1.01 % |
| length, lamina (cm) | 26.27 | 26.39 | +0.43 % | 26.28 | 26.39 | +0.39 % |
| length incl. petiole (cm) | 28.03 | 28.13 | +0.36 % | 28.03 | 28.14 | +0.38 % |
| width, lamina (cm) | 8.32 | 8.38 | +0.69 % | 8.32 | 8.40 | +1.05 % |
| perimeter, lamina (cm) | 58.46 | 59.35 | +1.5 % | 61.60 | 61.66 | +0.08 % |
| solidity | 0.969 | 0.970 | +0.1 % | 0.970 | 0.971 | +0.1 % |
| petiole length (cm) | 2.116 | 2.104 | −0.6 % | 2.113 | 2.110 | −0.1 % |
| cut chord part 1 / part 2 (cm) | | 7.668 / 7.690 | 0.22 mm | | 7.655 / 7.686 | 0.30 mm |

**Where does the +1 % area come from?** The stitched outline was registered onto the whole-leaf outline (150 dpi, Powell optimisation of IoU):
* A rigid fit gives IoU = 0.988. Allowing a uniform scale raises it to 0.992, with the best fit when the stitched leaf is **0.46 % larger in every direction**. That is consistent with +0.4 % in length and width and +1.0 % ≈ 1.0046² in area.
* The excess area forms a thin, even rim along the whole margin. **None of it lies within 5 mm of the seam.**

The re-assembly is therefore geometrically faithful. The difference is physical: the crinkled whole leaf did not lie as flat on the glass as the two smaller halves, so its projected area is about 1 % smaller. This is an inherent uncertainty of flatbed measurement of leaves that are not flat; see recommendation 2 in §7 on flattening.

**Holes and damage are sensitive to how flat the leaf lies.**
* Holes: 11 (14.1 mm²) whole vs 21 (21.2 mm²) in parts at 150 dpi; 17 vs 22 at 600 dpi. The flatter halves show more small holes with the bright lid behind them (§7, limitation 2).
* Damage: 0.70 vs 0.57 cm² at 150 dpi; 0.61 vs 0.71 cm² at 600 dpi, on a leaf that is less than 0.5 % damaged.

Small amounts of holes and damage should be interpreted with this placement-to-placement variability in mind.

### 6.6 Plausibility against the cocoa literature

* **Size.** Flora descriptions of *Theobroma cacao* give blades of about 15–50 cm long and 4–15 cm wide (World Flora DB species page; that page was blocked to automated access, so the range is taken from its search-indexed text). The scanned leaf (26.7 × 8.3 cm, L/W = 3.2) lies inside that range.
* **Area–L×W allometry.** Schmildt et al. (2017) give LA = 0.6736·(L·W)^1.0036 for cocoa clone PH-16, from shade and full-sun leaves. This uses lamina length and width, which is what LeafScan reports since v0.4. With the measured lamina *L* and *W* (150 dpi):

  | leaf | *L* (cm) | *W* (cm) | measured area (cm²) | model (cm²) | difference |
  |---|---:|---:|---:|---:|---:|
  | first leaf | 26.32 | 8.28 | 151.54 | 149.66 | +1.3 % |
  | leaf used for the cut test (whole) | 26.27 | 8.32 | 151.81 | 150.13 | +1.1 % |
  | damaged leaf | 21.90 | 7.20 | 107.20 | 108.14 | −0.9 % |

  All three leaves fall within ±1.3 % of the published model, and the shape coefficients *k* = A/(LW) = 0.680–0.695 are typical of cocoa. Such allometric models have residual errors of several percent, so this is a consistency check on length, width and area together, not a calibration. In v0.1, *L* included the petiole stub; the earlier one-leaf agreement of 0.05 % was partly fortuitous.
* Suárez Salazar et al. (Colombia, 2895 leaves) also report that length and width polynomials predict cocoa leaf area with R² = 0.98. This supports *L* and *W* as the right descriptors to export.

---

## 7. Limitations and recommendations

1. **Physical calibration (recommended before production use).** Scan an object of certified size, such as a steel ruler or a calibration grid, at each dpi you use, and compare. The software relies on the nominal dpi of the scanner. CIS flatbed scanners are typically accurate to a few tenths of a percent, but this was not measured here.
2. **Holes in leaves that are not flat.** A hole is only detected if the lid behind it appears background-coloured. On a crinkled leaf, some holes look grey or dark (shadowed) and **may be under-counted**. Hysteresis growth (δ = 10) recovers part of them, but holes with no clear background-coloured core are not detected. Dark-rimmed lesions where tissue remains ("window feeding") are deliberately not counted. To improve this:
   * flatten the leaf, for example with a clean glass plate or a weight on the closed lid;
   * use HD mode for holes below about 1 mm²;
   * check the red hole overlay. If needed, raise *Hole growth* to 15, which added more grey holes on the test leaf, but check for false positives.
   * For a bias-free count, a transmitted-light (backlit) setup is the standard, but the LiDE 400 has no transparency unit.
3. **Touching or overlapping leaves** are counted as one object, as in the MATLAB code. Place leaves apart.
4. **Leaves touching the scan edge** are flagged (orange in the table). Their length and area may be truncated.
5. **Perimeter depends on resolution** (§4). Compare it only at a fixed dpi.
6. **Windows (WIA) and macOS backends** follow the official APIs but were not tested on hardware during this work.

---

8. **Brown damage** is a colour class. It does not identify a cause, which could be disease, sunscald, senescence, mechanical injury or herbivore scars.
   * **Not counted:**
     * thin brown structures that are not compact, darker spots;
     * flecks smaller than 0.2 mm²;
     * grey, low-chroma dead tissue (*C*\* < 2).
   * **Wrongly flagged:**
     * young red or purple flush leaves of cocoa have *h*_ab < 80° and would be classified as damaged; analyse them with a lower threshold or not at all;
     * a few mm² at midrib junctions (§6.4).
   * Always inspect the magenta overlay.
9. **Petiole detection** assumes the petiole is narrower than 6 mm and at least 4 mm long outside the blade. A shorter stub is reported as `petiole_length_cm` = 0 and stays in the lamina. A petiole cut off by the scan edge is measured only as far as the scan shows it (`touches_border`).
10. **Cut leaves**:
    * only **two** parts and **one** straight cut are supported. Do not cut through a hole or a damaged patch if you can avoid it, because a hole on the cut becomes a notch;
    * scan both parts **with the same face down**. A mirrored part cannot be re-assembled, since reflection is not allowed;
    * a strongly curved or ragged cut will raise `cut_mismatch_mm`. Check the dashed seam in the overlay.

## 8. References

* Barber C.B., Dobkin D.P., Huhdanpaa H. (1996). The Quickhull algorithm for convex hulls. *ACM Trans. Math. Software* 22: 469–483.
* Canny J. (1986). A computational approach to edge detection. *IEEE TPAMI* 8: 679–698.
* CIE (1976). CIE 1976 L\*a\*b\* colour space and ΔE\*ab colour difference (CIE 15:2004).
* Cornut I. et al. (2022). Potassium-limitation of forest productivity, part 1. *EGUsphere* 2022: 1–37. (Citation requested by the MATLAB reference code.)
* le Maire G. (2020). *Leaf size, shape and symptoms traits, computed from scans* (MATLAB code). CIRAD Dataverse. (`dataverse_files/`)
* Lorensen W.E., Cline H.E. (1987). Marching cubes: a high resolution 3D surface construction algorithm. *Computer Graphics* 21(4): 163–169.
* Otsu N. (1979). A threshold selection method from gray-level histograms. *IEEE Trans. Syst. Man Cybern.* 9: 62–66.
* Danielsson P.-E. (1980). Euclidean distance mapping. *Computer Graphics and Image Processing* 14: 227–248.
* Pérez-Harguindeguy N. et al. (2013). New handbook for standardised measurement of plant functional traits worldwide. *Australian Journal of Botany* 61: 167–234. https://www.publish.csiro.au/bt/Fulltext/bt12225 (petiole inclusion: see also https://prometheusprotocols.net/comparing-leaf-area-across-species/)
* Scanner-based brown-lesion measurement with HSB hue thresholds (soybean): https://pmc.ncbi.nlm.nih.gov/articles/PMC5515311 ; Colour-analyzer, CIELAB *a*\*/*b*\* lesion segmentation (tomato *Botrytis*): https://0-plantmethods-biomedcentral-com.brum.beds.ac.uk/counter/pdf/10.1186/s13007-024-01193-4.pdf (identified from search abstracts; full texts not consulted).
* Ramanujan S. (1914). Modular equations and approximations to π. *Q. J. Math.* 45: 350–372. (Ellipse perimeter.)
* Rosenfeld A. (1970). Connectivity in digital pictures. *J. ACM* 17: 146–160.
* Schmildt E.R., Trevisan E., Belique M., Schmildt O. (2017). Allometric models for determining leaf area in the 'PH-16' cocoa tree in the shade and in full sun. *Agro@mbiente On-line* 11(1): 47–55. https://doaj.org/article/3b79d0cdd1a04c3b82a6c72123d84c52
* Suárez Salazar J.C. et al. Regression models for leaf area and leaf weight of cacao (*Theobroma cacao* L.), CIMAZ Macagual, Caquetá, Colombia (2895 leaves). Abstract consulted only, and the journal and year are not verified: https://agriperfiles.agri-d.net/display/n80107
* van der Walt S. et al. (2014). scikit-image: image processing in Python. *PeerJ* 2: e453.
* World Flora DB, *Theobroma cacao* L. https://worldfloradb.net/plants/theobroma-cacao-l (leaf dimensions as indexed; page not directly accessible).
