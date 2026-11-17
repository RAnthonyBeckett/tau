"""Brain lanes: ``local`` (a model on the lab's own hardware) and ``burst``
(any cloud or router endpoint). Both are optional.

GPU budget rule (only when the ``gpu`` organ may sense):
* If a heavy app named in the profile holds the card, or free VRAM is under
  the profile's floor, TAU uses the burst lane.
* TAU never loads a second local model while one is resident. Switching the
  local model first asks the server to unload (needs ``gpu`` at ``act``).
* With ``act`` it may ask the local server to unload to give the card back.

Nothing runs on a timer: the rule is evaluated once per model call.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from tau.brainclient import BrainClient, BrainError
from tau.profile import as_list, get, load_secret


def _norm_proc(name: str) -> str:
    n = os.path.basename(str(name).replace("\\", "/")).lower()
    return n[:-4] if n.endswith(".exe") else n


def lane_clients(profile: Mapping[str, Any]) -> Dict[str, BrainClient]:
    out: Dict[str, BrainClient] = {}
    loc = get(profile, "brain.local", {}) or {}
    if loc.get("url") and loc.get("model"):
        out["local"] = BrainClient(loc.get("engine") or "openai_compat", loc["url"], loc["model"])
    bur = get(profile, "brain.burst", {}) or {}
    if bur.get("url") and bur.get("model"):
        out["burst"] = BrainClient("openai_compat", bur["url"], bur["model"],
                                   api_key=load_secret(profile, bur.get("api_key_env")),
                                   cloud=bool(bur.get("cloud", True)))
    return out


@dataclass
class LaneChoice:
    lane: Optional[str]
    reason: str


class Lanes:
    def __init__(self, profile: Mapping[str, Any], organs=None,
                 clients: Optional[Dict[str, BrainClient]] = None):
        self.profile = profile
        self.organs = organs
        self.clients = clients if clients is not None else lane_clients(profile)
        self.forced: Optional[str] = None     # set by "use the burst lane" etc.
        self.last_choice: Optional[LaneChoice] = None

    # -- GPU sensing --------------------------------------------------------------
    def _gpu_allowed(self, level: str = "read") -> bool:
        if self.organs is None:
            return False
        if level == "read":
            return self.organs.can_read("gpu")
        return self.organs.level("gpu") == "act"

    def heavy_app_on_card(self) -> Optional[str]:
        from tau.senses.gpu import gpu_processes

        heavy = {_norm_proc(a) for a in as_list(get(self.profile, "brain.gpu.heavy_apps"))}
        if not heavy:
            return None
        for p in gpu_processes():
            if _norm_proc(p["name"]) in heavy:
                return p["name"]
        try:  # graphics apps don't always show in nvidia-smi's compute list
            import psutil
            for p in psutil.process_iter(["name"]):
                if _norm_proc(p.info["name"] or "") in heavy:
                    return p.info["name"]
        except Exception:
            pass
        return None

    def free_vram_gb(self) -> Optional[float]:
        from tau.senses.gpu import read_gpus

        g = read_gpus()
        return round(g[0]["free_mb"] / 1024, 1) if g else None

    # -- choosing -------------------------------------------------------------------
    def choose(self, privacy_local_only: bool = False) -> LaneChoice:
        has_local, has_burst = "local" in self.clients, "burst" in self.clients
        if privacy_local_only:
            has_burst = False
        if self.forced in self.clients and (self.forced != "burst" or has_burst):
            c = LaneChoice(self.forced, "you asked for it")
        elif not has_local and not has_burst:
            c = LaneChoice(None, "no brain is set up")
        elif not has_local:
            c = LaneChoice("burst", "only the burst lane is set up")
        elif not has_burst:
            c = LaneChoice("local", "only the local lane is set up")
        else:
            c = LaneChoice(get(self.profile, "brain.prefer", "local") or "local", "preferred lane")
            if self._gpu_allowed("read"):
                app = self.heavy_app_on_card()
                if app:
                    c = LaneChoice("burst", f"{app} has the GPU")
                    if self._gpu_allowed("act"):
                        request_unload(self.profile)
                else:
                    free = self.free_vram_gb()
                    floor = float(get(self.profile, "brain.gpu.min_free_vram_gb", 0) or 0)
                    if free is not None and free < floor and not self.local_resident():
                        c = LaneChoice("burst", f"only {free} GB of VRAM free")
        self.last_choice = c
        return c

    def local_resident(self) -> List[str]:
        """Models the local server has loaded right now (Ollama /api/ps; others: the configured one)."""
        loc = self.clients.get("local")
        if not loc:
            return []
        if loc.engine == "ollama":
            try:
                with urllib.request.urlopen(loc.url.rstrip("/") + "/api/ps", timeout=1.5) as r:
                    return [m.get("name") for m in json.loads(r.read()).get("models", [])]
            except Exception:
                return []
        return [loc.model]

    # -- calling ------------------------------------------------------------------------
    def chat(self, messages: List[Dict[str, str]], privacy_local_only: bool = False,
             max_tokens: int = 700) -> Tuple[str, str]:
        choice = self.choose(privacy_local_only)
        if choice.lane is None:
            raise BrainError("I don't have a brain connected yet. Run `tau setup brain`.")
        client = self.clients[choice.lane]
        try:
            return client.chat(messages, max_tokens=max_tokens), choice.lane
        except BrainError:
            other = "burst" if choice.lane == "local" else "local"
            if other in self.clients and not (other == "burst" and privacy_local_only):
                return self.clients[other].chat(messages, max_tokens=max_tokens), other
            raise

    def total_calls(self) -> int:
        return sum(c.calls for c in self.clients.values())

    def describe(self) -> str:
        parts = []
        for name, c in self.clients.items():
            parts.append(f"{name}: {c.model}")
        if not parts:
            return "No brain is set up."
        cur = self.last_choice.lane if self.last_choice else None
        return "; ".join(parts) + (f". Last used: {cur}." if cur else ".")

    def switch_model(self, name: str) -> str:
        choices = [m for m in as_list(self.profile.get("models")) if isinstance(m, dict)]
        pick = next((m for m in choices if m.get("name", "").lower() == name.lower()), None)
        if pick is None:
            return f"There's no model called {name} in your profile."
        lane = pick.get("lane", "local")
        if lane not in self.clients:
            return f"The {lane} lane isn't set up."
        client = self.clients[lane]
        if lane == "local":
            resident = [m for m in self.local_resident() if m and m != pick["model"]]
            if resident and client.engine == "ollama":
                if not self._gpu_allowed("act"):
                    return (f"{resident[0]} is still loaded and I won't load a second model. "
                            "I need gpu at act to unload it.")
                request_unload(self.profile, resident[0])
        client.model = pick["model"]
        self.forced = lane
        return f"Switched to {pick['name']} on the {lane} lane."


def request_unload(profile: Mapping[str, Any], model: Optional[str] = None) -> bool:
    """Ask the local model server to free the card. Never kills processes."""
    loc = get(profile, "brain.local", {}) or {}
    url = loc.get("unload_url")
    try:
        if url:
            req = urllib.request.Request(url, data=json.dumps({"model": model or loc.get("model")}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        elif loc.get("engine") == "ollama" and loc.get("url"):
            req = urllib.request.Request(loc["url"].rstrip("/") + "/api/generate",
                                         data=json.dumps({"model": model or loc.get("model"),
                                                          "keep_alive": 0}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        else:
            return False
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


# -- state header ------------------------------------------------------------------------
def state_header(profile: Mapping[str, Any], organs, extra: Optional[Dict[str, Any]] = None) -> str:
    """About 60 tokens of JSON about the lab right now, from allowed organs only."""
    h: Dict[str, Any] = {"host": get(profile, "machine.name") or socket.gethostname()}
    if organs is not None:
        if organs.can_read("window"):
            try:
                from tau.senses.window import focused_window
                w = focused_window()
                if w.get("title"):
                    h["win"] = (w.get("process") or "") + ":" + w["title"][:40]
            except Exception:
                pass
        if organs.can_read("gpu"):
            try:
                from tau.senses.gpu import read_gpus
                g = read_gpus()
                if g:
                    h["vram"] = f"{round(g[0]['free_mb'] / 1024)}/{round(g[0]['total_mb'] / 1024)}G free"
            except Exception:
                pass
        if organs.can_read("heartbeat"):
            try:
                from tau.senses.heartbeat import check_all
                res = check_all(profile)
                h["up"] = [n for n, ok in res.items() if ok][:6]
                down = [n for n, ok in res.items() if ok is False]
                if down:
                    h["down"] = down[:4]
            except Exception:
                pass
        if organs.can_read("cmdfail"):
            try:
                from tau.senses.cmdfail import last_failure
                f = last_failure()
                if f:
                    h["err"] = f"{f['command'][:40]} -> {f['code']}"
            except Exception:
                pass
    if extra:
        h.update({k: v for k, v in extra.items() if v})
    return json.dumps(h, separators=(",", ":"))
