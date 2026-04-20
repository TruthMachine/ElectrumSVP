#!/bin/bash
set -e

# ===[ CONFIG ]===
PROJECTS_DIR="$HOME/Projects"
APPDIR="$PROJECTS_DIR/ElectrumSV-macos-appdir"
SRC_DIR="$PROJECTS_DIR/electrumsv"
VENV_DIR="$SRC_DIR/venv"
PYTHON_BIN="$VENV_DIR/bin/python3.9"
ICON_PATH="$SRC_DIR/electrumsv/data/icons/electrum-sv.icns"
APP_NAME="ElectrumSVP-0.1.1.app"

# ===[ ENSURE OPENSSL EXISTS ]===
OPENSSL_DIR="/usr/local/opt/openssl@3"
if [ ! -d "$OPENSSL_DIR" ]; then
    echo "❌ OpenSSL 3 not found at $OPENSSL_DIR"
    echo "Please install it manually with Homebrew:"
    echo "    brew install openssl@3"
    exit 1
fi
echo "🔹 Using OpenSSL from $OPENSSL_DIR"

# ===[ CLEAN APPDIR ]===
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr"

# ===[ BACKUP PRELOADED HEADERS BEFORE INSTALL ]===
HEADERS_SRC="$SRC_DIR/electrumsv/resources/headersloaded/headers"
TMP_HEADERS="$SRC_DIR/headers_temp_backup"
if [ -f "$HEADERS_SRC" ]; then
    echo "📦 Backing up preloaded headers..."
    mkdir -p "$TMP_HEADERS"
    cp "$HEADERS_SRC" "$TMP_HEADERS/headers"
fi

# ===[ COPY PYTHON ]===
cp -a "$VENV_DIR/bin" "$APPDIR/usr/"
cp -a "$VENV_DIR/lib" "$APPDIR/usr/"
cp -a "$VENV_DIR/include" "$APPDIR/usr/"

# ===[ COPY EXTRA PYTHON STDLIB FILES ]===
PYTHON_STDLIB_SRC="$HOME/python3.9.13/lib/python3.9"
cp -r "$PYTHON_STDLIB_SRC/"* "$APPDIR/usr/lib/python3.9/"
cp -r "$PYTHON_STDLIB_SRC/lib-dynload" "$APPDIR/usr/lib/python3.9/"
cp -r "$PYTHON_STDLIB_SRC/encodings" "$APPDIR/usr/lib/python3.9/"

export PATH="$APPDIR/usr/bin:$PATH"
export DYLD_LIBRARY_PATH="$APPDIR/usr/lib"

# ===[ INSTALL ELECTRUMSV + DEPS ]===
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install --prefix="$APPDIR/usr" "$SRC_DIR"
"$PYTHON_BIN" -m pip install --prefix="$APPDIR/usr" bip38 ecdsa pycryptodomex pysocks pyzbar

# ===[ RESTORE PRELOADED HEADERS INTO APPDIR ]===
RESTORE_PATH="$APPDIR/usr/lib/python3.9/site-packages/electrumsv/resources/headersloaded"
if [ -f "$TMP_HEADERS/headers" ]; then
    echo "📁 Restoring preloaded headers..."
    mkdir -p "$RESTORE_PATH"
    cp "$TMP_HEADERS/headers" "$RESTORE_PATH/"
    rm -rf "$TMP_HEADERS"
fi

# ===[ COPY ELECTRUMSV DATA ]===
mkdir -p "$APPDIR/usr/share/electrumsv"
cp -r "$SRC_DIR/electrumsv/data" "$APPDIR/usr/share/electrumsv/"

