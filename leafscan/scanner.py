"""Cross-platform flatbed scanner access.

* Linux / macOS : SANE through the ``scanimage`` command line tool
  (Linux: package ``sane-utils``; macOS: ``brew install sane-backends``).
  The Canon LiDE 400 is supported by the SANE ``pixma`` backend.
* Windows       : Windows Image Acquisition (WIA 2.0) through COM
  (requires ``pywin32``). The Canon LiDE 400 ships a WIA driver.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # 1200-dpi A4 scans exceed Pillow's default guard


@dataclass
class ScannerDevice:
    id: str
    name: str
    backend: str  # 'sane' or 'wia'

    def __str__(self):
        return f"{self.name}  [{self.id}]"


class ScanError(RuntimeError):
    pass


# ----------------------------------------------------------------------------
# SANE (Linux / macOS)
# ----------------------------------------------------------------------------

def _scanimage() -> str | None:
    exe = shutil.which("scanimage")
    if exe:
        return exe
    for p in ("/opt/homebrew/bin/scanimage", "/usr/local/bin/scanimage", "/usr/bin/scanimage"):
        if os.path.exists(p):
            return p
    return None


def _sane_list() -> list[ScannerDevice]:
    exe = _scanimage()
    if not exe:
        raise ScanError("'scanimage' not found. Install SANE: Linux 'sudo apt install sane-utils', "
                        "macOS 'brew install sane-backends'.")
    out = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=60).stdout
    devs = []
    for m in re.finditer(r"device `([^']+)' is a (.+)", out):
        dev_id, name = m.group(1), m.group(2).strip()
        if dev_id.startswith("v4l"):  # webcams
            continue
        devs.append(ScannerDevice(dev_id, name, "sane"))
    # Prefer native backends over network re-exports of the same USB device
    rank = {"pixma": 0, "genesys": 0, "plustek": 0, "escl": 2, "airscan": 3}
    devs.sort(key=lambda d: rank.get(d.id.split(":")[0], 1))
    return devs


def _sane_scan(dev: ScannerDevice, dpi: int, out_path: str,
               progress: Callable[[float], None] | None) -> str:
    exe = _scanimage()
    out_path = os.path.splitext(out_path)[0] + ".tif"
    cmd = [exe, "-d", dev.id, "--mode", "Color", "--resolution", str(dpi),
           "--format=tiff", "-p", "-o", out_path]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, bufsize=1)
    buf, err_lines = "", []
    while True:
        ch = proc.stderr.read(1)
        if not ch:
            break
        if ch in "\r\n":
            m = re.search(r"Progress:\s*([\d.]+)%", buf)
            if m and progress:
                progress(float(m.group(1)))
            elif buf.strip():
                err_lines.append(buf.strip())
            buf = ""
        else:
            buf += ch
    proc.wait()
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise ScanError("scanimage failed: " + " | ".join(err_lines[-5:]))
    return out_path


# ----------------------------------------------------------------------------
# WIA (Windows)  -- WIA 2.0 Automation Layer
# ----------------------------------------------------------------------------
WIA_DEVICE_TYPE_SCANNER = 1
WIA_IPS_CUR_INTENT = 6146
WIA_IPS_XRES, WIA_IPS_YRES = 6147, 6148
WIA_IPS_XPOS, WIA_IPS_YPOS = 6149, 6150
WIA_IPS_XEXTENT, WIA_IPS_YEXTENT = 6151, 6152
WIA_INTENT_COLOR = 1
WIA_FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"


def _wia_list() -> list[ScannerDevice]:
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    dm = win32com.client.Dispatch("WIA.DeviceManager")
    devs = []
    for i in range(1, dm.DeviceInfos.Count + 1):
        info = dm.DeviceInfos.Item(i)
        if info.Type == WIA_DEVICE_TYPE_SCANNER:
            devs.append(ScannerDevice(info.DeviceID, info.Properties("Name").Value, "wia"))
    return devs


def _wia_set(props, pid, value):
    for p in props:
        if p.PropertyID == pid:
            p.Value = value
            return p
    return None


def _wia_scan(dev: ScannerDevice, dpi: int, out_path: str,
              progress: Callable[[float], None] | None) -> str:
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    dm = win32com.client.Dispatch("WIA.DeviceManager")
    info = None
    for i in range(1, dm.DeviceInfos.Count + 1):
        if dm.DeviceInfos.Item(i).DeviceID == dev.id:
            info = dm.DeviceInfos.Item(i)
    if info is None:
        raise ScanError("Scanner not found (disconnected?)")
    device = info.Connect()
    item = device.Items(1)
    props = item.Properties
    _wia_set(props, WIA_IPS_CUR_INTENT, WIA_INTENT_COLOR)
    _wia_set(props, WIA_IPS_XRES, dpi)
    _wia_set(props, WIA_IPS_YRES, dpi)
    _wia_set(props, WIA_IPS_XPOS, 0)
    _wia_set(props, WIA_IPS_YPOS, 0)
    # full platen: maximum extents allowed at this resolution
    for pid in (WIA_IPS_XEXTENT, WIA_IPS_YEXTENT):
        for p in props:
            if p.PropertyID == pid:
                try:
                    p.Value = p.SubTypeMax
                except Exception:
                    pass
    if progress:
        progress(5.0)
    img = item.Transfer(WIA_FORMAT_BMP)
    out_path = os.path.splitext(out_path)[0] + ".bmp"
    if os.path.exists(out_path):
        os.remove(out_path)
    img.SaveFile(out_path)
    if progress:
        progress(100.0)
    return out_path


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------

def list_scanners() -> list[ScannerDevice]:
    if sys.platform.startswith("win"):
        return _wia_list()
    return _sane_list()


def scan(dev: ScannerDevice, dpi: int, out_path: str,
         progress: Callable[[float], None] | None = None) -> tuple[np.ndarray, str]:
    """Acquire a colour scan of the full platen. Returns (RGB uint8 array, file path)."""
    if dev.backend == "wia":
        path = _wia_scan(dev, dpi, out_path, progress)
    else:
        path = _sane_scan(dev, dpi, out_path, progress)
    rgb = np.asarray(Image.open(path).convert("RGB"))
    return rgb, path


def load_image(path: str) -> tuple[np.ndarray, float | None]:
    """Load an image file and return (RGB array, dpi read from metadata or None)."""
    im = Image.open(path)
    dpi = im.info.get("dpi")
    # PNG stores pixels/metre, so 150 dpi reads back as 149.987: round to an integer
    dpi = float(round(dpi[0])) if dpi and dpi[0] and dpi[0] > 1 else None
    return np.asarray(im.convert("RGB")), dpi
