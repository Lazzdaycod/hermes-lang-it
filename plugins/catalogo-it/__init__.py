"""Hermes plugin: catalogo-it

Espone la chiave API del server llama.cpp locale per il fallback di traduzione
del frontend del plugin. Nessun tool, nessun hook: solo il router FastAPI.
"""


def register(toolset) -> None:
    """Registrazione richiesta dal loader dei plugin.

    Non espongo tool né hook: il plugin è solo UI + backend HTTP.
    """
    return None
