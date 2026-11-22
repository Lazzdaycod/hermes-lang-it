"""Hermes plugin: gpu-monitor

Chip nella status bar di Hermes Desktop con utilizzo GPU e VRAM dedicata,
letti dai contatori nativi di Windows (pdh.dll, tramite ctypes). Nessun
processo esterno, nessuna porta, nessuna dipendenza aggiuntiva.

Il tool ``get_gpu_usage`` espone la stessa lettura all'agente.
"""

from __future__ import annotations

import json

try:
    from .gpu_probe import read_gpu
except ImportError:
    from gpu_probe import read_gpu

_TOOL_DESCRIPTION = (
    "Live GPU utilization percentage and dedicated VRAM usage from Windows "
    "performance counters, plus the busiest GPU processes."
)


def register(ctx) -> None:
    """Registra il tool ``get_gpu_usage`` (richiesto dal loader Hermes)."""

    def _get_gpu_usage(args: dict, **_kw) -> str:
        return json.dumps(read_gpu())

    ctx.register_tool(
        name="get_gpu_usage",
        toolset="gpu-monitor",
        schema={
            "name": "get_gpu_usage",
            "description": _TOOL_DESCRIPTION,
            "parameters": {"type": "object", "properties": {}},
        },
        handler=_get_gpu_usage,
        description=_TOOL_DESCRIPTION,
        emoji="🎮",
    )
