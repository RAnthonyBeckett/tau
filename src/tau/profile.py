"""Load, fill and check the TAU profile.

The profile is plain YAML. The shipped template carries ``{{NAME}}``
placeholders; ``fill_template`` swaps each one for a value (JSON is valid
YAML, so values are written with ``json.dumps`` and the template's comments
survive). ``check_profile`` fails while any placeholder is left.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import yaml

from tau import paths

PLACEHOLDER = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\}\}")
_QUOTED = re.compile(r"([\"'])\{\{\s*([A-Z0-9_]+)\s*\}\}\1")

LEVELS = ("off", "sense", "suggest", "act")


class ProfileError(ValueError):
    """The profile cannot be used as it stands."""


def template_text() -> str:
    return paths.example_profile().read_text(encoding="utf-8")


def placeholders_in_text(text: str) -> List[str]:
    """Placeholders still present, ignoring ones inside YAML comments."""
    found: List[str] = []
    for line in text.splitlines():
        code = _strip_comment(line)
        for m in PLACEHOLDER.finditer(code):
            if m.group(1) not in found:
                found.append(m.group(1))
    return found


def _strip_comment(line: str) -> str:
    in_s = in_d = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def fill_template(values: Mapping[str, Any], text: Optional[str] = None,
                  skip_missing: bool = False) -> str:
    """Replace placeholders with values.

    A quoted placeholder (``"{{X}}"``) becomes the JSON form of the value, so
    lists, numbers and null land as real YAML types. With ``skip_missing`` any
    placeholder without a value becomes ``null`` (the feature stays off).
    """
    text = template_text() if text is None else text

    def quoted(m: re.Match) -> str:
        key = m.group(2)
        if key in values:
            return json.dumps(values[key])
        if skip_missing:
            return "null"
        return m.group(0)

    text = _QUOTED.sub(quoted, text)

    def bare(m: re.Match) -> str:
        key = m.group(1)
        if key in values:
            v = values[key]
            return "" if v is None else str(v)
        return "" if skip_missing else m.group(0)

    out_lines = []
    for line in text.splitlines(keepends=True):
        code = _strip_comment(line.rstrip("\n"))
        if PLACEHOLDER.search(code):
            line = PLACEHOLDER.sub(bare, code) + line[len(code):]
        out_lines.append(line)
    return "".join(out_lines)


def find_placeholders(data: Any, path: str = "") -> List[str]:
    """Every location in loaded data whose string value still has ``{{...}}``."""
    hits: List[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            hits += find_placeholders(v, f"{path}.{k}" if path else str(k))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            hits += find_placeholders(v, f"{path}[{i}]")
    elif isinstance(data, str) and PLACEHOLDER.search(data):
        hits.append(f"{path}: {data}")
    return hits


def check_profile(data: Mapping[str, Any]) -> List[str]:
    """Problems that stop TAU from starting. Empty list means fine."""
    problems = [f"unfilled placeholder at {h}" for h in find_placeholders(data)]
    organs = data.get("organs") or {}
    for name, level in organs.items():
        lv = level.get("default") if isinstance(level, dict) else level
        if lv not in LEVELS:
            problems.append(f"organ '{name}' has unknown level {lv!r}")
        if isinstance(level, dict):
            for m, mlv in (level.get("machines") or {}).items():
                if mlv not in LEVELS:
                    problems.append(f"organ '{name}' on '{m}' has unknown level {mlv!r}")
    if data.get("secrets") and isinstance(data["secrets"], dict):
        for k in data["secrets"]:
            if k not in ("env_file",):
                problems.append(f"secrets.{k}: secret values never go in the profile")
    return problems


def load_profile(path: Optional[Path] = None, check: bool = True) -> Dict[str, Any]:
    path = Path(path) if path else paths.profile_path()
    if not path.exists():
        raise ProfileError(f"no profile at {path}; run `tau setup`")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if check:
        problems = check_profile(data)
        if problems:
            raise ProfileError("profile not ready:\n  " + "\n  ".join(problems))
    return data


def save_profile(data: Mapping[str, Any], path: Optional[Path] = None) -> Path:
    path = Path(path) if path else paths.profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(data), sort_keys=False), encoding="utf-8")
    return path


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str) and "," in value:
        return [v.strip() for v in value.split(",") if v.strip()]
    return [value]


def get(data: Mapping[str, Any], dotted: str, default: Any = None) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return default
        cur = cur[part]
    return default if cur is None else cur


def assistant_name(data: Mapping[str, Any]) -> str:
    return str(get(data, "tau.name", "TAU"))


def wake_word(data: Mapping[str, Any]) -> str:
    return str(get(data, "tau.wake_word", assistant_name(data))).lower()


def load_secret(data: Mapping[str, Any], env_name: Optional[str]) -> Optional[str]:
    """Read a secret by env var name, falling back to the profile's .env file.

    The value is returned to the caller only; nothing here logs or stores it.
    """
    if not env_name:
        return None
    if os.environ.get(env_name):
        return os.environ[env_name]
    env_file = get(data, "secrets.env_file")
    if env_file and Path(env_file).expanduser().exists():
        for line in Path(env_file).expanduser().read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip().removeprefix("export ").strip() == env_name:
                return v.strip().strip('"').strip("'")
    return None


def iter_services(data: Mapping[str, Any]) -> Iterable[Dict[str, Any]]:
    for s in as_list(data.get("services")):
        if isinstance(s, dict) and s.get("name"):
            yield s
