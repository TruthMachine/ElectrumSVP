# Native, ad-hoc signed personal build using the active Python environment.
# Run from the repository root; see README.md. This does not replace release signing.
import ctypes
import ctypes.util
import os
from pathlib import Path
import runpy
import subprocess

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

target_arch = os.environ.get('ELECTRUMSV_TARGET_ARCH', 'universal2')
if target_arch not in ('universal2', 'arm64', 'x86_64'):
    raise SystemExit('ELECTRUMSV_TARGET_ARCH must be universal2, arm64 or x86_64.')

root = Path(SPECPATH).resolve().parents[1]
version = runpy.run_path(str(root / 'electrumsv/version.py'))['PACKAGE_VERSION']
commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
ctypes.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit', mode=ctypes.RTLD_GLOBAL)

libusb = os.environ.get('ELECTRUMSV_LIBUSB') or ctypes.util.find_library('usb-1.0')
if not libusb or not Path(libusb).is_file():
    raise SystemExit('Set ELECTRUMSV_LIBUSB to a native libusb-1.0.dylib (see README.md).')

datas = [
    (str(root / 'electrumsv/data'), 'electrumsv/data'),
    (str(root / 'electrumsv/resources'), 'electrumsv/resources'),
    # The inherited network recovery path also looks here in frozen builds.
    (str(root / 'electrumsv/resources/headersloaded'), 'usr/share/headersloaded'),
    (str(root / 'LICENCE'), '.'),
]
hiddenimports = collect_submodules('electrumsv.devices')
for package in ('trezorlib', 'btchip', 'keepkeylib'):
    datas += collect_data_files(package)
hiddenimports += collect_submodules('trezorlib.transport')
hiddenimports += ['PyQt5.QtMultimedia', 'PyQt5.QtMultimediaWidgets', 'zxingcpp', 'PIL']

a = Analysis(
    [str(root / 'electrum-sv')], pathex=[str(root)],
    datas=datas, binaries=[(libusb, '.')], hiddenimports=hiddenimports,
    runtime_hooks=[str(root / 'contrib/osx/runtime_appkit.py')],
    excludes=['pytest', 'IPython', 'tkinter'],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name=version,
    target_arch=target_arch, console=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='ElectrumSV-local', strip=False, upx=False)
app = BUNDLE(coll, name='ElectrumSV.app',
    icon=str(root / 'contrib/osx/ElectrumSV.icns'),
    bundle_identifier='io.electrumsv.ElectrumSV', version=version,
    info_plist={
        'CFBundleDisplayName': 'ElectrumSV',
        'LSMinimumSystemVersion': '11.0',
        'ElectrumSVPSourceCommit': commit,
        'ElectrumSVPTargetArchitecture': target_arch,
        'NSHighResolutionCapable': True,
        'NSSupportsAutomaticGraphicsSwitching': True,
        'NSCameraUsageDescription': 'Scan payment and transaction QR codes.',
    })
