"""Pop-out mode: a frameless, always-on-top window parked in a screen corner,
on any monitor (``display.corner`` / ``display.monitor`` from setup). Needs
``tau[display]`` (pywebview, BSD)."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping


def corner_position(corner: str, mon: Dict[str, int], size: int, margin: int = 16) -> tuple:
    x0, y0, w, h = mon["x"], mon["y"], mon["w"], mon["h"]
    left = x0 + margin
    right = x0 + w - size - margin
    top = y0 + margin
    bottom = y0 + h - size - margin
    return {"top-left": (left, top), "top-right": (right, top),
            "bottom-left": (left, bottom), "bottom-right": (right, bottom)}.get(corner, (right, top))


def open_popout(url: str, profile: Mapping[str, Any], monitors: List[Dict[str, int]]) -> None:
    import webview  # pywebview

    disp = profile.get("display") or {}
    size = int(disp.get("size_px") or 320)
    idx = int(disp.get("monitor") or 0)
    mon = monitors[idx] if 0 <= idx < len(monitors) else (monitors[0] if monitors else
                                                          {"x": 0, "y": 0, "w": 1920, "h": 1080})
    x, y = corner_position(disp.get("corner") or "top-right", mon, size)
    webview.create_window((profile.get("tau") or {}).get("name", "TAU"), url + "?mode=popout",
                          width=size, height=size, x=x, y=y, frameless=True, on_top=True,
                          resizable=False, background_color="#050304", easy_drag=True)
    webview.start()


def open_window(url: str, profile: Mapping[str, Any]) -> None:
    """A normal, resizable window holding the whole display (talk, type, conversation)."""
    import webview  # pywebview

    webview.create_window((profile.get("tau") or {}).get("name", "TAU"), url, width=1000, height=860,
                          min_size=(480, 560), resizable=True, background_color="#040203")
    webview.start()


def main(argv=None) -> int:
    """``python -m tau.display.popout URL``: the pop-out on its own (opened from Settings)."""
    import sys

    from tau.detect import monitors
    from tau.profile import load_profile

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 2
    open_popout(args[0], load_profile(check=False), monitors())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
