"""Catalogo Plugin IT — backend API routes.

Espone la chiave API del server llama.cpp locale (letta da
``$HERMES_HOME/runtimes/llamacpp/.api_key``) in modo che il frontend del plugin
possa usare il modello locale come fallback di traduzione quando il servizio
pubblico (MyMemory) non risponde.

Il file ``.api_key`` è generato da Hermes stesso; non è un segreto inserito a
mano. La route è montata solo quando il plugin è abilitato e resta sul
localhost del gateway.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


@router.get("/api-key")
async def api_key():
    """La chiave API del server llama.cpp locale, oppure una stringa vuota."""
    path = _hermes_home() / "runtimes" / "llamacpp" / ".api_key"
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    return JSONResponse({"key": key})
