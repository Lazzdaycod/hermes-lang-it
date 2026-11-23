"""llama.cpp — backend API routes (router FastAPI montato da Hermes).

Montato su ``/api/plugins/llama-cpp/`` quando il plugin e' in
``plugins.enabled`` (richiede il riavvio di Hermes Desktop).

Routes:
    GET  /status   backend GPU, server, modello attivo, preset correnti
    GET  /models   modelli GGUF su disco (+ ?dir=<path> per cartelle extra)
    POST /apply    {model, options, set_active} -> scrive presets.ini,
                   window override, modello attivo e reload del router
    POST /load     {model} -> load sul router (202, async)
    POST /unload   {model} -> unload sul router (202, async)
    POST /stop     ferma il server gestito
    POST /restart  riavvia il server gestito (background)
    GET  /update   stato + ultime versioni disponibili per le fonti
                   (Vulkan, CUDA e ROCm: build ufficiali ggml-org)
    POST /update   {variant: "vulkan" | "cuda" | "rocm"} — aggiorna la
                   singola fonte (PM: re-pin lockfile + download +
                   verifica); riavvia il server solo se la fonte
                   aggiornata e' il backend in uso

Nessuna credenziale (api_key del router) e' mai restituita al frontend:
le chiamate al router avvengono lato server.

L'aggiornamento usa le medesime fonti del package manager Hermes
(release di ggml-org/llama.cpp per tutte le build installabili, inclusa
la build ROCm Windows ufficiale; le build ROCm ottimizzate per AMD
restano raggiungibili dal link "build ottimizzate" della pagina).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger("hermes.llama-cpp")

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _ensure_github_token() -> None:
    """Usa il token di `gh` (autenticato sul PC) per le API GitHub del PM.

    Senza token l'API e' anonima (60 richieste/ora) e i controlli di
    aggiornamento la esauriscono in pochi minuti (HTTP 403 rate limit)."""
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return
    try:
        import subprocess
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        tok = (r.stdout or "").strip()
        if r.returncode == 0 and tok:
            os.environ["GH_TOKEN"] = tok
    except Exception:
        pass

from llama_cpp_core import (  # noqa: E402
    apply_preset,
    detect_gpu,
    enforce_user_ctx_cap,
    list_models,
    load_model_async,
    read_presets,
    status_snapshot,
    unload_model_async,
)

router = APIRouter()

# Auto-correzione del soffitto ctx: i modelli nuovi generati da un core aggiornato
# possono avere un ctx-size nativo enorme e non caricarsi. Una volta per mount.
try:
    _cap_report = enforce_user_ctx_cap()
    if _cap_report.get("changed"):
        logger.info("llama-cpp user ctx cap enforced: %s", _cap_report["changed"])
except Exception:
    logger.exception("llama-cpp enforce_user_ctx_cap failed")


@router.get("/status")
async def get_status():
    return JSONResponse(status_snapshot())


@router.get("/models")
async def get_models(dir: list[str] = Query(default=[])):
    """?dir=<path>&dir=<path> — cartelle extra ripetibili."""
    extra = [d.strip() for d in dir if d and d.strip()] or None
    try:
        return JSONResponse(list_models(extra))
    except Exception as exc:
        return JSONResponse({"models": [], "orphans": [], "router": {},
                             "error": str(exc)}, status_code=200)


@router.post("/apply")
async def post_apply(body: dict):
    result = apply_preset(
        model_id=body.get("model"),
        options=body.get("options") or {},
        set_active=bool(body.get("set_active")),
    )
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/load")
async def post_load(body: dict):
    result = load_model_async(body.get("model"))
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/unload")
async def post_unload(body: dict):
    result = unload_model_async(body.get("model"))
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


# ------------------------------------------------------------------ lifecycle
@router.post("/stop")
async def stop_server():
    """Stop the managed llama.cpp server (process-local supervisor)."""
    try:
        from hermes_cli.local_runtime import bootstrap
        bootstrap.shutdown_local_runtime()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"stop failed: {exc}")
    return JSONResponse({"ok": True})


@router.post("/restart")
async def restart_server():
    """Restart the managed llama.cpp server (runs in the background: the
    router can take up to ~2 min to become healthy after a cold boot)."""

    def _run():
        try:
            from hermes_cli.local_runtime import bootstrap
            from hermes_cli.config import load_config
            bootstrap.shutdown_local_runtime()
            bootstrap.ensure_local_runtime(load_config(), force=True)
        except Exception as exc:
            logger.warning("llama-cpp restart failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="llama-cpp-restart").start()
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------ update
#
# Stato condiviso della procedura di aggiornamento (visibile da GET /update,
# aggiornato dal thread di POST /update). Tre fonti aggiornabili:
#   - "vulkan" -> llamacpp-vulkan (ggml-org/llama.cpp, backend AMD/Intel)
#   - "cuda"   -> llamacpp-cuda (ggml-org/llama.cpp, build NVIDIA)
#   - "rocm"   -> llamacpp-hip (ggml-org/llama.cpp, build ROCm per AMD)
# Le versioni vengono sempre risolte dal package manager Hermes
# (pm.update.resolve_package).

# Nota: "rocm" punta alla build hip (llamacpp-hip, ggml-org/llama.cpp),
# la fonte ROCm ufficiale e verificata su questa macchina (7900 XTX).
_VARIANTS = {
    "vulkan": "llamacpp-vulkan",
    "cuda": "llamacpp-cuda",
    "rocm": "llamacpp-rocm-lemonade",
}

# Package PM -> chiave variante. Include anche le build che non sono piu'
# aggiornabili dal pannello (llamacpp-hip, build ROCm ufficiale ggml-org)
# ma che possono essere installate nello store e usate dal server.
_PACKAGE_TO_KEY = {
    "llamacpp-vulkan": "vulkan",
    "llamacpp-cuda": "cuda",
    "llamacpp-rocm-lemonade": "rocm",
    "llamacpp-hip": "rocm",
}

# Backend riconoscibili dal nome variant delle entry store (llamacpp-<x>-...):
# servono per capire se una build selezionata cambia il backend del runtime.
_KNOWN_BACKENDS = {"cuda", "vulkan", "metal", "hip", "cpu", "rocm-lemonade"}
# variant entry store -> chiave del backend nel config (local_runtime.backend).
_BACKEND_KEYS = {
    "cuda": "cuda",
    "vulkan": "vulkan",
    "metal": "metal",
    "hip": "hip",
    "cpu": "cpu",
    # Lemonade e' una build HIP/ROCm: il runtime la espone come backend suo.
    "rocm-lemonade": "rocm-lemonade",
}

# Fonti dei build per variante (mostrate nel pannello update). Vulkan e CUDA
# sono build ufficiali ggml-org/llama.cpp; ROCm è la build Lemonade sul repo
# lemonade-sdk/llamacpp-rocm (scelta esplicita dell'utente: non rimappare
# su ggml-org).
_VARIANT_SOURCE = {
    "vulkan": "ggml-org/llama.cpp (Vulkan)",
    "cuda": "ggml-org/llama.cpp (CUDA)",
    "rocm": "lemonade-sdk/llamacpp-rocm (ROCm)",
}
_VARIANT_DISPLAY = {
    "vulkan": "Vulkan",
    "cuda": "CUDA",
    "rocm": "ROCm",
    "hip": "ROCm",
    "lemonade": "ROCm",
}


def _display_variant(raw: str) -> str:
    """Variante grezza dell'entry store -> nome normalizzato per l'UI."""
    # Map every ROCm build (hip, rocm-lemonade, lemonade-*) to the canonical
    # 'rocm' name for display.
    low = (raw or "").strip().lower()
    if low == "hip" or low.endswith("-hip") or "rocm" in low or "lemonade" in low:
        return "rocm"
    return raw


def _display_name(raw: str) -> str:
    return _VARIANT_DISPLAY.get(_display_variant(raw), raw)

_HERMES_HOME = _PLUGIN_DIR.parent.parent

_update_lock = threading.Lock()
_update_state = {
    "status": "idle",
    "message": None,
    "progress": 0.0,
    "target": None,
    "variant": None,
    "check": None,
    "resolved_at": 0.0,
}
_RESOLVE_TTL = 120.0    # secondi: non interroghiamo l'upstream ad ogni poll


def _server_json_path() -> Path:
    return _HERMES_HOME / "runtimes" / "llamacpp" / "server.json"


def _active_variant() -> tuple[str, str | None]:
    """(nome package PM, build corrente) del backend in esecuzione.

    La directory della binary e' ``llamacpp-<variant>-<build>-<platform>``
    dentro ``tools/`` (PM store); ``server.json`` porta il percorso esatto.
    Le varianti possono essere multi-parola (es. ``lemonade-rocm``).
    """
    try:
        data = json.loads(_server_json_path().read_text(encoding="utf-8"))
    except Exception:
        return "", None
    m = re.search(r"llamacpp-([a-z0-9]+(?:-[a-z0-9]+)*?)-(\d+)",
                  str(data.get("executable", "")))
    if not m:
        return "", None
    return f"llamacpp-{m.group(1)}", m.group(2)


def _resolve_variant(pkg_name: str, current: str | None) -> dict:
    """Ultima versione disponibile per un package PM (nessuna cache)."""
    from pm import Lockfile, get_package, paths as pm_paths
    from pm.store import ALL_TARGETS
    from pm.update import resolve_package

    try:
        package = get_package(pkg_name)
    except Exception as exc:
        return {"ok": False, "error": f"package non trovato: {exc}"}
    lockfile = Lockfile(pm_paths.lockfile_path())
    targets = [t for t in ALL_TARGETS if package.missing_reason(t) is None]
    if not targets:
        return {"ok": False, "error": "nessun target compatibile con questa macchina"}
    try:
        decision = resolve_package(
            package, targets, lockfile.version(pkg_name),
            artifacts=lockfile.pinned_artifacts(pkg_name),
        )
    except Exception as exc:
        return {"ok": False, "error": f"check fallito: {exc}"}
    return {
        "ok": True,
        "package": pkg_name,
        "current": current,
        "pinned": lockfile.version(pkg_name),
        "latest": decision.version,
        "update_available": bool(decision.changed),
        "reason": decision.reason or "",
    }


def _variant_key(pkg_name: str | None) -> str | None:
    if not pkg_name:
        return None
    if pkg_name in _PACKAGE_TO_KEY:
        return _PACKAGE_TO_KEY[pkg_name]
    for key, name in _VARIANTS.items():
        if name == pkg_name:
            return key
    return None


def _compute_tuple(cc) -> tuple[int, int] | None:
    """\"6.1\" -> (6, 1); None se manca o non e' numerico."""
    try:
        major, _, minor = str(cc).strip().partition(".")
        return (int(major), int(minor or 0))
    except (TypeError, ValueError):
        return None


