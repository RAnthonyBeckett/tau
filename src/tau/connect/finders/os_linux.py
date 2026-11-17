"""Discovers ways Linux allows programs to be started or driven."""

from __future__ import annotations

import re

from tau.connect.ctx import LookContext, WalkRefused
from tau.connect.record import Connection, make_id
from tau.connect.registry import finder


def _find_message_bus_programs(ctx: LookContext) -> list[Connection]:
    user_text = ctx.run(["busctl", "--user", "list", "--no-pager", "--no-legend"], timeout=8.0)
    system_text = ctx.run(["busctl", "--system", "list", "--no-pager", "--no-legend"], timeout=8.0)

    connections: list[Connection] = []
    seen_names: set[str] = set()
    mpris_prefix = "org.mpris.MediaPlayer2."
    ignored_prefixes = (
        ":",
        "org.freedesktop.",
        "org.gtk.",
        "org.a11y.",
        "org.gnome.SessionManager",
    )

    sources = [
        (user_text, "user", "your session"),
        (system_text, "system", "the whole system"),
    ]

    for raw_text, bus_type, label in sources:
        if not raw_text:
            continue
        for line in raw_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split()
            if not parts:
                continue
            bus_name = parts[0]
            if bus_name.startswith(ignored_prefixes):
                continue
            name_key = bus_name.lower()
            if name_key in seen_names:
                continue
            seen_names.add(name_key)

            if bus_name.startswith(mpris_prefix):
                kind = "Media player with standard controls"
                raw_player = bus_name[len(mpris_prefix):]
                player_name = re.sub(r"\.instance.*$", "", raw_player)
                conn = Connection(
                    id=make_id(ctx.machine, "", kind, name_key),
                    machine=ctx.machine,
                    kind=kind,
                    name=player_name,
                    evidence="It offers the standard play, pause and skip controls to other programs.",
                    door="standard media controls",
                    state="Found",
                    allows="TAU could play, pause, skip and read what is playing.",
                    detail={"bus_name": bus_name, "bus": bus_type},
                )
            else:
                kind = "Program reachable on the message bus"
                conn = Connection(
                    id=make_id(ctx.machine, "", kind, name_key),
                    machine=ctx.machine,
                    kind=kind,
                    name=bus_name,
                    evidence=f"It has a name on the message bus for {label}, so other programs may send it messages.",
                    door="message bus",
                    state="Found",
                    allows="Nothing yet. If you switch it on, TAU will ask it what it can be asked to do.",
                    detail={"bus_name": bus_name, "bus": bus_type},
                )
            connections.append(conn)

    return connections


def _find_services_and_tasks(ctx: LookContext) -> list[Connection]:
    folders: list[tuple[str, str, str]] = [
        ("/etc/systemd/system", "this machine", "system"),
    ]
    home = ctx.env("HOME")
    if home:
        folders.append((home + "/.config/systemd/user", "your account", "user"))

    connections: list[Connection] = []
    ignored_prefixes = ("dbus-", "display-manager", "default.target", "ctrl-alt-del", "syslog")
    ignored_suffixes = (".wants", ".requires", ".d")

    for folder, label, scope in folders:
        entries = ctx.list_dir(folder, start=True)
        for entry in entries:
            if not isinstance(entry, str):
                continue
            if entry.endswith(ignored_suffixes):
                continue
            if entry.startswith(ignored_prefixes):
                continue

            if entry.endswith(".service"):
                kind = "Background service"
                name = entry.removesuffix(".service")
                door = "service manager"
                evidence = f"It was added to {label} as a background service."
                allows = "TAU could tell you whether it is running, and with your yes, start, stop or restart it."
                conn_id = make_id(ctx.machine, "", kind, f"{label}|{name.lower()}")
                connections.append(
                    Connection(
                        id=conn_id,
                        machine=ctx.machine,
                        kind=kind,
                        name=name,
                        evidence=evidence,
                        door=door,
                        state="Found",
                        allows=allows,
                        detail={"unit": entry, "scope": scope, "folder": folder},
                    )
                )
            elif entry.endswith(".timer"):
                kind = "Scheduled task"
                name = entry.removesuffix(".timer")
                door = "service manager"
                evidence = f"It was added to {label} to run on a schedule."
                allows = "TAU could tell you when it runs next, and with your yes, run it now or pause it."
                conn_id = make_id(ctx.machine, "", kind, f"{label}|{name.lower()}")
                connections.append(
                    Connection(
                        id=conn_id,
                        machine=ctx.machine,
                        kind=kind,
                        name=name,
                        evidence=evidence,
                        door=door,
                        state="Found",
                        allows=allows,
                        detail={"unit": entry, "scope": scope, "folder": folder},
                    )
                )

    return connections


def _find_onscreen_controls(ctx: LookContext) -> list[Connection]:
    kind = "On-screen controls"
    return [
        Connection(
            id=make_id(ctx.machine, "", kind, "ui"),
            machine=ctx.machine,
            kind=kind,
            name="Reading and pressing on-screen controls",
            evidence=(
                "Linux desktops let a helper program read the controls in a window and press them, "
                "when the accessibility service is on."
            ),
            door="accessibility interface",
            state="Found",
            allows="A last resort for programs that offer no other way in. TAU cannot do this yet.",
        )
    ]


@finder("linux_doors", timeout=45.0, max_rows=1000)
def find_linux_doors(ctx: LookContext) -> list[Connection]:
    """Finds doors and interfaces provided by Linux for starting and driving programs."""
    if ctx.system != "Linux":
        return []

    sections = [
        _find_message_bus_programs,
        _find_services_and_tasks,
        _find_onscreen_controls,
    ]

    connections: list[Connection] = []
    for section in sections:
        try:
            connections.extend(section(ctx))
        except WalkRefused:
            raise
        except Exception:
            pass

    connections.sort(key=lambda c: (c.kind, c.name.lower()))
    return connections
