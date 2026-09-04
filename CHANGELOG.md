# Changelog

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