def _gpu_requirement(key: str, gpu: dict) -> tuple[bool, str | None]:
    """La variante e' adatta alla GPU rilevata? (suitable, nota per la UI).

    Vulkan lo usa quasi ogni GPU; CUDA richiede una GPU NVIDIA sm_75+
    (le build 13.x hanno eliminato Maxwell/Pascal/Volta); ROCm (Lemonade)
    richiede una GPU AMD con bucket gfx. Le note vanno in colonna
    "Versione".
    """
    vendor = (gpu or {}).get("vendor")
    name = (gpu or {}).get("name")
    if key == "vulkan":
        if not name:
            return False, "Nessuna GPU rilevata"
        return True, None
    if key == "cuda":
        if vendor != "nvidia":
            return False, "Richiede una GPU NVIDIA"
        ct = _compute_tuple((gpu or {}).get("compute_capability"))
        if ct is None:
            return False, "Compute capability non rilevata (nvidia-smi assente)"
        if ct < (7, 5):
            sigla = str((gpu or {}).get("compute_capability")).replace(".", "")
            return False, (
                f"Nessuna build CUDA supportata da questa GPU: le build attuali "
                f"richiedono sm_75+ (CUDA 13 ha eliminato Maxwell/Pascal/Volta), "
                f"la tua GPU e' sm_{sigla}"
            )
        return True, None
    if key == "rocm":
        if vendor != "amd":
            return False, "Richiede una GPU AMD"
        if not (gpu or {}).get("amd_gfx"):
            return False, "Bucket gfx AMD non rilevato"
        return True, None
    return True, None


