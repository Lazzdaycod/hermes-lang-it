"""GPU temperature from Windows D3DKMT (gdi32.dll), in ctypes only.

No vendor SDK, no subprocess: KMTQAITYPE_ADAPTERPERFDATA is exposed by the
Windows kernel-mode driver for any WDDM adapter that reports thermal data
(verified on AMD Radeon RX 7900 XTX). Temperature arrives in deci-degrees C.
Reads fail soft: None when the adapter answers nothing usable.
"""

from __future__ import annotations

import ctypes as ct

_c_uint, _c_int, _c_void_p = ct.c_uint, ct.c_int, ct.c_void_p
_c_ulong, _c_ulonglong, _c_ubyte = ct.c_ulong, ct.c_ulonglong, ct.c_ubyte

class _Luid(ct.Structure):
    _fields_ = [("LowPart", _c_ulong), ("HighPart", _c_int)]

class _EnumAdapters2(ct.Structure):
    _fields_ = [("NumAdapters", _c_uint), ("pAdapters", _c_void_p)]

class _AdapterInfo(ct.Structure):
    _fields_ = [
        ("hAdapter", _c_uint), ("AdapterLuid", _Luid),
        ("NumOfSources", _c_uint), ("bPresentMoveRegionsPreferred", _c_int),
    ]

class _OpenFromLuid(ct.Structure):
    _fields_ = [("AdapterLuid", _Luid), ("hAdapter", _c_uint)]

class _CloseAdapter(ct.Structure):
    _fields_ = [("hAdapter", _c_uint)]

class _QueryAdapterInfo(ct.Structure):
    _fields_ = [
        ("hAdapter", _c_uint), ("Type", _c_int),
        ("pPrivateDriverData", _c_void_p), ("PrivateDriverDataSize", _c_uint),
    ]

class _AdapterPerfData(ct.Structure):
    _fields_ = [
        ("PhysicalAdapterIndex", _c_uint),
        ("MemoryFrequency", _c_ulonglong), ("MaxMemoryFrequency", _c_ulonglong),
        ("MaxMemoryFrequencyOC", _c_ulonglong), ("MemoryBandwidth", _c_ulonglong),
        ("PCIEBandwidth", _c_ulonglong), ("FanRPM", _c_uint), ("Power", _c_uint),
        ("Temperature", _c_uint), ("PowerStateOverride", _c_ubyte),
    ]

_KMTQAITYPE_ADAPTERPERFDATA = 62
_MAX_ADAPTERS = 8  # enough for the discrete card; stop at the first live reading


def _bind():
    gdi = ct.WinDLL("gdi32")
    gdi.D3DKMTEnumAdapters2.argtypes = [ct.POINTER(_EnumAdapters2)]
    gdi.D3DKMTEnumAdapters2.restype = ct.c_long
    gdi.D3DKMTOpenAdapterFromLuid.argtypes = [ct.POINTER(_OpenFromLuid)]
    gdi.D3DKMTOpenAdapterFromLuid.restype = ct.c_long
    gdi.D3DKMTCloseAdapter.argtypes = [ct.POINTER(_CloseAdapter)]
    gdi.D3DKMTCloseAdapter.restype = ct.c_long
    gdi.D3DKMTQueryAdapterInfo.argtypes = [ct.POINTER(_QueryAdapterInfo)]
    gdi.D3DKMTQueryAdapterInfo.restype = ct.c_long
    return gdi


_api = None
_api_failed = False


def _get_api():
    global _api, _api_failed
    if _api is None and not _api_failed:
        try:
            _api = _bind()
        except Exception:
            _api_failed = True
    return _api


def read_temperature() -> float | None:
    """GPU edge temperature in degrees Celsius, or None if unavailable."""
    api = _get_api()
    if api is None:
        return None

    listing = _EnumAdapters2()
    try:
        if api.D3DKMTEnumAdapters2(ct.byref(listing)) != 0 or not listing.NumAdapters:
            return None
        count = min(int(listing.NumAdapters), _MAX_ADAPTERS)
        adapters = (_AdapterInfo * count)()
        listing.pAdapters = ct.cast(adapters, _c_void_p)
        if api.D3DKMTEnumAdapters2(ct.byref(listing)) != 0:
            return None

        for adapter in adapters:
            open_struct = _OpenFromLuid(AdapterLuid=adapter.AdapterLuid)
            if api.D3DKMTOpenAdapterFromLuid(ct.byref(open_struct)) != 0:
                continue
            try:
                data = _AdapterPerfData()
                query = _QueryAdapterInfo(
                    hAdapter=open_struct.hAdapter,
                    Type=_KMTQAITYPE_ADAPTERPERFDATA,
                    pPrivateDriverData=ct.cast(ct.byref(data), _c_void_p),
                    PrivateDriverDataSize=ct.sizeof(data),
                )
                if api.D3DKMTQueryAdapterInfo(ct.byref(query)) == 0 and data.Temperature:
                    return round(data.Temperature / 10.0, 1)
            finally:
                api.D3DKMTCloseAdapter(
                    ct.byref(_CloseAdapter(hAdapter=open_struct.hAdapter))
                )
    except Exception:
        pass
    return None
