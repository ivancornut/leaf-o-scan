"""LeafScan graphical interface (PySide6 / Qt 6 - Linux, macOS, Windows)."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import traceback

import numpy as np
import pandas as pd
from PIL import Image
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QSlider, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QSpinBox, QSplitter, QTableView, QTabWidget, QVBoxLayout, QWidget,
                               QScrollArea, QAbstractItemView)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from . import __version__
from .analysis import AnalysisParams, analyse, params_dict
from .overlay import CLASS_COLORS, hex_color, leaf_color, render_overlay
from .scanner import ScannerDevice, list_scanners, load_image, scan
from .stitch import display_result, stitch_two

Image.MAX_IMAGE_PIXELS = None

LEAF_COLS = [  # (column, header, decimals)
    ("image", "Image", None), ("leaf", "Leaf", None),
    ("area_cm2", "Area (cm²)", 2), ("length_cm", "Length (cm)", 2), ("width_cm", "Width (cm)", 2),
    ("damage_pct", "Damaged (%)", 2), ("damage_area_cm2", "Damaged (cm²)", 2), ("n_damage_patches", "Damage patches", None),
    ("area_with_petiole_cm2", "Area incl. petiole (cm²)", 2), ("length_with_petiole_cm", "Length incl. petiole (cm)", 2),
    ("petiole_length_cm", "Petiole length (cm)", 2), ("petiole_width_mm", "Petiole width (mm)", 2),
    ("perimeter_cm", "Perimeter (cm)", 2), ("aspect_ratio", "L/W", 2),
    ("n_holes", "Holes (n)", None), ("holes_area_mm2", "Holes area (mm²)", 2), ("holes_pct", "Holes (%)", 3),
    ("hole_median_mm2", "Hole median (mm²)", 3), ("hole_max_mm2", "Hole max (mm²)", 2),
    ("area_outline_cm2", "Outline area (cm²)", 2), ("length_w35_cm", "Length >3.5 mm wide (cm)", 2),
    ("caliper_width_cm", "Caliper width (cm)", 2), ("max_width_rel_pos", "Max-width pos.", 2),
    ("circularity", "Circularity", 3), ("solidity", "Solidity", 3), ("lw_factor", "A/(L·W)", 3),
    ("greenness", "Greenness", 3), ("touches_border", "Touches edge", None), ("dpi", "dpi", None),
    ("n_parts", "Parts", 0), ("cut_mismatch_mm", "Cut mismatch (mm)", 2),
]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

class DataFrameModel(QAbstractTableModel):
    def __init__(self, cols=None):
        super().__init__()
        self.df = pd.DataFrame()
        self.cols = cols

    def set_df(self, df):
        self.beginResetModel()
        self.df = df.reset_index(drop=True)
        self.endResetModel()

    def _columns(self):
        if self.cols:
            return [c for c in self.cols if c[0] in self.df.columns]
        return [(c, c, 3) for c in self.df.columns]

    def rowCount(self, parent=QModelIndex()):
        return len(self.df)

    def columnCount(self, parent=QModelIndex()):
        return len(self._columns())

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        col, _, dec = self._columns()[index.column()]
        v = self.df.iloc[index.row()][col]
        if role == Qt.DisplayRole:
            if isinstance(v, (float, np.floating)) and np.isnan(v):
                return ""
            if isinstance(v, (float, np.floating)) and dec is not None:
                return f"{v:.{dec}f}"
            return str(v)
        if role == Qt.BackgroundRole and col == "touches_border" and bool(v):
            from PySide6.QtGui import QColor
            return QColor(255, 210, 160)
        if role == Qt.TextAlignmentRole and not isinstance(v, str):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self._columns()[section][1]
        return str(section + 1)


def _to_pixmap(rgb: np.ndarray) -> QPixmap:
    rgb = np.ascontiguousarray(rgb[..., :3])
    h, w, _ = rgb.shape
    return QPixmap.fromImage(QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy())


class ImageView(QGraphicsView):
    """Zoom (wheel) / pan (drag) viewer with a before/after split.

    The annotated image is shown left of a vertical divider and the original
    scan to the right. Drag the divider (yellow line) or use the slider below.
    """
    splitChanged = Signal(float)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.base = QGraphicsPixmapItem()                 # original scan (full width)
        self.clip = QGraphicsRectItem()                   # clips the annotated layer
        self.clip.setPen(QPen(Qt.NoPen))
        self.clip.setFlag(QGraphicsItem.ItemClipsChildrenToShape, True)
        self.annot = QGraphicsPixmapItem(self.clip)       # annotated scan
        for it in (self.base, self.annot):
            it.setTransformationMode(Qt.SmoothTransformation)
        pen = QPen(QColor(255, 220, 0)); pen.setWidth(3); pen.setCosmetic(True)
        self.line = QGraphicsLineItem(); self.line.setPen(pen); self.line.setZValue(10)
        for it in (self.base, self.clip, self.line):
            self.scene().addItem(it)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self._has = False
        self._split = 0.5
        self._dragging = False
        self._w = self._h = 1

    def set_images(self, base: np.ndarray, annotated: np.ndarray, keep_view=False):
        self.base.setPixmap(_to_pixmap(base))
        self.annot.setPixmap(_to_pixmap(annotated))
        self._h, self._w = base.shape[:2]
        self.scene().setSceneRect(0, 0, self._w, self._h)
        self.set_split(self._split)
        if not (keep_view and self._has):
            self.fit()
        self._has = True

    def set_image(self, rgb: np.ndarray, keep_view=False):
        self.set_images(rgb, rgb, keep_view)

    def set_split(self, frac: float):
        self._split = float(min(max(frac, 0.0), 1.0))
        x = self._split * self._w
        self.clip.setRect(0, 0, x, self._h)
        self.line.setLine(x, 0, x, self._h)
        self.line.setVisible(0.0 < self._split < 1.0)

    # --- divider dragging
    def _near_line(self, pos) -> bool:
        if not self.line.isVisible():
            return False
        lx = self.mapFromScene(self._split * self._w, 0).x()
        return abs(pos.x() - lx) <= 8

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._near_line(e.position().toPoint()):
            self._dragging = True
            self.setDragMode(QGraphicsView.NoDrag)
            e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._dragging:
            x = self.mapToScene(pos).x()
            self.set_split(x / self._w)
            self.splitChanged.emit(self._split)
            e.accept(); return
        self.viewport().setCursor(Qt.SplitHCursor if self._near_line(pos) else Qt.OpenHandCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._dragging = False
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            e.accept(); return
        super().mouseReleaseEvent(e)

    def fit(self):
        self._zoomed = False
        self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._has and not getattr(self, "_zoomed", False):
            self.fit()

    def wheelEvent(self, e):
        f = 1.25 if e.angleDelta().y() > 0 else 0.8
        self._zoomed = True
        self.scale(f, f)


class WorkerSignals(QObject):
    progress = Signal(float)
    done = Signal(object)
    error = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn, *args, **kw):
        super().__init__()
        self.fn, self.args, self.kw = fn, args, kw
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            try:
                res = self.fn(*self.args, progress=self.signals.progress.emit, **self.kw)
                self.signals.done.emit(res)
            except Exception as e:  # noqa: BLE001
                self.signals.error.emit(f"{e}\n\n{traceback.format_exc()}")
        except RuntimeError:
            pass  # window closed while the job was running: nobody to notify


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s.strip()) or "sample"


# ----------------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"LeafScan {__version__} - leaf area, shape and holes")
        self.resize(1500, 950)
        self.pool = QThreadPool.globalInstance()
        self._multi = None
        self._workers: set = set()
        self.leaves = pd.DataFrame()
        self.holes = pd.DataFrame()
        self.images: dict[str, dict] = {}   # image_id -> {rgb, path, dpi, result}
        self.current: str | None = None
        self.out_dir = os.path.join(os.path.expanduser("~"), "LeafScan_data")
        self._build()
        self.refresh_devices()

    # ------------------------------------------------------------------ UI
    def _build(self):
        # ---- left control panel
        panel = QWidget(); lay = QVBoxLayout(panel)

        g = QGroupBox("Scanner"); f = QFormLayout(g)
        self.dev_combo = QComboBox(); self.dev_combo.setMinimumWidth(220)
        b = QPushButton("↻"); b.setFixedWidth(32); b.setToolTip("Search scanners"); b.clicked.connect(self.refresh_devices)
        row = QHBoxLayout(); row.addWidget(self.dev_combo, 1); row.addWidget(b)
        f.addRow("Device", row)
        self.quick_dpi = QComboBox(); self.quick_dpi.addItems(["75", "150", "300"]); self.quick_dpi.setCurrentText("150")
        self.hd_dpi = QComboBox(); self.hd_dpi.addItems(["300", "600", "1200"]); self.hd_dpi.setCurrentText("600")
        f.addRow("Quick (dpi)", self.quick_dpi)
        f.addRow("HD (dpi)", self.hd_dpi)
        row = QHBoxLayout()
        self.btn_quick = QPushButton("Quick scan"); self.btn_quick.clicked.connect(lambda: self.do_scan(False))
        self.btn_hd = QPushButton("HD scan"); self.btn_hd.clicked.connect(lambda: self.do_scan(True))
        for bb in (self.btn_quick, self.btn_hd):
            bb.setMinimumHeight(38); row.addWidget(bb)
        f.addRow(row)
        self.cb_parts = QCheckBox("Leaf cut in 2 parts (scan each part, then stitch)")
        self.cb_parts.setToolTip("For leaves larger than the scanner: cut the leaf in two with one straight cut. "
                                 "You will be asked to place each part in turn; the parts are re-assembled "
                                 "digitally along the cut.")
        f.addRow(self.cb_parts)
        self.progress = QProgressBar(); self.progress.setValue(0)
        f.addRow(self.progress)
        lay.addWidget(g)

        g = QGroupBox("Sample"); f = QFormLayout(g)
        self.sample_id = QLineEdit("leaf_001")
        self.auto_inc = QCheckBox("Auto-increment after scan"); self.auto_inc.setChecked(True)
        f.addRow("Sample ID", self.sample_id); f.addRow(self.auto_inc)
        self.out_label = QLabel(self.out_dir); self.out_label.setWordWrap(True)
        b = QPushButton("Output folder…"); b.clicked.connect(self.choose_out)
        f.addRow(b, self.out_label)
        b = QPushButton("Open image file(s)…"); b.clicked.connect(self.open_files)
        f.addRow(b)
        b = QPushButton("Combine two part files…"); b.clicked.connect(self.open_parts)
        b.setToolTip("Select the two scans of a leaf that was cut in two")
        f.addRow(b)
        self.file_dpi = QSpinBox(); self.file_dpi.setRange(0, 4800); self.file_dpi.setSpecialValueText("from file")
        self.file_dpi.setToolTip("Resolution used for opened files when absent from metadata (or to override it)")
        f.addRow("File dpi", self.file_dpi)
        lay.addWidget(g)

        g = QGroupBox("Analysis parameters"); f = QFormLayout(g)
        d = AnalysisParams()
        self.p_minleaf = QDoubleSpinBox(); self.p_minleaf.setRange(0.01, 100); self.p_minleaf.setValue(d.min_leaf_area_cm2); self.p_minleaf.setSuffix(" cm²")
        self.p_minhole = QDoubleSpinBox(); self.p_minhole.setDecimals(3); self.p_minhole.setRange(0.001, 100); self.p_minhole.setValue(d.min_hole_area_mm2); self.p_minhole.setSuffix(" mm²")
        self.p_thoff = QDoubleSpinBox(); self.p_thoff.setRange(-30, 30); self.p_thoff.setValue(d.threshold_offset); self.p_thoff.setSuffix(" ΔE")
        self.p_grow = QDoubleSpinBox(); self.p_grow.setRange(0, 40); self.p_grow.setValue(d.hole_grow_delta); self.p_grow.setSuffix(" ΔE")
        self.p_minleaf.setToolTip("Objects smaller than this are ignored (dust, debris)")
        self.p_minhole.setToolTip("Holes smaller than this are ignored (noise). Use HD scans for small holes.")
        self.p_thoff.setToolTip("Shift of the automatic leaf/background threshold (Otsu on colour distance). "
                                "Positive = stricter leaf (smaller area).")
        self.p_grow.setToolTip("Holes seen through a non-flat leaf look grey. Hole regions grow from clear "
                               "background-coloured seeds into pixels up to this extra colour distance.")
        f.addRow("Min leaf area", self.p_minleaf); f.addRow("Min hole area", self.p_minhole)
        f.addRow("Threshold offset", self.p_thoff); f.addRow("Hole growth", self.p_grow)
        self.p_hue = QDoubleSpinBox(); self.p_hue.setRange(30, 110); self.p_hue.setValue(d.damage_hue_max_deg); self.p_hue.setSuffix(" °")
        self.p_hue.setToolTip("Tissue with CIELAB hue angle below this is 'brown' (damaged). Green ≈ 105–135°, "
                              "brown ≈ 50–65°. Lower = stricter.")
        self.p_dmgw = QDoubleSpinBox(); self.p_dmgw.setRange(0, 10); self.p_dmgw.setValue(d.damage_min_width_mm); self.p_dmgw.setSuffix(" mm")
        self.p_dmgw.setToolTip("Brown structures narrower than this (veins, midrib) are not counted, "
                               "except compact dark spots.")
        self.p_pet = QCheckBox("Separate petiole automatically"); self.p_pet.setChecked(d.separate_petiole)
        self.p_petw = QDoubleSpinBox(); self.p_petw.setRange(1, 20); self.p_petw.setValue(d.petiole_max_width_mm); self.p_petw.setSuffix(" mm")
        self.p_petw.setToolTip("Parts of the leaf narrower than this are candidate petioles (the tapering tip is rejected).")
        f.addRow("Damage hue max", self.p_hue); f.addRow("Damage min width", self.p_dmgw)
        f.addRow(self.p_pet); f.addRow("Petiole max width", self.p_petw)
        b = QPushButton("Re-analyse current image"); b.clicked.connect(self.reanalyse)
        f.addRow(b)
        lay.addWidget(g)

        g = QGroupBox("Display"); f = QVBoxLayout(g)
        self.c_out = QCheckBox("Leaf outlines"); self.c_hol = QCheckBox("Holes (orange)")
        self.c_ax = QCheckBox("Length (red) / width (green)")
        self.c_dmg = QCheckBox("Damaged tissue (magenta)"); self.c_pet = QCheckBox("Petiole (cyan)")
        for cb, key in ((self.c_hol, "holes"), (self.c_dmg, "damage"), (self.c_pet, "petiole")):
            cb.setStyleSheet(f"QCheckBox {{ color: {hex_color(CLASS_COLORS[key])}; font-weight: bold; }}")
        self.c_out.setStyleSheet(f"QCheckBox {{ color: {hex_color(leaf_color(1))}; font-weight: bold; }}")
        self.c_ax.setText("")  # rich two-colour label next to the box
        ax_lbl = QLabel(f"<b><span style='color:{hex_color(CLASS_COLORS['length'])}'>Length</span> / "
                        f"<span style='color:{hex_color(CLASS_COLORS['width'])}'>width</span></b>")
        for c in (self.c_out, self.c_hol, self.c_ax, self.c_dmg, self.c_pet):
            c.setChecked(True); c.toggled.connect(lambda _=None: self.show_current(keep_view=True))
            if c is self.c_ax:
                axrow = QHBoxLayout(); axrow.addWidget(c); axrow.addWidget(ax_lbl, 1); f.addLayout(axrow)
            else:
                f.addWidget(c)
        self.img_combo = QComboBox(); self.img_combo.currentTextChanged.connect(self.select_image)
        f.addWidget(QLabel("Image shown:")); f.addWidget(self.img_combo)
        b = QPushButton("Fit to window"); b.clicked.connect(lambda: self.view.fit()); f.addWidget(b)
        lay.addWidget(g)

        g = QGroupBox("Results"); f = QVBoxLayout(g)
        b = QPushButton("Export table (CSV / Excel)…"); b.clicked.connect(self.export); f.addWidget(b)
        b = QPushButton("Delete selected leaf rows"); b.clicked.connect(self.delete_rows); f.addWidget(b)
        b = QPushButton("Clear all results"); b.clicked.connect(self.clear_all); f.addWidget(b)
        lay.addWidget(g)
        lay.addStretch(1)
        scroll = QScrollArea(); scroll.setWidget(panel); scroll.setWidgetResizable(True); scroll.setMinimumWidth(330)

        # ---- right side: image + tabs
        self.view = ImageView()
        self.summary = QLabel("No image yet. Place leaves on the glass and press Quick scan or HD scan.")
        self.summary.setWordWrap(True)
        top = QWidget(); tl = QVBoxLayout(top); tl.setContentsMargins(0, 0, 0, 0)
        srow = QHBoxLayout()
        self.split_slider = QSlider(Qt.Horizontal); self.split_slider.setRange(0, 1000); self.split_slider.setValue(500)
        self.split_slider.setToolTip("Before/after: annotated scan on the left of the yellow line, original scan on the right. "
                                     "You can also drag the yellow line in the image.")
        self.split_slider.valueChanged.connect(lambda v: self.view.set_split(v / 1000))
        self.view.splitChanged.connect(self._split_from_view)
        b_a = QPushButton("Annotated"); b_a.clicked.connect(lambda: self.split_slider.setValue(1000))
        b_o = QPushButton("Original"); b_o.clicked.connect(lambda: self.split_slider.setValue(0))
        srow.addWidget(b_a); srow.addWidget(self.split_slider, 1); srow.addWidget(b_o)
        tl.addWidget(self.view, 1); tl.addLayout(srow); tl.addWidget(self.summary)

        self.tabs = QTabWidget()
        self.leaf_model = DataFrameModel(LEAF_COLS)
        self.leaf_table = QTableView(); self.leaf_table.setModel(self.leaf_model)
        self.leaf_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.leaf_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.leaf_table.doubleClicked.connect(self._row_clicked)
        self.hole_model = DataFrameModel([("image", "Image", None), ("leaf", "Leaf", None), ("hole", "Hole", None),
                                          ("area_mm2", "Area (mm²)", 3), ("eq_diameter_mm", "Eq. diameter (mm)", 3),
                                          ("centroid_x_px", "x (px)", 0), ("centroid_y_px", "y (px)", 0)])
        self.hole_table = QTableView(); self.hole_table.setModel(self.hole_model)
        self.hole_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.fig = Figure(figsize=(6, 3), tight_layout=True); self.canvas = FigureCanvasQTAgg(self.fig)
        dist = QWidget(); dl = QVBoxLayout(dist)
        self.dist_scope = QComboBox(); self.dist_scope.addItems(["Current image", "All images"])
        self.dist_scope.currentIndexChanged.connect(self.plot_dist)
        dl.addWidget(self.dist_scope); dl.addWidget(self.canvas, 1)
        self.tabs.addTab(self.leaf_table, "Leaves")
        self.tabs.addTab(self.hole_table, "Holes")
        self.tabs.addTab(dist, "Hole size distribution")

        right = QSplitter(Qt.Vertical); right.addWidget(top); right.addWidget(self.tabs)
        right.setStretchFactor(0, 4); right.setStretchFactor(1, 1)
        right.setSizes([720, 200])   # scan view gets most of the height; drag the splitter to change
        main = QSplitter(Qt.Horizontal); main.addWidget(scroll); main.addWidget(right)
        main.setStretchFactor(1, 1)
        self.setCentralWidget(main)

        m = self.menuBar().addMenu("&Help")
        a = QAction("About", self); a.triggered.connect(self.about); m.addAction(a)

    # ------------------------------------------------------------------ actions
    def params(self, dpi: float) -> AnalysisParams:
        return AnalysisParams(dpi=float(dpi), min_leaf_area_cm2=self.p_minleaf.value(),
                              min_hole_area_mm2=self.p_minhole.value(),
                              threshold_offset=self.p_thoff.value(), hole_grow_delta=self.p_grow.value(),
                              damage_hue_max_deg=self.p_hue.value(), damage_min_width_mm=self.p_dmgw.value(),
                              separate_petiole=self.p_pet.isChecked(), petiole_max_width_mm=self.p_petw.value())

    def _start(self, w: "Worker"):
        """Run a worker, keeping a Python reference until it finishes (PySide lifetime safety)."""
        w.setAutoDelete(False)
        self._workers.add(w)
        w.signals.done.connect(lambda *_: self._workers.discard(w))
        w.signals.error.connect(lambda *_: self._workers.discard(w))
        self.pool.start(w)

    def busy(self, on: bool, msg: str = ""):
        for b in (self.btn_quick, self.btn_hd):
            b.setEnabled(not on)
        if msg:
            self.statusBar().showMessage(msg)
        QApplication.setOverrideCursor(Qt.WaitCursor) if on else QApplication.restoreOverrideCursor()

    def refresh_devices(self):
        self.dev_combo.clear()
        self.statusBar().showMessage("Searching scanners…")
        w = Worker(lambda progress: list_scanners())
        w.signals.done.connect(self._devices_found)
        w.signals.error.connect(self._no_devices)
        self._start(w)

    @Slot(str)
    def _no_devices(self, e):
        self.statusBar().showMessage("No scanner: " + e.splitlines()[0])
        self.dev_combo.addItem("(no scanner found)", None)

    @Slot(float)
    def _on_progress(self, v):
        self.progress.setValue(int(v))

    def _devices_found(self, devs):
        self.dev_combo.clear()
        for d in devs:
            self.dev_combo.addItem(str(d), d)
        if not devs:
            self.dev_combo.addItem("(no scanner found)", None)
        self.statusBar().showMessage(f"{len(devs)} scanner(s) found")

    def choose_out(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.out_dir)
        if d:
            self.out_dir = d; self.out_label.setText(d)

    def _new_id(self) -> str:
        base = _safe(self.sample_id.text())
        return f"{base}_{dt.datetime.now():%Y%m%d-%H%M%S}"

    def do_scan(self, hd: bool):
        dev: ScannerDevice | None = self.dev_combo.currentData()
        if dev is None:
            QMessageBox.warning(self, "Scanner", "No scanner selected. Press ↻ to search again."); return
        dpi = int((self.hd_dpi if hd else self.quick_dpi).currentText())
        if self.cb_parts.isChecked():
            self._multi = dict(dev=dev, dpi=dpi, id=self._new_id(), parts=[])
            self._next_part()
            return
        image_id = self._new_id()
        os.makedirs(os.path.join(self.out_dir, "scans"), exist_ok=True)
        path = os.path.join(self.out_dir, "scans", image_id)
        self.progress.setValue(0)
        self.busy(True, f"Scanning {dpi} dpi…")
        params = self.params(dpi)

        def job(progress):
            rgb, fpath = scan(dev, dpi, path, progress)
            progress(100.0)
            res = analyse(rgb, params, image_id)
            return dict(id=image_id, rgb=rgb, path=fpath, dpi=dpi, result=res, params=params, scanned=True)

        w = Worker(job)
        # connect to bound methods only: they run in the GUI thread (queued connection)
        w.signals.progress.connect(self._on_progress)
        w.signals.done.connect(self._analysed)
        w.signals.error.connect(self._error)
        self._start(w)

    # ---------------------------------------------------------------- two-part leaves
    PART_TEXT = {1: "Place PART 1 on the glass (e.g. the base half, with the petiole), "
                    "and close the lid.\nPosition and angle do not matter.",
                 2: "Remove part 1 and place PART 2 (the other half) on the glass, "
                    "same face down, and close the lid.\nPosition and angle do not matter."}

    def _next_part(self):
        m = self._multi
        k = len(m["parts"]) + 1
        ok = QMessageBox.question(self, f"Cut leaf – part {k} of 2", self.PART_TEXT[k],
                                  QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Ok)
        if ok != QMessageBox.Ok:
            self._multi = None
            self.statusBar().showMessage("Two-part scan cancelled.")
            return
        os.makedirs(os.path.join(self.out_dir, "scans"), exist_ok=True)
        path = os.path.join(self.out_dir, "scans", f"{m['id']}_part{k}")
        self.progress.setValue(0)
        self.busy(True, f"Scanning part {k} of 2 at {m['dpi']} dpi…")
        dev, dpi = m["dev"], m["dpi"]
        w = Worker(lambda progress: scan(dev, dpi, path, progress))
        w.signals.progress.connect(self._on_progress)
        w.signals.done.connect(self._part_scanned)
        w.signals.error.connect(self._multi_error)
        self._start(w)

    @Slot(object)
    def _part_scanned(self, res):
        self.busy(False)
        m = self._multi
        if m is None:
            return
        rgb, fpath = res
        m["parts"].append((rgb, fpath))
        if len(m["parts"]) < 2:
            self._next_part()
        else:
            self._run_stitch(m["id"], m["dpi"], m["parts"], scanned=True)
            self._multi = None

    @Slot(str)
    def _multi_error(self, msg):
        self._multi = None
        self._error(msg)

    def _run_stitch(self, image_id, dpi, parts, scanned=False):
        params = self.params(dpi)
        self.busy(True, "Re-assembling the two parts…")
        (rgb1, f1), (rgb2, f2) = parts

        def job(progress):
            st = stitch_two(rgb1, rgb2, params, image_id)
            res = display_result(st)
            try:
                Image.fromarray(st.composite_rgb).save(
                    os.path.join(self.out_dir, "scans", image_id + "_stitched.png"), dpi=(dpi, dpi))
            except Exception:  # noqa: BLE001
                pass
            return dict(id=image_id, rgb=st.composite_rgb, path=f"{f1} + {f2}", dpi=dpi, result=res,
                        params=params, scanned=scanned, parts=parts)

        w = Worker(job)
        w.signals.done.connect(self._analysed)
        w.signals.error.connect(self._error)
        self._start(w)

    def open_parts(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select the TWO scans of a cut leaf", self.out_dir,
                                                "Images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp)")
        if not files:
            return
        if len(files) != 2:
            QMessageBox.warning(self, "Two parts", "Please select exactly two files."); return
        parts, dpis = [], []
        for fp in sorted(files):
            rgb, dpi = load_image(fp)
            parts.append((rgb, fp)); dpis.append(self.file_dpi.value() or dpi)
        if not dpis[0] or dpis[0] != dpis[1]:
            QMessageBox.warning(self, "Resolution", "Both parts need the same, known resolution (set 'File dpi')."); return
        os.makedirs(os.path.join(self.out_dir, "scans"), exist_ok=True)
        base = os.path.commonprefix([os.path.splitext(os.path.basename(f))[0] for f in files]).rstrip("_-") or "stitched"
        self._run_stitch(base, dpis[0], parts)

    def _increment(self):
        if not self.auto_inc.isChecked():
            return
        t = self.sample_id.text()
        m = re.search(r"(\d+)$", t)
        if m:
            n = m.group(1)
            self.sample_id.setText(t[:m.start()] + str(int(n) + 1).zfill(len(n)))

    def open_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Open scans", self.out_dir,
                                                "Images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp)")
        for fpath in files:
            rgb, dpi = load_image(fpath)
            if self.file_dpi.value() > 0:
                dpi = self.file_dpi.value()
            if not dpi:
                QMessageBox.warning(self, "Resolution unknown",
                                    f"{os.path.basename(fpath)} has no dpi metadata. Set 'File dpi' and open again.")
                continue
            image_id = os.path.splitext(os.path.basename(fpath))[0]
            params = self.params(dpi)
            self.busy(True, f"Analysing {image_id}…")
            w = Worker(lambda progress, rgb=rgb, params=params, image_id=image_id, fpath=fpath, dpi=dpi:
                       dict(id=image_id, rgb=rgb, path=fpath, dpi=dpi,
                            result=analyse(rgb, params, image_id), params=params, loaded=True))
            w.signals.done.connect(self._analysed); w.signals.error.connect(self._error)
            self._start(w)

    def reanalyse(self):
        if not self.current:
            return
        rec = self.images[self.current]
        if rec.get("parts"):
            self._run_stitch(rec["id"], rec["dpi"], rec["parts"])
            return
        params = self.params(rec["dpi"])
        self.busy(True, "Re-analysing…")
        w = Worker(lambda progress: dict(rec, result=analyse(rec["rgb"], params, rec["id"]), params=params))
        w.signals.done.connect(self._analysed); w.signals.error.connect(self._error)
        self._start(w)

    def _error(self, msg):
        self.busy(False, "Error")
        QMessageBox.critical(self, "Error", msg[:3000])

    def _analysed(self, rec):
        self.busy(False)
        iid = rec["id"]
        res = rec["result"]
        self.images[iid] = rec
        # replace rows of this image
        if len(self.leaves):
            self.leaves = self.leaves[self.leaves.image != iid]
        if len(self.holes):
            self.holes = self.holes[self.holes.image != iid]
        L = res.leaves.copy(); L.insert(1, "file", rec["path"]); L.insert(2, "scanned", dt.datetime.now().isoformat(timespec="seconds"))
        self.leaves = pd.concat([self.leaves, L], ignore_index=True) if len(self.leaves) else L
        self.holes = pd.concat([self.holes, res.hole_table], ignore_index=True) if len(self.holes) else res.hole_table.copy()
        self.leaf_model.set_df(self.leaves); self.hole_model.set_df(self.holes)
        if self.img_combo.findText(iid) < 0:
            self.img_combo.addItem(iid)
        self.img_combo.setCurrentText(iid)
        self.current = iid
        self.show_current()
        self._autosave(rec)
        if rec.pop("scanned", False):
            self._increment()
        n = len(res.leaves)
        warn = " ⚠ a leaf touches the scan edge (may be truncated)." if n and res.leaves.touches_border.any() else ""
        if "cut_mismatch_mm" in res.leaves and res.leaves.cut_mismatch_mm.max() > 2.0:
            warn += (f" ⚠ the two cut edges differ by {res.leaves.cut_mismatch_mm.max():.1f} mm: "
                     "check the re-assembled image.")
        self.statusBar().showMessage(f"{iid}: {n} leaf/leaves analysed (threshold ΔE={res.threshold:.1f}).{warn}")

    def select_image(self, iid):
        if iid in self.images:
            self.current = iid
            self.show_current()

    def show_current(self, keep_view=False):
        if not self.current:
            return
        rec = self.images[self.current]; res = rec["result"]
        ov = render_overlay(rec["rgb"], res, show_holes=self.c_hol.isChecked(),
                            show_axes=self.c_ax.isChecked(), show_outline=self.c_out.isChecked(),
                            show_damage=self.c_dmg.isChecked(), show_petiole=self.c_pet.isChecked())
        self.view.set_images(rec["rgb"], ov, keep_view=keep_view)
        C = {k: hex_color(v) for k, v in CLASS_COLORS.items()}

        def col(key_or_rgb, text):
            c = C[key_or_rgb] if isinstance(key_or_rgb, str) else hex_color(key_or_rgb)
            return f"<span style='color:{c}'>{text}</span>"

        parts = f" — {int(res.leaves.n_parts.max())} parts stitched" if "n_parts" in res.leaves and res.leaves.n_parts.notna().any() else ""
        lines = [f"<b>{self.current}</b> — {rec['dpi']} dpi — {len(res.leaves)} leaf/leaves{parts} "
                 f"<span style='color:#777'>(lamina values, petiole excluded)</span>"]
        for _, r in res.leaves.iterrows():
            lc = leaf_color(int(r.leaf))
            pet = (col("petiole", f"petiole <b>{r.petiole_length_cm:.2f} cm</b>") if r.petiole_length_cm > 0
                   else col("petiole", "no petiole found"))
            lines.append(
                col(lc, f"<b>Leaf {r.leaf}</b>: area <b>{r.area_cm2:.2f} cm²</b>") + " · "
                + col("length", f"length <b>{r.length_cm:.2f} cm</b>") + " · "
                + col("width", f"width <b>{r.width_cm:.2f} cm</b>") + " · "
                + col("holes", f"holes <b>{r.n_holes}</b> ({r.holes_area_mm2:.1f} mm², {r.holes_pct:.2f} %)") + " · "
                + col("damage", f"damaged <b>{r.damage_pct:.1f} %</b> ({r.damage_area_cm2:.2f} cm²)") + " · "
                + pet
                + (" <span style='color:#c60'>⚠ touches scan edge</span>" if r.touches_border else ""))
        self.summary.setText("<br>".join(lines))
        self.plot_dist()

    @Slot(float)
    def _split_from_view(self, frac):
        self.split_slider.blockSignals(True)
        self.split_slider.setValue(int(round(frac * 1000)))
        self.split_slider.blockSignals(False)

    def plot_dist(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        h = self.holes
        if len(h) and self.dist_scope.currentIndex() == 0 and self.current:
            h = h[h.image == self.current]
        if not len(h):
            ax.text(0.5, 0.5, "No holes detected", ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off()
        else:
            a = h.area_mm2.values
            lo, hi = np.floor(np.log10(a.min())), np.ceil(np.log10(a.max()))
            if hi <= lo:
                hi = lo + 1
            bins = np.logspace(lo, hi, int((hi - lo) * 4) + 1)
            ax.hist(a, bins=bins, color="#3a7d44", edgecolor="white")
            ax.set_xscale("log")
            ax.set_xlabel("Hole area (mm², log scale)"); ax.set_ylabel("Number of holes")
            ax.set_title(f"n = {len(a)}, total = {a.sum():.1f} mm², median = {np.median(a):.2f} mm²", fontsize=9)
            ax.spines[["top", "right"]].set_visible(False)
        self.canvas.draw_idle()

    def _row_clicked(self, idx):
        iid = self.leaves.iloc[idx.row()]["image"]
        if iid in self.images:
            self.img_combo.setCurrentText(iid)

    def _autosave(self, rec):
        """Save overlay image and append results to CSV files in the output folder."""
        try:
            os.makedirs(os.path.join(self.out_dir, "overlays"), exist_ok=True)
            res = rec["result"]
            ov = render_overlay(rec["rgb"], res)
            Image.fromarray(ov).save(os.path.join(self.out_dir, "overlays", rec["id"] + "_overlay.jpg"), quality=90)
            self.leaves.to_csv(os.path.join(self.out_dir, "results_leaves.csv"), index=False)
            self.holes.to_csv(os.path.join(self.out_dir, "results_holes.csv"), index=False)
            with open(os.path.join(self.out_dir, "overlays", rec["id"] + "_params.json"), "w") as fh:
                json.dump(dict(params_dict(rec["params"]), threshold_used=res.threshold,
                               source=rec["path"], leafscan_version=__version__), fh, indent=1)
        except Exception as e:  # noqa: BLE001
            self.statusBar().showMessage(f"Autosave failed: {e}")

    def export(self):
        if not len(self.leaves):
            QMessageBox.information(self, "Export", "No results yet."); return
        path, flt = QFileDialog.getSaveFileName(self, "Export results", os.path.join(self.out_dir, "leafscan_results.xlsx"),
                                                "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        if path.lower().endswith(".csv"):
            self.leaves.to_csv(path, index=False)
            self.holes.to_csv(path[:-4] + "_holes.csv", index=False)
        else:
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            with pd.ExcelWriter(path) as xw:
                self.leaves.to_excel(xw, sheet_name="leaves", index=False)
                self.holes.to_excel(xw, sheet_name="holes", index=False)
                pd.DataFrame([params_dict(self.params(0))]).drop(columns="dpi").to_excel(xw, sheet_name="parameters", index=False)
        self.statusBar().showMessage(f"Exported to {path}")

    def delete_rows(self):
        rows = sorted({i.row() for i in self.leaf_table.selectionModel().selectedRows()})
        if not rows:
            return
        drop = self.leaves.iloc[rows][["image", "leaf"]]
        key = set(map(tuple, drop.values))
        self.leaves = self.leaves[[(a, b) not in key for a, b in self.leaves[["image", "leaf"]].values]].reset_index(drop=True)
        if len(self.holes):
            self.holes = self.holes[[(a, b) not in key for a, b in self.holes[["image", "leaf"]].values]].reset_index(drop=True)
        self.leaf_model.set_df(self.leaves); self.hole_model.set_df(self.holes); self.plot_dist()

    def clear_all(self):
        if QMessageBox.question(self, "Clear", "Remove all results from the table? (Saved files are kept.)") != QMessageBox.Yes:
            return
        self.leaves = pd.DataFrame(); self.holes = pd.DataFrame()
        self.leaf_model.set_df(self.leaves); self.hole_model.set_df(self.holes); self.plot_dist()

    def about(self):
        QMessageBox.about(self, "About LeafScan",
                          f"LeafScan {__version__}\nLeaf area, shape and hole measurement from flatbed scans.\n"
                          "Segmentation inspired by G. le Maire (CIRAD, 2020) MATLAB code.\n"
                          "See REPORT.md for methods and validation.")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("LeafScan")
    w = MainWindow(); w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
