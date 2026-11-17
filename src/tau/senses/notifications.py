"""Recent system notifications.

* Windows: the ``UserNotificationListener`` API (WinRT). The OS asks the user
  once for consent; ``tau setup`` triggers that request. Needs ``tau[windows]``.
* Linux: freedesktop notifications on the session D-Bus. ``tau run`` starts a
  passive ``dbus-monitor`` that copies Notify calls into a small in-memory
  ring; nothing is written to disk.
"""

from __future__ import annotations

import collections
import platform
import shutil
import subprocess
import threading
from typing import Any, Deque, Dict, List

_RING: Deque[Dict[str, Any]] = collections.deque(maxlen=50)
_STARTED = threading.Event()


def request_access() -> str:
    """Ask the OS for notification access (Windows). Returns a plain status."""
    if platform.system() != "Windows":
        return "not needed on this system"
    try:
        import asyncio

        from winrt.windows.ui.notifications.management import (UserNotificationListener,
                                                                  UserNotificationListenerAccessStatus)

        listener = UserNotificationListener.current
        async def ask():
            return await listener.request_access_async()

        status = asyncio.run(ask())
        return "allowed" if status == UserNotificationListenerAccessStatus.ALLOWED else "denied"
    except Exception:
        import logging

        logging.getLogger("tau.notifications").exception("notification access request failed")
        return "unavailable"


def _windows_recent(limit: int) -> List[Dict[str, Any]]:
    import asyncio

    from winrt.windows.ui.notifications import NotificationKinds
    from winrt.windows.ui.notifications.management import UserNotificationListener

    async def go():
        notes = await UserNotificationListener.current.get_notifications_async(NotificationKinds.TOAST)
        out = []
        for n in list(notes)[-limit:]:
            texts = []
            try:
                binding = n.notification.visual.get_binding("ToastGeneric")
                texts = [t.text for t in binding.get_text_elements()]
            except Exception:
                pass
            out.append({"app": n.app_info.display_info.display_name if n.app_info else "?",
                        "title": texts[0] if texts else "", "body": " ".join(texts[1:]),
                        "at": str(n.creation_time)})
        return out

    return asyncio.run(go())


def parse_dbus_monitor(lines: List[str]) -> List[Dict[str, Any]]:
    """Turn ``dbus-monitor`` output for Notify calls into notifications."""
    out, cur, strings = [], None, []
    for line in lines:
        s = line.strip()
        if "member=Notify" in s:
            if cur is not None and len(strings) >= 4:
                out.append({"app": strings[0], "title": strings[2], "body": strings[3]})
            cur, strings = True, []
        elif cur and s.startswith("string "):
            strings.append(s[len("string "):].strip().strip('"'))
    if cur and len(strings) >= 4:
        out.append({"app": strings[0], "title": strings[2], "body": strings[3]})
    return out


def start_linux_listener() -> bool:
    if _STARTED.is_set() or not shutil.which("dbus-monitor"):
        return _STARTED.is_set()
    try:
        proc = subprocess.Popen(["dbus-monitor", "--session",
                                 "interface='org.freedesktop.Notifications',member='Notify'"],
                                stdout=subprocess.PIPE, text=True)
    except Exception:
        return False
    _STARTED.set()

    def pump():
        block: List[str] = []
        for line in proc.stdout:
            if "member=Notify" in line and block:
                _RING.extend(parse_dbus_monitor(block))
                block = []
            block.append(line)
            if len(block) > 40:
                _RING.extend(parse_dbus_monitor(block))
                block = []

    threading.Thread(target=pump, daemon=True).start()
    return True


def recent(limit: int = 5) -> List[Dict[str, Any]]:
    if platform.system() == "Windows":
        try:
            return _windows_recent(limit)
        except Exception:
            return []
    start_linux_listener()
    return list(_RING)[-limit:][::-1]
