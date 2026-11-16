"""Verify every binary slice and the signature of a local macOS app bundle."""
import argparse
from pathlib import Path
import plistlib
import subprocess

from merge_universal import architectures, is_macho


TARGETS = {'arm64': {'arm64'}, 'x86_64': {'x86_64'},
    'universal2': {'arm64', 'x86_64'}}


def check_bundle(bundle, target):
    expected = TARGETS[target]
    with (bundle / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    recorded = info.get('ElectrumSVPTargetArchitecture')
    if recorded is not None and recorded != target:
        raise ValueError(f'Bundle records target {recorded}, not {target}')
    executable = bundle / 'Contents/MacOS' / info['CFBundleExecutable']
    if not is_macho(executable):
        raise ValueError('Bundle executable is not a Mach-O file')
    count = 0
    for path in bundle.rglob('*'):
        if is_macho(path):
            actual = architectures(path)
            if actual != expected:
                raise ValueError(f'{path.relative_to(bundle)} has {sorted(actual)}; '
                    f'expected {sorted(expected)}')
            count += 1
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--arch', required=True, choices=tuple(TARGETS))
    args = parser.parse_args()
    count = check_bundle(args.bundle.resolve(), args.arch)
    print(f'Verified {count} {args.arch} Mach-O files and bundle signature.')
