"""Hermes plugin: llama-cpp

Gestione del runtime llama.cpp gestito da Hermes: rileva il backend GPU
(vulkan/cuda/rocm), elenca i modelli GGUF locali e permette di modificare i
preset per-modello (ctx, KV cache, flash-attn, batch, sampling) applicandoli
al server in corso d'opera (GET /models?reload=1).

Il router FastAPI (``dashboard/plugin_api.py``) e' montato da Hermes su
``/api/plugins/llama-cpp/``; nessuna credenziale e' mai esposta al frontend.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent

try:  # package import (preferred; avoids sys.path shadowing)
    from .llama_cpp_core import list_models, status_snapshot  # type: ignore
except ImportError:  # bare module import (no package context)
    if str(_PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(_PLUGIN_DIR))
    from llama_cpp_core import list_models, status_snapshot  # type: ignore

_TOOL_DESCRIPTION = (
    "Status of the Hermes-managed local llama.cpp server: GPU backend "
    "(vulkan/cuda/rocm), running state (port, pid), active model, available "
    "GGUF models with size and state (loaded/unloaded), and current presets "
    "(ctx, KV cache, flash-attn, batch, sampling)."
)


def _llama_server_status(args: dict, **_kw) -> str:
    snapshot = status_snapshot()
    models = list_models()
    return json.dumps({
        "backend": snapshot["backend"],
        "build": snapshot["build"],
        "running": snapshot["running"],
        "port": snapshot["port"],
        "active_model": snapshot["active_model"],
        "models": [
            {"id": m["id"], "size": m["size_human"], "status": m["status"],
             "active": m["active"]}
            for m in models["models"]
        ],
        "presets": snapshot["presets"],
    })


def register(ctx) -> None:
    """Registra i tool del plugin (richiesto dal loader Hermes)."""
    ctx.register_tool(
        name="llama_server_status",
        toolset="llama-cpp",
        schema={
            "name": "llama_server_status",
            "description": _TOOL_DESCRIPTION,
            "parameters": {"type": "object", "properties": {}},
        },
        handler=_llama_server_status,
        description=_TOOL_DESCRIPTION,
        emoji="🦙",
    )
