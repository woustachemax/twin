#!/usr/bin/env bash
# Fails if landing/index.html's download link or version text doesn't match
# the VERSION file. VERSION is the single source of truth for the app's
# version: setup.py reads it into the .app bundle, scripts/build_dmg.sh reads
# it for the .dmg filename, and this check keeps the landing page's release
# link and displayed version string from silently drifting away from it.
#
# Run before tagging a release (scripts/release.sh runs this automatically).
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="$(cat VERSION)"
LANDING="landing/index.html"
DMG_NAME="Twin-$VERSION.dmg"
EXPECTED_URL="https://github.com/woustachemax/twin/releases/download/v$VERSION/$DMG_NAME"

fail=0

if ! grep -qF "$EXPECTED_URL" "$LANDING"; then
  echo "error: $LANDING does not link to $EXPECTED_URL (VERSION says $VERSION)" >&2
  fail=1
fi

if ! grep -q "v$VERSION " "$LANDING"; then
  echo "error: $LANDING's displayed version text doesn't mention v$VERSION" >&2
  fail=1
fi

if [[ "$fail" -eq 0 ]]; then
  echo "ok: landing/index.html matches VERSION ($VERSION)"
else
  echo "update landing/index.html's download link and version text, then re-run this check" >&2
  exit 1
fi
