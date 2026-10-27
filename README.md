# WiseMirror for Home Assistant

<img src="custom_components/wisemirror/brand/icon.png" alt="WiseMirror icon" width="96" align="right">

Local control of bathroom mirrors with a built-in LED weather station that are
set up with the **WiseMirror** app (Adsmart Baolun; sold under brands such as
**Alasta**, **BYECOLD** and **GS Mirror**). Home Assistant talks to each
mirror directly: over UDP on your LAN, and over Bluetooth on the 2M09. No cloud
account is needed.

## Features

- **Automatic discovery:**
  - Bluetooth discovery of the 2M09.
  - DHCP discovery.
  - A background broadcast every 15 minutes that offers new mirrors.
  - Adding by IP address, including mirrors that only answer on the control port.
- **Wi-Fi setup from Home Assistant:** over Bluetooth in the config flow
  (2M09), or through the mirror's access point with an action.
- **Sensors:**
  - Indoor temperature and humidity.
  - Firmware version, weather location, connectivity, connection path, IP address.
  - The five alarms (read-only).
- **Controls:**
  - Display and sleep brightness: raw 0–100 numbers, plus Low/Medium/High
    presets.
  - Sleep (night) mode with start and end times.
  - 12/24-hour clock, day-month order, temperature unit, weekday language.
  - Two-day and today's weather, weather source.
  - Mirror name and weather location.
- **Location:** a one-press "Use home location" button, an action, and an
  option to follow Home Assistant's home location automatically.
- **Resilience:**
  - Rediscovers a mirror by MAC address after an IP change.
  - Falls back to the discovery query when the mirror does not answer the
    heartbeat.
  - Falls back to Bluetooth (2M09) when Wi-Fi is down.
- **Advanced:** raw command action, LAN firmware update, diagnostics with
  sensitive data redacted, and a standalone command-line tool.
- **Translations:** English, German and Spanish.

## Compatibility

