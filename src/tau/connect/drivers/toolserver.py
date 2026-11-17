"""Driver for connecting to and executing tools on MCP tool servers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional

from tau.connect import places, switches
from tau.connect.ctx import LookContext
from tau.connect.recipes import load
from tau.connect.store import LookStore

MAX_TEXT = 4000
_SECRET_NAME_PATTERN = re.compile(r"key|token|secret|password|passwd|auth", re.IGNORECASE)


def _extract_spec_from_dict(spec: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    cmd = spec.get("command")
    if isinstance(cmd, str) and cmd:
        raw_args = spec.get("args")
        if isinstance(raw_args, list) and all(isinstance(a, str) for a in raw_args):
            args_list = list(raw_args)
        else:
            args_list = []
        raw_env = spec.get("env")
        if isinstance(raw_env, dict):
            env_dict = {str(k): str(v) for k, v in raw_env.items() if isinstance(k, str) and isinstance(v, str)}
        else:
            env_dict = {}
        return {
            "transport": "command",
            "command": [cmd, *args_list],
            "env": env_dict,
        }

    url = spec.get("url")
    if not (isinstance(url, str) and url):
        url = spec.get("serverUrl")
    if not (isinstance(url, str) and url):
        url = spec.get("httpUrl")

    if isinstance(url, str) and url:
        token = None
        headers = spec.get("headers")
        if isinstance(headers, dict):
            auth = headers.get("Authorization")
            if isinstance(auth, str) and auth.startswith("Bearer "):
                token = auth[len("Bearer ") :]
        return {
            "transport": "address",
            "url": url,
            "token": token,
        }

    return None


def _find_toml_spec(text: str, keys: list[str], target_name: str) -> Optional[Dict[str, Any]]:
    current_server: str | None = None
    in_env_for: str | None = None
    server_data: dict[str, dict[str, Any]] = {}

    for line in text.splitlines():
        line_str = line.strip()
        if not line_str or line_str.startswith("#"):
            continue

        if line_str.startswith("["):
            current_server = None
            in_env_for = None
            for k in keys:
                m_env = re.match(rf"^\[{re.escape(k)}\.(.+)\.env\]$", line_str)
                if m_env:
                    raw = m_env.group(1).strip()
                    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
                        raw = raw[1:-1].strip()
                    in_env_for = raw
                    if raw not in server_data:
                        server_data[raw] = {"command": "", "args": [], "env": {}, "url": ""}
                    break

                m_srv = re.match(rf"^\[{re.escape(k)}\.(.+)\]$", line_str)
                if m_srv:
                    raw = m_srv.group(1).strip()
                    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
                        raw = raw[1:-1].strip()
                    if "." in raw:
                        break
                    current_server = raw
                    if raw not in server_data:
                        server_data[raw] = {"command": "", "args": [], "env": {}, "url": ""}
                    break
        else:
            if current_server is not None:
                m_cmd = re.match(r'^command\s*=\s*["\'](.*?)["\']', line_str)
                if m_cmd:
                    server_data[current_server]["command"] = m_cmd.group(1)
                else:
                    m_args = re.match(r"^args\s*=\s*(\[.*\])", line_str)
                    if m_args:
                        try:
                            parsed_args = json.loads(m_args.group(1))
                            if isinstance(parsed_args, list):
                                server_data[current_server]["args"] = parsed_args
                            else:
                                server_data[current_server]["args"] = []
                        except Exception:
                            server_data[current_server]["args"] = []
                    else:
                        m_url = re.match(r'^(?:url|serverUrl|httpUrl)\s*=\s*["\'](.*?)["\']', line_str)
                        if m_url:
                            server_data[current_server]["url"] = m_url.group(1)
            elif in_env_for is not None:
                m_env_var = re.match(r'^([A-Za-z0-9_]+)\s*=\s*["\'](.*?)["\']', line_str)
                if m_env_var:
                    server_data[in_env_for]["env"][m_env_var.group(1)] = m_env_var.group(2)

    for sname, sdata in server_data.items():
        if sname == target_name or sname.strip() == target_name:
            return _extract_spec_from_dict(sdata)

    return None


def find_start_spec(ctx: LookContext, conn: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Determine the start or reach specification for a saved connection."""
    try:
        kind = conn.get("kind")
        if kind == "Tool server set up for an assistant":
            detail = conn.get("detail")
            if not isinstance(detail, dict):
                return None
            listed_in = detail.get("listed_in")
            if not isinstance(listed_in, list):
                return None
            target_name = conn.get("name")
            if not isinstance(target_name, str) or not target_name:
                return None

            recipes = load("agent_settings")
            if not isinstance(recipes, list):
                return None

            home = ctx.env("USERPROFILE") if ctx.system == "Windows" and ctx.env("USERPROFILE") else ctx.env("HOME")
            appdata = ctx.env("APPDATA")
            localappdata = ctx.env("LOCALAPPDATA")
            replacements = {
                "{HOME}": home,
                "{APPDATA}": appdata,
                "{LOCALAPPDATA}": localappdata,
            }

            recipes_by_label: dict[str, dict[str, Any]] = {}
            for entry in recipes:
                if isinstance(entry, dict) and "label" in entry and entry["label"] not in recipes_by_label:
                    recipes_by_label[entry["label"]] = entry

            for prog_label in listed_in:
                entry = recipes_by_label.get(prog_label)
                if not entry:
                    continue
                paths_map = entry.get("paths", {})
                if not isinstance(paths_map, dict):
                    continue
                templates = paths_map.get(ctx.system, [])
                if not isinstance(templates, list):
                    continue
                keys = entry.get("keys", [])
                if not isinstance(keys, list):
                    keys = []
                fmt = entry.get("format", "")

                for template in templates:
                    if not isinstance(template, str) or not template:
                        continue
                    skip = False
                    resolved = template
                    for placeholder, val in replacements.items():
                        if placeholder in resolved:
                            if not val:
                                skip = True
                                break
                            resolved = resolved.replace(placeholder, val)
                    if skip:
                        continue
                    if ctx.system == "Windows":
                        resolved = resolved.replace("/", "\\")

                    text = ctx.read_text(resolved, limit=2000000)
                    if not text:
                        continue

                    if fmt == "json":
                        try:
                            data = json.loads(text)
                        except Exception:
                            continue
                        if not isinstance(data, dict):
                            continue
                        for k in keys:
                            servers_dict = data.get(k)
                            if isinstance(servers_dict, dict):
                                for raw_sname, raw_spec in servers_dict.items():
                                    if raw_sname == target_name or (
                                        isinstance(raw_sname, str) and raw_sname.strip() == target_name
                                    ):
                                        spec = raw_spec if isinstance(raw_spec, dict) else {}
                                        extracted = _extract_spec_from_dict(spec)
                                        if extracted is not None:
                                            return extracted
                    elif fmt == "toml":
                        extracted = _find_toml_spec(text, keys, target_name)
                        if extracted is not None:
                            return extracted

            return None

        if kind == "Assistant that can take on work":
            detail = conn.get("detail")
            if not isinstance(detail, dict):
                return None
            worker_cmd = detail.get("command")
            if not isinstance(worker_cmd, str) or not worker_cmd:
                return None

            workers = load("workers")
            if not isinstance(workers, list):
                return None

            target_entry = None
            for entry in workers:
                if isinstance(entry, dict) and entry.get("command") == worker_cmd:
                    target_entry = entry
                    break

            if target_entry is None:
                return None

            tool_server = target_entry.get("tool_server")
            if not isinstance(tool_server, list) or not tool_server or not all(isinstance(x, str) for x in tool_server):
                return None

            place = conn.get("place")
            if isinstance(place, str) and place.strip():
                cmd = [places.ENTER, "-d", place.strip(), "-e", "bash", "-ic", " ".join([worker_cmd, *tool_server])]
            else:
                cmd = [worker_cmd, *tool_server]

            return {
                "transport": "command",
                "command": cmd,
                "env": {},
            }

        return None
    except Exception:
        return None