def _variant_note(key: str, gpu: dict) -> str | None:
    """Dettaglio "perche' adatto" mostrato accanto alla versione."""
    if key == "rocm":
        bucket = (gpu or {}).get("amd_gfx")
        return f"Lemonade · bucket {bucket}" if bucket else "Lemonade ROCm"
    if key == "cuda":
        cc = (gpu or {}).get("compute_capability")
        return f"GPU NVIDIA sm_{cc.replace('.', '')} · build CUDA" if cc else "GPU NVIDIA · build CUDA"
    if key == "vulkan":
        return "build Vulkan (AMD/Intel/NVIDIA)"
    return None


def _resolve_all() -> dict:
    """Stato delle fonti aggiornabili (vulkan, cuda, rocm) per la GPU rilevata.

    ``current`` e' la build del server in esecuzione (per il backend attivo)
    o il pin installato nel lockfile (per le altre fonti). Le varianti non
    adatte alla GPU del PC sono marcate ``suitable=False`` e non risolvono
    alcuna versione: la UI le mostra disabilitate con la motivazione.
    """
    active_pkg, active_build = _active_variant()
    _ensure_github_token()
    gpu = detect_gpu()
    variants: dict = {}
    for key, pkg_name in _VARIANTS.items():
        suitable, note = _gpu_requirement(key, gpu)
        if not suitable:
            variants[key] = {
                "ok": True,
                "package": pkg_name,
                "current": None,
                "pinned": None,
                "latest": None,
                "update_available": False,
                "reason": "",
                "suitable": False,
                "note": note,
            }
            continue
        current = active_build if pkg_name == active_pkg else None
        info = _resolve_variant(pkg_name, current)
        if info.get("ok") and not current and info.get("pinned"):
            info["current"] = info["pinned"]
        info["suitable"] = True
        info["note"] = _variant_note(key, gpu)
        variants[key] = info
    return {"active": active_pkg or None, "variants": variants, "gpu": gpu}


