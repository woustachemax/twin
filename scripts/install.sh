#!/usr/bin/env bash
# Installs Twin without the Gatekeeper right-click dance: downloads the
# latest release's .dmg, mounts it, copies Twin.app into /Applications, and
# strips the quarantine flag so the first launch doesn't get blocked.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/woustachemax/twin/main/scripts/install.sh | bash
#
# Safe to re-run: an existing /Applications/Twin.app is replaced, not skipped.
# Every step checks its own exit code and fails loudly (a clear "error: ..."
# message on stderr, non-zero exit) rather than continuing past a problem.
set -euo pipefail

REPO="woustachemax/twin"
APP_NAME="Twin"
INSTALL_DIR="/Applications"
LATEST_RELEASE_API="https://api.github.com/repos/$REPO/releases/latest"

step() { echo "==> $*"; }
fail() { echo "error: $*" >&2; exit 1; }

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "Twin only runs on macOS."
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  fail "Twin's release build is Apple silicon only (this Mac reports $(uname -m))."
fi

command -v curl >/dev/null 2>&1 || fail "curl is required but wasn't found."
command -v hdiutil >/dev/null 2>&1 || fail "hdiutil is required but wasn't found."
command -v xattr >/dev/null 2>&1 || fail "xattr is required but wasn't found."

WORKDIR="$(mktemp -d)" || fail "couldn't create a temp directory."
MOUNT_DIR="$WORKDIR/mnt"
MOUNTED=0
cleanup() {
  if [[ "$MOUNTED" -eq 1 ]]; then
    hdiutil detach "$MOUNT_DIR" -quiet >/dev/null 2>&1 || true
  fi
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

step "Looking up the latest Twin release"
RELEASE_JSON="$(curl -fsSL "$LATEST_RELEASE_API")" || fail "couldn't reach the GitHub API ($LATEST_RELEASE_API)."

TAG="$(printf '%s' "$RELEASE_JSON" | grep -m1 '"tag_name"' | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/')"
DMG_URL="$(printf '%s' "$RELEASE_JSON" | grep -o '"browser_download_url": *"[^"]*\.dmg"' | head -n1 | sed -E 's/.*"(https:[^"]+)"$/\1/')"

[[ -n "$TAG" ]] || fail "couldn't find a tag_name in the GitHub API response."
[[ -n "$DMG_URL" ]] || fail "the latest release ($TAG) has no .dmg asset attached."

DMG_PATH="$WORKDIR/$(basename "$DMG_URL")"

step "Downloading $(basename "$DMG_URL") ($TAG)"
curl -fsSL "$DMG_URL" -o "$DMG_PATH" || fail "download failed."

step "Mounting the disk image"
mkdir -p "$MOUNT_DIR"
hdiutil attach "$DMG_PATH" -mountpoint "$MOUNT_DIR" -nobrowse -quiet || fail "hdiutil attach failed."
MOUNTED=1

APP_SRC="$MOUNT_DIR/$APP_NAME.app"
[[ -d "$APP_SRC" ]] || fail "$APP_NAME.app wasn't found inside the .dmg."

if [[ -d "$INSTALL_DIR/$APP_NAME.app" ]]; then
  step "Removing the existing $INSTALL_DIR/$APP_NAME.app"
  rm -rf "$INSTALL_DIR/$APP_NAME.app" || fail "couldn't remove the existing install (permissions on $INSTALL_DIR?)."
fi

step "Copying $APP_NAME.app to $INSTALL_DIR"
cp -R "$APP_SRC" "$INSTALL_DIR/" || fail "copy failed (permissions on $INSTALL_DIR? try: curl -fsSL <url> -o /tmp/twin-install.sh && sudo bash /tmp/twin-install.sh)."

step "Unmounting the disk image"
hdiutil detach "$MOUNT_DIR" -quiet || fail "hdiutil detach failed."
MOUNTED=0

step "Clearing the quarantine flag so Gatekeeper won't block the first launch"
xattr -cr "$INSTALL_DIR/$APP_NAME.app" || fail "xattr -cr failed."

echo
echo "Twin ($TAG) is installed at $INSTALL_DIR/$APP_NAME.app"
echo "Launch it from Spotlight (Cmd+Space, type Twin) or:"
echo "  open \"$INSTALL_DIR/$APP_NAME.app\""
