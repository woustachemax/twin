# Releasing Twin

This covers signing `Twin.app` with a real Developer ID, notarizing the `.dmg`
`scripts/build_dmg.sh` produces, and what has to be set up by hand before any
of that can run. Nothing below can be done from an agent session — it needs
your Apple Developer account, and the signing/notary credentials are yours to
hold, not something to hand over or commit.

## What you need to do first (one-time, in the Apple Developer portal)

1. **Enroll in the Apple Developer Program** at https://developer.apple.com/programs/ (US $99/year) if you haven't already. A free Apple ID is not enough — Developer ID certificates and notarization both require a paid membership.
2. **Create a Developer ID Application certificate**: in Xcode go to Settings → Accounts → your team → Manage Certificates → + → "Developer ID Application", or generate it from https://developer.appstoreconnect.apple.com/certificates and let Keychain Access import it. This is the certificate that signs apps distributed *outside* the Mac App Store. Confirm it's in your login keychain with:
   ```bash
   security find-identity -v -p codesigning
   ```
   You want a line like `"Developer ID Application: Your Name (TEAMID)"` — that exact string is what `CODESIGN_IDENTITY` below should be set to.
3. **Create an app-specific password or API key for notarization** — either works with `notarytool`:
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

None of this can be scripted or faked from here — it requires your Apple ID, your payment for the developer program, and access to a device you're signed into to approve the certificate/API key creation.

## What `scripts/build_dmg.sh` does once you've set that up

The script reads two environment variables. Both are optional — omit them and you get the same ad-hoc-signed, unnotarized build as before, just packaged as a `.dmg`.

| Variable | Value | Effect |
|---|---|---|
| `CODESIGN_IDENTITY` | `"Developer ID Application: Your Name (TEAMID)"` (exact string from `security find-identity -v -p codesigning`) | Signs `Twin.app` and the `.dmg` with your real identity instead of ad-hoc (`-s -`). |
| `NOTARY_PROFILE` | The keychain profile name from `xcrun notarytool store-credentials` (e.g. `twin-notary`) | Submits the signed `.dmg` to Apple for notarization and staples the ticket once it comes back. |

Neither variable is a secret by itself — `CODESIGN_IDENTITY` is a public certificate name, and `NOTARY_PROFILE` is just a label pointing at a credential already stored in your keychain. The actual private key and password never pass through the script, an env file, or a commit.

Run it with both set:

```bash
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export NOTARY_PROFILE="twin-notary"
scripts/build_dmg.sh
```

Internally, for a release build, the script:

1. Builds `Twin.app` with py2app (same as the plain `Building the app` step).
2. Signs it with `codesign --force --deep --options runtime --timestamp --sign "$CODESIGN_IDENTITY"` — the hardened runtime flag is required for notarization to accept it.
3. Verifies the signature with `codesign --verify --deep --strict`.
4. Packages it into `dist/Twin-<version>.dmg` with `create-dmg` (or a plain `hdiutil` dmg if `create-dmg` isn't installed).
5. Signs the `.dmg` itself.
6. Submits it with `xcrun notarytool submit dist/Twin-<version>.dmg --keychain-profile "$NOTARY_PROFILE" --wait` and waits for Apple's response.
7. Staples the ticket with `xcrun stapler staple dist/Twin-<version>.dmg`, so the `.dmg` opens offline without Gatekeeper needing to phone home.

If notarization is rejected, `notarytool` prints a log URL; the most common causes are a missing hardened-runtime entitlement or an unsigned nested binary, both of which would show up in that log.

## Doing it by hand, one step at a time

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
  have to be edited by hand when you bump `VERSION` — `scripts/check_version_sync.sh`
  checks they match and fails loudly if you forget.

To cut a release:

1. Bump the version in `VERSION` (just that file — nothing else needs editing for the app itself).
2. Update `landing/index.html`'s download `href` and version text to match.
3. Run `./scripts/check_version_sync.sh` to confirm they agree.
4. Build: `scripts/build_dmg.sh` (with `CODESIGN_IDENTITY`/`NOTARY_PROFILE` set for a real release).
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
resolve from scratch — exactly the kind of thing a fresh, disk-constrained,
per-minute-billed macOS GitHub Actions runner handles worst, and unsigned CI
builds still don't solve the actual blocker, since notarization needs your
Apple Developer credentials either way. A local build, using your machine's
existing pip/Homebrew caches and your already-configured signing identity and
keychain profile, is the more maintainable default for a single-maintainer
project at this scale. If release volume grows enough to justify it, a
`macos-latest` workflow that imports a certificate from a base64-encoded
secret and calls `notarytool`/`stapler` with API-key secrets is a reasonable
next step — but that means putting your Developer ID private key into GitHub
Secrets, which is a real trust decision to make deliberately, not a default.