def _set_state(**kw) -> None:
    with _update_lock:
        _update_state.update(kw)


def _run_update(variant: str) -> None:
    """Corpo dell'aggiornamento (thread daemon).

    Stessa sequenza di ``hermes pm update`` per un solo package:
    resolve -> re-pin del lockfile -> install (download + verifica).
    Se il package aggiornato e' quello che il server sta eseguendo, il
    runtime viene riavviato per rileggere il pin; altrimenti la build
    resta installata e il server continua con il backend attuale.
    """
    _ensure_github_token()
    try:
        from pm import Lockfile, get_package, paths as pm_paths
        from pm.store import ALL_TARGETS
        from pm.update import resolve_package
        from pm.cli import _pin_artifacts

        pkg_name = _VARIANTS.get(variant, "")
        if not pkg_name:
            raise RuntimeError(f"variante sconosciuta: {variant}")
        # ROCm (Lemonade) esiste solo per GPU AMD: verifica la GPU prima di
        # scaricare, così l'errore è chiaro e non a metà dell'update.
        if pkg_name == "llamacpp-rocm-lemonade":
            gpu = detect_gpu()
            if gpu.get("vendor") != "amd":
                raise RuntimeError(
                    "Nessuna GPU AMD rilevata: Lemonade ROCm non è adatto a questa macchina")
            if not gpu.get("amd_gfx"):
                raise RuntimeError("Bucket gfx non rilevato per la GPU AMD")
        if pkg_name == "llamacpp-cuda":
            gpu = detect_gpu()
            ct = _compute_tuple(gpu.get("compute_capability"))
            if ct is None:
                raise RuntimeError(
                    "Compute capability NVIDIA non rilevata (nvidia-smi assente): "
                    "impossibile verificare la compatibilità della build CUDA")
            if ct < (7, 5):
                sigla = str(gpu.get("compute_capability")).replace(".", "")
                raise RuntimeError(
                    f"Le build CUDA attuali richiedono sm_75+; la tua GPU e' sm_{sigla}. "
                    f"Usa Vulkan o riattiva una build gia' installata nello store.")
        active_pkg, _ = _active_variant()
        restart = pkg_name == active_pkg
        package = get_package(pkg_name)
        lockfile = Lockfile(pm_paths.lockfile_path())
        targets = [t for t in ALL_TARGETS if package.missing_reason(t) is None]
        if not targets:
            raise RuntimeError("nessun target compatibile con questa macchina")
        _set_state(status="running", variant=variant,
                   message="Controllo la versione disponibile…",
                   progress=0.0, target=None)
        decision = resolve_package(
            package, targets, lockfile.version(pkg_name),
            artifacts=lockfile.pinned_artifacts(pkg_name),
        )
        if not decision.changed or not decision.version:
            _set_state(status="done",
                       message=f"{pkg_name}: già alla versione più recente "
                               f"(b{decision.version or '?'})")
            return
        target = decision.version
        # ROCm/Lemonade: verifica esplicita che la release pubblichi l'asset
        # per il bucket gfx della GPU rilevata (non solo che il tag esista).
        if pkg_name == "llamacpp-rocm-lemonade":
            from pm.packages import _github_release_digests
            from pm.store import current_target
            target_name = current_target()
            asset = package.fetch_url(str(target), target_name).rsplit("/", 1)[-1]
            digests = _github_release_digests(
                "lemonade-sdk/llamacpp-rocm", f"b{target}")
            if asset not in digests:
                raise RuntimeError(
                    f"Lemonade non pubblica una build {gpu.get('amd_gfx')} per "
                    f"questa GPU (manca {asset} in b{target})")
        _set_state(target=target, message=f"Scarico b{target}…", progress=0.0)

        artifacts = _pin_artifacts(package, decision, lockfile.pinned_artifacts(pkg_name))
        lockfile.set_pin(pkg_name, target, artifacts)
        lockfile.save()

        import pm as pm_mod

        def _progress(stage, done, total, label):
            frac = (done / total) if total else 0
            _set_state(progress=min(0.95, 0.05 + 0.9 * frac),
                       message=label or stage or f"Installo b{target}…")

        def _download(done, total, _ranges):
            frac = (done / total) if total else 0
            _set_state(progress=min(0.95, 0.05 + 0.9 * frac),
                       message=f"Scarico b{target}… {frac:.0%}")

        pm_mod.ensure(pkg_name, explicit=True, verify=True,
                      progress=_progress, download_progress=_download)
        if restart:
            _set_state(progress=0.97, message="Riavvio del server…")
            from hermes_cli.local_runtime import bootstrap
            from hermes_cli.config import load_config
            bootstrap.shutdown_local_runtime()
            bootstrap.ensure_local_runtime(load_config(), force=True)
            _set_state(status="done",
                       message=f"Aggiornato a b{target}: il server sta ripartendo")
        else:
            _set_state(status="done",
                       message=f"Aggiornato a b{target}: build installata, il server "
                               f"continua con il backend attuale")
    except Exception as exc:
        logger.exception("llama-cpp update failed")
        _set_state(status="error", message=str(exc))
    finally:
        _set_state(check=None, resolved_at=0.0)  # forza un resolve fresco


