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
    GET  /update   sola lettura: build installata + ultime versioni note per
                   le fonti ufficiali ggml-org (Vulkan, CUDA, ROCm/HIP);
                   nessuna installazione, nessun lifecycle
    GET  /builds   build llama.cpp installate nello store (sola lettura)

Nessuna credenziale (api_key del router) e' mai restituita al frontend:
le chiamate al router avvengono lato server. Il plugin non chiama alcuna
route interna del core: stop/start/restart e installazioni del runtime
sono nel core (Impostazioni -> Modelli locali). Le versioni si leggono
dalle release pubbliche ggml-org/llama.cpp. Selezione/rimozione build:
in attesa di una route pubblica del core (issue upstream).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger("hermes.llama-cpp")

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from llama_cpp_core import (  # noqa: E402
    apply_preset,
    detect_gpu,
    hermes_home,
    list_models,
    load_model_async,
    read_presets,
    status_snapshot,
    unload_model_async,
)

router = APIRouter()


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


# ------------------------------------------------------------------ update
#
# Sola lettura: le ultime versioni note per tre fonti, tutte build
# ufficiali ggml-org/llama.cpp:
#   - "vulkan" -> llamacpp-vulkan (backend AMD/Intel/NVIDIA)
#   - "cuda"   -> llamacpp-cuda (build NVIDIA, sm_75+)
#   - "rocm"   -> llamacpp-hip (build ROCm/HIP ufficiale per AMD)
# Installazioni, stop/restart sono operazioni del core (Impostazioni ->
# Modelli locali); qui si leggono soltanto le release pubbliche GitHub.

_VARIANTS = {
    "vulkan": "llamacpp-vulkan",
    "cuda": "llamacpp-cuda",
    "rocm": "llamacpp-hip",
}

# Package PM -> chiave variante. Include le build legacy non piu' proposte
# (llamacpp-rocm-lemonade) per etichettare correttamente le entry store vecchie.
_PACKAGE_TO_KEY = {
    "llamacpp-vulkan": "vulkan",
    "llamacpp-cuda": "cuda",
    "llamacpp-hip": "rocm",
    "llamacpp-rocm-lemonade": "rocm",
}

