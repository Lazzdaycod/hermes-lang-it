"""Live GPU utilization + dedicated VRAM from Windows PDH counters.

Uses pdh.dll through ctypes: no pywin32 dependency, no subprocess, no port.
English counter names (PdhAddEnglishCounterW) work on any Windows language.
The first PdhCollectQueryData only initialises the counters, so every read
takes two samples a few hundred ms apart and returns the second one.
"""

from __future__ import annotations

import ctypes as ct
import ctypes.wintypes as wt
import re
import time

_PERF_DETAIL_WIZARD = 400
_PDH_FMT_DOUBLE = 0x0200
_PDH_FMT_LARGE = 0x0400
_SAMPLE_GAP_S = 0.45

_PID_RE = re.compile(r"pid_(\d+)")
_ENGINE_RE = re.compile(r"engtype_(.+)$")

try:
    from .gpu_temp import read_temperature
except ImportError:
    try:
        from gpu_temp import read_temperature
    except ImportError:
        def read_temperature():
            return None



class _CounterValue(ct.Structure):
    _fields_ = [
        ("status", wt.DWORD),
        ("doubleValue", ct.c_double),
        ("largeValue", ct.c_longlong),
    ]


class _Pdh:
    """Thin ctypes binding for the handful of PDH functions we need."""

    def __init__(self) -> None:
        dll = ct.WinDLL("pdh.dll")

        self.open = dll.PdhOpenQueryW
        self.open.argtypes = [wt.LPCWSTR, ct.c_size_t, ct.POINTER(wt.HANDLE)]
        self.open.restype = wt.DWORD

        self.add = dll.PdhAddEnglishCounterW
        self.add.argtypes = [wt.HANDLE, wt.LPCWSTR, ct.c_size_t, ct.POINTER(wt.HANDLE)]
        self.add.restype = wt.DWORD

        self.collect = dll.PdhCollectQueryData
        self.collect.argtypes = [wt.HANDLE]
        self.collect.restype = wt.DWORD

        self.value = dll.PdhGetFormattedCounterValue
        self.value.argtypes = [
            wt.HANDLE, wt.DWORD, ct.POINTER(wt.DWORD), ct.POINTER(_CounterValue)
        ]
        self.value.restype = wt.DWORD

        self.close = dll.PdhCloseQuery
        self.close.argtypes = [wt.HANDLE]
        self.close.restype = wt.DWORD

        self.enum = dll.PdhEnumObjectItemsW
        self.enum.argtypes = [
            wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR,
            wt.LPWSTR, ct.POINTER(wt.DWORD),
            wt.LPWSTR, ct.POINTER(wt.DWORD),
            wt.DWORD, wt.DWORD,
        ]
        self.enum.restype = wt.DWORD

    def instances(self, obj: str) -> list[str]:
        counters = wt.DWORD(0)
        instances = wt.DWORD(0)
        self.enum(None, None, obj, None, ct.byref(counters), None,
                  ct.byref(instances), _PERF_DETAIL_WIZARD, 0)
        if not instances.value:
            return []
        counter_buf = (wt.WCHAR * max(counters.value, 1))()
        buf = (wt.WCHAR * instances.value)()
        status = self.enum(None, None, obj, counter_buf, ct.byref(counters), buf,
                           ct.byref(instances), _PERF_DETAIL_WIZARD, 0)
        if status != 0:
            return []
        raw = ct.string_at(buf, instances.value * ct.sizeof(wt.WCHAR))
        return [item for item in raw.decode("utf-16-le", "replace").split("\x00") if item]


_api = None
_api_failed = False


def _get_api():
    global _api, _api_failed
    if _api is None and not _api_failed:
        try:
            _api = _Pdh()
        except Exception:
            _api_failed = True
    return _api


def _engine_label(instance: str) -> str:
    match = _ENGINE_RE.search(instance)
    return match.group(1) if match else "GPU"


def _pid(instance: str) -> int | None:
    match = _PID_RE.search(instance)
    return int(match.group(1)) if match else None


def _read_gpu_base() -> dict:
    """Return GPU usage, dedicated VRAM and the busiest engines.

    Shape: available, util_percent, vram_used_bytes, engines, source, error.
    Never raises: a missing pdh.dll or a counter failure returns available=False.
    """
    api = _get_api()
    if api is None:
        return {
            "available": False,
            "util_percent": None,
            "vram_used_bytes": None,
            "engines": [],
            "source": "Windows PDH (pdh.dll)",
            "error": "PDH non disponibile",
        }

    query = wt.HANDLE()
    if api.open(None, 0, ct.byref(query)) != 0:
        return {
            "available": False, "util_percent": None, "vram_used_bytes": None,
            "engines": [], "source": "Windows PDH (pdh.dll)",
            "error": "PdhOpenQuery non riuscita",
        }

    counters: list[tuple[str, str, wt.HANDLE]] = []
    try:
        for instance in api.instances("GPU Engine"):
            handle = wt.HANDLE()
            path = "\\GPU Engine(" + instance + ")\\Utilization Percentage"
            if api.add(query, path, 0, ct.byref(handle)) == 0:
                counters.append(("util", instance, handle))

        for instance in api.instances("GPU Adapter Memory"):
            handle = wt.HANDLE()
            path = "\\GPU Adapter Memory(" + instance + ")\\Dedicated Usage"
            if api.add(query, path, 0, ct.byref(handle)) == 0:
                counters.append(("mem", instance, handle))

        if not counters:
            return {
                "available": False, "util_percent": None, "vram_used_bytes": None,
                "engines": [], "source": "Windows PDH (GPU Engine)",
                "error": "nessun contatore GPU trovato",
            }

        api.collect(query)
        time.sleep(_SAMPLE_GAP_S)
        api.collect(query)

        total = 0.0
        vram = 0
        engines: list[dict] = []
        for kind, instance, handle in counters:
            sample = _CounterValue()
            typ = wt.DWORD()
            if api.value(handle, _PDH_FMT_DOUBLE, ct.byref(typ), ct.byref(sample)) != 0:
                continue
            if kind == "util":
                pct = sample.doubleValue
                if pct > 0.05:
                    total += pct
                    engines.append({
                        "pid": _pid(instance),
                        "engine": _engine_label(instance),
                        "percent": round(pct, 1),
                    })
            else:
                vram = max(vram, int(sample.doubleValue))

        engines.sort(key=lambda item: item["percent"], reverse=True)
        return {
            "available": True,
            "util_percent": round(total, 1),
            "vram_used_bytes": vram or None,
            "engines": engines[:4],
            "source": "Windows PDH · GPU Engine + GPU Adapter Memory",
            "error": None,
        }
    except Exception as exc:
        return {
            "available": False, "util_percent": None, "vram_used_bytes": None,
            "engines": [], "source": "Windows PDH (GPU Engine)",
            "error": str(exc),
        }
    finally:
        try:
            api.close(query)
        except Exception:
            pass


def read_gpu() -> dict:
    """Snapshot completo: contatori PDH + temperatura D3DKMT."""
    data = _read_gpu_base()
    data["temp_c"] = read_temperature()
    return data