@router.get("/update")
async def update_check():
    """Stato dell'aggiornamento + ultime versioni disponibili per le
    fonti (Vulkan e CUDA: build ufficiali ggml-org; ROCm: build Lemonade,
    repo lemonade-sdk/llamacpp-rocm). Il resolve viene interrogato al
    massimo ogni ``_RESOLVE_TTL`` secondi per non martellare l'upstream
    ad ogni poll.
    """
    check = None
    with _update_lock:
        if (
            _update_state["check"] is not None
            and time.monotonic() - _update_state["resolved_at"] <= _RESOLVE_TTL
        ):
            check = _update_state["check"]
    if check is None:
        check = await asyncio.to_thread(_resolve_all)
        _set_state(check=check, resolved_at=time.monotonic())

    with _update_lock:
        state = {k: _update_state[k] for k in ("status", "message", "progress", "target", "variant")}
    variants = {}
    for key, info in (check.get("variants") or {}).items():
        entry = {
            "package": info.get("package"),
            "current": f"b{info['current']}" if info.get("current") else None,
            "latest": f"b{info.get('latest')}" if info.get("latest") else None,
            "update_available": bool(info.get("update_available")),
            "source": _VARIANT_SOURCE.get(key),
            "suitable": bool(info.get("suitable", info.get("ok"))),
            "note": info.get("note"),
        }
        if not info.get("ok") and info.get("error"):
            entry["error"] = info["error"]
        variants[key] = entry
    payload: dict = {
        **state,
        "variants": variants,
        "gpu": check.get("gpu"),
        "active": check.get("active"),
        "active_key": None,
        "package": None,
        "current": None,
        "latest": None,
        "update_available": False,
    }
    # Campi in testa = backend attivo (compatibilità col frontend precedente).
    active_key = _variant_key(check.get("active"))
    if active_key:
        payload["active_key"] = active_key
    if active_key and active_key in variants:
        payload.update({
            "package": variants[active_key].get("package"),
            "current": variants[active_key].get("current"),
            "latest": variants[active_key].get("latest"),
            "update_available": variants[active_key].get("update_available"),
        })
    return JSONResponse(payload)


@router.post("/update")
async def update_server(body: dict = None):
    """Aggiorna una singola fonte: ``{variant: "vulkan" | "rocm"}``
    (omessa = la fonte del backend in uso). Re-pin del lockfile +
    download verificato (PM), poi riavvio del server solo se la fonte
    aggiornata e' quella in esecuzione. L'operazione gira in background:
    lo stato si legge da GET /update (status/progress/message)."""
    variant = (body or {}).get("variant") or "active"
    if variant == "active":
        variant = _variant_key(_active_variant()[0]) or "vulkan"
    if variant not in _VARIANTS:
        raise HTTPException(status_code=400,
                            detail=f"variante sconosciuta: {variant}")
    with _update_lock:
        if _update_state["status"] == "running":
            raise HTTPException(status_code=409, detail="update already running")
    threading.Thread(target=_run_update, args=(variant,), daemon=True,
                     name="llama-cpp-update").start()
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------ bench

def _bench_history_path() -> Path:
    return _HERMES_HOME / "runtimes" / "llamacpp" / "bench.json"


def _load_bench_history() -> list[dict]:
    try:
        data = json.loads(_bench_history_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_bench_history(entries: list[dict]) -> None:
    path = _bench_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries[-100:], indent=1), encoding="utf-8")


def _bench_prompt(tokens: int) -> str:
    unit = "The quick brown fox jumps over the lazy dog. "
    reps = max(1, int(tokens * 3.2 / len(unit)) + 1)
    return unit * reps