def _stdio_transport(command: List[str], env: Mapping[str, str], timeout: float) -> Any:
    """Create a StdioTransport instance running with the supplied environment."""
    from openjarvis.mcp.transport import StdioTransport

    extra_env = {str(k): str(v) for k, v in env.items()}

    class _EnvStdio(StdioTransport):
        def _start(self) -> None:
            cmd = list(self._command)
            if cmd:
                resolved = shutil.which(cmd[0])
                if resolved:
                    cmd[0] = resolved
            self._command = cmd

            popen_kwargs: dict[str, Any] = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "env": {**os.environ, **extra_env},
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            self._process = subprocess.Popen(self._command, **popen_kwargs)
            self._reader_thread = threading.Thread(
                target=self._read_stdout,
                name="openjarvis-mcp-stdout",
                daemon=True,
            )
            self._reader_thread.start()
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(self._process,),
                name="openjarvis-mcp-stderr",
                daemon=True,
            )
            self._stderr_thread.start()

    return _EnvStdio(command, response_timeout=timeout)


def open_client(spec: Mapping[str, Any], timeout: float = 60.0) -> Any:
    """Connect to a tool server and return an initialized client."""
    from openjarvis.mcp.client import MCPClient
    from openjarvis.mcp.transport import StreamableHTTPTransport

    transport_type = spec.get("transport")
    transport = None
    client = None
    try:
        if transport_type == "command":
            cmd = spec.get("command", [])
            env = spec.get("env") or {}
            transport = _stdio_transport(cmd, env, timeout)
        elif transport_type == "address":
            url = spec.get("url", "")
            token = spec.get("token")
            transport = StreamableHTTPTransport(url, token=token, request_timeout=timeout)
        else:
            raise ValueError(f"Unknown transport: {transport_type}")

        client = MCPClient(transport)
        client.initialize()
        return client
    except Exception:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        elif transport is not None and hasattr(transport, "close"):
            try:
                transport.close()
            except Exception:
                pass
        raise