# Fonti dei build per variante (mostrate nel pannello update): tutte le build
# installabili sono release ufficiali ggml-org/llama.cpp, ROCm inclusa.
_VARIANT_SOURCE = {
    "vulkan": "ggml-org/llama.cpp (Vulkan)",
    "cuda": "ggml-org/llama.cpp (CUDA)",
    "rocm": "ggml-org/llama.cpp (ROCm)",
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


# Cache del resolve versioni (sola lettura): non interroghiamo l'upstream
# ad ogni poll. La UI mostra le versioni; installazioni e lifecycle sono
# operazioni del core, non del plugin.
_resolve_lock = threading.Lock()
_resolve_cache: dict = {"ts": 0.0, "data": None}
_RESOLVE_TTL = 120.0    # secondi


def _server_json_path() -> Path:
    return hermes_home() / "runtimes" / "llamacpp" / "server.json"


def _store_root() -> Path:
    return hermes_home() / "tools"


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


def _platform_family() -> str | None:
    if os.name == "nt":
        return "win32"
    if sys.platform.startswith("linux"):
        return "linux"
    return None


def _platform_target() -> str | None:
    family = _platform_family()
    if family is None:
        return None
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    return f"{family}-{arch}"


# Sottostringhe platform/backend negli asset pubblici (ggml-org/llama.cpp):
# i nomi sono del tipo llama-b12546-bin-win-vulkan-x64.zip; il numero di
# versione cambia, la sottostringa no. Gli archivi cudart ausiliari non
# contano (prefisso llama-).
_GH_ASSET_PREFIX = {
    "vulkan": {"win32": "win-vulkan-", "linux": "ubuntu-vulkan-"},
    "cuda": {"win32": "win-cuda-", "linux": "ubuntu-cuda-"},
    "rocm": {"win32": "win-rocm-", "linux": "ubuntu-rocm-"},
}
_GH_REPO = "ggml-org/llama.cpp"
_GH_LATEST_TTL = 600.0  # 10 min: l'API anonima ha 60 richieste/ora
_gh_latest_cache: dict = {"ts": 0.0, "data": None}


def _gh_latest_release() -> dict:
    """Ultima release build (tag ``bNNNNN``) di ggml-org/llama.cpp, in cache.

    ``releases/latest`` non va bene: il repo pubblica anche release non-build
    (es. v1.0.0) che GitHub puo' marcare come "latest". Si scorre l'elenco
    delle release e si prende il tag build piu' alto.

    Usa un token GitHub solo se l'utente lo ha gia' impostato nell'ambiente
    (GH_TOKEN/GITHUB_TOKEN): il plugin non lo richiede a `gh` ne' lo scrive.
    """
    now = time.monotonic()
    if _gh_latest_cache["data"] is not None and now - _gh_latest_cache["ts"] <= _GH_LATEST_TTL:
        return _gh_latest_cache["data"]
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "hermes-llama-cpp-plugin"}
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(
        f"https://api.github.com/repos/{_GH_REPO}/releases?per_page=30", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            releases = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise RuntimeError(
                "GitHub rate limit (anonymous: 60 req/h) — set GH_TOKEN or retry later")
        raise RuntimeError(f"GitHub HTTP {exc.code}")
    best: tuple[int, dict] | None = None
    for rel in releases if isinstance(releases, list) else []:
        tag = str(rel.get("tag_name") or "")
        m = re.fullmatch(r"b(\d+)", tag)
        if not m or rel.get("draft"):
            continue
        num = int(m.group(1))
        if best is None or num > best[0]:
            best = (num, rel)
    if best is None:
        raise RuntimeError("no build release (bNNNNN) found on GitHub")
    rel = best[1]
    out = {"tag": str(rel.get("tag_name") or ""),
           "assets": [str(a.get("name") or "") for a in (rel.get("assets") or [])]}
    _gh_latest_cache.update(ts=now, data=out)
    return out


def _installed_build(pkg_name: str) -> str | None:
    """Build installata di un package nello store (sola lettura directory)."""
    target = _platform_target()
    if target is None:
        return None
    root = _store_root()
    best: int | None = None
    try:
        names = os.listdir(root)
    except OSError:
        return None
    for name in names:
        if not name.startswith(pkg_name + "-"):
            continue
        info = _parse_store_entry(name)
        if not info or not info["build"] or info["platform"] != target:
            continue
        if f"llamacpp-{info['variant']}" != pkg_name:
            continue
        try:
            num = int(info["build"])
        except ValueError:
            continue
        if best is None or num > best:
            best = num
    return str(best) if best is not None else None


def _resolve_variant(pkg_name: str, current: str | None) -> dict:
    """Ultima versione disponibile per una fonte (release pubbliche GitHub)."""
    key = _variant_key(pkg_name)
    family = _platform_family()
    prefix = (_GH_ASSET_PREFIX.get(key or "") or {}).get(family or "")
    if not prefix:
        return {"ok": False, "error": "no build published for this platform"}
    try:
        release = _gh_latest_release()
    except Exception as exc:
        return {"ok": False, "error": f"check failed: {exc}"}
    tag = release["tag"]
    version = tag[1:] if tag.startswith("b") else tag
    if not version:
        return {"ok": False, "error": "GitHub release without a valid tag"}
    if not any(n.startswith("llama-") and prefix in n for n in release["assets"]):
        return {"ok": False,
                "error": f"release {tag} does not publish a {family} build for this source"}
    installed = current or _installed_build(pkg_name)
    return {
        "ok": True,
        "package": pkg_name,
        "current": installed,
        "pinned": None,
        "latest": version,
        "update_available": installed != version,
        "reason": "" if installed else "not installed",
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
    """"6.1" -> (6, 1); None se manca o non e' numerico."""
    try:
        major, _, minor = str(cc).strip().partition(".")
        return (int(major), int(minor or 0))
    except (TypeError, ValueError):
        return None


# Le build CUDA ufficiali che il PM propone sono della linea 13.x: CUDA Toolkit
# 13.0 ha rimosso Maxwell, Pascal e Volta (architetture pre-Turing), quindi
# le GPU devono essere sm_75+. Una GTX 1060 (sm_61) non puo' eseguirle: il
# server muore al primo forward con "no kernel image is available".
_CUDA_MIN_COMPUTE = (7, 5)


def _gpu_requirement(key: str, gpu: dict) -> tuple[bool, str | None]:
    """La variante e' adatta alla GPU rilevata? (suitable, nota per la UI).

    Vulkan lo usa quasi ogni GPU; CUDA richiede una GPU NVIDIA sm_75+ (le
    build 13.x hanno eliminato Pascal); ROCm richiede una GPU AMD.
    Le note vanno in colonna "Versione".
    """
    vendor = (gpu or {}).get("vendor")
    name = (gpu or {}).get("name")
    if key == "vulkan":
        if not name:
            return False, "No GPU detected"
        return True, None
    if key == "cuda":
        if vendor != "nvidia":
            return False, "Requires an NVIDIA GPU"
        ct = _compute_tuple((gpu or {}).get("compute_capability"))
        if ct is None:
            return False, "Compute capability not detected (nvidia-smi missing)"
        if ct < _CUDA_MIN_COMPUTE:
            sigla = str((gpu or {}).get("compute_capability")).replace(".", "")
            return False, (
                f"Current CUDA builds require sm_75+ (CUDA 13 dropped "
                f"Maxwell/Pascal/Volta); your GPU is sm_{sigla}"
            )
        return True, None
    if key == "rocm":
        if vendor != "amd":
            return False, "Requires an AMD GPU"
        return True, None
    return True, None


def _variant_note(key: str, gpu: dict) -> str | None:
    """Dettaglio "perche' adatto" mostrato accanto alla versione."""
    if key == "rocm":
        return "AMD GPU · official ROCm build (HIP)"
    if key == "cuda":
        cc = (gpu or {}).get("compute_capability")
        return f"NVIDIA GPU sm_{cc} · CUDA build" if cc else "NVIDIA GPU · CUDA build"
    if key == "vulkan":
        return "Vulkan build (AMD/Intel/NVIDIA)"
    return None


def _resolve_all() -> dict:
    """Stato delle fonti aggiornabili (vulkan, cuda, rocm) per la GPU rilevata.

    ``current`` e' la build del server in esecuzione (per il backend attivo)
    o la build installata nello store (per le altre fonti). Le varianti non
    adatte alla GPU del PC sono marcate ``suitable=False`` e non risolvono
    alcuna versione: la UI le mostra disabilitate con la motivazione.
    """
    active_pkg, active_build = _active_variant()
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
        info["suitable"] = True
        info["note"] = _variant_note(key, gpu)
        variants[key] = info
    return {"active": active_pkg or None, "variants": variants, "gpu": gpu}


@router.get("/update")
async def update_check():
    """Sola lettura: build installata + ultime versioni note per le fonti
    (build ufficiali ggml-org/llama.cpp: Vulkan, CUDA e ROCm/HIP).
    Il resolve viene interrogato al massimo ogni ``_RESOLVE_TTL`` secondi.
    Stop/start/restart e installazioni sono operazioni del core
    (Impostazioni -> Modelli locali); il plugin non le esegue.
    """
    now = time.monotonic()
    with _resolve_lock:
        check = _resolve_cache["data"] if now - _resolve_cache["ts"] <= _RESOLVE_TTL else None
    if check is None:
        check = await asyncio.to_thread(_resolve_all)
        with _resolve_lock:
            _resolve_cache.update(ts=now, data=check)

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
    return JSONResponse({
        "variants": variants,
        "gpu": check.get("gpu"),
        "active": check.get("active"),
        "active_key": _variant_key(check.get("active")),
    })


# ------------------------------------------------------------------ bench

def _bench_history_path() -> Path:
    return hermes_home() / "runtimes" / "llamacpp" / "bench.json"


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
        return {"ok": False, "error": "server not running (runtimes/llamacpp/server.json missing)"}
    if not status_snapshot().get("running"):
        return {"ok": False, "error": "llama.cpp server not running"}
    if not _bench_model_loaded(model):
        load_model_async(model)
        for _ in range(180):
            time.sleep(1.0)
            if _bench_model_loaded(model):
                break
        if not _bench_model_loaded(model):
            return {"ok": False, "error": f"model not loaded: {model}"}
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
        raise HTTPException(status_code=400, detail="model missing")
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
# Elenco in sola lettura delle build llama.cpp installate nello store (tools/).
# La selezione e la rimozione richiedevano internals privati del PM (re-pin
# del lockfile, facts.json, cancellazione store): sono state rimosse in attesa
# di una route pubblica del core ("select/remove installed llama.cpp build",
# issue upstream NousResearch/hermes-agent#157878). Il backend attivo resta
# quello configurato nel runtime.

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
    """Tutte le build llama.cpp nello store (sola lettura, nessun internals PM).

    ``active`` e' la build che il server sta eseguendo (server.json): una
    build appena aggiornata puo' essere installata senza essere in esecuzione.
    """
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
        out.append({
            "entry": name,
            "package": pkg_name,
            "variant": _display_variant(info["variant"]),
            "display": _display_name(info["variant"]),
            "build": info["build"],
            "platform": info["platform"],
            "deprecated": info["deprecated"],
            "active": active,
            "size_mb": _entry_size_mb(entry),
        })
    return out


@router.get("/builds")
async def list_builds():
    """Build llama.cpp installate nello store, con build attiva (sola lettura)."""
    return JSONResponse({"builds": await asyncio.to_thread(_list_builds)})