def _bench_once(base_url: str, api_key: str, model: str, prompt: str,
                max_tokens: int, timeout: float = 900.0) -> dict:
    """Una misura via API OpenAI-compatible (/v1/completions, stream)."""
    import urllib.request

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    _base = base_url.rstrip("/")
    _url = (_base + "/chat/completions" if _base.endswith("/v1")
            else _base + "/v1/chat/completions")
    req = urllib.request.Request(
        _url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
    )
    t0 = time.perf_counter()
    ttft = None
    chunks = 0
    usage: dict = {}
    timings: dict = {}
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except Exception:
                continue
            if isinstance(obj.get("usage"), dict):
                usage = obj["usage"]
            if isinstance(obj.get("timings"), dict):
                timings = obj["timings"]
            choices = obj.get("choices") or []
            delta = ""
            if choices:
                delta = (choices[0].get("delta") or {}).get("content") or ""
            if delta:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                chunks += 1
    total = time.perf_counter() - t0
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    if not completion_tokens:
        completion_tokens = chunks
    prompt_ms = timings.get("prompt_ms")
    predicted_ms = timings.get("predicted_ms")
    tok_per_s = completion_tokens / max(1e-6, total - (ttft or 0.0))
    if predicted_ms and completion_tokens:
        tok_per_s = completion_tokens / max(1e-6, float(predicted_ms) / 1000.0)
    out = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "ttft_s": round(ttft or 0.0, 3),
        "total_s": round(total, 3),
        "tok_per_s": round(tok_per_s, 1),
        "total_tok_per_s": round(completion_tokens / max(1e-6, total), 1),
    }
    if prompt_ms:
        out["prompt_ms"] = round(float(prompt_ms), 1)
        out["prefill_tok_per_s"] = (
            round(prompt_tokens / max(1e-6, float(prompt_ms) / 1000.0), 1)
            if prompt_tokens else 0.0)
    if predicted_ms:
        out["decode_ms"] = round(float(predicted_ms), 1)
    return out


def _bench_model_loaded(model: str) -> bool:
    return (status_snapshot().get("router_models") or {}).get(model) == "loaded"


def _run_bench(model: str, mode: str, prompt_tokens: int, output_tokens: int,
               runs: int) -> dict:
    try:
        state = json.loads(_server_json_path().read_text(encoding="utf-8"))
    except Exception:
        state = {}
    base_url = str(state.get("base_url") or "")
    api_key = str(state.get("api_key") or "")
    if not base_url:
        return {"ok": False, "error": "server non attivo (runtimes/llamacpp/server.json mancante)"}
    if not status_snapshot().get("running"):
        return {"ok": False, "error": "server llama.cpp non attivo"}
    if not _bench_model_loaded(model):
        load_model_async(model)
        for _ in range(180):
            time.sleep(1.0)
            if _bench_model_loaded(model):
                break
        if not _bench_model_loaded(model):
            return {"ok": False, "error": f"modello non caricato: {model}"}
    snap = status_snapshot()
    active_pkg, _active_build = _active_variant()
    preset = read_presets().get(model) or {}
    server = {
        "backend": snap.get("backend"),
        "build": snap.get("build"),
        "package": active_pkg,
        "executable": snap.get("executable"),
    }
    results: list[dict] = []
    for _ in range(runs):
        if mode in ("generation", "both"):
            r = _bench_once(base_url, api_key, model, "Once upon a time", output_tokens)
            r["mode"] = "generation"
            results.append(r)
        if mode in ("prefill", "both"):
            r = _bench_once(base_url, api_key, model, _bench_prompt(prompt_tokens), 1)
            r["mode"] = "prefill"
            if not r.get("prefill_tok_per_s"):
                r["prefill_tok_per_s"] = round(
                    r["prompt_tokens"] / max(1e-6, r["total_s"]), 1)
            results.append(r)
    entry = {"ts": time.time(), "model": model, "mode": mode,
             "prompt_tokens": prompt_tokens, "output_tokens": output_tokens,
             "preset": preset, "server": server, "runs": results}
    history = _load_bench_history()
    history.append(entry)
    _save_bench_history(history)
    return {"ok": True, "results": results, "entry": entry}


