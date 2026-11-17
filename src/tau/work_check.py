"""Check TAU and connections: read-only checks, each with a time limit and a next step.

Nothing here listens to the microphone, plays sound, sends a worker request or costs money.
The microphone and speaker tests stay separate (Settings, Voice and hearing) because they do."""

from __future__ import annotations

import concurrent.futures as cf
import os
from pathlib import Path
from typing import Any, Callable, Dict, List

from tau.profile import get

LIMIT = 8.0      # seconds per check, so a slow server never freezes the display


def _brain(asst) -> List[Dict[str, Any]]:
    from tau.doctor import brain_checks

    out = []
    for name, state, say in brain_checks(asst.profile):
        out.append({"what": f"Brain ({name})", "state": "Ready" if state == "ok" or state == "OK" else
                    ("Needs connection" if "key" in say else "Unavailable"), "say": say,
                    "next": "" if state in ("ok", "OK") else "Settings, Brain"})
    return out


def _audio(asst) -> List[Dict[str, Any]]:
    try:
        import sounddevice as sd  # noqa: F401

        devs = sd.query_devices()
        ins = sum(1 for d in devs if d.get("max_input_channels", 0) > 0)
        outs = sum(1 for d in devs if d.get("max_output_channels", 0) > 0)
        ok = ins and outs
        return [{"what": "Sound devices", "state": "Ready" if ok else "Unavailable",
                 "say": f"{ins} microphone(s) and {outs} speaker(s) found. I didn't listen or play anything.",
                 "next": "" if ok else "Settings, Voice and hearing"}]
    except Exception as exc:
        return [{"what": "Sound devices", "state": "Unavailable",
                 "say": f"I can't see sound devices ({type(exc).__name__}). You can still type.",
                 "next": "Install TAU's voice extra, or check your sound settings."}]


def voice_status(profile) -> Dict[str, Any]:
    """Is the selected voice ready to speak? A British voice also needs the British pronunciation model."""
    voice = str(get(profile, "tau.voice_name") or "af_heart")
    if not voice or "{{" in voice:
        return {"what": "Voice", "state": "Unavailable", "say": "No voice is chosen.", "next": "Settings, Voice and hearing"}
    from tau.kokoro_voice import FALLBACK_REPOS, voice_on_disk

    missing = []
    if not voice_on_disk(voice):
        missing.append(f"the voice file for {voice}")
    british = voice.startswith("b")
    try:
        from huggingface_hub import try_to_load_from_cache

        if not isinstance(try_to_load_from_cache(repo_id=FALLBACK_REPOS[british], filename="config.json"), str):
            missing.append(("the British" if british else "the") + " pronunciation model")
    except Exception:
        missing.append("the pronunciation model (I can't check the model cache)")
    if missing:
        return {"what": "Voice", "state": "Unavailable", "say": f"{voice} isn't ready: missing {', '.join(missing)}.",
                "next": "Settings, Voice and hearing: pick the voice again to fetch what's missing. "
                        "Your old voice stays selected if fetching fails."}
    return {"what": "Voice", "state": "Ready", "say": f"{voice} and its pronunciation model are on this PC."}


def _projects(asst) -> List[Dict[str, Any]]:
    out = []
    for p in asst.work.data["projects"].values():
        ok = Path(p["path"]).is_dir() and os.access(p["path"], os.R_OK)
        out.append({"what": f"Folder: {p['name']}", "state": "Ready" if ok else "Unavailable",
                    "say": ("I can read it" + (" and workers may change files there." if p["access"] == "change" else ", read only."))
                    if ok else f"I can't open {p['path']} any more.",
                    "next": "" if ok else "Add a folder: reconnect it or forget it", "flow": "" if ok else "folder"})
    if not out:
        out.append({"what": "Folders", "state": "Needs connection", "say": "No folders added yet.",
                    "next": "Add a folder", "flow": "folder"})
    return out


def _workers(asst) -> List[Dict[str, Any]]:
    out = []
    for c in asst.work.detect_all():
        out.append({"what": c["label"], "state": c["state"] if c.get("connected") or c["state"] == "Unavailable"
                    else "Found", "say": c["why"], "next": c.get("next", ""), "flow": "app"})
    return out


def _nodes(asst) -> List[Dict[str, Any]]:
    try:
        machines = [m.title for m in asst.lab_map.machines()]
    except Exception:
        machines = []
    live = set((getattr(asst.lab, "nodes", None) or {}).keys()) if asst.lab is not None else set()
    me = str(get(asst.profile, "machine.name") or "")
    out = []
    for m in machines:
        if m.lower() == me.lower():
            continue
        on = m in live
        out.append({"what": f"Machine: {m}", "state": "Ready" if on else "Unavailable",
                    "say": "Its node is connected (lab monitoring works). Sending work to it isn't built yet." if on
                    else "Its node isn't connected right now.",
                    "next": "" if on else "Start TAU's node on that machine."})
    return out


CHECKS: List[Callable] = [_brain, _audio, lambda a: [voice_status(a.profile)], _projects, _workers, _nodes]


def check_all(asst, limit: float = LIMIT) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    pool = cf.ThreadPoolExecutor(max_workers=len(CHECKS))
    futs = [(fn, pool.submit(fn, asst)) for fn in CHECKS]
    for fn, f in futs:
        try:
            results += f.result(timeout=limit)
        except cf.TimeoutError:
            results.append({"what": getattr(fn, "__name__", "check").strip("_<>").title(), "state": "Status unknown",
                            "say": f"That check took longer than {int(limit)} seconds, so I stopped waiting."})
        except Exception as exc:
            results.append({"what": getattr(fn, "__name__", "check").strip("_<>").title(), "state": "Status unknown",
                            "say": f"That check failed ({type(exc).__name__})."})
    pool.shutdown(wait=False, cancel_futures=True)
    return {"checks": results,
            "not_checked": "Not checked: the microphone and speaker themselves (those tests listen and play; "
                           "run them from Settings, Voice and hearing), whether a worker is signed in or has credit "
                           "(that needs a paid request), and remote work on other machines (not built yet)."}