| Model (as reported by the mirror) | Status |
| --- | --- |
| `2M09`, firmware `V1.9.250215` (Alasta) | Protocol confirmed on hardware by [jorgediez/ha-wisemirror](https://github.com/jorgediez/ha-wisemirror): discovery, settings, weather and all writes |
| `8J11`, `8J12`, `2K02`, other `2M09` firmware | Should work; not tested |

The protocol is documented in [docs/PROTOCOL.md](docs/PROTOCOL.md). Parts not
yet confirmed on hardware: the heartbeat, Bluetooth control, AP-mode setup and the
firmware update. Please
[report how your mirror behaves](https://github.com/tomquist/ha-wisemirror/issues/new?template=device_report.yml).

Hardware-dependent behaviour:
- **Humidity:** models that report no humidity reading get a disabled humidity
  entity (you can still enable it).
- **Key tone:** the switch is disabled by default, since not every model has
  buttons.

## Prerequisites

- **Network:** the mirror is on a 2.4 GHz network that Home Assistant can
  reach. The integration can do the Wi-Fi setup itself (see below), or you can
  use the WiseMirror app once.
- **Same segment:** for the broadcast discovery, Home Assistant and the mirror
  must be on the same L2 segment/VLAN. Control uses UDP 8001 on the mirror;
  discovery uses UDP 8000.
- **DHCP reservation:** recommended. If the IP changes anyway, the
  integration finds the mirror again.
- **Home Assistant 2025.1 or newer.** CI tests the oldest and the current
  release. The integration icon shows from 2026.3 onwards.

## Installation

### HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tomquist&repository=ha-wisemirror&category=integration)

Or open **HACS → ⋮ → Custom repositories**, add
`https://github.com/tomquist/ha-wisemirror` as an **Integration**, install
**WiseMirror** and restart Home Assistant.

### Manual

Copy `custom_components/wisemirror` into `<config>/custom_components/` and
restart Home Assistant.

## Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=wisemirror)

Mirrors usually show up under **Settings → Devices & services → Discovered**:

- **Bluetooth:** a 2M09 is found by its advertisement. If it is not on Wi-Fi
  yet, the flow asks for your network, sends the credentials over Bluetooth,
  waits for the mirror to join, and then sends its name and your home location.
- **DHCP:** when a device with an Espressif hostname (`ESP_…`, `espressif…`)
  or the 30:30:F9 MAC prefix requests an address, the integration sends it the
  discovery query and ignores it if no mirror answers. Which hostname mirrors
  use is not yet confirmed.
- **Background broadcast:** while at least one mirror is configured, the
  integration repeats the discovery broadcast every 15 minutes. It offers any
  new mirror it finds and updates the addresses of known ones.

To add a mirror by hand, use **Add integration → WiseMirror**. Mirrors that
answer the broadcast are listed; otherwise, or if you choose **Enter IP address
manually**, enter the address. A mirror that ignores the discovery query but
answers a settings read on port 8001 is added keyed by its IP address.

**Change IP address:** open the mirror's entry and choose **⋮ → Reconfigure**.
An address that answers with a different mirror's MAC is refused.

### Options

| Option | Default | Description |
| --- | --- | --- |
| Poll interval | 30 s | Temperature, humidity and reachability, 5–3600 s. |
| Settings refresh interval | 300 s | Picks up changes made in the app or on the mirror, 30–3600 s. Settings are also re-read after every change. |
| Use these intervals for all mirrors | off | Copies both intervals to every configured mirror. |
| Follow Home Assistant's home location | off | Keeps the weather location in sync with **Settings → System → General**. While it is off, the integration never changes the location on its own. |

## Entities

| Platform | Entity | Notes |
| --- | --- | --- |
| Sensor | Temperature | In the mirror's unit; Home Assistant converts it. |
| Sensor | Humidity | Disabled by default on models without a sensor. |
| Binary sensor | Connectivity | Stays available while the mirror is offline, so automations can see "off". Diagnostic. |
| Sensor | Firmware, Connection (Wi-Fi/Bluetooth), Weather location | Diagnostic. |
| Sensor | IP address | Diagnostic, disabled by default. |
| Sensor | Alarm 1–5, Brightness duration, Time/date mode | Read-only values; disabled by default. |
| Binary sensor | Always bright | Read-only flag; disabled by default. |
| Number | Display brightness, Sleep brightness | Raw level 0–100. |
| Select | Display brightness preset, Sleep brightness preset | Low/Medium/High. |
| Switch | Sleep mode, Two-day weather | |
| Switch | 24-hour clock, Day before month | Configuration. |
| Switch | Key tone | Configuration, disabled by default. |
| Switch | Today's weather | Configuration, disabled by default. |
| Select | Temperature unit, Weekday language, Weather source | Weather-source options depend on model and firmware. |
| Time | Sleep start, Sleep end | |
| Text | Mirror name, Weather location string | The location as raw `city(lat,lon)(key)`. |
| Button | Use home location | Sends the home zone's name and coordinates. |

All entities except Connectivity become unavailable after 4 missed polls. The mirror confirms every change; if it doesn't, you get an error
in the UI.

## Actions

All actions take Home Assistant targets: mirror devices, their entities, or
areas.

### `wisemirror.set_location`

Sets the weather location on one or more mirrors. Any field you leave out
(`city`/`name`, `latitude`, `longitude`) is taken from the home zone.

```yaml
action: wisemirror.set_location
target:
  device_id: <your mirror>
data:
  name: Madrid
  latitude: 40.4168
  longitude: -3.7038
```

### `wisemirror.send_command`

Sends any `A5` frame to one mirror and returns the request and reply as hex.

```yaml
action: wisemirror.send_command
target:
  device_id: <your mirror>
data:
  command: 6
  payload: "40"
response_variable: result
```

### `wisemirror.update_firmware`

Serves an image over HTTP (port 5000, or another free port), sends `0x13` to
TCP 8408 and reports the `0x14` progress frames as `wisemirror_firmware_progress`
events. The file must be inside `allowlist_external_dirs`. The integration does
not ship firmware, and the mirror performs no signature check: use only an
image made for your model.

### `wisemirror.provision_wifi`

Sends Wi-Fi credentials, either with `method: access_point` (TCP
`192.168.4.1:8408`, while Home Assistant is on the mirror's `WiseMirror-XXXX`
network) or with `method: bluetooth` (needs `ble_address`).

## Tips

### Night mode or automations

The mirror's own night mode (Sleep mode, Sleep start/end, Sleep brightness)
runs on the mirror with a single fixed window. For more flexibility, turn it
off and drive **Display brightness** from automations instead, based on the
sun, presence, weekdays and so on. Don't use both at once, or two schedulers
will fight over the brightness.

```yaml
automation:
  - alias: Dim the mirror at night
    triggers:
      - trigger: sun
        event: sunset
    actions:
      - action: number.set_value
        target:
          entity_id: number.bathroom_mirror_display_brightness
        data:
          value: 5
```

## Troubleshooting

- **Mirror not discovered:**
  - Check that Home Assistant and the mirror share a VLAN/subnet and that UDP
    broadcasts are not filtered (guest and mesh networks often isolate
    clients).
  - Add it by IP instead.
- **Probing a mirror:** from any computer on the same network:

  ```bash
  python3 scripts/wisemirror.py discover
  python3 scripts/wisemirror.py status <mirror-ip>
  ```

  `status` shows whether the mirror answers the heartbeat, settings, weather
  and discovery queries.
- **Debug logging:**

  ```yaml
  logger:
    logs:
      custom_components.wisemirror: debug
  ```

- **Diagnostics:** Settings → Devices & services → WiseMirror → ⋮ → *Download
  diagnostics* (IP, MAC and location are redacted). Please attach it to bug
  reports.

## Removal

1. **Settings → Devices & services → WiseMirror**, then **⋮ → Delete** on each
   mirror.
2. Remove **WiseMirror** in HACS, or delete
   `<config>/custom_components/wisemirror`.
3. Restart Home Assistant.

The mirrors keep their settings, and the WiseMirror app can manage them as before.

## Command-line tool

`scripts/wisemirror.py` uses the integration's protocol code and needs only
Python 3.12+:

```text
discover [--host IP] [--query temperature|old|new]
status IP                       heartbeat, settings, weather and discovery
set IP SETTING VALUE…           key-tone, 24h, day-first, celsius, brightness,
                                sleep, sleep-window, sleep-brightness, name,
                                location, weather, chinese-weekday
raw IP CODE [HEX]               any command
provision SSID PASSWORD         AP-mode Wi-Fi setup
firmware IP FILE [--model M]    LAN firmware update (needs aiohttp)
```

## Security

The mirror has no authentication: anyone on the same network can control it
over UDP 8001 and TCP 8408. Wi-Fi credentials travel in clear text on every
setup path, and firmware updates are not signed. See [SECURITY.md](SECURITY.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Device reports for other models and
firmware are especially welcome.

## Acknowledgements

Real-device findings (the 2M09 discovery reply, °C and weather-source values,
0–100 brightness, humidity 0 on models without a sensor) come from Jorge Diez's
[jorgediez/ha-wisemirror](https://github.com/jorgediez/ha-wisemirror).

## License

[Apache License 2.0](LICENSE). Copyright 2026 Tom Quist.

## Disclaimer

This is an unofficial community project, not affiliated with or endorsed by
Adsmart Baolun, the makers of the WiseMirror app, or any mirror brand.
