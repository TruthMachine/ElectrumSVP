"""Prepare a universal2 dependency tree BEFORE running PyInstaller.

Inputs must use the same Python and package versions. Never use this on frozen
executables: their appended PyInstaller archives cannot be combined with lipo.
The source trees remain untouched; the destination must not already exist.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


MACHO = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
    b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
    b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


def is_macho(path):
    if not path.is_file() or path.is_symlink():
        return False
    with path.open('rb') as stream:
        return stream.read(4) in MACHO


def architectures(path):
    return set(subprocess.check_output(['lipo', '-archs', str(path)], text=True).split())


def merge(arm, intel, output):
    shutil.copytree(arm, output, symlinks=True, ignore=shutil.ignore_patterns('__pycache__'))
    # Preserve platform-specific modules (notably CPython's sysconfig data).
    for path in intel.rglob('*'):
        if '__pycache__' in path.parts:
            continue
        target = output / path.relative_to(intel)
        if path.is_dir() or target.exists() or target.is_symlink():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            target.symlink_to(path.readlink())
        else:
            shutil.copy2(path, target)

    count = 0
    for target in output.rglob('*'):
        if not is_macho(target):
            continue
        relative = target.relative_to(output)
        sources = [arm / relative, intel / relative]
        slices = {}
        for source in sources:
            if is_macho(source):
                for arch in architectures(source) & {'arm64', 'x86_64'}:
                    slices.setdefault(arch, source)
        if set(slices) != {'arm64', 'x86_64'}:
            # These optional Intel accelerators are absent from ARM wheels.
            # PyCryptodome catches their absence and uses the bundled portable
            # AES/GHASH implementation. Keep every shipped binary universal.
            if relative.parts[0] in {'Crypto', 'Cryptodome'} and target.name in {
                    '_ghash_clmul.abi3.so', '_raw_aesni.abi3.so'}:
                target.unlink()
                print(f'Using portable cryptography instead of {relative}')
                continue
            raise RuntimeError(f'Missing architecture in {relative}: {sorted(slices)}')
        with tempfile.TemporaryDirectory() as temporary:
            thin = []
            for arch, source in sorted(slices.items()):
                path = Path(temporary) / arch
                if len(architectures(source)) == 1:
                    shutil.copy2(source, path)
                else:
                    subprocess.run(['lipo', str(source), '-thin', arch, '-output', str(path)],
                        check=True)
                thin.append(str(path))
            subprocess.run(['lipo', '-create', *thin, '-output', str(target)], check=True)
        subprocess.run(['codesign', '--force', '--sign', '-', str(target)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        count += 1
    print(f'Merged and signed {count} universal2 binaries in {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('arm64', type=Path)
    parser.add_argument('x86_64', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    merge(args.arm64.resolve(), args.x86_64.resolve(), args.output.resolve())
