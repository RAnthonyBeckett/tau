"""One model endpoint, called through OpenJarvis engines when available.

``BrainClient`` wraps ``openjarvis.engine.ollama.OllamaEngine`` or
``openjarvis.engine.openai_compat_engines.OpenAICompatEngine``. Without
OpenJarvis it falls back to a small OpenAI-compatible HTTP call so TAU's core
and tests still run.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class BrainError(RuntimeError):
    pass


@dataclass
class BrainClient:
    engine: str            # "ollama" or anything OpenAI-compatible
    url: str
    model: str
    api_key: Optional[str] = field(default=None, repr=False)
    cloud: bool = False
    timeout: float = 120.0
    calls: int = 0         # counted so tests can prove "zero model calls"

    # -- calls --------------------------------------------------------------
    def chat(self, messages: List[Dict[str, str]], max_tokens: int = 512,
             temperature: float = 0.4) -> str:
        self.calls += 1
        try:
            return self._chat_openjarvis(messages, max_tokens, temperature)
        except ImportError:
            return self._chat_http(messages, max_tokens, temperature)

    def _chat_openjarvis(self, messages, max_tokens, temperature) -> str:
        from openjarvis.core.types import Message, Role

        role_map = {"system": Role.SYSTEM, "user": Role.USER, "assistant": Role.ASSISTANT}
        msgs = [Message(role=role_map[m["role"]], content=m["content"]) for m in messages]
        eng = self._oj_engine()
        try:
            out = eng.generate(msgs, model=self.model, temperature=temperature, max_tokens=max_tokens)
        except Exception as exc:
            raise BrainError(str(exc)) from exc
        finally:
            try:
                eng.close()
            except Exception:
                pass
        return str(out.get("content", ""))

    def _oj_engine(self):
        if self.engine == "ollama":
            from openjarvis.engine.ollama import OllamaEngine

            return OllamaEngine(host=self.url, timeout=self.timeout)
        from openjarvis.engine.openai_compat_engines import (
            OpenAICompatEngine, normalize_openai_base_url)

        return OpenAICompatEngine(host=normalize_openai_base_url(self.url),
                                  api_key=self.api_key, timeout=self.timeout)

    def _chat_http(self, messages, max_tokens, temperature) -> str:
        base = self.url.rstrip("/")
        if self.engine == "ollama":
            url, body = f"{base}/api/chat", {"model": self.model, "messages": messages, "stream": False}
        else:
            if base.endswith("/v1"):
                base = base[:-3]
            url = f"{base}/v1/chat/completions"
            body = {"model": self.model, "messages": messages,
                    "max_tokens": max_tokens, "temperature": temperature}
        data = _post(url, body, self.api_key, self.timeout)
        if self.engine == "ollama":
            return data.get("message", {}).get("content", "")
        return data["choices"][0]["message"]["content"]

    def test_call(self) -> str:
        """One real call. Raises BrainError when the brain does not answer."""
        try:
            reply = self.chat([{"role": "user", "content": "Reply with the single word: ready"}],
                              max_tokens=8, temperature=0)
        except BrainError:
            raise
        except Exception as exc:
            raise BrainError(str(exc)) from exc
        if not reply or not reply.strip():
            raise BrainError("the brain answered with nothing")
        return reply


def _post(url: str, body: Dict[str, Any], api_key: Optional[str], timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise BrainError(str(exc)) from exc


def get_json(url: str, timeout: float = 1.5, api_key: Optional[str] = None) -> Any:
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def post_json(url: str, body: Dict[str, Any], timeout: float = 2.0) -> Any:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())
