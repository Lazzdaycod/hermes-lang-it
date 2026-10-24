"""Catalogo Plugin IT — backend API routes.

Nessuna route esposta: il plugin e' solo UI + traduzione lato client
(Google Translate / MyMemory). Nessuna lettura di credenziali locali.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
