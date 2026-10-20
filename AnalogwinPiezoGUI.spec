# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

SPECDIR = Path(SPECPATH).resolve()
if str(SPECDIR) not in sys.path:
    sys.path.insert(0, str(SPECDIR))

from build_support.bundle_paths import firmware_tool_files, tcl_tk_bundle

bundle = tcl_tk_bundle(sys.base_prefix)
# Firmware flasher tools (avrdude + CH340 driver installer) ship inside
# 'assets/tools' via the 'assets' data entry below; fail the build early if
# they are missing.  No firmware .hex is bundled.
firmware_tool_files(SPECDIR)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=bundle.as_binaries(),
    datas=[('assets', 'assets'), *bundle.as_datas()],
    hiddenimports=['_tkinter', 'serial', 'serial.tools.list_ports'],
    hookspath=['pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=['tk_runtime_hook.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AnalogwinPiezoGUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\app_icon.ico'],
)