# ===[ COPY QT PLUGINS ]===
QT5_DIR="$VENV_DIR/lib/python3.9/site-packages/PyQt5/Qt5"
mkdir -p "$APPDIR/usr/plugins"
cp -r "$QT5_DIR/plugins"/* "$APPDIR/usr/plugins/"

# ===[ CREATE MAC .app STRUCTURE ]===
MAC_APP_DIR="$PROJECTS_DIR/$APP_NAME"
rm -rf "$MAC_APP_DIR"
mkdir -p "$MAC_APP_DIR/Contents/MacOS"
mkdir -p "$MAC_APP_DIR/Contents/Resources"
mkdir -p "$MAC_APP_DIR/Contents/Frameworks"

# Copy icon
cp "$ICON_PATH" "$MAC_APP_DIR/Contents/Resources/electrum-sv.icns"

# ===[ BUNDLE OPENSSL 3 (FIXED) ]===
echo "🔹 Bundling OpenSSL 3..."
cp "$OPENSSL_DIR/lib/libssl.3.dylib" "$MAC_APP_DIR/Contents/Frameworks/"
cp "$OPENSSL_DIR/lib/libcrypto.3.dylib" "$MAC_APP_DIR/Contents/Frameworks/"

# ===[ PYTHON LAUNCHER ]===
cat > "$MAC_APP_DIR/Contents/MacOS/ElectrumSV.py" << 'EOF'
#!/usr/bin/env python3
import sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../usr/lib/python3.9/site-packages"))

os.environ["PYTHONHOME"] = os.path.join(HERE, "../usr")
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(HERE, "../usr/plugins/platforms")
os.environ["ELECTRUMSV_DATA_DIR"] = os.path.join(HERE, "../usr/share/electrumsv/data")

from electrumsv.main_entrypoint import main
main()
EOF
chmod +x "$MAC_APP_DIR/Contents/MacOS/ElectrumSV.py"

# ===[ SHELL LAUNCHER WRAPPER ]===
cat > "$MAC_APP_DIR/Contents/MacOS/ElectrumSV" << 'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONHOME="$DIR/../usr"
export DYLD_LIBRARY_PATH="$DIR/../Frameworks:$DIR/../usr/lib"
export QT_QPA_PLATFORM_PLUGIN_PATH="$DIR/../usr/plugins/platforms"
exec "$DIR/../usr/bin/python3.9" "$DIR/ElectrumSV.py"
EOF
chmod +x "$MAC_APP_DIR/Contents/MacOS/ElectrumSV"

# ===[ Info.plist ]===
cat > "$MAC_APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>ElectrumSV</string>
    <key>CFBundleDisplayName</key>
    <string>ElectrumSV</string>
    <key>CFBundleIdentifier</key>
    <string>org.electrumsv.app</string>
    <key>CFBundleVersion</key>
    <string>1.3.16</string>
    <key>CFBundleExecutable</key>
    <string>ElectrumSV</string>
    <key>CFBundleIconFile</key>
    <string>electrum-sv.icns</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.12</string>
</dict>
</plist>
EOF

# ===[ COPY APPDIR INTO .APP ]===
cp -r "$APPDIR/usr" "$MAC_APP_DIR/Contents/"

# ===[ AGGRESSIVE STRIPPING ]===
APP_USR="$MAC_APP_DIR/Contents/usr"
echo "🔹 Stripping .app bundle..."

find "$APP_USR/lib/python3.9" -type d -name "__pycache__" -exec rm -rf {} +
find "$APP_USR/lib/python3.9" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
find "$APP_USR/lib/python3.9/site-packages" -type d \( -name "tests" -o -name "*.dist-info" -o -name "*.egg-info" -o -name "pip" \) -exec rm -rf {} +
rm -rf "$APP_USR/lib/python3.9/test"
rm -rf "$APP_USR/include"
find "$APP_USR/lib" -type f -name "*.a" -delete
find "$APP_USR/lib" -type f -name "*.so*" -exec strip -x {} \; 2>/dev/null || true
find "$APP_USR/bin" -type f -exec strip -x {} \; 2>/dev/null || true

# ===[ PATCH ALL .SO FILES INCLUDING _ssl (FIX) ]===
echo "🔹 Patching OpenSSL linkage..."

PY_BASE="$APP_USR/lib/python3.9"

for so in "$PY_BASE"/lib-dynload/*.so; do
    install_name_tool -change "$OPENSSL_DIR/lib/libssl.3.dylib" "@executable_path/../Frameworks/libssl.3.dylib" "$so" 2>/dev/null || true
    install_name_tool -change "$OPENSSL_DIR/lib/libcrypto.3.dylib" "@executable_path/../Frameworks/libcrypto.3.dylib" "$so" 2>/dev/null || true
done

find "$PY_BASE/site-packages" -name "*.so" | while read so; do
    install_name_tool -change "$OPENSSL_DIR/lib/libssl.3.dylib" "@executable_path/../Frameworks/libssl.3.dylib" "$so" 2>/dev/null || true
    install_name_tool -change "$OPENSSL_DIR/lib/libcrypto.3.dylib" "@executable_path/../Frameworks/libcrypto.3.dylib" "$so" 2>/dev/null || true
done

echo "🔍 Verifying _ssl linkage..."
otool -L "$PY_BASE/lib-dynload/_ssl.cpython-39-darwin.so"

echo "✅ Build complete!"
du -sh "$MAC_APP_DIR"