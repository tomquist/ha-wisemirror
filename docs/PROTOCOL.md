# WiseMirror protocol

Reference for the local protocol of WiseMirror mirrors, as implemented in
[`custom_components/wisemirror/protocol.py`](../custom_components/wisemirror/protocol.py).
Facts confirmed on a real mirror are marked **(hardware)**: an Alasta 2M09
with firmware V1.9.250215, as reported by
[jorgediez/ha-wisemirror](https://github.com/jorgediez/ha-wisemirror).
The rest has not yet been confirmed on a device.

| Model | Bluetooth |
| --- | --- |
| 2M09 | yes |
| 8J12 | no |
| 2K02 | no |
| 8J11 | no |

Carriers:

| Port | Transport | Use |
| --- | --- | --- |
| UDP 8000 | broadcast or unicast | discovery query, ASCII reply |
| UDP 8001 | unicast | `A5` command frames and replies |
| TCP 8408 | mirror listens | AP-mode Wi-Fi setup (`0x15`), firmware update (`0x13`/`0x14`) |
| GATT `0xAE00` | BLE (2M09) | chunked `A5` frames, status read, Wi-Fi setup |
| UDP 7001 / 18266 / TCP 18267 | — | EspTouch (SmartConfig) setup; not implemented |

There is no authentication and no encryption on any carrier.

## Frame

All commands, and most replies, on every carrier are one frame of `N + 8` bytes:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 1 | `0xA5` |
| 1 | 1 | command code (replies echo it) |
| 2 | 2 | payload length `N`, big-endian |
| 4 | 1 | sub-field, always `0x00` |
| 5 | 1 | status: `0x01` in requests; `0x01` in a reply means accepted |
| 6 | N | payload |
| 6+N | 1 | checksum: sum of bytes 1 … 5+N, mod 256 |
| 7+N | 1 | `0x5A` |

Example, brightness 50: `A5 06 00 01 00 01 32 3A 5A`. The test suite checks
a set of reference frames byte for byte (`tests/test_protocol.py`).

## Discovery (UDP 8000)

The client sends one ASCII string, by broadcast to `255.255.255.255:8000` or
unicast to a known address, from source port 4026 (4025 for the new-device
query).

| Query | Reply |
| --- | --- |
| `What is the temperature of LEDWifi` | `<word> <model>+<temp>+<unit>+<version>+<mac>+<ip>,<humidity>` |
| `Are you a old device that link to LEDWiFi` | `<word> <model>+1+<mac>+<ip>` |
| `Are you a new device that link to LEDWiFi` | `<word> <model>+0+<mac>+<ip>` |

Real reply **(hardware)**:
`I'm 2M09+25+1+V1.9.250215+30:30:f9:f3:ce:b8+192.168.1.244,0`

- The model must contain `8J11`, `8J12`, `2K02` or `2M09`.
- Unit `1` means °C **(hardware)**.
- Humidity `0` means the model has no humidity sensor **(hardware)**.
- A reply that reports IP `0.0.0.0` is taken from the sender's address
  instead.

The temperature query answers reliably on hardware. The integration falls back
to it when a mirror does not answer the heartbeat.

## Control (UDP 8001)

Each request goes from a fresh socket and waits 800 ms for the reply,
retrying every second, 3 tries in total. A reply is success when byte 1 is the
request's code and byte 5 is `0x01` **(hardware)**. Only one request is in
flight per mirror.

| Code | Function | Payload |
| --- | --- | --- |
| `0x01` | key tone | `[0\|1]` |
| `0x02` | 24-hour clock | `[0\|1]` |
| `0x03` | day before month | `[0\|1]` |
| `0x04` | temperature unit, 1 = °C **(hardware)** | `[0\|1]` |
| `0x06` | display brightness; presets 30/50/86, hardware takes 0–100 **(hardware)** | `[level]` |
| `0x0B` | weather location | 2-byte BE length + `<url-encoded city>(<lat>,<lon>)(<AccuWeather key>)` |
| `0x0C` | sleep (night) mode | `[0\|1]` |
| `0x0D` | sleep window, 24 h | `[startH, startM, endH, endM]` |
| `0x0E` | sleep brightness; presets 10/20/30 (UDP only) | `[level]` |
| `0x0F` | device name; reply bytes 6–9 = firmware | 1-byte length + UTF-8 |
| `0x10` | read settings | `[00]` |
| `0x12` | heartbeat | `[00]` |
| `0x16` | read weather settings | `[00]` |
| `0x30` | weather display | `[flags, source, 00, 00]` |
| `0x31` | weekday language, 1 = Chinese | `[0\|1]` |
| `0x05`, `0x07`–`0x09`, `0x11` | meaning unknown | — |

Weather source byte **(hardware)**: 255 Auto, 1 NO.1, 2 NO.2, 3 NO.3, 0 NO.4,
4 NO.5. Auto and NO.4 are only available on newer firmware (8J11 above 25, 8J12
above 43, 2K02 above 22, every 2M09), and NO.5 only on the 2M09. Weather flags:
bit 0 two-day weather **(hardware)**, bit 1 today's weather.

### Heartbeat reply (`0x12`)

| Offset | Meaning |
| --- | --- |
| 6 | temperature, signed |
| 7 | unit, 1 = °C |
| 8 | firmware version, BCD (`0x46` = 4.6) |
| 9–11 | build date YY MM DD, binary (`17 06 01` = 2023-06-01) |
| 12 | humidity % (0 = no sensor) |
| 13… | optional JSON: `mainDeviceName`, `hum`, `temp`, `unit` |

Whether the 2M09 answers the heartbeat is not confirmed.

### Settings block (reply to `0x10`, ≥ 38 bytes)

| Offset | Meaning |
| --- | --- |
| 6 | flags: bit 0 key tone, 1 24-hour, 2 day first, 3 °C, 4 always bright, 5 Chinese weekday |
| 7 | brightness |
| 8 | brightness duration (no setter) |
| 9 | time/date mode (no setter) |
| 10–29 | five alarms × [on, hour, minute, weekday mask] (read-only) |
| 30–35 | sleep on, start H, start M, end H, end M, sleep brightness |
| 36–37 | location length, BE |
| 38… | location string |

Bytes 6, 7 and 30 onwards, including the location, are confirmed on hardware **(hardware)**.

### Weather block (reply to `0x16`)

Byte 6 flags, 7 source **(hardware)**, 8–9 unknown, 10–11 location length, 12… location.

## Bluetooth (2M09)

Advertised names: `WiseMirror_BLE…` or `WISE`. The manufacturer data after the
company ID is: model code (9 = 2M09), flags (bit 0 provisioned, bit 1 °C),
temperature (s16 BE), humidity, and the 6-byte Wi-Fi MAC.

Messages are written to `0xAE01` in chunks of
`[msg id][total length][offset][≤17 data bytes]`. The msg id is random, between
1 and 254, never `0x5A`. Each chunk is acknowledged on `0xAE02` by a
notification that echoes the msg id and offset; a notification of 2 bytes or
less signals an error. Unacknowledged chunks are resent every 5 s, up to 3
times. Reading `0xAE01` returns the settings block with 4 weather bytes
inserted at offset 36. Wi-Fi credentials go as bare JSON
`{"pwd":"…","ssid":"…"}`, chunked the same way.

## TCP 8408

- **AP-mode setup:** connect to `192.168.4.1:8408` while joined to the mirror's
  `WiseMirror-XXXX` network, send `0x15` with the JSON credentials, and wait
  for a frame with code `0x15`.
- **Firmware update:** serve the image over HTTP (port 5000, or any free
  port), send `0x13` with that port (2 bytes BE), then read `0x14` progress
  frames (byte 6 = percent; 100 done, above 100 failed). The mirror fetches
  `GET /AST<MODEL>.bin`. There is no signature check.
