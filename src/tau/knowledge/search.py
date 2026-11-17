"""One search over every knowledge source the user turned on.

* OKF folders (``knowledge.okf_folders`` plus TAU's own brain, minus
  ``private/``): read one file at a time; ranked by trust tier first
  (human-reviewed > machine-confirmed > unverified), then match strength;
  stale, draft and deprecated concepts come with a warning.
* SQLite FTS5 folders (``knowledge.fts_folders``).
* MCP servers (``knowledge.mcp_servers``) through OpenJarvis's MCP client.
* OpenJarvis's own memory, where it is installed and switched on.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping

from tau import okf, paths
from tau.profile import as_list, get


def _terms(q: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", q.lower()) if len(w) > 2]


def search_okf(folder: Path, query: str, limit: int = 5) -> List[Dict[str, Any]]:
    terms = _terms(query)
    if not terms:
        return []
    hits = []
    for c in okf.iter_concepts(Path(folder)):
        if "private" in Path(c.id).parts:
            continue
        hay_head = f"{c.title} {c.description} {' '.join(map(str, c.meta.get('tags') or []))}".lower()
        hay_body = c.body.lower()
        score = sum(3 * hay_head.count(t) + min(hay_body.count(t), 5) for t in terms)
        if score:
            hits.append((c, score))
    hits.sort(key=lambda x: (x[0].rank(), x[1]), reverse=True)
    out = []
    for c, score in hits[:limit]:
        out.append({"source": "okf", "folder": str(folder), "id": c.id, "title": c.title,
                    "description": c.description, "tier": c.tier, "status": c.status,
                    "stale": c.stale, "warning": c.warning(), "score": score})
    return out


def search_mcp(servers: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """Ask each listed MCP server's search-like tool, if it has one."""
    out = []
    try:
        from openjarvis.mcp.client import MCPClient
        from openjarvis.mcp.transport import StdioTransport
    except Exception:
        return out
    for s in servers:
        if not isinstance(s, dict) or not s.get("command"):
            continue
        client = MCPClient(StdioTransport(list(s["command"]), response_timeout=30))
        try:
            client.initialize()
            tools = client.list_tools()
            tool = next((t for t in tools if re.search(r"search|query|find", t.name, re.I)), None)
            if tool is None:
                continue
            props = list((tool.parameters or {}).get("properties", {}) or {"query": {}})
            res = client.call_tool(tool.name, {props[0]: query})
            for item in res.get("content", [])[:5]:
                if item.get("type") == "text":
                    out.append({"source": "mcp", "server": s.get("name"), "tool": tool.name,
                                "snippet": item["text"][:500]})
        except Exception:
            continue
        finally:
            try:
                client.close()
            except Exception:
                pass
    return out


def search_openjarvis_memory(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Facts OpenJarvis's automatic memory has stored (trusted ones only)."""
    try:
        from openjarvis.core.config import load_config
        from openjarvis.memory.store import load_configured_facts
    except Exception:
        return []
    try:
        facts = load_configured_facts(load_config())
    except Exception:
        return []
    terms = _terms(query)
    scored = []
    for f in facts:
        if not f.trusted_for_recall:
            continue
        s = sum(f.text.lower().count(t) for t in terms)
        if s:
            scored.append((s, f.text))
    scored.sort(reverse=True)
    return [{"source": "openjarvis-memory", "snippet": t} for _, t in scored[:limit]]


def search_all(profile: Mapping[str, Any], query: str, limit: int = 5) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    folders = [paths.brain_dir(profile)] + [Path(str(f)).expanduser()
                                            for f in as_list(get(profile, "knowledge.okf_folders"))]
    for f in folders:
        if Path(f).is_dir():
            hits += search_okf(Path(f), query, limit)
    if as_list(get(profile, "knowledge.fts_folders")):
        from tau.knowledge.fts import FTSIndex
        hits += FTSIndex().search(query, limit)
    hits += search_mcp(as_list(get(profile, "knowledge.mcp_servers")), query)
    if get(profile, "knowledge.use_openjarvis_memory", False):
        hits += search_openjarvis_memory(query, limit)
    return hits
