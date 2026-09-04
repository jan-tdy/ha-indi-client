# Changelog

## 1.0.0b1 - 2026-09-04

- Brand assets added (`custom_components/indi_client/brand/`) so the `hacs` CI validation passes.
- Releases are now fully automatic: pushing to `main` with a bumped `manifest.json` version creates the `vX.Y.Z` tag and GitHub Release by itself - no manual tagging.
- CI now requires every PR to bump `manifest.json`'s `version` compared to `main`, so a merge always ships a release.
- Added `CLAUDE.md` (contributor/agent guidance) and a README disclaimer: unaffiliated with the INDI Library project; noted as originating from the author's DevControl2 system.

## 1.0.0b0 - 2026-09-04

Initial beta release.

- Bidirectional INDI protocol client (Text/Number/Switch/Light vectors) connecting to `indiserver` as an additional client, alongside tools like CCDciel or KStars/EKOS.
- Automatic entity creation for every discovered device/property: `sensor`, `number`, `text`, `select`, `switch`, `binary_sensor`.
- Dedicated "Connected" switch per device (INDI `CONNECTION` property) and a diagnostic "Server connected" binary sensor for the `indiserver` link itself.
- Per-device log/message sensor with recent history in its attributes.
- `indi_client.refresh` and `indi_client.set_property` services for properties without a dedicated entity.
- Config flow with host/port setup, plus an options flow to change the connection later.
- Diagnostics download support (Settings -> Devices -> INDI Client -> Download diagnostics).

### Known limitations

- BLOB properties (images, previews) are not fetched yet.
- Multi-element number vectors (e.g. simultaneous RA/DEC slews) are exposed as independent `number` entities; some drivers expect all elements of such a vector together.
