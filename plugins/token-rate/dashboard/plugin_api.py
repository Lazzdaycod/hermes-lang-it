"""Token-rate — backend API routes (router FastAPI montato da Hermes).

Il router e' montato solo quando il plugin e' in ``plugins.enabled`` e resta
sul localhost del gateway, sotto ``/api/plugins/token-rate/``.

Routes:
    GET  /status   stato del monitor (attivo, fonte log, ultimo aggiornamento)
    GET  /metrics  stato + ultimi t/s output/input
    POST /start    attiva il monitor
    POST /stop     disattiva il monitor

Lo stato "attivo" vive su FILE (``$HERMES_HOME/cache/token-rate-state.json``),
non in memoria: se il backend serve le richieste da piu' worker (o ricarica il
modulo), un flag globale non sarebbe condiviso e /start su un worker non
risulterebbe a /metrics su un altro. La lettura del log e' on-demand, quindi
non serve alcun thread.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

# token_rate_log.py vive una directory sopra (radice del plugin).
_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from token_rate_log import find_log_path, hermes_home, read_rates  # noqa: E402

router = APIRouter()


# ---------------------------------------------------------------------------
# Stato su file
# ---------------------------------------------------------------------------

def _state_path() -> Path:
    return hermes_home() / "cache" / "token-rate-state.json"


def _read_enabled() -> bool:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return bool(data.get("enabled"))
    except Exception:
        return False


def _write_enabled(value: bool) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"enabled": bool(value)}), encoding="utf-8")
    except OSError:
        pass


def _snapshot(with_rates: bool) -> dict:
    log = find_log_path()
    enabled = _read_enabled()
    rates = {"tps": None, "input_tps": None, "source": None}
    if with_rates and enabled:
        try:
            rates = read_rates()
        except Exception:
            pass
    return {
        "enabled": enabled,
        "tps": rates.get("tps"),
        "input_tps": rates.get("input_tps"),
        "source": rates.get("source"),
        "log_found": str(log) if log else None,
        "last_poll": time.time() if (with_rates and enabled) else 0.0,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/status")
async def get_status():
    return JSONResponse(_snapshot(with_rates=False))


@router.get("/metrics")
async def get_metrics():
    # Lettura on-demand: nessun thread da mantenere, nessuno stato condiviso.
    return JSONResponse(_snapshot(with_rates=True))


@router.post("/start")
async def post_start():
    _write_enabled(True)
    return JSONResponse({**_snapshot(with_rates=True), "message": "monitor avviato"})


@router.post("/stop")
async def post_stop():
    _write_enabled(False)
    return JSONResponse({**_snapshot(with_rates=False), "message": "monitor fermato"})
