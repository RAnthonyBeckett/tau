"""Wire TAU into OpenJarvis.

* Every organ action becomes an OpenJarvis tool named ``tau_<organ>_<action>``
  in ``openjarvis.core.registry.ToolRegistry``. The tool runs through TAU's
  permission check, so an OpenJarvis agent gets exactly the powers the user
  granted TAU and no more.
* ``enable_in_openjarvis_config`` adds those tool names to
  ``[tools] enabled`` in OpenJarvis's own ``config.toml`` (after a yes).

Every OpenJarvis import is guarded: TAU's core still runs without it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from tau.organs import OrganRegistry, Organs

_BOUND: dict = {"organs": None}


def openjarvis_available() -> bool:
    try:
        import openjarvis  # noqa: F401
        return True
    except Exception:
        return False


def bind(organs: Organs) -> None:
    """Point the registered tools at the live ``Organs`` instance."""
    _BOUND["organs"] = organs


def tool_name(organ: str, action: str) -> str:
    return f"tau_{organ}_{action}"


def register_with_openjarvis() -> List[str]:
    """Register every organ action as an OpenJarvis tool. Idempotent."""
    try:
        from openjarvis.core.registry import ToolRegistry
        from openjarvis.core.types import ToolResult
        from openjarvis.tools._stubs import BaseTool, ToolSpec
    except Exception:
        return []

    names: List[str] = []
    for organ in OrganRegistry.all():
        for action, spec in organ.actions.items():
            name = tool_name(organ.name, action)
            names.append(name)
            if ToolRegistry.contains(name):
                continue

            def make(organ_name: str = organ.name, action_name: str = action,
                     aspec: Any = spec, tname: str = name):
                class _TauTool(BaseTool):
                    tool_id = tname
                    is_local = True

                    @property
                    def spec(self) -> ToolSpec:  # type: ignore[override]
                        return ToolSpec(
                            name=tname,
                            description=f"TAU {organ_name}: {aspec.description}",
                            parameters={"type": "object", "properties": {
                                "machine": {"type": "string"},
                                "args": {"type": "object"}}},
                            category="tau",
                            requires_confirmation=aspec.destructive,
                            metadata={"tau_organ": organ_name, "tau_kind": aspec.kind},
                        )

                    def execute(self, **params: Any) -> ToolResult:
                        organs: Optional[Organs] = _BOUND["organs"]
                        if organs is None:
                            return ToolResult(tname, "TAU is not running.", success=False)
                        out = organs.call(organ_name, action_name,
                                          machine=params.get("machine"), via="openjarvis",
                                          **(params.get("args") or {}))
                        return ToolResult(tname, out.message, success=out.ok,
                                          metadata={"status": out.status})

                _TauTool.__name__ = f"TauTool_{organ_name}_{action_name}"
                return _TauTool

            ToolRegistry.register_value(name, make())
    return names


def enable_in_openjarvis_config(names: List[str], config_path: Optional[Path] = None) -> Optional[Path]:
    """Add TAU tool names to OpenJarvis's ``[tools] enabled`` list."""
    try:
        import tomlkit
        from openjarvis.core.paths import get_config_path
    except Exception:
        return None
    path = Path(config_path) if config_path else get_config_path()
    doc = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()
    tools = doc.get("tools")
    if tools is None:
        tools = tomlkit.table()
        doc["tools"] = tools
    enabled = list(tools.get("enabled", []))
    for n in names:
        if n not in enabled:
            enabled.append(n)
    tools["enabled"] = enabled
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


def openjarvis_engine_hosts() -> dict:
    """Engine hosts OpenJarvis already knows (from its config), for detection."""
    try:
        from openjarvis.core.config import load_config

        cfg = load_config()
        eng = cfg.engine
        out = {}
        for key in ("ollama", "llamacpp", "lmstudio", "vllm"):
            try:
                v = getattr(getattr(eng, key), "host")
            except Exception:
                v = None
            if v:
                out[key] = v
        return out
    except Exception:
        return {}
