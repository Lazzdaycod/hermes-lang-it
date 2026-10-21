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


def _local_endpoint() -> dict:
    """base_url + modello del provider locale, letti dal config di Hermes.

    Serve al fallback di traduzione: la porta del server locale cambia a ogni
    avvio, quindi non puo' essere scritta nel frontend. Quando il config non
    espone un base_url, il frontend semplicemente non usa il fallback locale.
    """
    try:
        from ruamel.yaml import YAML

        path = _hermes_home() / "config.yaml"
        with open(path, encoding="utf-8") as fh:
            data = YAML(typ="safe").load(fh) or {}
    except Exception:
        return {}
    model = data.get("model") if isinstance(data, dict) else None
    if not isinstance(model, dict):
        return {}
    base_url = str(model.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        return {}
    name = model.get("default") or model.get("model") or model.get("name") or ""
    return {"base_url": base_url, "model": str(name)}


@router.get("/api-key")
async def api_key():
    """Chiave API del server llama.cpp locale + endpoint e modello del provider locale."""
    path = _hermes_home() / "runtimes" / "llamacpp" / ".api_key"
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    return JSONResponse({"key": key, **_local_endpoint()})