def _tool_rows(tools: Any) -> List[Dict[str, Any]]:
    """Convert raw tool objects into normalized summary dictionaries."""
    rows: list[dict[str, Any]] = []
    if not tools:
        return rows

    for t in tools:
        if hasattr(t, "name"):
            name = str(t.name or "")
        elif isinstance(t, dict):
            name = str(t.get("name") or "")
        else:
            name = ""

        if hasattr(t, "description"):
            desc = str(t.description or "")[:300]
        elif isinstance(t, dict):
            desc = str(t.get("description") or "")[:300]
        else:
            desc = ""

        params_obj = getattr(t, "parameters", None)
        if params_obj is None and isinstance(t, dict):
            params_obj = t.get("parameters") or t.get("inputSchema")

        params: list[str] = []
        if isinstance(params_obj, dict):
            props = params_obj.get("properties")
            if isinstance(props, dict):
                params = sorted(str(k) for k in props.keys())

        ann = getattr(t, "annotations", None)
        if ann is None and isinstance(t, dict):
            ann = t.get("annotations")
        read_only = False
        if isinstance(ann, dict) and ann.get("readOnlyHint") is True:
            read_only = True

        rows.append({
            "name": name,
            "description": desc,
            "params": params,
            "read_only": read_only,
        })

    rows.sort(key=lambda r: str(r["name"]))
    return rows[:200]


