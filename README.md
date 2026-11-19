# LeafScan

LeafScan is a desktop application written entirely in Python. It scans leaves on a flatbed scanner (tested with a Canon CanoScan LiDE 400) and measures, for each leaf:

* **surface** in cm²: the net tissue area and the outline area (holes included)
* **shape**: length (maximum Feret diameter), width (maximum width perpendicular to the length axis), perimeter, aspect ratio, circularity, solidity, and the relative position of the maximum width. **All size and shape values are measured on the blade, with the petiole excluded**; length and area including the petiole are given in separate columns
* **holes**: number of holes, total area, percentage of the leaf, size statistics, a per-hole table and a size-distribution histogram
* **brown damage**: necrotic area in cm² and as % of the lamina, number of patches and the largest patch. Veins and yellowing are not counted.
* **petiole**: separated automatically (cyan in the overlay). Its length (measured along the curve) and width are reported, together with the lamina area and length without the petiole
* **colour**: mean RGB and the greenness index
* **leaves larger than the scanner**: cut the leaf in two, tick *Leaf cut in 2 parts* and scan. You are prompted for each part. The parts are re-assembled along the cut, areas, holes and damage are summed, and the shape is measured on the re-assembled leaf

Results build up in a table, which you can export to Excel or CSV. Every scan is saved automatically.

Methods, equations, validation and sources are documented in **[REPORT.md](REPORT.md)**.

## Installation

You need Python 3.10 or newer.

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

On Windows, use `.venv\Scripts\pip install -r requirements.txt` instead.

### Scanner driver

| OS | Backend | What to install |
|---|---|---|
| Linux | SANE (`scanimage`) | `sudo apt install sane-utils` (the LiDE 400 uses the `pixma` backend) |
| macOS | SANE (`scanimage`) | `brew install sane-backends` |
| Windows | WIA 2.0 | Canon's LiDE 400 driver (WIA) and `pywin32`, which `requirements.txt` installs |

The Linux backend is tested on the LiDE 400. The Windows (WIA) and macOS backends are written to the documented APIs but have **not been tested on real hardware yet**.

You can analyse an image file with **Open image file(s)…** without a scanner. The file needs dpi metadata, or you can set the resolution with the *File dpi* field.

## Running

```bash
./run_leafscan.sh
```

On Windows, run `run_leafscan.bat`. Both launchers run `python -m leafscan`.

**venv outside the project folder (Windows).** `run_leafscan.bat` and `build_exe.bat` look for the Python interpreter in this order:

1. a venv folder given as an argument, for example `run_leafscan.bat C:\envs\leafscan`
2. the venv folder written on the first line of a file **`venv_path.txt`** next to the `.bat` files, for example `C:\envs\leafscan` (no quotes). Use this to make double-clicking work.
3. the currently activated venv (after running `C:\envs\leafscan\Scripts\activate`)
4. `.venv` inside the project folder
5. `python` on the PATH

Each `.bat` prints which Python it uses.

From a Python editor (Spyder, VS Code, IDLE, PyCharm), open **`run_leafscan.py`** in the project folder and press *Run*. You can also type `python run_leafscan.py` in a terminal. Make sure the editor uses the interpreter of the `.venv` you installed the requirements into.

Do not run `leafscan/gui.py` or other files inside the `leafscan/` folder on their own. They are parts of a package, and Python cannot resolve their imports when they are started directly.

1. Place the leaves flat on the glass, without overlapping, and close the lid.
2. Enter a **Sample ID**. It increments automatically after each scan.
3. Press **Quick scan** (150 dpi by default, about 16 s) or **HD scan** (600 dpi by default, about 35 s).
4. Check the overlay. Leaf outlines are drawn in blue (other colours for further leaves), holes in orange, damaged tissue in magenta, the petiole in cyan, the length in red and the width in green. The values under the image use the same colours. To compare with the raw scan, drag the **yellow divider** in the image or use the slider below it. The annotated scan is on the left and the original on the right; the *Annotated* and *Original* buttons jump to either end. Mouse wheel zooms, and dragging elsewhere pans.
5. Export the table with **Export table**.

Output folder (default `~/LeafScan_data`):

```
scans/<id>.tif              raw scan
overlays/<id>_overlay.jpg   diagnostic overlay
overlays/<id>_params.json   parameters and threshold used
results_leaves.csv          one row per leaf (updated after every scan)
results_holes.csv           one row per hole
```

## Building a Windows program (.exe)

On the Windows PC, after the installation above, double-click **`build_exe.bat`** in the project folder (it finds your venv as described under *Running*). You can also run it in a terminal:

```bash
build_exe.bat
```

It installs PyInstaller into `.venv` and builds `dist\LeafScan\LeafScan.exe` in a few minutes. The result is about 400 MB.

* Copy or zip the **whole `dist\LeafScan` folder**, not just the `.exe`, because it needs the files next to it. Python does not need to be installed on the computers that use it, but each one needs the Canon WIA scanner driver.
* PyInstaller cannot cross-compile: build on Windows to get a Windows `.exe`. The same command, without `.bat`, makes a macOS or Linux build on those systems.
* Windows SmartScreen or an antivirus may warn about an unsigned new `.exe`. Choose *More info → Run anyway*, or ask IT to allow it.
* After changing the code, run `build_exe.bat` again.

## Tests and validation

```bash
.venv/bin/python -m pytest tests
```

```bash
.venv/bin/python validation/validate.py validation/real_scans/cocoa_leaf_150dpi.png validation/real_scans/cocoa_leaf_600dpi.tif
```
