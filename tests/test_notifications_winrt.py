"""Bug 7 from the first real install: ``request_access`` handed a WinRT
IAsyncOperation straight to ``asyncio.run()``, which only takes coroutines.
These tests use a fake WinRT module whose async operation is awaitable but
is not a coroutine, which is exactly what pywinrt returns."""
import asyncio
import sys
import types

import pytest

from tau.senses import notifications


class FakeAsyncOperation:
    """Awaitable like a WinRT IAsyncOperation, but not a coroutine."""

    def __init__(self, result):
        self.result = result

    def __await__(self):
        yield from asyncio.sleep(0).__await__()
        return self.result


ALLOWED, DENIED = 1, 2


@pytest.fixture
def fake_winrt(monkeypatch):
    status = {"value": ALLOWED}

    class Listener:
        def request_access_async(self):
            return FakeAsyncOperation(status["value"])

        def get_notifications_async(self, kinds):
            note = types.SimpleNamespace(
                notification=types.SimpleNamespace(visual=types.SimpleNamespace(
                    get_binding=lambda name: types.SimpleNamespace(get_text_elements=lambda: [
                        types.SimpleNamespace(text="Backup"), types.SimpleNamespace(text="finished")]))),
                app_info=types.SimpleNamespace(display_info=types.SimpleNamespace(display_name="Backup app")),
                creation_time="now")
            return FakeAsyncOperation([note])

    mgmt = types.ModuleType("winrt.windows.ui.notifications.management")
    mgmt.UserNotificationListener = types.SimpleNamespace(current=Listener())
    mgmt.UserNotificationListenerAccessStatus = types.SimpleNamespace(ALLOWED=ALLOWED, DENIED=DENIED)
    notes = types.ModuleType("winrt.windows.ui.notifications")
    notes.NotificationKinds = types.SimpleNamespace(TOAST=1)
    for name in ("winrt", "winrt.windows", "winrt.windows.ui"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "winrt.windows.ui.notifications", notes)
    monkeypatch.setitem(sys.modules, "winrt.windows.ui.notifications.management", mgmt)
    monkeypatch.setattr(notifications.platform, "system", lambda: "Windows")
    return status


def test_fake_operation_is_not_a_coroutine():
    # The old code did asyncio.run(op); prove the fake would have reproduced the crash.
    with pytest.raises((ValueError, TypeError)):
        asyncio.run(FakeAsyncOperation(ALLOWED))


def test_request_access_awaits_the_winrt_operation(fake_winrt):
    assert notifications.request_access() == "allowed"
    fake_winrt["value"] = DENIED
    assert notifications.request_access() == "denied"


def test_recent_reads_toasts_through_the_same_path(fake_winrt):
    got = notifications.recent(5)
    assert got == [{"app": "Backup app", "title": "Backup", "body": "finished", "at": "now"}]