def list_tools(
    store: LookStore,
    machine: str,
    connection_id: str,
    ctx: LookContext,
    opener: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Inspect and report the available tools for an active connection."""
    conn = switches.find(store, machine, connection_id)
    if conn is None:
        return {"ok": False, "say": "I can't find that connection any more. Look again, then try once more."}

    if not switches.allowed(store, machine, connection_id, "sense"):
        return {"ok": False, "say": "That connection is switched off. Switch it on first."}

    spec = find_start_spec(ctx, conn)
    if spec is None:
        return {
            "ok": False,
            "say": "TAU couldn't work out how to start it. The program that listed it may have changed its settings.",
        }

    client = None
    try:
        open_fn = opener or open_client
        client = open_fn(spec)
        tools = client.list_tools()
    except Exception:
        return {"ok": False, "say": "It didn't answer. It may need to be started, or it may need a sign-in."}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    return {"ok": True, "tools": _tool_rows(tools)}


def _args_summary(args: Optional[Mapping[str, Any]]) -> str:
    """Format argument dictionary for human confirmation, hiding sensitive values."""
    if not args:
        return "nothing"

    pairs: list[str] = []
    for k, v in args.items():
        name_str = str(k)
        if _SECRET_NAME_PATTERN.search(name_str):
            val_str = "(hidden)"
        else:
            val_str = str(v)[:80]
        pairs.append(f"{name_str}: {val_str}")
        if len(pairs) == 12:
            break

    if not pairs:
        return "nothing"
    return "; ".join(pairs)


def call_tool(
    store: LookStore,
    machine: str,
    connection_id: str,
    tool: str,
    args: Optional[Mapping[str, Any]],
    ctx: LookContext,
    ask: Callable[[str], bool],
    actionlog: Any = None,
    who: str = "you",
    opener: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Execute a tool on a connected server, observing confirmation and recording receipts."""
    conn = switches.find(store, machine, connection_id)
    if conn is None:
        return {"ok": False, "say": "I can't find that connection any more. Look again, then try once more."}

    setting = conn.get("setting")
    if not switches.allowed(store, machine, connection_id, "suggest"):
        if setting == "sense":
            return {"ok": False, "say": "TAU may look at that connection but not use it. Raise its level first."}
        return {"ok": False, "say": "That connection is switched off. Switch it on first."}

    if not isinstance(tool, str) or not tool or (args is not None and not isinstance(args, Mapping)):
        return {"ok": False, "say": "I need the name of one of its tools."}

    conn_name = str(conn.get("name", ""))

    if setting == "suggest":
        question = f"TAU wants to use {tool} on {conn_name} with {_args_summary(args)}. Go ahead?"
        if ask(question) is not True:
            return {"ok": False, "say": "Okay, I didn't use it."}

    spec = find_start_spec(ctx, conn)
    if spec is None:
        return {
            "ok": False,
            "say": "TAU couldn't work out how to start it. The program that listed it may have changed its settings.",
        }

    def _write_receipt(what: str) -> None:
        if actionlog is not None:
            receipt_who = who if setting == "suggest" else "TAU, at the level you set"
            try:
                actionlog.receipt(what, receipt_who, machine=machine, organ="connections")
            except Exception:
                pass

    client = None
    try:
        open_fn = opener or open_client
        client = open_fn(spec)
        result = client.call_tool(tool, dict(args or {}))
    except Exception:
        _write_receipt(f"Tried {tool} on {conn_name}; it did not answer")
        return {"ok": False, "say": "It didn't answer, so I can't say whether it was done. Check it yourself."}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    raw_content = result.get("content", []) if isinstance(result, dict) else []
    text_parts: list[str] = []
    if isinstance(raw_content, list):
        for item in raw_content:
            if isinstance(item, dict) and item.get("type") == "text":
                val = item.get("text")
                if isinstance(val, str):
                    text_parts.append(val)
    text = "\n".join(text_parts)[:MAX_TEXT]

    if isinstance(result, dict) and result.get("isError"):
        _write_receipt(f"Used {tool} on {conn_name}; it reported a problem")
        return {"ok": False, "say": "The tool reported a problem.", "text": text}

    _write_receipt(f"Used {tool} on {conn_name}")
    return {"ok": True, "say": f"Done. {conn_name} ran {tool}.", "text": text}