@router.post("/bench")
async def run_bench(body: dict = None):
    """Benchmark di velocita' (tok/s) sul modello caricato, via API del server."""
    body = body or {}
    model = str(body.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model mancante")
    mode = str(body.get("mode") or "both").strip()
    if mode not in ("generation", "prefill", "both"):
        mode = "both"
    prompt_tokens = max(16, min(65536, int(body.get("prompt_tokens") or 2048)))
    output_tokens = max(1, min(4096, int(body.get("output_tokens") or 128)))
    runs = max(1, min(5, int(body.get("runs") or 1)))
    return JSONResponse(await asyncio.to_thread(
        _run_bench, model, mode, prompt_tokens, output_tokens, runs))


@router.get("/bench/history")
async def bench_history():
    return JSONResponse({"entries": await asyncio.to_thread(_load_bench_history)})


@router.get("/bench/leaderboard")
async def bench_leaderboard():
    """Classifica: miglior risultato per modello, con preset e server del test."""
    entries = await asyncio.to_thread(_load_bench_history)
    best: dict[str, dict] = {}
    for e in entries:
        runs = e.get("runs") or []
        gen = max([r.get("tok_per_s", 0) for r in runs if r.get("mode") == "generation"] or [0])
        pre = max([r.get("prefill_tok_per_s", 0) for r in runs if r.get("mode") == "prefill"] or [0])
        model = e.get("model") or "?"
        row = best.get(model)
        if row is None or gen > row["gen"]:
            best[model] = {"gen": gen, "pre": pre, "entry": e}
    rows = sorted(best.values(), key=lambda r: r["gen"], reverse=True)
    return JSONResponse({"rows": [{
        "model": r["entry"].get("model"),
        "generation_tok_per_s": r["gen"],
        "prefill_tok_per_s": r["pre"],
        "preset": r["entry"].get("preset") or {},
        "server": r["entry"].get("server") or {},
        "ts": r["entry"].get("ts"),
    } for r in rows]})


# ------------------------------------------------------------------ builds
#
# Selezione e rimozione delle build llama.cpp installate nel PM store
# (tools/). Il backend attivo e' quello registrato in facts.json: per
# cambiare build si re-pinna il lockfile e si re-registra il fact (stessa
# sequenza di ``hermes pm``), poi si riavvia il server solo se la build
# scelta e' del backend in esecuzione. Le build non attive restano nello
# store come semplici directory e si possono rimuovere liberamente.

_STORE_ROOT: Path | None = None


def _store_root() -> Path:
    global _STORE_ROOT
    if _STORE_ROOT is None:
        from pm import paths as pm_paths
        _STORE_ROOT = pm_paths.store_root()
    return _STORE_ROOT


def _parse_store_entry(name: str) -> dict | None:
    """Parsa il nome di una entry dello store:
    ``llamacpp-<variant>-<build>-<platform>``.

    Il variant puo' essere multi-parola (es. ``lemonade-rocm``); il build
    e' l'unico segmento puramente numerico. Le entry senza numero di build
    (es. ``llamacpp-rocm-custom``) restituiscono ``build=None``.
    """
    deprecated = name.endswith(".deprecated")
    n = name[: -len(".deprecated")] if deprecated else name
    parts = n.split("-")
    if not parts or parts[0] != "llamacpp":
        return None
    for i, p in enumerate(parts):
        if i > 1 and p.isdigit():
            return {
                "variant": "-".join(parts[1:i]),
                "build": p,
                "platform": "-".join(parts[i + 1:]),
                "deprecated": deprecated,
            }
    return {
        "variant": "-".join(parts[1:]),
        "build": None,
        "platform": None,
        "deprecated": deprecated,
    }


def _entry_size_mb(entry: Path) -> float | None:
    try:
        total = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        return round(total / (1024 * 1024), 1)
    except OSError:
        return None


def _list_builds() -> list[dict]:
    """Tutte le build llama.cpp nello store, con stato attivo/verificato.

    ``active`` e' la build che il server sta eseguendo (server.json), non
    il pin per-pacchetto di facts.json: una build appena aggiornata e' il
    pin del suo pacchetto ma non e' in esecuzione, e se fosse marcata
    "attiva" il frontend non offrirebbe piu' selezionarla o eliminarla.
    """
    from pm import get_package

    root = _store_root()
    active_pkg, active_build = _active_variant()
    out: list[dict] = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        if not name.startswith("llamacpp"):
            continue
        entry = root / name
        if not entry.is_dir():
            continue
        info = _parse_store_entry(name)
        if info is None:
            continue
        pkg_name = f"llamacpp-{info['variant']}"
        active = (info["build"] is not None
                  and pkg_name == active_pkg
                  and info["build"] == active_build)
        verified = None
        selectable = True
        if info["build"] and info["platform"]:
            try:
                pkg = get_package(pkg_name)
            except Exception:
                pkg = None
                selectable = False  # package rimosso dal registro PM
            if pkg is not None:
                try:
                    verified = not pkg.verify(entry, info["platform"])
                except Exception:
                    verified = None
        out.append({
            "entry": name,
            "package": pkg_name,
            "variant": _display_variant(info["variant"]),
            "display": _display_name(info["variant"]),
            "build": info["build"],
            "platform": info["platform"],
            "deprecated": info["deprecated"],
            "selectable": selectable,
            "active": active,
            "verified": verified,
            "size_mb": _entry_size_mb(entry),
        })
    return out


def _restart_runtime() -> None:
    """Riavvia il runtime llama.cpp gestito (stesso codice del flusso update)."""
    from hermes_cli.local_runtime import bootstrap
    from hermes_cli.config import load_config
    bootstrap.shutdown_local_runtime()
    bootstrap.ensure_local_runtime(load_config(), force=True)


@router.get("/builds")
async def list_builds():
    """Build llama.cpp installate nel PM store, con build attiva e stato."""
    return JSONResponse({"builds": await asyncio.to_thread(_list_builds)})


@router.post("/builds/select")
async def select_build(body: dict = None):
    """Attiva una build installata: ``{entry: "llamacpp-<variant>-<build>-<platform>"}``.

    Re-pin del lockfile + re-record del fact (PM), poi riavvio del server
    solo se la build scelta e' del backend in esecuzione.
    """
    entry_name = (body or {}).get("entry")
    if not entry_name:
        raise HTTPException(status_code=400, detail="entry mancante")
    info = _parse_store_entry(entry_name)
    if info is None or not info["build"] or not info["platform"]:
        raise HTTPException(status_code=400,
                            detail="build non valida o senza numero di versione")
    root = _store_root()
    entry = root / entry_name
    if not entry.is_dir():
        raise HTTPException(status_code=404, detail="build non trovata nello store")
    pkg_name = f"llamacpp-{info['variant']}"
    build, target = info["build"], info["platform"]
    try:
        from pm import get_package
        package = get_package(pkg_name)
    except Exception as exc:
        # Es. entry legacy di un package rimosso dal registry PM
        # (llamacpp-rocm-lemonade): 400 esplicito invece di 500.
        logger.warning("llama-cpp build select: package non disponibile: %s (%s)",
                       pkg_name, exc)
        raise HTTPException(status_code=400,
                            detail=f"pacchetto PM non disponibile: {pkg_name}")
    try:
        from pm import paths as pm_paths
        from pm.lock import Facts, Lockfile
        from pm.store import tree_digest

        reason = package.verify(entry, target)
        if reason:
            raise HTTPException(status_code=400,
                                detail=f"build non verificata: {reason}")
        url = package.fetch_url(build, target)
        sha = package.known_sha256(build, url)
        if not sha:
            raise HTTPException(status_code=400,
                                detail="impossibile recuperare l'hash dell'archivio")
        lockfile = Lockfile(pm_paths.lockfile_path())
        lockfile.set_pin(pkg_name, build, {target: {"url": url, "sha256": sha}})
        lockfile.save()
        facts = Facts(root / "facts.json")
        facts.record(
            pkg_name, build, entry_name, package.env(entry, target), root,
            target=target, artifacts=[sha], digest=tree_digest(entry),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("llama-cpp build select failed")
        raise HTTPException(status_code=500, detail=f"selezione fallita: {exc}")
    restart = pkg_name == _active_variant()[0]
    backend_key = _BACKEND_KEYS.get(info["variant"])
    if not restart and backend_key:
        # Build di un backend diverso da quello in esecuzione: aggiorna
        # local_runtime.backend nel config e riavvia il runtime, altrimenti
        # il re-pin da solo non cambia mai la build attiva (il runtime
        # parte sempre col backend configurato).
        from hermes_cli.config import load_config, save_config
        cfg = load_config()
        lr = cfg.setdefault("local_runtime", {})
        if lr.get("backend") != backend_key:
            lr["backend"] = backend_key
            save_config(cfg, merge_existing=True)
        restart = True
    if restart:
        threading.Thread(target=_restart_runtime, daemon=True,
                         name="llama-cpp-build-select").start()
    return JSONResponse({"ok": True, "entry": entry_name, "build": build,
                         "restart": restart})


@router.post("/builds/delete")
async def delete_build(body: dict = None):
    """Rimuove una build dallo store: ``{entry: "..."}``.

    Non si puo' rimuovere la build in esecuzione dal server (prima
    selezionane un'altra); i pin per-pacchetto di facts.json non
    bloccano la rimozione.
    """
    entry_name = (body or {}).get("entry")
    if not entry_name:
        raise HTTPException(status_code=400, detail="entry mancante")
    info = _parse_store_entry(entry_name)
    if info is None:
        raise HTTPException(status_code=400, detail="build non valida")
    root = _store_root()
    entry = root / entry_name
    if not entry.is_dir():
        raise HTTPException(status_code=404, detail="build non trovata nello store")
    pkg_name = f"llamacpp-{info['variant']}"
    active_pkg, active_build = _active_variant()
    if pkg_name == active_pkg and info["build"] == active_build:
        raise HTTPException(status_code=409,
                            detail="e' la build in esecuzione: prima selezionane un'altra")
    try:
        shutil.rmtree(entry)
    except Exception as exc:
        logger.exception("llama-cpp build delete failed")
        raise HTTPException(status_code=500, detail=f"rimozione fallita: {exc}")
    # Se il fatto per-pacchetto puntava alla build rimossa, lo si elimina
    # per non lasciarlo pendente (un eventuale pm.ensure ri-scarichera').
    try:
        from pm.lock import Facts
        facts = Facts(root / "facts.json")
        if (facts.get(pkg_name) or {}).get("entry") == entry_name:
            facts.forget([pkg_name])
    except Exception:
        logger.exception("llama-cpp build delete: facts cleanup failed")
    return JSONResponse({"ok": True, "entry": entry_name})
