#!/usr/bin/env bash
# Tags and publishes a GitHub Release for the .dmg scripts/build_dmg.sh built.
#
# This is a thin wrapper around `git tag` + `gh release create` that reads
# the version from VERSION, so the git tag, the release title, and the
# uploaded asset name can't drift from each other or from the .dmg
# build_dmg.sh actually produced. It does NOT build the .dmg itself — run
# scripts/build_dmg.sh first (ideally with CODESIGN_IDENTITY and
# NOTARY_PROFILE set, see docs/RELEASING.md).
#
# This pushes a tag and creates a public GitHub Release: it is a real,
# visible, hard-to-fully-undo action. Run it yourself when you're ready to
# publish; nothing in this repo invokes it for you.
#
# Usage:
#   scripts/release.sh
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="$(cat VERSION)"
TAG="v$VERSION"
DMG_PATH="dist/Twin-$VERSION.dmg"

if [[ ! -f "$DMG_PATH" ]]; then
  echo "error: $DMG_PATH not found - run scripts/build_dmg.sh first" >&2
  exit 1
fi

echo "==> Checking landing/index.html matches VERSION ($VERSION)"
./scripts/check_version_sync.sh

if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "error: tag $TAG already exists locally. Bump VERSION for a new release." >&2
  exit 1
fi

echo "==> This will tag $TAG, push it to origin, and create a public GitHub Release"
echo "    with $DMG_PATH attached. Press enter to continue, or Ctrl-C to stop."
read -r _

git tag "$TAG"
git push origin "$TAG"

gh release create "$TAG" "$DMG_PATH" \
  --title "Twin $VERSION" \
  --notes "See CHANGELOG or commit history for what's new in $VERSION."

echo "==> Published: https://github.com/woustachemax/twin/releases/tag/$TAG"
