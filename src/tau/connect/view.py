"""View model and folding logic for connection lists."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping

KIND_ORDER: list[str] = [
    "Assistant that can take on work",
    "Model server",
    "Model",
    "Tool server set up for an assistant",
    "Tool server registered with Windows",
    "Program that offers TAU's own connection",
    "Web service that describes itself",
    "Program that accepts scripted commands",
    "Catalogue file",
    "Web page",
    "Media player with standard controls",
    "Linux environment inside this machine",
    "Container on this machine",
    "Folder this machine shares",
    "Shared folder from another machine",
    "Drive",
    "Machine that could be paired",
    "Scheduled task",
    "Background service",
    "Installed program",
    "Command-line tool",
    "Command set for the command shell",
    "Link type that starts a program",
    "File type with extra actions",
    "Program reachable on the message bus",
    "Program listening on a port",
    "On-screen controls",
]

_KIND_INDEX: dict[str, int] = {k: i for i, k in enumerate(KIND_ORDER)}
_SO_RE = re.compile(r"\.so(\.\d+)*$", re.IGNORECASE)
_VERSION_RE = re.compile(r"^v?\d+(\.\d+)*$")
_DROP_WORDS = {
    "64bit",
    "32bit",
    "64-bit",
    "32-bit",
    "x64",
    "x86",
    "edition",
}
_NOISE_PHRASES = (
    "documentation",
    "readme",
    "release notes",
    "website",
    "help file",
    "user guide",
    "license",
)


def is_noise(conn: Mapping[str, Any]) -> bool:
    """Return True for connections that should never be shown."""
    if conn.get("setting", "off") != "off":
        return False
    kind = conn.get("kind", "")
    name = conn.get("name", "")
    if kind == "Command-line tool":
        lower = name.lower()
        if _SO_RE.search(name) or lower.endswith((".dll", ".a")):
            return True
    elif kind == "Installed program":
        lower = name.lower()
        if lower.startswith("uninstall") or any(p in lower for p in _NOISE_PHRASES):
            return True
    return False


def program_key(name: str) -> str:
    """Return a normalised name used to merge spellings of one program."""
    raw = name.lower()
    if " - " in raw:
        raw = raw.split(" - ", 1)[0]
    while "(" in raw and ")" in raw:
        new_raw = re.sub(r"\([^)]*\)", "", raw)
        if new_raw == raw:
            break
        raw = new_raw
    words = raw.split()
    kept = [
        w for w in words
        if w not in _DROP_WORDS and not _VERSION_RE.match(w)
    ]
    res = " ".join(kept).strip()
    if not res:
        return name.lower().strip()
    return res


def _make_one(conn: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "type": "one",
        "name": conn.get("name", ""),
        "state": conn.get("state", "Found"),
        "setting": conn.get("setting", "off"),
        "id": conn.get("id", ""),
        "evidence": conn.get("evidence", ""),
        "allows": conn.get("allows", ""),
        "needs": conn.get("needs", ""),
        "door": conn.get("door", ""),
        "detail": dict(conn.get("detail") or {}),
    }


def _row_is_on(row: Mapping[str, Any]) -> bool:
    if row.get("type") == "bundle":
        return int(row.get("on", 0)) > 0
    return row.get("setting", "off") != "off"


def fold(connections: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Take connections of one machine, place and kind and return display rows."""
    clean = [c for c in connections if not is_noise(c)]
    if not clean:
        return []

    kind = clean[0].get("kind", "")
    rows: list[dict[str, Any]] = []

    if kind == "Command-line tool":
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for conn in clean:
            folder = str((conn.get("detail") or {}).get("folder", ""))
            groups.setdefault(folder, []).append(conn)
        for folder, members in groups.items():
            if len(members) >= 4 and folder:
                items = [_make_one(c) for c in members]
                items.sort(key=lambda it: str(it.get("name", "")).lower())
                on_count = sum(1 for it in items if it.get("setting") != "off")
                rows.append({
                    "type": "bundle",
                    "name": f"{len(items)} commands in {folder}",
                    "count": len(items),
                    "on": on_count,
                    "items": items,
                })
            else:
                for c in members:
                    rows.append(_make_one(c))

    elif kind == "Installed program":
        prog_groups: dict[str, list[Mapping[str, Any]]] = {}
        for conn in clean:
            k = program_key(str(conn.get("name", "")))
            prog_groups.setdefault(k, []).append(conn)
        for k, members in prog_groups.items():
            if len(members) > 1:
                sorted_members = sorted(
                    members,
                    key=lambda c: (len(str(c.get("name", ""))), str(c.get("name", ""))),
                )
                chosen = sorted_members[0]
                row = _make_one(chosen)
                chosen_idx = members.index(chosen)
                other_members = [c for idx, c in enumerate(members) if idx != chosen_idx]
                other_names = sorted(str(c.get("name", "")) for c in other_members)
                all_ids = sorted(str(c.get("id", "")) for c in members)
                row["detail"]["also_listed_as"] = other_names
                row["detail"]["ids"] = all_ids

                setting = "off"
                for c in members:
                    s = str(c.get("setting", "off"))
                    if s != "off":
                        setting = s
                        break
                row["setting"] = setting

                if any(c.get("state") == "On" for c in members):
                    row["state"] = "On"
                else:
                    row["state"] = chosen.get("state", "Found")
                rows.append(row)
            else:
                rows.append(_make_one(members[0]))

    elif kind == "File type with extra actions":
        file_groups: dict[str, list[Mapping[str, Any]]] = {}
        singles: list[Mapping[str, Any]] = []
        for conn in clean:
            name = str(conn.get("name", ""))
            m = re.search(r"\(([^()]+)\)\s*$", name)
            if m:
                prog = m.group(1).strip()
                file_groups.setdefault(prog, []).append(conn)
            else:
                singles.append(conn)
        for prog, members in file_groups.items():
            if len(members) >= 4:
                items = [_make_one(c) for c in members]
                items.sort(key=lambda it: str(it.get("name", "")).lower())
                on_count = sum(1 for it in items if it.get("setting") != "off")
                rows.append({
                    "type": "bundle",
                    "name": f"{len(items)} file types handled by {prog}",
                    "count": len(items),
                    "on": on_count,
                    "items": items,
                })
            else:
                for c in members:
                    rows.append(_make_one(c))
        for c in singles:
            rows.append(_make_one(c))

    elif kind == "Link type that starts a program":
        scheme_groups: dict[str, list[Mapping[str, Any]]] = {}
        non_bundle: list[Mapping[str, Any]] = []
        for conn in clean:
            scheme = ""
            detail = conn.get("detail")
            if isinstance(detail, dict) and detail.get("scheme"):
                scheme = str(detail["scheme"])
            if not scheme:
                scheme = str(conn.get("name", "")).replace("://", "")
            if "-" in scheme:
                part = scheme.split("-", 1)[0]
                scheme_groups.setdefault(part, []).append(conn)
            else:
                non_bundle.append(conn)
        for part, members in scheme_groups.items():
            if len(members) >= 5:
                items = [_make_one(c) for c in members]
                items.sort(key=lambda it: str(it.get("name", "")).lower())
                on_count = sum(1 for it in items if it.get("setting") != "off")
                rows.append({
                    "type": "bundle",
                    "name": f"{len(items)} link types starting with {part}-",
                    "count": len(items),
                    "on": on_count,
                    "items": items,
                })
            else:
                for c in members:
                    rows.append(_make_one(c))
        for c in non_bundle:
            rows.append(_make_one(c))

    else:
        for c in clean:
            rows.append(_make_one(c))

    rows.sort(
        key=lambda r: (
            0 if _row_is_on(r) else 1,
            str(r.get("name", "")).lower(),
        )
    )
    return rows


