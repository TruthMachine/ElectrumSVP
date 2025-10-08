#!/bin/bash
set -e

# ===[ CONFIG ]===
APPDIR=~/AppImageBuild/electrumsv-appdir
PYTHON_DIR=~/AppImageBuild/python-3.9.13
SRC_DIR=~/AppImageBuild/electrumsv
ICON_PATH=~/Downloads/electrumsvicon.png
APPIMAGE_TOOL=~/AppImageBuild/appimagetool-x86_64.AppImage
APPIMAGE_NAME="ElectrumSV-1.3.16-x86_64.AppImage"
VENV_DIR=~/AppImageBuild/py39-venv
QT5_DIR=$VENV_DIR/lib/python3.9/site-packages/PyQt5/Qt5

export VENV_DIR="$VENV_DIR"

# ===[ CLEAN APPDIR ]===
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr"

# ===[ REMOVE OLD BYTECODE ]===
find "$SRC_DIR" -name "*.pyc" -delete
find "$SRC_DIR" -type d -name "__pycache__" -exec rm -rf {} +

# ===[ COPY PYTHON ]===
cp -a "$PYTHON_DIR/bin" "$APPDIR/usr/"
cp -a "$PYTHON_DIR/lib" "$APPDIR/usr/"
cp -a "$PYTHON_DIR/include" "$APPDIR/usr/"
patchelf --set-interpreter /lib64/ld-linux-x86-64.so.2 "$APPDIR/usr/bin/python3.9" || true
export LD_LIBRARY_PATH="$PYTHON_DIR/lib"

# ===[ CUSTOM SQLITE ]===
cp -av "$HOME/AppImageBuild/sqlite-install/lib/libsqlite3.so"* "$APPDIR/usr/lib/"
echo "✓ Copied SQLite"

# ===[ INSTALL ELECTRUMSV + DEPS ]===
"$PYTHON_DIR/bin/python3.9" -m ensurepip
"$PYTHON_DIR/bin/pip3" install --prefix="$APPDIR/usr" -r "$SRC_DIR/contrib/requirements/requirements.txt"

# Sweep + pyzbar + pysocks
"$PYTHON_DIR/bin/pip3" install --prefix="$APPDIR/usr" bip38 ecdsa pycryptodomex pysocks pyzbar
ZBAR_LIB=$(ldconfig -p | grep libzbar.so.0 | head -n1 | awk '{print $4}')
[ -n "$ZBAR_LIB" ] && cp -v "$ZBAR_LIB" "$APPDIR/usr/lib/" || echo "⚠ libzbar not found"

# Install ElectrumSV package
"$PYTHON_DIR/bin/pip3" install --prefix="$APPDIR/usr" "$SRC_DIR"

# Hardware wallet packages
"$PYTHON_DIR/bin/pip3" install --prefix="$APPDIR/usr" -r "$SRC_DIR/contrib/requirements/requirements-hw.txt"
echo "✓ Installed hardware wallet packages"

# ===[ BUNDLE LIBUSB + LIBUDEV + HIDAPI CONDITIONALLY ]===
mkdir -p "$APPDIR/usr/lib"

for LIB in libusb-1.0.so libudev.so libhidapi-hidraw.so.0 libhidapi-libusb.so.0; do
    SRC_LIB=$(ldconfig -p | grep "$LIB" | head -n1 | awk '{print $4}')
    if [ -n "$SRC_LIB" ]; then
        cp -v "$SRC_LIB" "$APPDIR/usr/lib/"
        echo "✓ Bundled $LIB"
    else
        echo "⚠ $LIB not found, skipping"
    fi
done

# HIDAPI symlinks (only if originals exist)
[ -f "$APPDIR/usr/lib/libusb-1.0.so.0" ] && ln -sf libusb-1.0.so.0 "$APPDIR/usr/lib/libusb-1-150b88da.0.so.0.1.0"
[ -f "$APPDIR/usr/lib/libudev.so.1.6.9" ] && ln -sf libudev.so.1.6.9 "$APPDIR/usr/lib/libudev-05fd5387.so.1.6.2"

# HIDAPI dependencies
for lib in libelf.so.1 libz.so.1 liblzma.so.5 libbz2.so.1.0; do
    SRC_LIB=$(ldconfig -p | grep "$lib" | head -n1 | awk '{print $4}')
    [ -n "$SRC_LIB" ] && cp -v "$SRC_LIB" "$APPDIR/usr/lib/" || echo "⚠ $lib not found"
done

# ===[ HEADERS ]===
mkdir -p "$APPDIR/usr/share/headersloaded"
cp "$SRC_DIR/electrumsv/resources/headersloaded/headers" "$APPDIR/usr/share/headersloaded/"
echo "✓ Copied headers"

# ===[ DESKTOP FILE + ICON ]===
mkdir -p "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp "$ICON_PATH" "$APPDIR/usr/share/icons/hicolor/256x256/apps/electrumsv.png"
cp "$ICON_PATH" "$APPDIR/electrumsv.png"

