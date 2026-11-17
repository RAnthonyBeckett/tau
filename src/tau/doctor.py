"""``tau doctor``: check each organ and say plainly what's missing."""

from __future__ import annotations

import importlib.util
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tau import paths

OK, WARN, FAIL = "ok", "warn", "fail"


def _has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def check_profile() -> Tuple[str, str, Optional[Dict[str, Any]]]:
    from tau.profile import ProfileError, load_profile

    try:
        data = load_profile()
        return OK, f"profile at {paths.profile_path()} is complete", data
    except ProfileError as exc:
        return FAIL, str(exc), None


def organ_checks(profile: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    from tau.organs import OrganRegistry, level_for

    out = []
    for organ in OrganRegistry.all():
        lv = level_for(profile, organ.name)
        name = organ.name
        if lv == "off":
            out.append((name, OK, "off"))
            continue
        status, msg = OK, f"at {lv}"
        if name == "voice":
            if not _has("sounddevice"):
                status, msg = WARN, "needs `pip install tau[voice]` for the speaker and mic"
            elif not _has("kokoro"):
                status, msg = WARN, "Kokoro isn't installed; TAU will answer in text only"
            wm = (profile.get("tau") or {}).get("wake_model")
            if not wm or not Path(str(wm)).exists():
                status, msg = WARN, msg + "; no wake model yet (click-to-talk works; see scripts/train_wake_word.py)"
        elif name == "display":
            if not _has("webview"):
                msg += "; pop-out needs `pip install tau[display]` (the docked page works without it)"
        elif name == "gpu":
            if not shutil.which("nvidia-smi"):
                status, msg = WARN, "nvidia-smi not found; GPU readings and the GPU budget rule are off"
        elif name == "notifications":
            if platform.system() == "Windows" and not _has("winrt"):
                status, msg = WARN, "needs `pip install tau[windows]` and one-time consent (tau setup devices)"
            elif platform.system() == "Linux" and not shutil.which("dbus-monitor"):
                status, msg = WARN, "dbus-monitor not found (install your distro's dbus tools)"
        elif name == "window":
            if platform.system() == "Linux" and not shutil.which("xdotool"):
                status, msg = WARN, "xdotool not found; on Wayland the focused window can't be read"
        elif name == "clipboard":
            if platform.system() == "Linux" and not any(shutil.which(c) for c in ("wl-copy", "xclip", "xsel")):
                status, msg = WARN, "no clipboard tool (install wl-clipboard or xclip)"
        elif name == "handoff":
            ads = ((profile.get("handoff") or {}).get("adapters") or {})
            have = [k for k, v in ads.items() if isinstance(v, dict) and v.get("template")]
            if not have:
                status, msg = WARN, "no hand-off template set (handoff.adapters in the profile)"
        elif name == "gesture":
            status, msg = WARN, "gesture tracking isn't built yet"
        out.append((name, status, msg))
    return out


def brain_checks(profile: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    from tau.detect import probe_model_server

    out = []
    loc = (profile.get("brain") or {}).get("local") or {}
    if loc.get("url"):
        hit = probe_model_server(loc.get("engine") or "openai_compat", loc["url"])
        out.append(("local lane", OK if hit else FAIL,
                    f"{loc['url']} answers" if hit else f"{loc['url']} isn't answering; is the model server running?"))
    bur = (profile.get("brain") or {}).get("burst") or {}
    if bur.get("url"):
        from tau.profile import load_secret
        key = load_secret(profile, bur.get("api_key_env"))
        out.append(("burst lane", OK if key else WARN,
                    "key found" if key else f"no key in ${bur.get('api_key_env')} or the .env file"))
    if not out:
        out.append(("brain", FAIL, "no brain connected; run `tau setup brain`"))
    return out


def openjarvis_checks() -> List[Tuple[str, str, str]]:
    try:
        from openjarvis.cli.doctor_cmd import _run_all_checks
    except Exception:
        return [("openjarvis", FAIL, "OpenJarvis isn't installed; `pip install openjarvis`")]
    out = []
    try:
        for c in _run_all_checks():
            st = getattr(c, "status", "ok")
            out.append((f"openjarvis: {getattr(c, 'name', '?')}", OK if st == "ok" else WARN if st == "warn" else FAIL,
                        getattr(c, "message", "")))
    except Exception as exc:
        out.append(("openjarvis", WARN, f"its own checks couldn't run ({type(exc).__name__})"))
    return out


def run() -> int:
    st, msg, profile = check_profile()
    rows: List[Tuple[str, str, str]] = [("profile", st, msg)]
    if profile:
        rows += brain_checks(profile)
        rows += organ_checks(profile)
    rows += openjarvis_checks()
    mark = {OK: "  ok ", WARN: " note", FAIL: " FIX "}
    for name, s, m in rows:
        print(f"[{mark[s]}] {name}: {m}")
    return 1 if any(s == FAIL for _, s, _ in rows) else 0
