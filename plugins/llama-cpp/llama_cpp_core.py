"""llama-studio — core logic shared by the API router and the agent tool.

Reads the state of the Hermes-managed llama.cpp runtime (runtimes/llamacpp)
and applies per-model presets. No external process, no credentials exposed.

Preset keys (presets.ini, one section per model id):
    ctx-size, batch-size, ubatch-size, cache-type-k, cache-type-v,
    flash-attn, override-tensor, n-gpu-layers, model, temp, top-k, top-p

The router (llama-server --models-max) re-reads presets.ini on
GET /models?reload=1: new launch flags apply to unloaded models, and a
loaded model whose flags changed is unloaded so the next request restarts
it with the new flags.
"""

from __future__ import annotations

import configparser
import json
import os
import platform
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

VALID_CACHE_TYPES = ("f16", "bf16", "q8_0", "q4_0")
VALID_FLASH_ATTN = ("on", "off", "auto")


def _load_yaml(path: Path) -> dict:
    """Parse a YAML config file. PyYAML is missing in the dashboard venv,
    so prefer hermes_yaml and fall back to ruamel."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception:
        return {}
    try:
        from hermes_yaml import safe_load  # type: ignore
        data = safe_load(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    try:
        from ruamel.yaml import YAML  # type: ignore
        data = YAML(typ="safe").load(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

# Option key (API) -> presets.ini key
OPTION_MAP = {
    "ctx_size": "ctx-size",
    "batch_size": "batch-size",
    "ubatch_size": "ubatch-size",
    "cache_type_k": "cache-type-k",
    "cache_type_v": "cache-type-v",
    "flash_attn": "flash-attn",
    "temp": "temp",
    "top_k": "top-k",
    "top_p": "top-p",
    "repeat_penalty": "repeat-penalty",
    "n_gpu_layers": "n-gpu-layers",
    "cache_ram": "cache-ram",
}

DEFAULT_OPTIONS = {
    "ctx_size": 65000,
    "batch_size": 2048,
    "ubatch_size": 2048,
    "n_gpu_layers": 999,
    "cache_type_k": "q8_0",
    "cache_type_v": "q8_0",
    "flash_attn": "on",
    "temp": 1.0,
    "top_k": 20,
    "top_p": 0.95,
    "repeat_penalty": 1.0,
    "cache_ram": 8192,
}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def hermes_home() -> Path:
    try:
        from hermes_cli.config import get_default_hermes_root  # type: ignore
        p = get_default_hermes_root()
        if p:
            return Path(p)
    except Exception:
        pass
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "hermes"
    return Path.home() / ".hermes"


def runtimes_dir() -> Path:
    return hermes_home() / "runtimes" / "llamacpp"


def models_dir() -> Path:
    return hermes_home() / "models"


def config_path() -> Path:
    return hermes_home() / "config.yaml"


def presets_path() -> Path:
    return runtimes_dir() / "presets.ini"


def server_state_path() -> Path:
    return runtimes_dir() / "server.json"


def window_overrides_path() -> Path:
    return runtimes_dir() / "window_overrides.json"


# ---------------------------------------------------------------------------
# Server state / backend detection
# ---------------------------------------------------------------------------

def read_server_state() -> dict:
    """Contents of server.json ({} when missing/corrupt)."""
    try:
        return json.loads(server_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def detect_backend(state: dict | None = None) -> dict:
    """Backend + build tag from the supervised executable path.

    Example path: .../tools/llamacpp-vulkan-11671-win32-x64/llama-server.exe
    -> {"backend": "vulkan", "build": "11671"}

    Le variant possono essere multi-parola (es. la build Lemonade
    ``llamacpp-rocm-lemonade-1369-...`` -> backend ``rocm-lemonade``).
    """
    state = state if state is not None else read_server_state()
    exe = str(state.get("executable") or "")
    m = re.search(r"llamacpp-([a-z0-9]+(?:-[a-z0-9]+)*?)-(\d+)", exe)
    if m:
        return {"backend": m.group(1), "build": m.group(2), "executable": exe}
    # Fallback: the user-chosen preference in config.yaml (local_runtime.backend).
    data = _load_yaml(config_path())
    pref = ((data.get("local_runtime") or {}).get("backend") or "").lower()
    if pref in ("vulkan", "cuda", "rocm", "rocm-lemonade", "cpu"):
        return {"backend": pref, "build": None, "executable": exe, "from_config": True}
    return {"backend": "unknown", "build": None, "executable": exe}


def router_probe(timeout_s: float = 4.0) -> dict:
    """Probe the managed router: running flag + port + pid. No key exposed."""
    state = read_server_state()
    port = None
    m = re.search(r":(\d{2,5})/", str(state.get("base_url") or ""))
    if m:
        port = int(m.group(1))
    pid = state.get("pid")
    out = {"running": False, "port": port, "pid": pid, "models": {}}
    if not port:
        return out
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/models",
            headers={"Authorization": f"Bearer {state.get('api_key') or ''}"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            data = json.loads(r.read() or b"{}")
        out["running"] = True
        for item in data.get("data", []):
            out["models"][item.get("id")] = (item.get("status") or {}).get("value", "unknown")
    except Exception:
        pass
    return out


def _router_request(route: str, body: dict | None = None, timeout_s: float = 30.0) -> dict:
    state = read_server_state()
    m = re.search(r":(\d{2,5})/", str(state.get("base_url") or ""))
    if not m:
        raise RuntimeError("no managed router state (runtimes/llamacpp/server.json)")
    headers = {"Authorization": f"Bearer {state.get('api_key') or ''}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:{m.group(1)}{route}",
        headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def router_reload_presets() -> bool:
    """Ask the router to re-read presets.ini (GET /models?reload=1)."""
    try:
        _router_request("/models?reload=1", timeout_s=15)
        return True
    except Exception:
        return False


def _router_action_async(route: str, model_id: str) -> dict:
    """Fire a blocking router action (load/unload) in a daemon thread.

    Loading a model can take minutes, so the API responds immediately and
    the UI polls /status for the real state.
    """
    model_id = str(model_id or "").strip()
    if not model_id:
        return {"ok": False, "error": "model is required"}
    if not router_probe(timeout_s=3.0).get("running"):
        return {"ok": False, "error": "router not running"}

    def _work() -> None:
        try:
            _router_request(route, {"model": model_id}, timeout_s=900)
        except Exception:
            pass  # fire-and-forget; state is visible via /status

    threading.Thread(target=_work, daemon=True).start()
    return {"ok": True, "model": model_id, "started": True}


def load_model_async(model_id: str) -> dict:
    """Ask the router to load a model into VRAM in the background."""
    return _router_action_async("/models/load", model_id)


def unload_model_async(model_id: str) -> dict:
    """Ask the router to free a model's VRAM in the background."""
    return _router_action_async("/models/unload", model_id)


