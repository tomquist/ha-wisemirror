#!/usr/bin/env python3
"""Command-line tool for WiseMirror mirrors, for experimenting and debugging.

Uses the integration's own protocol and transport modules; Home Assistant is
not needed (``firmware`` also needs aiohttp).

Examples:
  python3 scripts/wisemirror.py discover
  python3 scripts/wisemirror.py status 192.168.1.50
  python3 scripts/wisemirror.py set 192.168.1.50 brightness 40
  python3 scripts/wisemirror.py set 192.168.1.50 sleep-window 23:00 06:30
  python3 scripts/wisemirror.py raw 192.168.1.50 0x12 00
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import sys
import types

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "wisemirror"


def _load(name: str) -> types.ModuleType:
    """Import a module of the integration without running its __init__."""
    package = "wisemirror_cli"
    if package not in sys.modules:
        pkg = types.ModuleType(package)
        pkg.__path__ = [str(PACKAGE_DIR)]
        sys.modules[package] = pkg
    full = f"{package}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, PACKAGE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


p = _load("protocol")
t = _load("transport")


def _json(value: object) -> str:
    def default(obj: object) -> object:
        if isinstance(obj, bytes):
            return obj.hex(" ")
        return str(obj)

    return json.dumps(value, indent=2, default=default, ensure_ascii=False)


def _hhmm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def _bool(value: str) -> bool:
    if value.lower() in ("1", "on", "true", "yes"):
        return True
    if value.lower() in ("0", "off", "false", "no"):
        return False
    raise argparse.ArgumentTypeError(f"not a boolean: {value}")


SETTERS = {
    "key-tone": ("on|off", lambda a: p.build_key_tone(_bool(a[0]))),
    "24h": ("on|off", lambda a: p.build_clock_24h(_bool(a[0]))),
    "day-first": ("on|off", lambda a: p.build_day_first(_bool(a[0]))),
    "celsius": ("on|off", lambda a: p.build_temp_unit(_bool(a[0]))),
    "brightness": ("0-100", lambda a: p.build_brightness(int(a[0]))),
    "sleep": ("on|off", lambda a: p.build_sleep_mode(_bool(a[0]))),
    "sleep-window": (
        "HH:MM HH:MM",
        lambda a: p.build_sleep_window(*_hhmm(a[0]), *_hhmm(a[1])),
    ),
    "sleep-brightness": ("0-100", lambda a: p.build_sleep_brightness(int(a[0]))),
    "name": ("NAME", lambda a: p.build_name(" ".join(a))),
    "location": (
        "CITY LAT LON [KEY]",
        lambda a: p.build_location(
            p.format_location(
                a[0], float(a[1]), float(a[2]), a[3] if len(a) > 3 else ""
            )
        ),
    ),
    "weather": (
        "FLAGS SOURCE",
        lambda a: p.build_weather(int(a[0], 0), int(a[1], 0)),
    ),
    "chinese-weekday": ("on|off", lambda a: p.build_weekday_language(_bool(a[0]))),
}


async def cmd_discover(args: argparse.Namespace) -> None:
    query = {
        "temperature": p.QUERY_TEMPERATURE,
        "old": p.QUERY_OLD_DEVICE,
        "new": p.QUERY_NEW_DEVICE,
    }[args.query]
    replies = await t.async_discover(args.host, query=query, timeout=args.timeout)
    if not replies:
        print("No mirror answered.", file=sys.stderr)
    for reply in replies:
        print(_json(asdict(reply)))


async def cmd_status(args: argparse.Namespace) -> None:
    client = t.WiseMirrorUdpClient(args.ip)
    result: dict[str, object] = {}
    for label, call in (
        ("heartbeat", client.heartbeat),
        ("settings", client.read_settings),
        ("weather", client.read_weather),
    ):
        try:
            result[label] = asdict(await call())
        except (t.WiseMirrorError, p.ProtocolError) as err:
            result[label] = f"error: {err}"
    replies = await t.async_discover(args.ip, timeout=1.0)
    result["discovery"] = asdict(replies[0]) if replies else "no reply"
    print(_json(result))


async def cmd_set(args: argparse.Namespace) -> None:
    _usage, build = SETTERS[args.setting]
    frame = build(args.values)
    reply = await t.WiseMirrorUdpClient(args.ip).request(frame)
    print(f"-> {frame.hex(' ')}\n<- {reply.hex(' ')}")


async def cmd_raw(args: argparse.Namespace) -> None:
    frame = p.encode_frame(int(args.command, 0), bytes.fromhex(args.payload))
    reply = await t.WiseMirrorUdpClient(args.ip).request(frame, tries=args.tries)
    print(f"-> {frame.hex(' ')}\n<- {reply.hex(' ')}")


async def cmd_provision(args: argparse.Namespace) -> None:
    await t.async_provision_ap(args.ssid, args.password, args.host)
    print("The mirror accepted the credentials.")


async def cmd_firmware(args: argparse.Namespace) -> None:
    firmware = _load("firmware")
    image = Path(args.file).read_bytes()

    def progress(report: object) -> None:
        print(f"progress: {report.percent}%")

    await firmware.async_update_firmware(args.ip, image, args.model, progress)
    print("Update finished.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("discover", help="broadcast (or unicast) the discovery query")
    sp.add_argument("--host", default=t.BROADCAST)
    sp.add_argument(
        "--query", choices=["temperature", "old", "new"], default="temperature"
    )
    sp.add_argument("--timeout", type=float, default=2.0)
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("status", help="heartbeat, settings, weather and discovery")
    sp.add_argument("ip")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser(
        "set",
        help="change a setting",
        epilog="\n".join(f"  {name} {usage}" for name, (usage, _) in SETTERS.items()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp.add_argument("ip")
    sp.add_argument("setting", choices=list(SETTERS))
    sp.add_argument("values", nargs="*")
    sp.set_defaults(func=cmd_set)

    sp = sub.add_parser("raw", help="send any command code with a hex payload")
    sp.add_argument("ip")
    sp.add_argument("command", help="command code, e.g. 0x12")
    sp.add_argument("payload", nargs="?", default="00", help="hex payload")
    sp.add_argument("--tries", type=int, default=3)
    sp.set_defaults(func=cmd_raw)

    sp = sub.add_parser("provision", help="send Wi-Fi credentials in AP mode")
    sp.add_argument("ssid")
    sp.add_argument("password")
    sp.add_argument("--host", default=p.AP_HOST)
    sp.set_defaults(func=cmd_provision)

    sp = sub.add_parser("firmware", help="update firmware over the LAN (needs aiohttp)")
    sp.add_argument("ip")
    sp.add_argument("file")
    sp.add_argument("--model", help="model code, e.g. 8J12")
    sp.set_defaults(func=cmd_firmware)

    args = parser.parse_args()
    try:
        asyncio.run(args.func(args))
    except (t.WiseMirrorError, p.ProtocolError, ValueError, IndexError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
