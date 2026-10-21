"""Log parsing condiviso dal plugin token-rate.

Legge ``$HERMES_HOME/logs/llama-server.log`` ed estrae:
  * t/s di output  -> righe ``tg = 20.6 t/s``
  * t/s di input   -> righe ``prompt eval time = ... 159.67 tokens per second``

Importato sia da ``__init__.py`` (tool agente) sia da
``dashboard/plugin_api.py`` (router FastAPI).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_TG_RE = re.compile(r"tg\s*=\s*([\d.]+)\s*t/s")
_IN_RE = re.compile(r"(?:prompt eval time|prompt processing)[^\r\n]*?([\d.]+)\s*tokens per second")
_N_LINES = 200


def hermes_home() -> Path:
    """HERMES_HOME, con fallback su hermes_constants e poi ~/.hermes."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def log_candidates() -> list[Path]:
    home = hermes_home()
    return [
        home / "logs" / "llama-server.log",
        home / "logs" / "llama_server.log",
        Path.home() / ".hermes" / "logs" / "llama-server.log",
    ]


def find_log_path() -> Path | None:
    for candidate in log_candidates():
        if candidate.is_file():
            return candidate
    return None


def _read_last_lines(path: Path, n: int = _N_LINES) -> list[str]:
    """Ultime *n* righe di *path* (lettura a ritroso, ok su file grandi)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            buf = b""
            pos = size
            while pos > 0 and buf.count(b"\n") < n:
                step = min(8192, pos)
                pos -= step
                fh.seek(pos)
                buf = fh.read(step) + buf
        return buf.decode("utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def read_rates(n: int = _N_LINES) -> dict:
    """Ultimi t/s di output (``tps``) e di input (``input_tps``) nel log."""
    path = find_log_path()
    if path is None:
        return {"tps": None, "input_tps": None, "source": None}
    tps = input_tps = None
    for line in _read_last_lines(path, n):
        m = _TG_RE.search(line)
        if m:
            try:
                tps = float(m.group(1))
            except ValueError:
                pass
        m = _IN_RE.search(line)
        if m:
            try:
                input_tps = float(m.group(1))
            except ValueError:
                pass
    return {"tps": tps, "input_tps": input_tps, "source": str(path)}
