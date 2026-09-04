# INDI Client for Home Assistant

[![HACS Custom][hacs-shield]][hacs-url]
[![Validate][validate-shield]][validate-url]
[![Tests][tests-shield]][tests-url]
[![License: MIT][license-shield]][license-url]

A [Home Assistant](https://www.home-assistant.io/) custom integration that connects to a running
[INDI](https://indilib.org) server (`indiserver`, default TCP port `7624`) **as an additional
client**, exactly the way [CCDciel](https://www.ap-i.net/ccdciel/en/start), KStars/EKOS or
`indi_getprop`/`indi_setprop` would. It does not take exclusive control of any device: it simply
reads whatever properties the server broadcasts (already reflecting changes made by other clients)
and, when a property allows it, can send its own commands to change it - bidirectionally.

> **Status:** early beta (`v1.0.0b0`). The protocol core and entity mapping are functional and
> unit tested, but this has not yet been run against every INDI driver in the wild. Feedback and
> bug reports are very welcome.

## Why

Typical astronomy setups already run `indiserver` with drivers for the mount, camera, focuser,
filter wheel, dome, weather station, etc., controlled from an astronomy application (CCDciel,
KStars/EKOS, ...). This integration lets Home Assistant sit alongside that application as a peer
client, so you can, for example:

- Park/unpark the mount from an HA automation (e.g. on rain or high wind).
- Read and set a CCD's target temperature, and watch the current sensor temperature.
- See device connection state and recent driver log messages inside HA.
- Trigger dome/roof or power-switch style properties from dashboards and automations.

## How it works

INDI is a push-based, XML-over-TCP protocol: the client sends `getProperties`, the server answers
with `def*Vector` elements describing each device's properties, and later pushes `set*Vector`
elements whenever a value changes (caused by *any* connected client, or by the driver itself). This
integration implements that protocol directly in Python (`custom_components/indi_client/indi/`,
stdlib-only, no `pyindi-client`/SWIG dependency), keeps a live model of every device/property, and
maps them onto Home Assistant entities automatically - there is no hardcoded list of "supported"
devices or drivers.

### Entity mapping

| INDI vector type | Permission | Rule | Home Assistant entity |
|---|---|---|---|
| Number | read-only | - | `sensor` (device_class `temperature`/`humidity` inferred by element name, e.g. `*TEMPERATURE`) |
| Number | writable | - | `number` |
| Text | read-only | - | `sensor` |
| Text | writable | - | `text` |
| Light | any | - | `sensor` (diagnostic; state is `Idle`/`Ok`/`Busy`/`Alert`) |
| Switch | writable | `OneOfMany` / `AtMostOne` | `select` |
| Switch | read-only | `OneOfMany` / `AtMostOne` | `sensor` (name of the active option) |
| Switch | writable | `AnyOfMany` | `switch` per element |
| Switch | read-only | `AnyOfMany` | `binary_sensor` per element |
| `CONNECTION` (standard property) | writable | - | `switch` named **Connected** (special-cased for a nicer toggle instead of a Connect/Disconnect dropdown) |
| device/server log messages | - | - | `sensor` **Last message** per device, with recent history in `history` attribute |
| server TCP link | - | - | diagnostic `binary_sensor` **Server connected** |

Each INDI device becomes its own Home Assistant device (grouping all of its entities), linked to a
parent "INDI Server (host:port)" hub device.

So, concretely:

- **Parking a mount** - the standard `TELESCOPE_PARK` switch vector (rule `OneOfMany`, options
  `PARK`/`UNPARK`) becomes a `select` entity; picking `Park` sends the park command.
- **Setting CCD temperature** - `CCD_TEMPERATURE` becomes a `number` entity; changing it sends a
  `newNumberVector`, and the driver's actual sensor reading (if exposed as a separate read-only
  element) is a `sensor`.
- **Checking logs** - every device gets a diagnostic **Last message** sensor showing the latest
  INDI log line, with the last 25 messages available as an attribute.
- **Connectivity** - the `switch.<device>_connected` entity reflects and controls the driver's
  `CONNECTION` property, and `binary_sensor.server_connected` reflects the TCP link to `indiserver`
  itself.

### Escape hatch: raw property access

Not every property needs a dedicated entity to be usable. Two services cover anything the
automatic mapping does not (yet) turn into a nice entity:

```yaml
# Re-request property definitions (e.g. after a driver was reloaded)
service: indi_client.refresh
data:
  config_entry_id: <config entry id>
  device: "Telescope Simulator"   # optional

# Send a raw new*Vector command
service: indi_client.set_property
data:
  config_entry_id: <config entry id>
  device: "Telescope Simulator"
  property: "TELESCOPE_PARK"
  type: "Switch"
  values:
    PARK: "On"
```

## Installation

### Via HACS (custom repository)

This integration is not yet in the default HACS store. Add it as a custom repository:

1. HACS -> Integrations -> the `⋮` menu (top right) -> **Custom repositories**.
2. URL: `https://github.com/jan-tdy/ha-indi-client`, category: **Integration**.
3. Install **INDI Client**, then restart Home Assistant.

### Manual

Copy `custom_components/indi_client` into your Home Assistant `config/custom_components/`
directory, then restart Home Assistant.

## Configuration

Everything is configured through the UI - no YAML required.

1. **Settings -> Devices & services -> Add integration -> INDI Client**.
2. Enter the host and port of your `indiserver` (default port `7624`).
3. Home Assistant validates the connection and creates one entry per server. Entities appear
   automatically as `indiserver` announces devices/properties - this can take a few seconds after
   setup, and again whenever a driver is (re)connected.

To change the host/port later, open the integration entry and use **Configure** (options flow).

## Example automations

Park the mount when it starts raining:

```yaml
automation:
  - alias: Park mount on rain
    trigger:
      - platform: state
        entity_id: binary_sensor.rain_sensor
        to: "on"
    action:
      - action: select.select_option
        target:
          entity_id: select.telescope_simulator_parking
        data:
          option: "Park"
```

Cool the CCD down before an imaging session:

```yaml
automation:
  - alias: Cool CCD before imaging
    trigger:
      - platform: time
        at: "21:30:00"
    action:
      - action: number.set_value
        target:
          entity_id: number.ccd_simulator_temperature
        data:
          value: -10
```

Notify when a device reports an Alert state:

```yaml
automation:
  - alias: INDI device alert
    trigger:
      - platform: state
        entity_id: sensor.telescope_simulator_last_message
    condition:
      - condition: template
        value_template: "{{ 'Alert' in trigger.to_state.attributes.get('history', [''])[-1] }}"
    action:
      - action: notify.mobile_app
        data:
          message: "{{ trigger.to_state.state }}"
```

## Known limitations

- **No BLOB/image support yet.** This client never sends `enableBLOB`, so image/preview data is
  not fetched (and never reaches this client, avoiding the overhead of parsing large base64
  payloads). Planned for a future release.
- **Multi-element number vectors** (e.g. `EQUATORIAL_EOD_COORD` with RA + DEC for a GOTO) are
  exposed as independent `number` entities. Setting one sends only that element; most drivers keep
  the other element's last known value, but this is driver-dependent. For coordinated multi-element
  commands, prefer the `indi_client.set_property` service, or set both entities and use an
  automation with a short delay.
- Number units are inferred heuristically from the element name (e.g. `*TEMPERATURE` -> °C); INDI
  itself does not transmit explicit units.
- Tested primarily against INDI's own driver simulators; real-hardware driver quirks may surface
  edge cases - please open an issue if you hit one.

## Development

The `custom_components/indi_client/indi/` package (protocol parsing, wire formatting, the asyncio
client) is pure Python with no Home Assistant dependency, so it can be unit tested in isolation:

```bash
pip install -r requirements_test.txt
pytest
```

### Releasing

HACS tracks GitHub Releases, not `CHANGELOG.md` directly. To cut a release:

1. Bump `version` in `custom_components/indi_client/manifest.json` (and add an entry to
   `CHANGELOG.md`).
2. Tag the commit `vX.Y.Z` (matching the manifest version) and push the tag, e.g.
   `git tag v1.0.0b0 && git push origin v1.0.0b0`.
3. [`.github/workflows/release.yml`](.github/workflows/release.yml) then verifies the tag matches
   `manifest.json` and publishes a GitHub Release with auto-generated notes - that release is what
   HACS shows to users when an update is available.

## License

[MIT](LICENSE) - permissive and compatible with HACS; contributions and forks are welcome.

[hacs-shield]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
[validate-shield]: https://github.com/jan-tdy/ha-indi-client/actions/workflows/validate.yml/badge.svg
[validate-url]: https://github.com/jan-tdy/ha-indi-client/actions/workflows/validate.yml
[tests-shield]: https://github.com/jan-tdy/ha-indi-client/actions/workflows/test.yml/badge.svg
[tests-url]: https://github.com/jan-tdy/ha-indi-client/actions/workflows/test.yml
[license-shield]: https://img.shields.io/badge/license-MIT-blue.svg
[license-url]: LICENSE
