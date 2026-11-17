"""Context providing machine access primitives for finders."""

from __future__ import annotations

import http.client
import os
import platform
import subprocess
import sys
from typing import Any, Callable, Dict, Mapping, Optional

LOOPBACK = "127.0.0.1"


class WalkRefused(RuntimeError):
    """Raised when directory traversal limits are exceeded."""


class LookContext:
    """Execution context exposing restricted machine inspection methods."""

    def __init__(
        self,
        machine: str,
        run: Optional[Callable[..., str]] = None,
        read_text: Optional[Callable[..., str]] = None,
        list_dir: Optional[Callable[..., list[str]]] = None,
        http_get: Optional[Callable[..., tuple[int, dict[str, str], str]]] = None,
        registry_read: Optional[Callable[..., dict[str, Any]]] = None,
        max_dirs: int = 200,
        snapshot: Optional[Dict[str, Any]] = None,
        environ: Optional[Mapping[str, str]] = None,
        system: Optional[str] = None,
    ) -> None:
        self.machine = machine
        self.max_dirs = max_dirs
        self.snapshot = dict(snapshot or {})
        self.environ = dict(os.environ if environ is None else environ)
        self.system = system or platform.system()
        self._depth: dict[str, int] = {}
        self.dirs_listed = 0
        self._custom_run = run
        self._custom_read_text = read_text
        self._custom_list_dir = list_dir
        self._custom_http_get = http_get
        self._custom_registry_read = registry_read

    def env(self, name: str, default: str = "") -> str:
        """Return environment variable value, case-insensitive on Windows."""
        if self.system == "Windows":
            target = name.lower()
            for k, v in self.environ.items():
                if k.lower() == target:
                    return v
            return default
        return self.environ.get(name, default)

    def list_dir(self, path: Any, start: bool = False) -> list[str]:
        """Return directory entry names without recursing deeper than allowed."""
        norm_path = os.path.normcase(os.path.abspath(str(path)))
        if start:
            depth = 0
        else:
            parent = os.path.dirname(norm_path)
            depth = self._depth[parent] + 1 if parent in self._depth else 0
            if depth > 1:
                raise WalkRefused(f"Depth limit exceeded for folder: {path}")
        if self.dirs_listed >= self.max_dirs:
            raise WalkRefused(f"Directory listing limit reached: {self.max_dirs}")
        self._depth[norm_path] = depth
        self.dirs_listed += 1

        if self._custom_list_dir is not None:
            return self._custom_list_dir(path)
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []

    def read_text(self, path: Any, limit: int = 200000) -> str:
        """Return the start of a text file, or an empty string on error."""
        if self._custom_read_text is not None:
            try:
                return self._custom_read_text(path, limit=limit)
            except TypeError:
                return self._custom_read_text(path)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(limit)
        except OSError:
            return ""

    def run(self, cmd: list[str], timeout: float = 10.0) -> str:
        """Return standard output of a command, or empty string on failure."""
        if not isinstance(cmd, list):
            raise TypeError("cmd must be a list of strings")
        if self._custom_run is not None:
            try:
                return self._custom_run(cmd, timeout=timeout)
            except TypeError:
                return self._custom_run(cmd)

        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                creationflags=flags,
            )
            if proc.returncode != 0:
                return ""
            return proc.stdout
        except Exception:
            return ""

    def http_get(
        self,
        port: int,
        path: str = "/",
        timeout: float = 1.0,
    ) -> tuple[int, dict[str, str], str]:
        """Perform a single GET request against the local loopback address."""
        if not isinstance(port, int) or isinstance(port, bool) or port < 1 or port > 65535:
            raise ValueError(f"Port must be an integer between 1 and 65535, got: {port}")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError(f"Path must start with '/', got: {path}")

        if self._custom_http_get is not None:
            try:
                return self._custom_http_get(port, path=path, timeout=timeout)
            except TypeError:
                return self._custom_http_get(port, path, timeout)

        conn = None
        try:
            conn = http.client.HTTPConnection(LOOPBACK, port, timeout=timeout)
            conn.request("GET", path)
            resp = conn.getresponse()
            status = int(resp.status)
            headers = {str(k).lower(): str(v) for k, v in resp.getheaders()}
            raw = resp.read(65536)
            body = raw.decode("utf-8", errors="replace")[:65536]
            return (status, headers, body)
        except Exception:
            return (0, {}, "")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def registry_read(self, hive: str, key: str, max_subkeys: int = 5000) -> dict[str, Any]:
        """Read subkeys and values for a registry key on Windows."""
        if self._custom_registry_read is not None:
            data = self._custom_registry_read(hive, key)
            if isinstance(data, dict) and "subkeys" in data and isinstance(data["subkeys"], list):
                if len(data["subkeys"]) > max_subkeys:
                    clipped = dict(data)
                    clipped["subkeys"] = data["subkeys"][:max_subkeys]
                    return clipped
            return data

        if sys.platform != "win32":
            return {"subkeys": [], "values": {}}

        try:
            import winreg

            hive_map = {
                "HKLM": winreg.HKEY_LOCAL_MACHINE,
                "HKCU": winreg.HKEY_CURRENT_USER,
                "HKCR": winreg.HKEY_CLASSES_ROOT,
            }
            if hive not in hive_map:
                return {"subkeys": [], "values": {}}

            root = hive_map[hive]
            with winreg.OpenKey(root, key) as reg_key:
                subkeys: list[str] = []
                values: dict[str, Any] = {}
                index = 0
                while index < max_subkeys:
                    try:
                        subkeys.append(winreg.EnumKey(reg_key, index))
                        index += 1
                    except OSError:
                        break

                index = 0
                while True:
                    try:
                        val_name, val_data, _ = winreg.EnumValue(reg_key, index)
                        values[val_name] = val_data
                        index += 1
                    except OSError:
                        break

                return {"subkeys": subkeys, "values": values}
        except Exception:
            return {"subkeys": [], "values": {}}
