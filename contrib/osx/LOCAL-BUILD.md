# Personal Universal macOS build

This recipe builds a single `ElectrumSV.app` with arm64 and x86_64
slices. It uses Python 3.10.20 and PyInstaller 6.22.3, with the dependency snapshot
in `requirements-local.txt`. It is separate from the inherited release script:
no global Python switch, old Intel USB bottle, Developer ID or notarization is
required. Do not use this recipe as a signed release certification.

## Prepare dependencies

On Apple Silicon, install both matching CPython 3.10.20 standalone distributions
with uv. Running the Intel interpreter requires Rosetta. Create one environment
for each architecture, using the corresponding interpreter. Install
`setuptools==65.7.0`, `cython==0.29.37` and `wheel==0.48.0` first, then install
`requirements-local.txt` with `--no-build-isolation`. The older build tools are
needed by the retained hidapi 0.10.0 pin; this is not a recommendation to upgrade
the vendor SDKs independently.

For the Intel dependency install, set `ARCHFLAGS`, `CFLAGS` and `LDFLAGS` to
`-arch x86_64` so source-built extensions target Intel too. Keep these variables
scoped to that install command. Do not change the existing development environment.

Use `merge_universal.py ARM_TREE INTEL_TREE NEW_OUTPUT_TREE` twice: once on the
matching Python distribution roots, and once on their environments' `site-packages`
directories. Create a new environment with the merged Python and place the merged
packages in its `site-packages`. The helper refuses an existing output directory,
preserves the inputs, and ad-hoc signs each merged binary. Both input environments
must have identical package versions and Python source files. Intel-only optional
AES-NI/CLMUL accelerators are omitted; PyCryptodome's portable AES/GHASH libraries
remain available on both architectures.

Build libusb 1.0.30 from its official release source using:

```sh
CFLAGS='-arch arm64 -arch x86_64 -mmacosx-version-min=11.0' \
LDFLAGS='-arch arm64 -arch x86_64 -mmacosx-version-min=11.0' \
./configure --disable-static --disable-examples-build --disable-tests-build
make -j8
```

Source: https://github.com/libusb/libusb/releases/tag/v1.0.30
The release tarball SHA-256 used locally was
`fea36f34f9156400209595e300840767ab1a385ede1dc7ee893015aea9c6dbaf`.

## Package and check

From the repository root, using the prepared Universal environment:

```sh
ELECTRUMSV_LIBUSB=/absolute/path/to/libusb/.libs/libusb-1.0.0.dylib \
  /absolute/path/to/universal-env/bin/python -m PyInstaller \
  --distpath /absolute/path/outside-synced-folders --workpath build/local contrib/osx/local.spec
codesign --verify --deep --strict '/absolute/path/outside-synced-folders/ElectrumSV.app'
```

Build outside cloud-synchronized folders: macOS file-provider attributes can invalidate
framework signatures when a bundle is built inside Documents. Archive the checked
bundle for transport rather than changing Gatekeeper settings.

`local.spec` explicitly requests `target_arch='universal2'`. Do not merge two
finished PyInstaller executables with lipo: their embedded archives do not merge.
Check **all** Mach-O files in the bundle, not only its launcher, for both slices.
The app uses Qt's current QR scanner and includes zxing-cpp; the obsolete native
CalinsQRReader helper is not needed by `scan_barcode()`.

For startup checks, use the bundle executable with `--help`, then `-D` pointing
to a fresh temporary profile and `gui --offline`. Repeat with `arch -arm64` and
`arch -x86_64`. Never use an existing wallet directory for a packaging smoke test.
When another wallet is running, set `"rpcport": 0` in the temporary profile's config
so the smoke test uses its own ephemeral loopback port. Do not change the owner's
normal RPC port. Offline startup also skips the remote header-tip check.
Source regressions and protocol tests do not substitute for testing hardware
through the packaged GUI. Intel startup under Rosetta is not an Intel-machine
hardware test; camera access and older macOS releases also need separate testing.

For a portable archive of the app (preserving its framework symlinks):

```sh
ditto -c -k --keepParent '/absolute/path/outside-synced-folders/ElectrumSV.app' \
  'dist/local/ElectrumSVP-universal.zip'
```

The app keeps the original `ElectrumSV.app` name, version-number executable and
`io.electrumsv.ElectrumSV` bundle identifier. Normal launches use the existing
ElectrumSV profile unless `-D` is provided. The bundle embeds the source
commit in `Info.plist`; record dependency versions, artifact hashes and actual test
coverage alongside each personal build. No Gatekeeper settings should be changed.
