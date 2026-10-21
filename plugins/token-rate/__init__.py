"""Hermes plugin: token-rate

Contatore dei token/sec del server llama.cpp locale.

Il router FastAPI (``dashboard/plugin_api.py``) e' montato da Hermes su
``/api/plugins/token-rate/`` e legge direttamente ``llama-server.log``:
nessun processo esterno, nessuna porta, nessun watchdog.

Il tool ``get_token_rate`` offre la stessa lettura all'agente.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Rende importabile token_rate_log.py sia quando il plugin e' importato come
# pacchetto (loader Hermes) sia quando il router viene caricato standalone.
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from token_rate_log import read_rates  # noqa: E402

_TOOL_DESCRIPTION = (
    "Token/sec in tempo reale (output e input) del server llama.cpp locale. "
    "Utile per sapere quanto e' veloce il modello locale."
)


def register(ctx) -> None:
    """Registra il tool ``get_token_rate`` (richiesto dal loader Hermes)."""

    def _get_token_rate() -> dict:
        return read_rates()

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
