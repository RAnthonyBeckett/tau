"""Keep library noise off the user's screen.

Hugging Face, transformers, torch, loguru and native libraries print warnings,
progress bars and notices to stderr. In TAU's processes all of that goes to
TAU's log file instead, and the screen only gets TAU's own plain sentences.
Set ``TAU_DEBUG=1`` to see everything again.

``install()`` runs at the start of every ``tau`` command (except the ones that
run the user's own commands, whose errors the user must see).
"""

from __future__ import annotations

import logging
import os
import sys
import warnings
from pathlib import Path
from typing import Optional

ENV = {
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "HF_HUB_VERBOSITY": "error",
    "TRANSFORMERS_VERBOSITY": "error",
    "TRANSFORMERS_NO_ADVISORY_WARNINGS": "1",
    "TOKENIZERS_PARALLELISM": "false",
    "TQDM_DISABLE": "1",
    "ORT_LOG_SEVERITY_LEVEL": "3",       # onnxruntime: errors only
    "CT2_VERBOSE": "-1",                 # ctranslate2 (faster-whisper): quiet
}

_LOG_FILE: Optional[Path] = None


def log_file() -> Path:
    if _LOG_FILE is not None:
        return _LOG_FILE
    from tau import paths

    return paths.tau_home() / "log" / "tau.log"


def install(path: Optional[Path] = None, redirect_stderr: bool = True) -> Optional[Path]:
    """Send warnings and library output to the log file. Returns the log path."""
    global _LOG_FILE
    if os.environ.get("TAU_DEBUG") == "1":
        return None
    for k, v in ENV.items():
        os.environ.setdefault(k, v)
    if _models_ready():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")   # everything is on disk: no network checks mid-talk
    try:
        target = Path(path) if path else log_file()
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    _LOG_FILE = target

    handler = logging.FileHandler(target, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.captureWarnings(True)              # warnings.warn(...) -> the log, not the screen
    warnings.simplefilter("default")
    for name in ("huggingface_hub", "transformers", "torch", "faster_whisper", "spacy", "phonemizer"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False
        lg.setLevel(logging.WARNING)
    try:
        from loguru import logger

        logger.remove()
        logger.add(str(target), level="WARNING")
    except Exception:
        pass
    if redirect_stderr:
        _redirect_stderr(target)
    return target


def _redirect_stderr(target: Path) -> None:
    """Point stderr (Python's and the process's file descriptor 2) at the log file.

    Native libraries write straight to descriptor 2, which no Python setting
    can catch, so the descriptor itself is redirected.
    """
    try:
        f = open(target, "a", encoding="utf-8", buffering=1)
    except Exception:
        return
    try:
        sys.stderr.flush()
    except Exception:
        pass
    try:
        os.dup2(f.fileno(), 2)
    except Exception:
        pass
    sys.stderr = f


def _models_ready() -> bool:
    try:
        from tau.fetch import marker

        return marker().exists()
    except Exception:
        return False


def plain_failure(exc: BaseException) -> str:
    """One sentence for the screen; the full traceback goes to the log."""
    import traceback

    logging.getLogger("tau").error("unexpected error\n%s",
                                   "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    where = log_file()
    return (f"Something went wrong inside TAU ({type(exc).__name__}). "
            f"The details are in {where}. Running the same thing again often works.")