def _kind_sort_key(kind_name: str) -> tuple[int, str]:
    if kind_name in _KIND_INDEX:
        return (_KIND_INDEX[kind_name], "")
    return (len(KIND_ORDER), kind_name.lower())


def tree(
    looks: Mapping[str, Mapping[str, Any]],
    only_on: bool = False,
    text: str = "",
) -> List[Dict[str, Any]]:
    """Build the whole connection screen grouped by machine, place, and kind."""
    clean_text = text.strip().lower() if text else ""
    filter_active = only_on or bool(clean_text)

    def matches_filter(c: Mapping[str, Any]) -> bool:
        if only_on and c.get("setting", "off") == "off":
            return False
        if clean_text:
            name_ok = clean_text in str(c.get("name", "")).lower()
            kind_ok = clean_text in str(c.get("kind", "")).lower()
            ev_ok = clean_text in str(c.get("evidence", "")).lower()
            if not (name_ok or kind_ok or ev_ok):
                return False
        return True

    result: list[dict[str, Any]] = []

    for machine_key in sorted(looks.keys(), key=lambda s: s.lower()):
        look = looks[machine_key]
        machine_name = str(look.get("machine") or machine_key)
        at = str(look.get("at") or "")
        note_val = look.get("note")
        note = "" if note_val is None else str(note_val)

        raw_conns = look.get("connections") or []
        clean_conns = [c for c in raw_conns if not is_noise(c)]
        filtered_conns = [c for c in clean_conns if matches_filter(c)]

        if filter_active and not filtered_conns:
            continue

        finders = look.get("finders") or {}
        stopped_early = sorted(
            str(name)
            for name, rep in finders.items()
            if isinstance(rep, dict) and rep.get("stopped_early") is True
        )

        target_conns = filtered_conns if filter_active else clean_conns

        all_other_places = sorted(
            {str(c.get("place", "")) for c in target_conns if str(c.get("place", "")) != ""}
        )
        has_root = any(str(c.get("place", "")) == "" for c in target_conns)

        places_to_visit: list[str] = []
        if not filter_active:
            places_to_visit.append("")
            places_to_visit.extend(all_other_places)
        else:
            if has_root:
                places_to_visit.append("")
            places_to_visit.extend(all_other_places)

        places_list: list[dict[str, Any]] = []
        for p in places_to_visit:
            p_conns = [c for c in filtered_conns if str(c.get("place", "")) == p]
            if filter_active and not p_conns:
                continue

            p_found = len(p_conns)
            p_on = sum(1 for c in p_conns if c.get("setting", "off") != "off")

            distinct_kinds = sorted(
                {str(c.get("kind", "")) for c in p_conns},
                key=_kind_sort_key,
            )
            kinds_list: list[dict[str, Any]] = []
            for k in distinct_kinds:
                k_conns = [c for c in p_conns if str(c.get("kind", "")) == k]
                k_rows = fold(k_conns)
                if k_rows:
                    k_found = len(k_conns)
                    k_on = sum(1 for c in k_conns if c.get("setting", "off") != "off")
                    kinds_list.append({
                        "kind": k,
                        "found": k_found,
                        "on": k_on,
                        "rows": k_rows,
                    })

            title = "This machine" if p == "" else p
            places_list.append({
                "place": p,
                "title": title,
                "found": p_found,
                "on": p_on,
                "kinds": kinds_list,
            })

        m_found = len(filtered_conns)
        m_on = sum(1 for c in filtered_conns if c.get("setting", "off") != "off")

        result.append({
            "machine": machine_name,
            "at": at,
            "note": note,
            "found": m_found,
            "on": m_on,
            "stopped_early": stopped_early,
            "places": places_list,
        })

    return result


