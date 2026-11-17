"""Finder registry and execution engine for connection inspection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import pkgutil
import threading
import time
from typing import Any, Callable, Optional

from tau.connect.ctx import LookContext, WalkRefused
from tau.connect.record import Connection, merge


@dataclass
class FinderSpec:
    """Specification of a registered finder."""

    name: str
    func: Callable[[LookContext], list[Connection]]
    timeout: float = 10.0
    max_rows: int = 500


FINDERS: dict[str, FinderSpec] = {}


def finder(
    name: str,
    timeout: float = 10.0,
    max_rows: int = 500,
) -> Callable[[Callable[[LookContext], list[Connection]]], Callable[[LookContext], list[Connection]]]:
    """Register a finder function in the registry."""

    def decorator(
        func: Callable[[LookContext], list[Connection]],
    ) -> Callable[[LookContext], list[Connection]]:
        registry = globals().get("FINDERS")
        if registry is None:
            registry = FINDERS
        if name in registry:
            raise ValueError(f"Finder already registered: {name}")
        registry[name] = FinderSpec(name=name, func=func, timeout=timeout, max_rows=max_rows)
        return func

    return decorator


def load_finders() -> None:
    """Import all finder modules from the finders package."""
    import tau.connect.finders as finders_pkg

    for _, mod_name, is_pkg in pkgutil.iter_modules(finders_pkg.__path__):
        if not is_pkg:
            importlib.import_module(f"tau.connect.finders.{mod_name}")


def run_look(
    ctx: LookContext,
    finders: Optional[dict[str, FinderSpec]] = None,
    previous: Optional[list[dict[str, Any]]] = None,
    now: Optional[str] = None,
) -> dict[str, Any]:
    """Execute finders and combine discoveries with previous state."""
    if finders is None:
        load_finders()
        specs = dict(globals().get("FINDERS", FINDERS))
    else:
        specs = dict(finders)

    if now is None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    all_found: list[Connection] = []
    reports: dict[str, dict[str, Any]] = {}

    for name in sorted(specs.keys()):
        spec = specs[name]
        results_container: list[Any] = []
        exc_container: list[BaseException] = []

        def worker() -> None:
            try:
                res = spec.func(ctx)
                results_container.append(res)
            except BaseException as exc:
                exc_container.append(exc)

        t0 = time.perf_counter()
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=spec.timeout)
        elapsed = round(time.perf_counter() - t0, 2)

        if thread.is_alive():
            reports[name] = {
                "count": 0,
                "stopped_early": True,
                "seconds": elapsed,
                "error": "ran out of time",
            }
            continue

        if exc_container:
            exc = exc_container[0]
            if isinstance(exc, WalkRefused):
                reports[name] = {
                    "count": 0,
                    "stopped_early": True,
                    "seconds": elapsed,
                    "error": "tried to read too many folders",
                }
            else:
                reports[name] = {
                    "count": 0,
                    "stopped_early": False,
                    "seconds": elapsed,
                    "error": "stopped with an error",
                }
            continue

        raw = results_container[0] if results_container else []
        valid = [item for item in raw if isinstance(item, Connection)] if isinstance(raw, list) else []

        if len(valid) > spec.max_rows:
            kept = valid[: spec.max_rows]
            stopped_early = True
        else:
            kept = valid
            stopped_early = False

        all_found.extend(kept)
        reports[name] = {
            "count": len(kept),
            "stopped_early": stopped_early,
            "seconds": elapsed,
            "error": "",
        }

    return {
        "machine": ctx.machine,
        "at": now,
        "connections": merge(previous or [], all_found, now),
        "finders": reports,
    }


def counts(look: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Return counts of found and on connections grouped by kind."""
    tally: dict[str, dict[str, int]] = {}
    for entry in look.get("connections", []):
        kind = entry.get("kind", "")
        if not kind:
            continue
        if kind not in tally:
            tally[kind] = {"found": 0, "on": 0}
        if entry.get("state") != "Not found this time":
            tally[kind]["found"] += 1
        if entry.get("state") == "On":
            tally[kind]["on"] += 1
    return tally
