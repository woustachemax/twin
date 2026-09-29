# Releasing Twin

Twin ships **ad-hoc signed** (`codesign -s -`), not signed with a paid Developer
ID or notarized by Apple. That's a deliberate call, not a gap to fill later:
Developer ID enrollment costs US $99/year, and that isn't worth it for a
portfolio project without real public download volume. This covers building
and shipping the ad-hoc signed `.dmg` as-is, and what to tell people about
opening it. Real Developer ID signing and notarization are covered at the
bottom, as an optional upgrade if that ever changes.

## Opening an ad-hoc signed build (Gatekeeper)

An ad-hoc signature satisfies `codesign` but not Gatekeeper's "identified
developer" check, so the *first* launch after downloading needs one extra
step, and after that it opens normally like any other app. Three ways to do it:

- **`scripts/install.sh`** (recommended): downloads the latest release's
  `.dmg` via `curl`, not a browser, so it never picks up the quarantine
  attribute in the first place, and it also runs `xattr -cr` on the installed
  app as a belt-and-suspenders step. No Gatekeeper block, no manual steps.
  See the one-liner in the README's [Installing a release
  build](../README.md#installing-a-release-build) section.
- **System Settings** (manual `.dmg` download, current macOS): double-clicking
  a freshly-downloaded `Twin.app` shows "Twin can't be opened because Apple
  cannot check it for malicious software," with no bypass in that dialog
  itself. Open **System Settings → Privacy & Security**, scroll to the
  Security section, and click **Open Anyway** next to the blocked-app notice,
  then confirm **Open** in the dialog that follows. (Right-click → Open used
  to surface its own bypass dialog directly; recent macOS versions route it
  through System Settings instead.)
- **Terminal**: `xattr -cr /Applications/Twin.app` strips the quarantine
  attribute a browser download adds, which has the same effect without
  touching System Settings.

This is the instruction to hand anyone downloading a built `.dmg`. It's also
in the README and on the landing page, so all three should stay in sync if
this ever changes.

## Building and shipping the .dmg

```bash
brew install create-dmg   # optional; falls back to a plain hdiutil .dmg without it
scripts/build_dmg.sh
```

With no environment variables set (the normal case), this builds `Twin.app`
with py2app, ad-hoc signs it (`codesign -s -`), and packages it into
`dist/Twin-<version>.dmg`. That's the build that ships. Point people at the
[opening instructions](#opening-an-ad-hoc-signed-build-gatekeeper) above; don't
treat the lack of a paid signature as something to apologize for or fix later.

## Versioning

`VERSION` (a single line at the repo root, e.g. `0.1.0`) is the one place the
app's version lives. Everything else reads it instead of hardcoding a number:

- `setup.py` reads it for `CFBundleShortVersionString`/`CFBundleVersion`.
- `scripts/build_dmg.sh` reads it for the `.dmg` filename (`dist/Twin-<version>.dmg`).
- `scripts/release.sh` reads it for the git tag (`v<version>`) and the release title.
- `landing/index.html`'s download button is a direct link to that tag's asset:
  `https://github.com/woustachemax/twin/releases/download/v<version>/Twin-<version>.dmg`.
  Since the landing page is a single static HTML file with no build/templating
  step, that link and the version text next to it (`v<version> · macOS...`)
  have to be edited by hand when you bump `VERSION`. `scripts/check_version_sync.sh`
  checks they match and fails loudly if you forget.

To cut a release:

1. Bump the version in `VERSION` (just that file, nothing else needs editing for the app itself).
2. Update `landing/index.html`'s download `href` and version text to match.
3. Run `./scripts/check_version_sync.sh` to confirm they agree.
4. Build: `scripts/build_dmg.sh`.
5. Publish: `scripts/release.sh`, which re-runs the version check, then tags, pushes the tag, and runs `gh release create` to upload `dist/Twin-<version>.dmg` as a release asset. It asks for confirmation before it pushes anything, since tagging and publishing a release are visible, hard-to-fully-undo actions.
6. Deploy the landing page (`cd landing && vercel --prod` or however you currently deploy it) so the updated download link goes live.

If you'd rather do the release step by hand instead of `scripts/release.sh`:

```bash
git tag v0.1.0
git push origin v0.1.0
gh release create v0.1.0 dist/Twin-0.1.0.dmg --title "Twin 0.1.0" --notes "..."
```

### Why this is a local script and not a GitHub Actions workflow

`setup.py` bundles `torch` and `transformers` for the Donut OCR model
(`document_ingest.py`), which makes the py2app build large and slow to
resolve from scratch, exactly the kind of thing a fresh, disk-constrained,
per-minute-billed macOS GitHub Actions runner handles worst. A local build,
using your machine's existing pip/Homebrew caches, is the more maintainable
default for a single-maintainer project at this scale, ad-hoc signed or not.
If release volume ever grows enough to justify it, a `macos-latest` workflow
is a reasonable next step; see the notarization section below for what that
would additionally need if you also want CI to notarize.

## Signing with a real Developer ID and notarizing (optional, if you want this later)

Everything above is enough to ship. This section only matters if you decide
the $99/year Apple Developer Program membership is worth it later, for
example if Twin gets real public download volume and the manual-download
Gatekeeper block becomes enough friction to lose people. Nothing here can be
done from an agent session: it needs your Apple Developer account, and the
signing/notary credentials are yours to hold, not something to hand over or
commit.

### One-time setup, in the Apple Developer portal

1. **Enroll in the Apple Developer Program** at https://developer.apple.com/programs/ (US $99/year). A free Apple ID is not enough: Developer ID certificates and notarization both require a paid membership.
2. **Create a Developer ID Application certificate**: in Xcode go to Settings → Accounts → your team → Manage Certificates → + → "Developer ID Application", or generate it from https://developer.appstoreconnect.apple.com/certificates and let Keychain Access import it. This is the certificate that signs apps distributed *outside* the Mac App Store. Confirm it's in your login keychain with:
   ```bash
   security find-identity -v -p codesigning
   ```
   You want a line like `"Developer ID Application: Your Name (TEAMID)"`; that exact string is what `CODESIGN_IDENTITY` below should be set to.
3. **Create an app-specific password or API key for notarization**: either works with `notarytool`:
   - App-specific password: generate one at https://appleid.apple.com/account/manage under Sign-In and Security → App-Specific Passwords.
   - Or an App Store Connect API key: Users and Access → Integrations → App Store Connect API in App Store Connect, download the `.p8` key.
4. **Store those notarization credentials in a keychain profile** (this is a one-time local step, and it's what keeps the actual secret out of the repo and out of shell history):
   ```bash
   xcrun notarytool store-credentials "twin-notary" \
     --apple-id "you@example.com" \
     --team-id "TEAMID" \
     --password "the-app-specific-password"
   ```
   This writes the credential into your login keychain under the profile name `twin-notary` (call it whatever you like) and prints nothing sensitive back out. From then on, `notarytool` and `stapler` are invoked with `--keychain-profile twin-notary`, never a raw password.

None of this can be scripted or faked from here: it requires your Apple ID, your payment for the developer program, and access to a device you're signed into to approve the certificate/API key creation.

### What `scripts/build_dmg.sh` does once you've set that up

The script reads two environment variables. Both are optional: omit them and you get the same ad-hoc-signed, unnotarized build described at the top of this file.

| Variable | Value | Effect |
|---|---|---|
| `CODESIGN_IDENTITY` | `"Developer ID Application: Your Name (TEAMID)"` (exact string from `security find-identity -v -p codesigning`) | Signs `Twin.app` and the `.dmg` with your real identity instead of ad-hoc (`-s -`). |
| `NOTARY_PROFILE` | The keychain profile name from `xcrun notarytool store-credentials` (e.g. `twin-notary`) | Submits the signed `.dmg` to Apple for notarization and staples the ticket once it comes back. |

Neither variable is a secret by itself: `CODESIGN_IDENTITY` is a public certificate name, and `NOTARY_PROFILE` is just a label pointing at a credential already stored in your keychain. The actual private key and password never pass through the script, an env file, or a commit.

Run it with both set:

```bash
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export NOTARY_PROFILE="twin-notary"
scripts/build_dmg.sh
```

Internally, with both set, the script additionally:

1. Signs `Twin.app` with `codesign --force --deep --options runtime --timestamp --sign "$CODESIGN_IDENTITY"`: the hardened runtime flag is required for notarization to accept it.
2. Verifies the signature with `codesign --verify --deep --strict`.
3. Signs the `.dmg` itself.
4. Submits it with `xcrun notarytool submit dist/Twin-<version>.dmg --keychain-profile "$NOTARY_PROFILE" --wait` and waits for Apple's response.
5. Staples the ticket with `xcrun stapler staple dist/Twin-<version>.dmg`, so the `.dmg` opens offline without Gatekeeper needing to phone home, and needs none of the right-click/`xattr` workaround anymore.

If notarization is rejected, `notarytool` prints a log URL; the most common causes are a missing hardened-runtime entitlement or an unsigned nested binary, both of which would show up in that log.

### Doing it by hand, one step at a time

If you'd rather run each command yourself instead of through the script:

```bash
# 1. Build
rm -rf build dist && python3 setup.py py2app

# 2. Sign with your real identity
codesign --force --deep --options runtime --timestamp \
  --sign "Developer ID Application: Your Name (TEAMID)" dist/Twin.app
codesign --verify --deep --strict --verbose=2 dist/Twin.app

# 3. Build the dmg (see scripts/build_dmg.sh for the create-dmg flags)
# ... or just run scripts/build_dmg.sh with CODESIGN_IDENTITY unset and sign the dmg after:
codesign --force --sign "Developer ID Application: Your Name (TEAMID)" dist/Twin-0.1.0.dmg

# 4. Notarize
xcrun notarytool submit dist/Twin-0.1.0.dmg --keychain-profile "twin-notary" --wait

# 5. Staple
xcrun stapler staple dist/Twin-0.1.0.dmg

# 6. Confirm Gatekeeper accepts it
spctl -a -t open --context context:primary-signature -v dist/Twin-0.1.0.dmg
```
