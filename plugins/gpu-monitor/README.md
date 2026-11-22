# gpu-monitor — live GPU chip for Hermes Desktop

Status-bar chip with live **GPU utilization** and **dedicated VRAM**, read from
Windows performance counters through `pdh.dll` in-process. Temperature comes
from the Windows D3DKMT adapter-perf query (`gdi32.dll`). No external process,
no port, no network, no extra dependency.

## Features

- **Status-bar chip**: `GPU <util>% · <VRAM> · <temp>°`, refreshed every 2 s.
- **Details on click** (or via the “GPU Monitor: show GPU usage” palette
  command): utilization, dedicated VRAM, temperature, busiest GPU engines /
  processes and the data source.
- **Agent tool**: `get_gpu_usage` returns the same snapshot as JSON.
- **English / Italian** UI.

## Routes

- `GET /api/plugins/gpu-monitor/metrics` — snapshot (cached 1.5 s).
- `GET /api/plugins/gpu-monitor/status` — alias.

## Tool

- `get_gpu_usage` — live GPU utilization %, dedicated VRAM bytes, busiest
  engines, temperature and source.

## Requirements

- Hermes `>= 0.21.6`.
- Windows (PDH + D3DKMT). On other platforms the chip reports “GPU counters
  unavailable”.

## Disclosure

- Reads Windows performance counters in-process (`pdh.dll`) and the D3DKMT
  adapter query (`gdi32.dll`). It spawns no process, opens no port, makes no
  network call and writes no file. The chip and the agent tool expose only the
  GPU counters locally.
