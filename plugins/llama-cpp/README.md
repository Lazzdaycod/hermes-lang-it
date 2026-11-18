# llama-cpp — Hermes llama.cpp server manager

Desktop page + FastAPI backend for the Hermes-managed **llama.cpp** runtime: detect
the GPU and pick the right build, list local GGUF models, edit and apply per-model
presets, load/unload the active model, and benchmark generation / prefill speed.

## Features

- **GPU detection by model, not just vendor.** `detect_gpu()` reads the active
  build's `--list-devices` output (Vulkan/CUDA/ROCm/Metal) and reports the GPU
  name, vendor and the AMD RDNA bucket (`gfx120X` / `gfx110X` / `gfx103X`).
- **Update variants matched to the detected GPU.** Vulkan (any GPU), CUDA
  (NVIDIA only) and ROCm via [Lemonade](https://github.com/lemonade-sdk/llamacpp-rocm)
  (AMD only, bucket-qualified asset). The panel shows which variant is suitable
  for *your* GPU and disables the wrong one; no hard-coded model list.
- **GGUF model manager.** Lists local models with size and state
  (loaded/unloaded), loads/unloads the active model on the running server.
- **Per-model presets.** Context window, `n-gpu-layers`, KV cache type/quant,
  flash attention, batch and sampling (temp, top-k, top-p). `n-gpu-layers = 999`
  is the default, so the whole model goes to VRAM; no `override-tensor` is ever
  emitted. Custom values survive preset regeneration.
- **Benchmark.** Generation (tok/s, TTFT) and prefill (prompt tok/s) measured
  against the running server via `/v1/chat/completions`, using the server's own
  `timings` when available. Results are stored in
  `$HERMES_HOME/runtimes/llamacpp/bench.json`, and the leaderboard keeps the best
  result per model **together with the preset and the llama.cpp build that
  produced it**.
- **Agent tool.** `llama_server_status` exposes backend, build, running state,
  active model, model list and current presets.

## Install

```sh
hermes plugins install Lazzdaycod/hermes-lang-it/plugins/llama-cpp --enable
```

Then restart Hermes Desktop once so the backend routes get mounted. The page is
opt-in: enable the plugin in **Capabilities → Plugins**.

## Requirements

- Hermes `>= 0.21.6` (plugin manifest `requires_hermes`).
- Python backend dependencies: `fastapi` (provided by the plugin environment).
- A Hermes-managed llama.cpp runtime (`hermes local-runtime` / the llama-cpp
  provider) with at least one GGUF model.

## Files

- `dashboard/plugin_api.py` — FastAPI router (mounted at `/api/plugins/llama-cpp/`).
- `dashboard/manifest.json` — page metadata (icon `Cpu`).
- `desktop/plugin.js` — Desktop page UI.
- `llama_cpp_core.py` — GPU detection, model list, preset handling, benchmark core.
- `plugin.yaml` — plugin manifest.

## Disclosure

- The plugin reads and writes **inside `$HERMES_HOME`**: `runtimes/llamacpp/`
  (server state, presets, bench history), and it launches/stops the managed
  `llama-server` process.
- Benchmark calls go to the **local** llama.cpp server only.
- Update checks fetch public GitHub release metadata
  (`ggml-org/llama.cpp`, `lemonade-sdk/llamacpp-rocm`); downloading a build is an
  explicit user action. A GitHub token (`gh auth token` / `GH_TOKEN`) is used when
  available to raise the anonymous rate limit.
- No telemetry, no credentials in the UI, no third-party network calls.

## License

MIT — see the repository [LICENSE](../../LICENSE).