cat > "$APPDIR/electrumsv.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ElectrumSV
Exec=AppRun
Icon=electrumsv
Categories=Finance;Network;
EOF

# ===[ APPIMAGE LAUNCHER ]===
cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
export LD_LIBRARY_PATH="$HERE/usr/lib/python3.9/site-packages/hidapi.libs:$HERE/usr/lib:$LD_LIBRARY_PATH"
export PYTHONHOME="$HERE/usr"
export PYTHONPATH="$HERE/usr/lib/python3.9/site-packages"
export QT_QPA_PLATFORM_PLUGIN_PATH="$HERE/usr/plugins/platforms"
exec "$HERE/usr/bin/python3.9" -s "$HERE/usr/bin/electrumsv" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# ===[ GMP / SSL / LIBFFI ]===
cp /usr/lib/x86_64-linux-gnu/libgmp.so.10 "$APPDIR/usr/lib/"
ln -sf libgmp.so.10 "$APPDIR/usr/lib/libgmp-6a3274b3.so.10.3.2"
cp /usr/lib/x86_64-linux-gnu/libssl.so.1.1 /usr/lib/x86_64-linux-gnu/libcrypto.so.1.1 /usr/lib/x86_64-linux-gnu/libffi.so.6* "$APPDIR/usr/lib/"

# ===[ QT5 + X11 + ICU ]===
mkdir -p "$APPDIR/usr/lib/qt5/plugins"
cp -a $QT5_DIR/lib/libQt5*.so* "$APPDIR/usr/lib/"
cp -a $QT5_DIR/lib/libicu*.so* "$APPDIR/usr/lib/"
[ -d "$VENV_DIR/lib/python3.9/site-packages/PyQt5/Qt5/plugins" ] && cp -r "$VENV_DIR/lib/python3.9/site-packages/PyQt5/Qt5/plugins"/* "$APPDIR/usr/lib/qt5/plugins/"
cp -r /usr/lib/x86_64-linux-gnu/qt5/plugins/platforms "$APPDIR/usr/lib/qt5/plugins/"
cp -r /usr/lib/x86_64-linux-gnu/qt5/plugins/styles "$APPDIR/usr/lib/qt5/plugins/" || true
cp -r /usr/lib/x86_64-linux-gnu/qt5/plugins/iconengines "$APPDIR/usr/lib/qt5/plugins/" || true

# X11/xcb libraries
cp /usr/lib/x86_64-linux-gnu/libxcb*.so* "$APPDIR/usr/lib/"
cp -a /usr/lib/x86_64-linux-gnu/libX11.so* /usr/lib/x86_64-linux-gnu/libX11-xcb.so* /usr/lib/x86_64-linux-gnu/libxkbcommon*.so* "$APPDIR/usr/lib/"
cp /usr/lib/x86_64-linux-gnu/libXrender.so* /usr/lib/x86_64-linux-gnu/libXrandr.so* /usr/lib/x86_64-linux-gnu/libXcursor.so* "$APPDIR/usr/lib/"
cp /usr/lib/x86_64-linux-gnu/libXfixes.so* /usr/lib/x86_64-linux-gnu/libXi.so* /usr/lib/x86_64-linux-gnu/libXext.so* "$APPDIR/usr/lib/"
cp /usr/lib/x86_64-linux-gnu/libXtst.so* /usr/lib/x86_64-linux-gnu/libSM.so* /usr/lib/x86_64-linux-gnu/libICE.so* /usr/lib/x86_64-linux-gnu/libGL.so* "$APPDIR/usr/lib/"

# Plugin patching
mkdir -p "$APPDIR/usr/plugins/platforms"
cp "$QT5_DIR/plugins/platforms/libqxcb.so" "$APPDIR/usr/plugins/platforms/"
find "$APPDIR/usr/plugins" -type f -name "*.so" | while read -r plugin; do
    patchelf --set-rpath '$ORIGIN/../../lib' "$plugin" || true
done

# ===[ ELF RPATH FIX ]===
find "$APPDIR/usr" -type f -exec file {} \; | grep ELF | cut -d: -f1 | while read -r elf_file; do
    patchelf --remove-rpath "$elf_file" || true
    patchelf --set-rpath '$ORIGIN/../lib' "$elf_file" || true
done

# ===[ BUILD APPIMAGE ]===
"$APPIMAGE_TOOL" "$APPDIR"

DEFAULT_APPIMAGE="ElectrumSV-x86_64.AppImage"
if [ -f "$DEFAULT_APPIMAGE" ]; then
    cp "$DEFAULT_APPIMAGE" "$APPIMAGE_NAME"
    echo "✅ AppImage copied to: $APPIMAGE_NAME"
else
    echo "❌ Failed: Default AppImage ($DEFAULT_APPIMAGE) not found."
    exit 1
fi

