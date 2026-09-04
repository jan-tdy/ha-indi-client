# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## What this repo is

`ha-indi-client` is a Home Assistant custom integration (HACS category:
integration) that speaks the [INDI](https://indilib.org) protocol
(`indi_client` domain, `custom_components/indi_client/`). It connects to a
running `indiserver` as an additional client - the same way CCDciel or
KStars/EKOS would - and mirrors devices/properties as HA entities,
bidirectionally.

It is an independent, unofficial client: **not affiliated with, endorsed
by, or sponsored by the INDI Library project (indilib.org)**. "INDI" is
used only to describe protocol compatibility. Keep that framing intact in
the README/docs when editing them - don't imply endorsement.

It is one component of Ján's (JapySoft) private **DevControl2** system for
the Bombol.Space telescope hosting facility, but this integration itself
is generic - it makes no assumption about any specific installation and
works with any INDI driver/device.

## Releasing (do this automatically, don't ask)

HACS reads GitHub Releases, not `CHANGELOG.md`. Releases are fully
automatic via `.github/workflows/release.yml`: on every push to `main`,
it reads `version` from `custom_components/indi_client/manifest.json`;
if a `vX.Y.Z` tag/release for that version doesn't exist yet, the
workflow creates the tag and the GitHub Release itself (auto-generated
notes). **No manual `git tag`/`git push` step is needed or wanted.**

This is **CI-enforced**: `.github/workflows/require-version-bump.yml` fails
any PR into `main` whose `manifest.json` `version` is unchanged from
`main`. So every PR, however small, must:

1. Bump `version` in `custom_components/indi_client/manifest.json`, following
   semver: a real user-facing feature (a new platform/entity type, a new
   capability) bumps minor (`1.1.0` -> `1.2.0`); a fix or small tweak
   bumps patch (`1.1.0` -> `1.1.1`). The `1.0.0b0`/`b1`/`b2` prereleases
   were the initial bring-up; `1.1.0` (camera support) moved to plain
   semver and there's no reason to go back to beta suffixes.
2. Add a short entry to `CHANGELOG.md` describing the change (kept as
   human-readable history in the repo; not what HACS shows, but still
   maintained).
3. Get that change merged to `main` (PR + merge, per the repo's normal
   flow). The release workflow takes it from there - do not manually
   create or push tags.

## Development

The `custom_components/indi_client/indi/` package (protocol parsing,
wire formatting, the asyncio client) is pure Python, no Home Assistant
dependency:

```bash
pip install -r requirements_test.txt
pytest
```

CI also runs `hassfest` and the `hacs/action` validator (see
`.github/workflows/validate.yml`) - both must pass on every PR.
