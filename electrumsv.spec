# -*- mode: python -*-

import os
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files, collect_submodules
from PyInstaller.building.build_main import Analysis, PYZ, EXE

# -----------------------------
# Paths
# -----------------------------
HOME = os.path.abspath(r"C:\Users\Administrator\Documents\ElectrumSVP")
VENV = os.path.join(HOME, 'venv32')
ICON = os.path.join(HOME, 'electrumsv', 'data', 'icons', 'electrum-sv.ico')
README = os.path.join(HOME, 'README.md')
ENTRY_SCRIPT = os.path.join(HOME, 'run_electrumsv.py')

# -----------------------------
# Binaries
# -----------------------------
binaries = collect_dynamic_libs('PyQt5')

# Qt platform plugins
qt_platforms_dir = os.path.join(VENV, 'Lib', 'site-packages', 'PyQt5', 'Qt', 'plugins', 'platforms')
if os.path.isdir(qt_platforms_dir):
    binaries += [(os.path.join(qt_platforms_dir, f), 'PyQt5/Qt5/plugins/platforms')
                 for f in os.listdir(qt_platforms_dir) if os.path.isfile(os.path.join(qt_platforms_dir, f))]

# Include pyzbar DLLs (optional, for ctypes fallback)
for dll in ['libzbar-32.dll', 'libiconv-2.dll']:
    path = os.path.join(VENV, 'Lib', 'site-packages', 'pyzbar', dll)
    if os.path.isfile(path):
        binaries.append((path, '.'))

# -----------------------------
# Data files
# -----------------------------
datas = []

# Include electrumsv/data and resources recursively
for folder in ['data', 'resources']:
    dir_path = os.path.join(HOME, 'electrumsv', folder)
    for root, _, files in os.walk(dir_path):
        datas += [(os.path.join(root, f), os.path.relpath(root, HOME)) for f in files]

# Keep core crypto library
datas += collect_data_files('electrumsv_secp256k1')

# -----------------------------
# Hidden imports
# -----------------------------
hiddenimports = [
    'pkg_resources.py2_warn',
    'socks',
    'bip38',
    'electrumsv.main_entrypoint',
    'electrumsv.gui.qt.main_window',
    'pyzbar',
]

# Core + crypto modules
for pkg in ['aiorpcx', 'bitcoinx', 'electrumsv_secp256k1']:
    hiddenimports += collect_submodules(pkg)

# -----------------------------
# Analysis
# -----------------------------
a = Analysis(
    [ENTRY_SCRIPT],
    pathex=[HOME],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

# -----------------------------
# Standalone EXE
# -----------------------------
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name=os.path.join(HOME, 'dist', 'ElectrumSV.exe'),
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON
)

# -----------------------------
# Optional portable EXE with README
# -----------------------------
portable_datas = a.datas + [(README, '.', 'DATA')] if os.path.isfile(README) else a.datas

exe_portable = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    portable_datas,
    name=os.path.join(HOME, 'dist', 'ElectrumSV-portable.exe'),
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON
)