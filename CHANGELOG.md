# Changelog

## 1.3.1 - 2026-10-08

- **Fixed entities staying "available" with stale values after a device disconnects**: indiserver
  keeps a disconnected driver's last known properties around and only flips its `CONNECTION.CONNECT`
  switch to `Off` - it doesn't remove the device. Every entity now also checks its device's
  `CONNECTION` property (falling back to "connected" for drivers that don't expose one) and
  re-renders as soon as it changes, instead of only reacting to its own next property update. The
  dedicated `switch.*_connected` entity is exempt, since it's how you reconnect the device. Fixes #8.
- **Large camera BLOB frames no longer block the event loop**: a `setBLOBVector` carrying a
  multi-megabyte FITS frame was XML-parsed and base64-decoded synchronously inside the asyncio read
  loop, stalling Home Assistant's event loop (and every other integration sharing it) for the
  duration. BLOB vectors are now parsed and decoded in a thread executor; only the (cheap) merging of
  the result into state and dispatching callbacks still happens on the event loop. Fixes #9.

## 1.3.0 - 2026-09-28

- **Camera preview support for 3-plane RGB cube FITS frames**: some drivers send already-debayered
  color frames as a FITS cube (`NAXIS=3`, `NAXIS3=3` - one plane each for R, G and B) rather than a
  plain 2-D image. Previously `decode_grayscale` rejected any `NAXIS != 2`, so these frames produced
  no camera preview at all. A new `decode_image()` handles both the existing 2-D grayscale case and
  this 3-plane RGB case, and the camera entity now renders an RGB JPEG preview for the latter. Fixes #7.

## 1.2.2 - 2026-09-26

- **Fixed server-level messages being silently dropped**: INDI `<message>` elements sent without a `device` attribute (server-wide messages from `indiserver` itself, not tied to any driver) were received and buffered internally but never surfaced anywhere in Home Assistant, because the message sensor factory bailed out on an empty device name. A dedicated "Last message" sensor is now created for these too, attached to the existing "INDI Server (host:port)" hub device. Fixes #6.

## 1.2.1 - 2026-09-25

- **Automatic reconnection**: if the TCP connection to `indiserver` drops (server restart, network blip, USB-triggered driver crash), `INDIClient` now keeps retrying with exponential backoff (5s up to 60s) and re-sends `getProperties` on success, instead of leaving the integration permanently unavailable until a manual reload. Fixes #5.

## 1.2.0 - 2026-09-04

Follow-up fixes to 1.1.0's camera support, from CodeRabbit review on #3:

- **Fixed a startup race** that could silently drop entities for any property (not just BLOB/camera) defined by a driver in the brief window between the client connecting and a platform finishing its setup. `INDIClient.connect()` now only opens the socket; a new `start()` begins reading and requests properties, called only after every platform has subscribed.
- **Stricter BLOB decoding**: line-wrapped base64 (indiserver's normal formatting) still decodes fine, but genuinely malformed data is now rejected instead of silently producing a corrupt image - and no longer fires a false "property updated" notification when that happens, so a camera keeps showing its last good frame instead.
- **FITS decoding now rejects `NAXIS` other than 2** (e.g. multi-plane cubes) instead of silently reading only the first plane.

## 1.1.0 - 2026-09-04

- Camera previews: a `camera` entity is now created for every INDI BLOB property (e.g. a CCD's
  `CCD1`). It automatically requests image data (`enableBLOB ... Also`) and decodes/stretches
  standard FITS frames into a JPEG preview (`numpy` + `Pillow`, now declared as requirements).
  Raw JPEG BLOBs are passed through unchanged. See the README for what this preview does and
  doesn't do (no debayering, no calibration - it's a quick look, not processed data).

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