# ---------------------------------------------------------------------------
# Presets (presets.ini)
# ---------------------------------------------------------------------------

def read_presets() -> dict:
    """{section: {key: value}} from presets.ini ({} when missing)."""
    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read_string(presets_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {section: dict(parser.items(section)) for section in parser.sections()}


def write_presets(sections: dict) -> None:
    """Rewrite presets.ini preserving section order (RawConfigParser, no % magic)."""
    parser = configparser.RawConfigParser(strict=False)
    for section, items in sections.items():
        parser.add_section(section)
        for key, value in items.items():
            parser.set(section, key, str(value))
    path = presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        parser.write(fh)


def read_window_overrides() -> dict:
    try:
        with open(window_overrides_path(), encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return {str(k): int(v) for k, v in data.items()}
    except Exception:
        return {}


def save_window_override(model_id: str, window: int) -> None:
    overrides = read_window_overrides()
    overrides[str(model_id)] = int(window)
    path = window_overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(overrides, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# Models on disk
# ---------------------------------------------------------------------------

def _is_split_part(name: str) -> bool:
    return bool(re.search(r"(-part-|\d{5}-of-\d{5})", name))


def list_models(extra_dirs: list[str] | None = None) -> dict:
    """Disk models + router status + preset membership.

    Returns {"models": [...], "orphans": [...]} where orphans are preset
    sections whose .gguf is no longer on disk.
    """
    presets = read_presets()
    probe = router_probe()
    router_status = probe.get("models") or {}

    dirs = [models_dir()]
    for raw in extra_dirs or []:
        try:
            p = Path(os.path.expandvars(os.path.expanduser(str(raw))).strip())
            if p.is_dir() and p not in dirs:
                dirs.append(p)
        except Exception:
            continue

    entries: list[dict] = []
    seen: set[str] = set()
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix.lower() != ".gguf" or not f.is_file() or _is_split_part(f.name):
                continue
            model_id = f.stem
            if model_id in seen:
                continue
            seen.add(model_id)
            try:
                size = f.stat().st_size
            except OSError:
                size = 0
            entries.append({
                "id": model_id,
                "path": str(f),
                "size_bytes": size,
                "size_human": _human_size(size),
                "quant": _quant(model_id),
                "status": router_status.get(model_id, "unknown"),
                "in_preset": model_id in presets,
                "active": model_id == active_model(),
            })

    orphans = []
    for section in presets:
        if section in seen:
            continue
        # The preset may point at a .gguf outside the scanned dirs — honor it.
        preset_path = str((presets.get(section) or {}).get("model") or "")
        if preset_path and Path(preset_path).exists():
            try:
                size = Path(preset_path).stat().st_size
            except OSError:
                size = 0
            entries.append({
                "id": section,
                "path": preset_path,
                "size_bytes": size,
                "size_human": _human_size(size),
                "quant": _quant(section),
                "status": router_status.get(section, "unknown"),
                "in_preset": True,
                "active": section == active_model(),
                "from_preset_path": True,
            })
        else:
            orphans.append({
                "id": section, "in_preset": True, "status": router_status.get(section, "unknown"),
                "active": section == active_model(), "path": preset_path,
                "size_bytes": 0, "size_human": None, "quant": _quant(section),
            })
    entries.sort(key=lambda e: (not e["active"], e["id"]))
    return {"models": entries, "orphans": orphans, "router": probe}


def _human_size(n: int) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return "?"


def _quant(name: str) -> str:
    m = re.search(r"(Q\d+_[A-Z0-9_]+|I\d+_[A-Z0-9_]+|BF16|F16)", name, re.IGNORECASE)
    return m.group(1).upper() if m else "?"


# ---------------------------------------------------------------------------
# Active model (config.yaml -> model.default)
# ---------------------------------------------------------------------------

def active_model() -> str | None:
    data = _load_yaml(config_path())
    return (data.get("model") or {}).get("default")


def set_active_model(model_id: str) -> dict:
    """Set model.default in config.yaml. Prefers Hermes's own save_config
    (same path `hermes config set` uses); falls back to a targeted YAML edit."""
    model_id = str(model_id).strip()
    if not model_id:
        return {"ok": False, "error": "empty model id"}
    try:
        from hermes_cli.config import save_config  # type: ignore
        save_config({"model": {"default": model_id}}, merge_existing=True)
        return {"ok": True, "method": "save_config", "active_model": active_model()}
    except Exception as exc:
        try:
            import hermes_yaml  # type: ignore
            path = config_path()
            data = _load_yaml(path)
            data.setdefault("model", {})["default"] = model_id
            path.write_text(
                hermes_yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
                encoding="utf-8")
            return {"ok": True, "method": "yaml-fallback", "active_model": active_model()}
        except Exception as exc2:
            return {"ok": False, "error": f"save_config: {exc}; yaml-fallback: {exc2}"}


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _validate_options(options: dict) -> list[str]:
    errors: list[str] = []
    for key in ("ctx_size", "batch_size", "ubatch_size", "top_k", "cache_ram"):
        if key in options and options[key] is not None:
            try:
                v = int(options[key])
                options[key] = v
                lo, hi = {"ctx_size": (256, 262144), "batch_size": (16, 32768),
                          "ubatch_size": (16, 32768), "top_k": (1, 256),
                          "cache_ram": (0, 65536)}[key]
                if not lo <= v <= hi:
                    errors.append(f"{key} must be between {lo} and {hi}")
            except (TypeError, ValueError):
                errors.append(f"{key} must be an integer")
    # n_gpu_layers: "" (or None) means "remove the key"; otherwise an int 0..999
    # (999 = all layers on the GPU/VRAM, 0 = everything on the CPU/RAM).
    if options.get("n_gpu_layers") not in (None, ""):
        try:
            v = int(options["n_gpu_layers"])
            options["n_gpu_layers"] = v
            if not 0 <= v <= 999:
                errors.append("n_gpu_layers must be between 0 and 999")
        except (TypeError, ValueError):
            errors.append("n_gpu_layers must be an integer")
    for key in ("temp", "top_p", "repeat_penalty"):
        if key in options and options[key] is not None:
            try:
                v = float(options[key])
                options[key] = v
                lo, hi = {"temp": (0.0, 2.0), "top_p": (0.0, 1.0),
                          "repeat_penalty": (0.0, 2.0)}[key]
                if not lo <= v <= hi:
                    errors.append(f"{key} must be between {lo} and {hi}")
            except (TypeError, ValueError):
                errors.append(f"{key} must be a number")
    for key in ("cache_type_k", "cache_type_v"):
        if key in options and options[key] is not None:
            if str(options[key]).lower() not in VALID_CACHE_TYPES:
                errors.append(f"{key} must be one of {', '.join(VALID_CACHE_TYPES)}")
    if options.get("flash_attn") is not None:
        if str(options["flash_attn"]).lower() not in VALID_FLASH_ATTN:
            errors.append(f"flash_attn must be one of {', '.join(VALID_FLASH_ATTN)}")
    if not errors:
        bs, us = options.get("batch_size"), options.get("ubatch_size")
        if bs is not None and us is not None and us > bs:
            errors.append("ubatch_size cannot exceed batch_size")
    return errors


def apply_preset(model_id: str, options: dict | None = None,
                 set_active: bool = False) -> dict:
    """Write the model's preset section, the ctx window override and (optionally)
    the active model, then ask the router to reload the preset file."""
    model_id = str(model_id or "").strip()
    if not model_id:
        return {"ok": False, "error": "model is required"}
    # "" is kept: it means "remove this key from the section".
    options = {k: v for k, v in (options or {}).items() if v is not None}
    errors = _validate_options(options)
    if errors:
        return {"ok": False, "error": "; ".join(errors)}

    # Resolve the .gguf path: existing preset section, else the Hermes models dir.
    presets = read_presets()
    section = dict(presets.get(model_id) or {})
    model_path = section.get("model")
    if not model_path or not Path(model_path).exists():
        candidate = models_dir() / f"{model_id}.gguf"
        if candidate.exists():
            model_path = str(candidate)
    if not model_path or not Path(model_path).exists():
        return {"ok": False,
                "error": f"no .gguf found for '{model_id}' (models dir: {models_dir()})"}

    for api_key, ini_key in OPTION_MAP.items():
        if api_key in options:
            value = options[api_key]
            if value == "":
                section.pop(ini_key, None)
                continue
            if isinstance(value, float) and value.is_integer() and ini_key in ("temp", "repeat-penalty", "cache-ram"):
                value = int(value)
            section[ini_key] = value if not isinstance(value, bool) else str(value).lower()
    section["model"] = model_path

    try:
        write_presets({**presets, model_id: section})
    except Exception as exc:
        return {"ok": False, "error": f"failed writing presets.ini: {exc}"}

    result = {"ok": True, "model": model_id, "preset": section, "reloaded": False,
              "window_override": None, "active": None}

    if "ctx_size" in options:
        try:
            save_window_override(model_id, int(options["ctx_size"]))
            result["window_override"] = int(options["ctx_size"])
        except Exception as exc:
            result["window_override"] = None
            result.setdefault("warnings", []).append(f"window override: {exc}")

    if set_active:
        result["active"] = set_active_model(model_id)
        if not result["active"].get("ok"):
            result["ok"] = False

    if router_probe(timeout_s=3.0).get("running"):
        result["reloaded"] = router_reload_presets()
    return result


# ---------------------------------------------------------------------------
# System info and Hermes local models
# ---------------------------------------------------------------------------

_SYSTEM_INFO: dict | None = None
_SYSTEM_INFO_TS: float = 0.0
_SYSTEM_TTL = 600.0  # 10 minuti: l'hardware non cambia di frequente


def _first(v):
    """PowerShell 5.1 sometimes wraps scalars in single-item lists: normalize."""
    return v[0] if isinstance(v, (list, tuple)) and v else v


def system_info() -> dict:
    """CPU, RAM, GPU, OS. On Windows via PowerShell (Get-CimInstance).
    Cached for 10 minutes."""
    global _SYSTEM_INFO, _SYSTEM_INFO_TS
    now = time.time()
    if _SYSTEM_INFO is not None and now - _SYSTEM_INFO_TS < _SYSTEM_TTL:
        return _SYSTEM_INFO
    info: dict = {
        "os": f"{platform.system()} {platform.release()}",
        "cpu": None,
        "cores_physical": None,
        "cores_logical": os.cpu_count(),
        "clock_mhz": None,
        "ram_total_gb": None,
        "ram_free_gb": None,
        "gpus": [],
    }
    if os.name == "nt":
        try:
            ps = (
                "$p=Get-CimInstance Win32_Processor;"
                "$g=@(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name | Sort-Object -Unique);"
                "@{'cpu'=($p.Name -join ' ');"
                "'cores'=($p | Measure-Object -Property NumberOfCores -Sum).Sum;"
                "'clock'=($p | Measure-Object -Property MaxClockSpeed -Maximum).Maximum;"
                "'ramTotal'=[math]::Round((Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize/1MB,1);"
                "'ramFree'=[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,1);"
                "'gpus'=$g} | ConvertTo-Json -Compress"
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=15,
            )
            data = json.loads(r.stdout.strip().splitlines()[-1])
            info["cpu"] = (_first(data.get("cpu")) or "").strip() or None
            info["cores_physical"] = _first(data.get("cores"))
            info["clock_mhz"] = _first(data.get("clock"))
            info["ram_total_gb"] = data.get("ramTotal")
            info["ram_free_gb"] = data.get("ramFree")
            g = data.get("gpus")
            info["gpus"] = [g] if isinstance(g, str) else (g or [])
        except Exception:
            pass
    _SYSTEM_INFO, _SYSTEM_INFO_TS = info, now
    return info


def _gpu_vendor(name: str | None) -> str:
    """Vendor dalla stringa del nome GPU (WMI/nvidia-smi)."""
    low = (name or "").lower()
    if any(k in low for k in ("nvidia", "geforce", "quadro", "tesla", "rtx", "gtx")):
        return "nvidia"
    if any(k in low for k in ("radeon", "amd", "instinct")):
        return "amd"
    if any(k in low for k in ("arc", "intel", "iris", "uhd")):
        return "intel"
    return "other"


def detect_gpu() -> dict:
    """GPU primaria rilevata (modello specifico) + vendor + bucket AMD.

    La UI parte da qui: i backend mostrati come "adatti" e le note per quelli
    no si basano su questa rilevazione, non su hardcoded 7900 XTX. La GPU
    primaria e' la discreta preferita (NVIDIA > AMD > Intel) tra quelle che
    l'OS dichiara; il nome e' il modello specifico ("AMD Radeon RX 7900
    XTX", "NVIDIA GeForce RTX 4090", ...).
    """
    info = system_info()
    names = [str(n).strip() for n in (info.get("gpus") or []) if str(n).strip()]

    def _rank(n: str) -> int:
        return {"nvidia": 0, "amd": 1}.get(_gpu_vendor(n), 2)

    ordered = sorted(names, key=_rank)
    name = ordered[0] if ordered else None
    vendor = _gpu_vendor(name) if name else None
    amd_gfx = None
    if vendor == "amd":
        try:
            from pm.packages import _amd_gfx_bucket
            amd_gfx = _amd_gfx_bucket() or None
        except Exception:
            amd_gfx = None
    return {
        "names": names,
        "name": name,
        "vendor": vendor,
        "model": (name or "").strip() or None,
        "amd_gfx": amd_gfx,
        "cuda": vendor == "nvidia",
    }


_HERMES_LOCAL: list | None = None
_HERMES_LOCAL_TS: float = 0.0
_HERMES_LOCAL_TTL = 60.0  # 60 secondi

_LOCAL_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "[::1]")


def hermes_local_models() -> list:
    """Local AI models used by Hermes: providers with a local base_url in
    config.yaml, queried via their OpenAI-compatible /models endpoint.
    Cached for 60 seconds."""
    global _HERMES_LOCAL, _HERMES_LOCAL_TS
    now = time.time()
    if _HERMES_LOCAL is not None and now - _HERMES_LOCAL_TS < _HERMES_LOCAL_TTL:
        return _HERMES_LOCAL
    out: list = []
    default_model = None
    try:
        text = config_path().read_text(encoding="utf-8-sig")
    except Exception:
        text = ""
    dm = re.search(r"^\s*default\s*:\s*['\"]?([\w./-]+)", text, re.M)
    if dm:
        default_model = dm.group(1)
    # Active model block: provider name / api_key / base_url. The api_key
    # (when present) is sent to the matching base_url probe only.
    mb_url = None
    mb_key = None
    mb_provider = None
    mblk = re.search(r"^model:\s*$", text, re.M)
    if mblk:
        rest = text[mblk.end():]
        nxt = re.search(r"^[^#\s]", rest, re.M)
        section = rest[: nxt.start()] if nxt else rest
        k = re.search(r'^\s*api_key\s*:\s*["\']?([^\s"\']+)', section, re.M)
        if k:
            mb_key = k.group(1)
        k = re.search(r'^\s*provider\s*:\s*["\']?([^\s"\']+)', section, re.M)
        if k:
            mb_provider = k.group(1)
        k = re.search(r'^\s*base_url\s*:\s*["\']?(https?://[^\s"\']+)', section, re.M)
        if k:
            mb_url = k.group(1)
        bu_re = re.compile(r"^\s*(?:base_url|baseUrl)\s*:\s*['\"]?(https?://[^\s'\"]+)", re.M)
    seen = set()
    for m in bu_re.finditer(text):
        bu = m.group(1)
        host_m = re.match(r"https?://([^/]+)", bu)
        if not host_m or host_m.group(1).split(":")[0] not in _LOCAL_HOSTS:
            continue
        if bu in seen:
            continue
        seen.add(bu)
        entry = {
            "provider": host_m.group(1),
            "base_url": bu,
            "models": [],
            "default_model": None,
            "error": None,
        }
        url = bu.rstrip("/")
        url = url[:-7] if url.endswith("/models") else url + "/models"
        try:
            headers = {"Accept": "application/json"}
            if mb_key:
                if mb_url is None:
                    use_key = True
                else:
                    hm2 = re.match("https?://([^/]+)", mb_url)
                    use_key = bool(
                        hm2
                        and hm2.group(1).split(":")[0]
                        == host_m.group(1).split(":")[0]
                    )
                if use_key:
                    headers["Authorization"] = "Bearer " + mb_key
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read().decode("utf-8"))
            ids = [d.get("id") for d in data.get("data", [])
                   if isinstance(d, dict) and d.get("id")]
            entry["models"] = ids
            if default_model and default_model in ids:
                entry["default_model"] = default_model
        except Exception as exc:
            entry["error"] = str(exc)[:160]
        out.append(entry)
    _HERMES_LOCAL, _HERMES_LOCAL_TS = out, now
    return out


# ---------------------------------------------------------------------------
# Status snapshot (single call, used by /status and the agent tool)
# ---------------------------------------------------------------------------

_SERVER_CMD: str | None = None
_SERVER_CMD_AT: float = 0.0


def server_command() -> str | None:
    """Command line of the running llama-server process (cached 60s).

    Windows: PowerShell Get-CimInstance (no wmic dependency); POSIX: /proc.
    """
    global _SERVER_CMD, _SERVER_CMD_AT
    if _SERVER_CMD is not None and time.time() - _SERVER_CMD_AT < 60:
        return _SERVER_CMD
    state = read_server_state()
    pid = state.get("pid")
    cmd: str | None = None
    try:
        if pid:
            if os.name == "nt":
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').CommandLine" % int(pid)],
                    capture_output=True, text=True, timeout=15)
                cmd = (r.stdout or "").strip() or None
            elif os.path.exists("/proc/%s/cmdline" % pid):
                raw = Path("/proc/%s/cmdline" % pid).read_bytes()
                cmd = raw.replace(b"\0", b" ").decode(errors="replace").strip() or None
    except Exception:
        cmd = None
    _SERVER_CMD, _SERVER_CMD_AT = cmd, time.time()
    return cmd


def status_snapshot() -> dict:
    backend = detect_backend()
    probe = router_probe()
    presets = read_presets()
    try:
        system = system_info()
    except Exception:
        system = None
    try:
        local_models = hermes_local_models()
    except Exception:
        local_models = []
    return {
        "backend": backend.get("backend", "unknown"),
        "build": backend.get("build"),
        "executable": backend.get("executable") or None,
        "backend_from_config": bool(backend.get("from_config")),
        "running": bool(probe.get("running")),
        "port": probe.get("port"),
        "pid": probe.get("pid"),
        "active_model": active_model(),
        "models_dir": str(models_dir()),
        "router_models": probe.get("models") or {},
        "presets": presets,
        "server_command": server_command() if probe.get("running") else None,
        "window_overrides": read_window_overrides(),
        "valid_cache_types": list(VALID_CACHE_TYPES),
        "valid_flash_attn": list(VALID_FLASH_ATTN),
        "default_options": dict(DEFAULT_OPTIONS),
        "system": system,
        "hermes_local_models": local_models,
    }
