"""Hermes plugin: token-rate

Contatore dei token/sec del server llama.cpp locale.

Il router FastAPI (``dashboard/plugin_api.py``) e' montato da Hermes su
``/api/plugins/token-rate/`` e legge direttamente ``llama-server.log``:
nessun processo esterno, nessuna porta, nessun watchdog.

Il tool ``get_token_rate`` offre la stessa lettura all'agente.
"""

from __future__ import annotations

import json
from pathlib import Path

# Rende importabile token_rate_log.py sia quando il plugin e' importato come
# pacchetto (loader Hermes) sia quando il router viene caricato standalone.
try:
    from .token_rate_log import read_rates  # type: ignore
except ImportError:  # pragma: no cover - fallback per caricamento standalone
    import sys

    _PLUGIN_DIR = Path(__file__).resolve().parent
    if str(_PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(_PLUGIN_DIR))
    from token_rate_log import read_rates  # noqa: E402

_TOOL_DESCRIPTION = (
    "Real-time output and input tokens-per-second of the local llama.cpp server. "
    "Useful to know how fast the local model is."
)


def register(ctx) -> None:
    """Registra il tool ``get_token_rate`` (richiesto dal loader Hermes)."""

    def _get_token_rate(args: dict, **_kw) -> str:
        return json.dumps(read_rates())

    ctx.register_tool(
        name="get_token_rate",
        toolset="token-rate",
        schema={
            "name": "get_token_rate",
            "description": _TOOL_DESCRIPTION,
            "parameters": {"type": "object", "properties": {}},
        },
        handler=_get_token_rate,
        description=_TOOL_DESCRIPTION,
        emoji="⚡",
    )
