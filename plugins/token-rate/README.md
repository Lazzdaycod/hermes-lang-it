# token-rate

Live **tokens/sec** for a local **llama.cpp** model, as a chip in the Hermes
Desktop status bar.

```
Qwen3.8 ↑7.4↓184
```

Left side: the active model (truncated to 8 characters). Right side: output
(`↑`) and input (`↓`) tokens per second of the last request the local server
handled.

## Using it

The counter starts **off** (`<model> off`). Start it with either:

- a **click on the chip** in the status bar, or
- the command palette (**Ctrl/Cmd+K**) → *Token Rate: start/stop the tok/s
  counter*.

A second click (or the same palette entry) stops it. The on/off state is kept in
`$HERMES_HOME/cache/token-rate-state.json`, so it survives a Hermes restart.

It also registers the **`get_token_rate`** agent tool, so the model can read its
own speed.

## How it works

1. Hermes reads `$HERMES_HOME/logs/llama-server.log` — the last 200 lines:
   `tg = X t/s` is the output rate, `prompt eval time ... X tokens per second`
   the input rate.
2. Its FastAPI backend (`dashboard/plugin_api.py`) serves that at
   `/api/plugins/token-rate/`: `GET /metrics`, `GET /status`, `POST /start`,
   `POST /stop`.
3. The desktop chip polls `/metrics` once a second.

The reading happens inside the Hermes process: there is no sidecar server, no
extra port, and nothing to start by hand.

## Requirements

- Hermes `>=0.21.6` (unified plugin packages with a `dashboard/plugin_api.py`).
- A local llama.cpp server whose log Hermes writes to
  `$HERMES_HOME/logs/llama-server.log`. Without it the chip stays at `off` and
  reports no values.

## Disclosure

- **Reads one local file** — the llama.cpp log — and nothing else.
- **No network calls**, no telemetry, no external process, no listening port.
- **No credentials**: no API key, no `.env` read, no other plugin's or tool's
  store. It never talks to the local server over HTTP either — it only reads the
  log file.
- The only file it writes is its own on/off flag
  (`$HERMES_HOME/cache/token-rate-state.json`).

## Notes

- The log can contain several llama.cpp servers (the proxy logs more than one
  port): the numbers are those of the most recent request, not per-model.
- Input rate is the prompt/prefill speed of the last request, so it is `—` until
  a request with a measurable prompt has gone through.

## License

MIT — see the repository [LICENSE](../../LICENSE).
