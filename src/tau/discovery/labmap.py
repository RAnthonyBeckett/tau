"""The lab map: an OKF folder with one note per machine, service and drive.

Layout under ``<brain>/lab/``::

    index.md                     all machines
    log.md
    <machine>.md                 type "Machine": hardware, listening ports, containers
    <machine>/services/<svc>.md  type "System Service" (or "Container")
    <machine>/drives/<drive>.md  type "Drive"

Questions ("what's running on X", "what's on port N", "what services does the
lab have") are answered by reading these notes' frontmatter. No model is called.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from tau import okf

ACTOR = "tau-discovery/" + okf.TAU_ACTOR.split("/", 1)[1]
STALE_DAYS = 7


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-.")
    return s or "unnamed"


def short_name(name: str) -> str:
    """The short hostname: the first part before a dot, lower case."""
    return str(name).split(".", 1)[0].strip().lower()


def nickname_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in (value or []) if str(v).strip()]


class LabMap:
    def __init__(self, root: Path, nicknames: Optional[Any] = None):
        self.root = Path(root)
        # {machine: [nickname, ...]} or a callable returning it (the live profile)
        self.nicknames = nicknames

    # -- names ---------------------------------------------------------------------
    def _nicks(self) -> Dict[str, List[str]]:
        n = self.nicknames() if callable(self.nicknames) else self.nicknames
        return {str(k): nickname_list(v) for k, v in (n or {}).items()}

    def known_names(self) -> List[str]:
        try:
            return sorted({str(c.meta.get("machine") or c.title) for c in self.machines()}, key=str.lower)
        except Exception:
            return []

    def resolve(self, name: Optional[str], known: Optional[List[str]] = None) -> Optional[str]:
        """The lab-map machine a spoken name means, or None. One resolver for every lab question.

        In order: the exact name (any case), a nickname from Settings, the short hostname, then a
        unique prefix ("Hal" for "Halcyon")."""
        if not name:
            return None
        want = str(name).strip().strip("?.!,'\"").lower()
        if not want:
            return None
        known = self.known_names() if known is None else known
        for k in known:
            if k.lower() == want:
                return k
        nicks = {k.lower(): v for k, v in self._nicks().items()}
        for k in known:
            if want in {n.lower() for n in nicks.get(k.lower(), [])}:
                return k
        for k in known:
            if short_name(k) == want:
                return k
        pref = [k for k in known if k.lower().startswith(want) or short_name(k).startswith(want)]
        return pref[0] if len(pref) == 1 else None

    def unknown_machine(self, name: str) -> Dict[str, Any]:
        known = self.known_names()
        if not known:
            return {"say": f"I don't know a machine called {name}, and my lab map is empty so far.",
                    "services": [], "hits": [], "known": []}
        nicks = self._nicks()
        parts = [k + (f" ({', '.join(nicks[k])})" if nicks.get(k) else "") for k in known]
        return {"say": f"I don't know a machine called {name}. The machines I know: {', '.join(parts)}.",
                "services": [], "hits": [], "known": known}

    # -- writing -------------------------------------------------------------------
    def write_snapshot(self, snap: Dict[str, Any]) -> Path:
        m = snap["machine"]
        name = slug(m["name"])
        when = okf.now_utc()
        stale = when + dt.timedelta(days=STALE_DAYS)
        verified = [{"by": "process:tau-discovery", "at": okf.iso(when)}]
        src = [{"resource": f"discovery pass on {m['name']} ({snap.get('platform')})", "id": "discovery"}]
        mdir = self.root / name
        # services: rewrite the folder so removed services disappear
        for sub in ("services", "drives"):
            d = mdir / sub
            if d.exists():
                for f in d.glob("*.md"):
                    f.unlink()
        ports_by_pid: Dict[Any, List[int]] = {}
        for p in snap.get("ports", []):
            ports_by_pid.setdefault(p.get("pid"), []).append(p["port"])
        running = 0
        for s in snap.get("services", []):
            ports = sorted(set(ports_by_pid.get(s.get("pid"), []))) if s.get("pid") else []
            if s.get("state") == "running":
                running += 1
            okf.write_concept(
                self.root, f"{name}/services/{slug(s['name'])}", "System Service",
                f"# Service\n\n{s.get('display') or s['name']} on [{m['name']}](/{name}.md).\n",
                title=s["name"], description=f"{s.get('state', '?')} on {m['name']}",
                tags=["service", m["name"]], sources=src, generated_by=ACTOR, verified=verified,
                stale_after=stale, update_index=False, log_entry=None,
                extra={"machine": m["name"], "state": s.get("state"), "start_mode": s.get("start_mode"),
                       "pid": s.get("pid"), "ports": ports, "kind": s.get("kind")})
        for c in snap.get("containers", []):
            okf.write_concept(
                self.root, f"{name}/services/container-{slug(c['name'])}", "Container",
                f"# Container\n\n`{c.get('image')}` on [{m['name']}](/{name}.md). Ports: {c.get('ports') or '-'}\n",
                title=str(c["name"]), description=f"{c.get('state')} container on {m['name']}",
                tags=["container", m["name"]], sources=src, generated_by=ACTOR, verified=verified,
                stale_after=stale, update_index=False,
                extra={"machine": m["name"], "state": c.get("state"), "image": c.get("image")})
        for d in snap.get("drives", []):
            okf.write_concept(
                self.root, f"{name}/drives/{slug(d['mount'])}", "Drive",
                f"# Drive\n\n{d['mount']} ({d.get('fstype')}). "
                + ("TAU never walks this drive.\n" if d.get("never_walk") else "\n"),
                title=str(d["mount"]),
                description=("network drive" if d.get("network") else "slow drive" if d.get("slow") else "drive")
                + f" on {m['name']}",
                tags=["drive", m["name"]], sources=src, generated_by=ACTOR, verified=verified,
                stale_after=stale, update_index=False,
                extra={k: d.get(k) for k in ("mount", "fstype", "total_gb", "free_gb", "network",
                                              "slow", "never_walk")} | {"machine": m["name"]})
        gpus = m.get("gpus") or []
        body = [f"# Machine\n\n- OS: {m.get('os')}\n- CPU: {m.get('cpu')}\n- RAM: {m.get('ram_gb')} GB"]
        for g in gpus:
            body.append(f"- GPU: {g.get('name')} ({round(g.get('total_mb', 0) / 1024)} GB)")
        body.append(f"\n# Services\n\n{running} running of {len(snap.get('services', []))}; "
                    f"see [services]({name}/services/).")
        body.append("\n# Listening ports\n\n| Port | Address | Process |\n|---|---|---|")
        for p in sorted(snap.get("ports", []), key=lambda x: x["port"]):
            body.append(f"| {p['port']} | {p.get('address')} | {p.get('process') or '?'} |")
        if snap.get("agent_clis"):
            body.append("\n# Agent tools\n\n" + "\n".join(f"- {c['name']}: `{c['path']}`"
                                                          for c in snap["agent_clis"]))
        if snap.get("model_servers"):
            body.append("\n# Model servers\n\n" + "\n".join(
                f"- {s['engine']} at {s['url']}: {', '.join(s.get('models') or [])}"
                for s in snap["model_servers"]))
        path = okf.write_concept(
            self.root, name, "Machine", "\n".join(body),
            title=m["name"], description=f"{m.get('os')}, {len(snap.get('services', []))} services, "
                                         f"{len(snap.get('ports', []))} open ports",
            tags=["machine"], sources=src, generated_by=ACTOR, verified=verified, stale_after=stale,
            update_index=False, log_entry=f"Discovery pass on [{m['name']}](/{name}.md)",
            extra={"machine": m["name"], "platform": snap.get("platform"),
                   "listening": [{"port": p["port"], "address": p.get("address"),
                                  "process": p.get("process"), "pid": p.get("pid")}
                                 for p in snap.get("ports", [])],
                   "gpus": gpus, "agent_clis": [c["name"] for c in snap.get("agent_clis", [])],
                   "model_servers": snap.get("model_servers", [])})
        self.reindex()
        return path

    def reindex(self) -> None:
        for folder in [self.root] + sorted(p for p in self.root.rglob("*") if p.is_dir()):
            okf.write_index(folder, is_root=False, heading="Lab map" if folder == self.root else None)

    # -- reading -------------------------------------------------------------------------
    def machines(self) -> List[okf.Concept]:
        return [c for c in okf.iter_concepts(self.root, with_body=False) if c.type == "Machine"]

    def services(self, machine: Optional[str] = None) -> List[okf.Concept]:
        out = [c for c in okf.iter_concepts(self.root, with_body=False)
               if c.type in ("System Service", "Container")]
        if machine:
            real = self.resolve(machine) or machine
            out = [c for c in out if str(c.meta.get("machine", "")).lower() == real.lower()]
        return out

    def _stale_note(self, concepts: List[okf.Concept]) -> str:
        if any(c.stale for c in concepts):
            return " Some of this is over a week old; say 're-check the lab' to refresh."
        return ""

    def answer_services(self, machine: Optional[str] = None, query: str = "",
                        running_only: bool = True) -> Dict[str, Any]:
        if machine:
            real = self.resolve(machine)
            if real is None:
                return self.unknown_machine(machine)
            machine = real
        svcs = self.services(machine)
        if query:
            q = query.lower()
            svcs = [s for s in svcs if q in s.title.lower() or q in s.description.lower()]
        if running_only:
            svcs = [s for s in svcs if s.meta.get("state") in ("running", "active")]
        names = sorted({s.title for s in svcs})
        where = f" on {machine}" if machine else " in the lab"
        if not names:
            return {"say": f"I don't see any running services{where}" + (f" matching {query}." if query else "."),
                    "services": []}
        head = ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else "")
        return {"say": f"{len(names)} running{where}: {head}." + self._stale_note(svcs),
                "services": [{"name": s.title, "machine": s.meta.get("machine"),
                              "state": s.meta.get("state"), "ports": s.meta.get("ports")} for s in svcs]}

    def answer_port(self, port: int, machine: Optional[str] = None) -> Dict[str, Any]:
        hits = []
        if machine:
            real = self.resolve(machine)
            if real is None:
                return self.unknown_machine(machine)
            machine = real
        ms = self.machines()
        for m in ms:
            if machine and m.meta.get("machine", "").lower() != machine.lower():
                continue
            for p in m.meta.get("listening") or []:
                if int(p.get("port", -1)) == int(port):
                    hits.append((m.meta.get("machine"), p))
        if not hits:
            return {"say": f"Nothing is listening on port {port} that I know of.", "hits": []}
        parts = [f"{p.get('process') or 'an unknown process'} on {mach}" for mach, p in hits]
        return {"say": f"Port {port}: " + "; ".join(parts) + "." + self._stale_note(ms),
                "hits": [{"machine": mach, **p} for mach, p in hits]}

    def answer_machine(self, machine: str) -> Dict[str, Any]:
        real = self.resolve(machine)
        if real is None:
            return self.unknown_machine(machine)
        return self.answer_services(machine=real)
