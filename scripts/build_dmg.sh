#!/usr/bin/env bash
# Builds Twin.app with py2app, signs it, and packages it into a distributable
# .dmg with a background and an Applications-folder shortcut.
#
# Usage:
#   scripts/build_dmg.sh
#
# Env vars (all optional — see docs/RELEASING.md for how to set them up):
#   CODESIGN_IDENTITY   "Developer ID Application: Your Name (TEAMID)"
#                        Signs the app and dmg for distribution. When unset,
#                        the app is ad-hoc signed (codesign -s -), which only
#                        runs on the machine that built it.
#   NOTARY_PROFILE       Name of a keychain profile created with
#                        `xcrun notarytool store-credentials`. Required to
#                        notarize and staple; skipped when unset.
#
# This script never reads a raw Apple ID password, API key, or certificate —
# only an identity name and a keychain profile name. The actual secrets live
# in the login keychain (for the notary profile) and the Keychain Access
# certificate store (for the signing identity), both outside this repo.
set -euo pipefail

cd "$(dirname "$0")/.."

APP_NAME="Twin"
VERSION="$(cat VERSION)"
DIST_DIR="dist"
APP_PATH="$DIST_DIR/$APP_NAME.app"
DMG_PATH="$DIST_DIR/$APP_NAME-$VERSION.dmg"
STAGING_DIR="build/dmg-staging"
BACKGROUND="assets/dmg/background.png"

echo "==> Building $APP_NAME.app v$VERSION with py2app"
rm -rf build "$DIST_DIR"
python3 setup.py py2app

echo "==> Signing $APP_NAME.app"
if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  codesign --force --deep --options runtime --timestamp --sign "$CODESIGN_IDENTITY" "$APP_PATH"
  codesign --verify --deep --strict --verbose=2 "$APP_PATH"
else
  echo "    CODESIGN_IDENTITY not set - ad-hoc signing (dev-machine only, see docs/RELEASING.md)"
  codesign --force --deep -s - "$APP_PATH"
fi

echo "==> Assembling $(basename "$DMG_PATH")"
rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"
cp -R "$APP_PATH" "$STAGING_DIR/"
rm -f "$DMG_PATH"

# --window-size, --icon-size, --text-size, --icon, and --app-drop-link here
# must stay in sync with the constants at the top of
# assets/dmg/make_dmg_background.py - the background image (including the
# label legibility pads) is drawn assuming exactly these values. Also:
# Finder's window bounds include the title bar, so roughly the bottom
# 35-40pt of WINDOW_H renders below the visible content area - keep
# background content above that band.
if command -v create-dmg >/dev/null 2>&1; then
  create-dmg \
    --volname "$APP_NAME" \
    --window-size 540 380 \
    --icon-size 96 \
    --text-size 16 \
    --background "$BACKGROUND" \
    --icon "$APP_NAME.app" 140 190 \
    --app-drop-link 400 190 \
    --hide-extension "$APP_NAME.app" \
    --no-internet-enable \
    "$DMG_PATH" \
    "$STAGING_DIR"
else
  echo "    create-dmg not found (brew install create-dmg) - falling back to a plain hdiutil dmg"
  ln -sf /Applications "$STAGING_DIR/Applications"
  hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING_DIR" -ov -format UDZO "$DMG_PATH"
fi

rm -rf "$STAGING_DIR"

if [[ ! -f "$DMG_PATH" ]]; then
  echo "error: $DMG_PATH was not created" >&2
  exit 1
fi

if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  echo "==> Signing $(basename "$DMG_PATH")"
  codesign --force --sign "$CODESIGN_IDENTITY" "$DMG_PATH"
fi

if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  echo "==> Submitting $(basename "$DMG_PATH") for notarization"
  xcrun notarytool submit "$DMG_PATH" --keychain-profile "$NOTARY_PROFILE" --wait
  echo "==> Stapling notarization ticket"
  xcrun stapler staple "$DMG_PATH"
else
  echo "    NOTARY_PROFILE not set - skipping notarization (see docs/RELEASING.md)"
fi

echo "==> Done: $DMG_PATH"
