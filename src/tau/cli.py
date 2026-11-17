"""The ``tau`` command. Lives next to OpenJarvis's own ``jarvis`` command."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional


def _yes(question: str) -> bool:
    try:
        return (sys.stdout.write(f"{question} [y/N] "), sys.stdout.flush(), input())[2].strip().lower().startswith("y")
    except EOFError:
        return False


def cmd_setup(a) -> int:
    from tau.setup import ConsoleAsker, Setup

    Setup(ConsoleAsker()).run(a.topic)
    return 0


def cmd_run(a) -> int:
    from tau.runtime import Runtime

    Runtime().build(want_voice=not a.text).start(popout=a.popout, window=a.window,
                                                  open_browser=not a.no_browser, console=not a.no_console)
    return 0


def cmd_fetch(a) -> int:
    import os

    os.environ["HF_HUB_OFFLINE"] = "0"     # this command is the one place that downloads
    from tau.fetch import fetch_all
    from tau.profile import ProfileError, get, load_profile

    try:
        voice = get(load_profile(check=False), "tau.voice_name") or "af_heart"
    except (ProfileError, OSError):
        voice = "af_heart"
    return 0 if fetch_all(voice=str(voice)) else 1


def cmd_grant(a) -> int:
    from tau.organs.grants import grant
    from tau.profile import assistant_name, load_profile

    name = assistant_name(load_profile(check=False))
    print(grant(a.organ, a.level, (lambda q: True) if a.yes else (lambda q: _yes(q)), a.machine, name=name))
    return 0


def cmd_revoke(a) -> int:
    from tau.organs.grants import revoke

    print(revoke(a.organ, (lambda q: True) if a.yes else (lambda q: _yes(q)), a.machine))
    return 0


def cmd_status(a) -> int:
    from tau import paths
    from tau.hub.crypto import load_json
    from tau.organs import OrganRegistry, level_for
    from tau.output import PinBoard
    from tau.profile import get, load_profile

    p = load_profile(check=False)
    print(f"{get(p, 'tau.name', 'TAU')} on {get(p, 'machine.name', '?')} ({get(p, 'tau.role', '?')})")
    for lane in ("local", "burst"):
        b = get(p, f"brain.{lane}", {}) or {}
        print(f"  {lane} lane: {b.get('model') + ' at ' + b.get('url') if b.get('url') else 'not set'}")
    print("  organs: " + ", ".join(f"{o.name}={level_for(p, o.name)}" for o in OrganRegistry.all()))
    nodes = load_json(paths.state_dir() / "nodes.json") or {}
    print(f"  paired nodes: {', '.join(nodes) or 'none'}")
    if get(p, "hub.port"):
        print(f"  hub port: {get(p, 'hub.port')}")
    print(f"  pin cards waiting: {len(PinBoard().waiting())}")
    print(f"  settings and notes folder: {paths.tau_home()}")
    return 0


def cmd_doctor(a) -> int:
    from tau.doctor import run

    return run()


def cmd_discover(a) -> int:
    import json

    from tau import paths
    from tau.discovery import collect, run_discovery
    from tau.profile import ProfileError, get, load_profile

    if a.dry_run:
        # Read-only and profile-free: collect, report, write nothing to the lab map.
        try:
            name = get(load_profile(check=False), "machine.name")
        except ProfileError:
            name = None
        snap = collect(machine_name=name)
        print(f"{snap['machine']['name']} ({snap['platform']}): {len(snap['services'])} services "
              f"({sum(s.get('state') in ('running', 'active') for s in snap['services'])} running), "
              f"{len(snap['ports'])} listening ports, {len(snap['drives'])} drives "
              f"({sum(bool(d.get('never_walk')) for d in snap['drives'])} never walked), "
              f"{len(snap['containers'])} containers, agent tools: "
              f"{', '.join(c['name'] for c in snap.get('agent_clis', [])) or 'none'}.")
        if a.out:
            from pathlib import Path
            Path(a.out).write_text(json.dumps(snap, indent=1, default=str), encoding="utf-8")
            print(f"Wrote {a.out}")
        return 0 if snap["services"] or snap["ports"] or snap["drives"] else 1
    p = load_profile(check=False)
    snap = run_discovery(paths.brain_dir(p) / "lab", machine_name=get(p, "machine.name"))
    print(f"Mapped {len(snap['services'])} services, {len(snap['ports'])} listening ports and "
          f"{len(snap['drives'])} drives on {snap['machine']['name']}.")
    if a.neighbours or _yes("Also list other devices on your network (neighbour table only, no scanning)?"):
        from tau.discovery.neighbours import list_neighbours
        for n in list_neighbours():
            print(f"  {n['address']}  {n.get('name', '')}  {n['mac']}")
    return 0


def cmd_pair(a) -> int:
    from tau.hub.server import HubServer
    from tau.profile import get, load_profile

    p = load_profile(check=False)
    hub = HubServer(get(p, "hub.bind") or "127.0.0.1", int(get(p, "hub.port") or 0))
    code = hub.new_pairing_code()
    print(f"Pairing code (valid 10 minutes, works once): {code}")
    print(f"On the other machine: tau node --pair {code} --hub {get(p, 'hub.host')}:{get(p, 'hub.port')}")
    return 0


def cmd_node(a) -> int:
    import socket

    from tau.discovery import collect
    from tau.hub.node import NodeClient

    if getattr(a, "setup_port_owners", False):
        from tau.discovery import portowners

        return portowners.setup(ask=_yes)
    name = a.name or socket.gethostname()
    node = NodeClient(name)
    if a.pair:
        host, _, port = (a.hub or "").rpartition(":")
        if not host or not port:
            print("Give the hub as --hub HOST:PORT (shown by `tau pair` on the hub).")
            return 2
        node.pair(host, int(port), a.pair)
        print("Paired with the hub.")
    if not node.paired():
        print("This machine isn't paired yet. Run `tau pair` on the hub, then `tau node --pair CODE --hub HOST:PORT`.")
        return 2
    print("Mapping this machine (read-only)...")
    snap = collect(machine_name=name)
    node.connect()
    print(f"Connected to the hub as {name}.")
    node.serve(snapshot=snap)
    return 0


def cmd_wrap(a) -> int:
    from tau.senses.cmdfail import wrap

    argv = a.argv[1:] if a.argv and a.argv[0] == "--" else a.argv
    return wrap(argv)


def cmd_record(a) -> int:
    from tau.senses.cmdfail import record

    cmd = " ".join(a.argv[1:] if a.argv and a.argv[0] == "--" else a.argv)
    record(cmd, a.code, source=a.source)
    return 0


def cmd_hook(a) -> int:
    from tau.senses.cmdfail import HOOKS

    print(HOOKS[a.shell])
    return 0


def cmd_okf(a) -> int:
    from tau import okf

    rep = okf.validate(a.path)
    for e in rep.errors:
        print(f"error: {e}")
    for w in rep.warnings:
        print(f"note: {w}")
    print("valid OKF" if rep.ok else f"{len(rep.errors)} problem(s)")
    return 0 if rep.ok else 1


def cmd_say(a) -> int:
    from tau.assistant import Assistant
    from tau.profile import load_profile

    asst = Assistant(load_profile())
    asst.brain.ensure(asst.profile)
    print(asst.handle(" ".join(a.words)).say)
    return 0


def cmd_check(a) -> int:
    import importlib.util

    from tau.assistant import Assistant
    from tau.check import Devices, HardwareCheck
    from tau.profile import get, load_profile
    from tau.setup import ConsoleAsker

    asst = Assistant(load_profile())
    asst.brain.ensure(asst.profile)
    have_audio = importlib.util.find_spec("sounddevice") is not None
    devices = Devices(get(asst.profile, "audio.mic"), get(asst.profile, "audio.speaker")) if have_audio else None
    transcribe = None
    if have_audio and importlib.util.find_spec("faster_whisper") is not None:
        from tau.ears.pipeline import Transcriber
        transcribe = Transcriber()
    hc = HardwareCheck(asst.profile, ConsoleAsker(), devices=devices, transcribe=transcribe,
                       organs=asst.organs, brain=asst.brain, pins=asst.pins)
    hc.run()
    print(hc.summary())
    print("Saved to the journal; each failed item also has a pin card.")
    return 1 if any(r.status == "failed" for r in hc.results) else 0


def cmd_licence(a) -> int:
    from tau import licences, paths

    rep = licences.check(live_models=not a.offline)
    print(licences.text_report(rep))
    if a.write_readme:
        readme = paths.shipped_file("README.md")
        if readme.exists() and licences.write_readme(rep, readme):
            print(f"Updated the licence table in {readme}.")
    return 0 if rep.ok else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="tau", description="TAU: a voice-first lab assistant on OpenJarvis")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="first-run setup, or one topic again")
    s.add_argument("topic", nargs="?", help="brain | names | hub | lab | devices | display | pacing | voice | handoff")
    s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("run", help="start TAU")
    s.add_argument("--popout", action="store_true", help="frameless always-on-top window")
    s.add_argument("--text", action="store_true", help="no audio, text only")
    s.add_argument("--window", action="store_true", help="open the display in its own window")
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--no-console", action="store_true", help="no typing in this terminal; use the display")
    s.set_defaults(fn=cmd_run)
    sub.add_parser("fetch-models", help="download the voice and hearing models now").set_defaults(fn=cmd_fetch)
    for name, fn in (("grant", cmd_grant), ("revoke", cmd_revoke)):
        s = sub.add_parser(name, help=f"{name} an organ's permission")
        s.add_argument("organ")
        if name == "grant":
            s.add_argument("level", choices=["off", "sense", "suggest", "act"])
        s.add_argument("--machine")
        s.add_argument("--yes", action="store_true", help="skip the confirmation question")
        s.set_defaults(fn=fn)
    sub.add_parser("status", help="what TAU is set to").set_defaults(fn=cmd_status)
    sub.add_parser("doctor", help="check every organ and say what's missing").set_defaults(fn=cmd_doctor)
    sub.add_parser("check", help="guided hardware check, one device at a time").set_defaults(fn=cmd_check)
    s = sub.add_parser("discover", help="re-map this machine (read-only)")
    s.add_argument("--neighbours", action="store_true")
    s.add_argument("--dry-run", action="store_true", help="collect and report only; write nothing")
    s.add_argument("--out", help="with --dry-run: save the snapshot as JSON here")
    s.set_defaults(fn=cmd_discover)
    sub.add_parser("pair", help="show a one-time code for a new node").set_defaults(fn=cmd_pair)
    s = sub.add_parser("node", help="run the node helper on a non-hub machine")
    s.add_argument("--pair", metavar="CODE")
    s.add_argument("--hub", metavar="HOST:PORT")
    s.add_argument("--name")
    s.add_argument("--setup-port-owners", action="store_true",
                   help="print (and, with your sudo, install) one sudoers line so I can name the program on every port")
    s.set_defaults(fn=cmd_node)
    s = sub.add_parser("wrap", help="run a command and record it if it fails")
    s.add_argument("argv", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_wrap)
    s = sub.add_parser("_record", help=argparse.SUPPRESS)
    s.add_argument("--code", type=int, required=True)
    s.add_argument("--source", default="hook")
    s.add_argument("argv", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_record)
    s = sub.add_parser("hook", help="print a shell hook that records failed commands")
    s.add_argument("shell", choices=["bash", "zsh", "powershell"])
    s.set_defaults(fn=cmd_hook)
    s = sub.add_parser("okf", help="validate an OKF folder")
    s.add_argument("action", choices=["validate"])
    s.add_argument("path")
    s.set_defaults(fn=cmd_okf)
    for alias in ("licence-check", "license-check"):
        s = sub.add_parser(alias, help="fail on GPL/AGPL packages or non-commercial models" if alias == "licence-check" else argparse.SUPPRESS)
        s.add_argument("--offline", action="store_true", help="use recorded model-card licences only")
        s.add_argument("--write-readme", action="store_true", help="regenerate the README dependency table")
        s.set_defaults(fn=cmd_licence)
    s = sub.add_parser("say", help="send one line to TAU and print the reply")
    s.add_argument("words", nargs="+")
    s.set_defaults(fn=cmd_say)
    return ap


# These run the user's own commands, whose errors the user must see on screen.
LOUD = {"wrap", "_record", "hook"}


def main(argv: Optional[List[str]] = None) -> None:
    a = build_parser().parse_args(argv)
    if a.cmd not in LOUD:
        from tau import quiet

        quiet.install()
    try:
        code = a.fn(a)
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:  # plain words, not a traceback (the traceback goes to the log)
        from tau.profile import ProfileError
        if isinstance(exc, ProfileError):
            print(exc)
            code = 2
        else:
            from tau import quiet

            print(quiet.plain_failure(exc))
            code = 1
    sys.exit(code or 0)
