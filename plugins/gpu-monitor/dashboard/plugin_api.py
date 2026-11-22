"""gpu-monitor — backend API routes (router FastAPI montato da Hermes).

Sotto ``/api/plugins/gpu-monitor/``:

    GET /metrics  utilizzo GPU + VRAM dedicata + processi piu' attivi
    GET /status   alias di /metrics

La lettura usa pdh.dll in-process: nessun subprocess, nessun watchdog.
Sync def: gira nel threadpool di FastAPI e non blocca l'event loop.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from gpu_probe import read_gpu  # noqa: E402

router = APIRouter()

# Cache di sola ottimizzazione (1.5s): non e' stato funzionale, quindi e'
# innocua se worker diversi hanno cache diverse. Evita doppie letture PDH
# quando chip e palette chiedono lo snapshot nello stesso istante.
_cache: dict = {"at": 0.0, "data": None}
_TTL_S = 1.5


def _snapshot() -> dict:
    now = time.monotonic()
    if _cache["data"] is not None and now - _cache["at"] < _TTL_S:
        return _cache["data"]
    data = read_gpu()
    _cache["at"] = now
    _cache["data"] = data
    return data


@router.get("/status")
def get_status():
    return JSONResponse(_snapshot())


@router.get("/metrics")
def get_metrics():
    return JSONResponse(_snapshot())
