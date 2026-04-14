#!/bin/bash
set -e

# === CONFIGURATION ===
APP_NAME="ElectrumSVP-0.1.0"
PROJECTS_DIR="$HOME/Projects"
APP_PATH="$PROJECTS_DIR/${APP_NAME}.app"
DMG_TMP="$PROJECTS_DIR/${APP_NAME}-dmg"
DMG_OUTPUT="$HOME/Desktop/${APP_NAME}.dmg"
VOL_NAME="${APP_NAME}"

# === CLEAN PREVIOUS BUILDS ===
rm -rf "$DMG_TMP"
mkdir -p "$DMG_TMP"

# === COPY APP AND CREATE SHORTCUT ===
echo "Copying ${APP_NAME}.app into DMG directory..."
cp -R "$APP_PATH" "$DMG_TMP/"
ln -s /Applications "$DMG_TMP/Applications"

# === CREATE DMG ===
echo "Creating unsigned DMG at: $DMG_OUTPUT"
hdiutil create -volname "$VOL_NAME" \
  -srcfolder "$DMG_TMP" \
  -ov -format UDZO "$DMG_OUTPUT"

# === CLEANUP ===
rm -rf "$DMG_TMP"

echo ""
echo "✅ DMG created successfully!"
echo "Location: $DMG_OUTPUT"
echo "You can now upload it or share it directly."
echo ""
echo "ℹ️ When a user opens it, they'll need to right-click → Open once to 
bypass Gatekeeper."

