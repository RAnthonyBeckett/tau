"""Connection discovery package for TAU."""

from __future__ import annotations

from tau.connect.ctx import LOOPBACK, LookContext, WalkRefused
from tau.connect.lab import (
    NOT_CONNECTED_NOTE,
    OLD_HELPER_NOTE,
    look_at_machine,
    look_everywhere,
    look_here,
)
from tau.connect.places import inside_context, list_places, look_at_places
from tau.connect.record import OFF, STATES, Connection, make_id, merge
from tau.connect.registry import (
    FINDERS,
    FinderSpec,
    counts,
    finder,
    load_finders,
    run_look,
)
from tau.connect.screen import (
    LEVEL_CHOICES,
    handle as handle_connections,
    screen_view,
    spoken_summary,
)
from tau.connect.store import LookStore
from tau.connect.switches import (
    allowed,
    describe_switch,
    switch,
    switch_all_off,
    switched_on,
)
from tau.connect.view import (
    KIND_ORDER,
    find_ids,
    fold,
    is_noise,
    program_key,
    summary_lines,
    tree,
)
from tau.connect import catalogues, notes
from tau.connect.notes import add_note

__all__ = [
    "Connection",
    "FINDERS",
    "FinderSpec",
    "KIND_ORDER",
    "LEVEL_CHOICES",
    "LOOPBACK",
    "LookContext",
    "LookStore",
    "NOT_CONNECTED_NOTE",
    "OFF",
    "OLD_HELPER_NOTE",
    "STATES",
    "WalkRefused",
    "add_note",
    "allowed",
    "catalogues",
    "counts",
    "describe_switch",
    "find_ids",
    "finder",
    "fold",
    "handle_connections",
    "inside_context",
    "is_noise",
    "list_places",
    "load_finders",
    "look_at_machine",
    "look_at_places",
    "look_everywhere",
    "look_here",
    "make_id",
    "merge",
    "notes",
    "program_key",
    "run_look",
    "screen_view",
    "spoken_summary",
    "summary_lines",
    "switch",
    "switch_all_off",
    "switched_on",
    "tree",
]
