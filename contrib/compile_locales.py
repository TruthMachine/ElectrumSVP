"""Compile checked-in translations locally without contacting a translation service."""
import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--msgfmt', default='msgfmt', help='Path to GNU gettext msgfmt')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    for source in sorted((root / 'data_in/locale').glob('*/LC_MESSAGES/electrumsv.po')):
        target = root / 'electrumsv/data/locale' / source.relative_to(root / 'data_in/locale')
        target = target.with_suffix('.mo')
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([args.msgfmt, '--check-format', '-o', str(target), str(source)], check=True)
        print(source.parts[-3])


if __name__ == '__main__':
    main()