def summary_lines(looks: Mapping[str, Mapping[str, Any]]) -> List[str]:
    """Return short plain sentences summarising connection status across machines."""
    if not looks:
        return ["TAU hasn't looked at any machine yet."]

    lines: list[str] = []
    for k in sorted(looks.keys(), key=lambda s: s.lower()):
        look = looks[k]
        machine = str(look.get("machine") or k)
        conns = [c for c in (look.get("connections") or []) if not is_noise(c)]
        found = len(conns)
        on = sum(1 for c in conns if c.get("setting", "off") != "off")
        lines.append(f"{machine}: {found} ways to connect, {on} switched on.")

        note = look.get("note")
        if note:
            lines.append(str(note))

        finders = look.get("finders") or {}
        stopped = sum(
            1 for rep in finders.values()
            if isinstance(rep, dict) and rep.get("stopped_early") is True
        )
        if stopped > 0:
            word = "part" if stopped == 1 else "parts"
            lines.append(f"{machine}: {stopped} {word} of the look stopped early.")

    return lines


def find_ids(row: Mapping[str, Any]) -> List[str]:
    """Return all connection ids represented by a display row."""
    if row.get("type") == "bundle":
        result: list[str] = []
        for item in row.get("items") or []:
            result.extend(find_ids(item))
        return result

    detail = row.get("detail")
    if isinstance(detail, dict) and "ids" in detail and isinstance(detail["ids"], list):
        return [str(i) for i in detail["ids"]]

    conn_id = row.get("id")
    if conn_id:
        return [str(conn_id)]
    return []
