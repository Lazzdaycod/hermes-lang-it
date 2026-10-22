# catalogo-it

The official **Hermes plugin catalog**, browsed inside Hermes Desktop, in
Italian — with plugin descriptions translated on the fly.

## Using it

- **Sidebar → *Catalogo Plugin*** (route `/catalogo`), or
- the command palette (**Ctrl/Cmd+K**) → *Apri catalogo plugin* / *Cerca nel
  catalogo plugin…*.

The list shows every entry of the public Hermes catalog with category and tier
badges, a search field, and category/tier filters. Selecting an entry opens a
detail panel with its description, version, platforms, repository and docs
links, and an **Install plugin** button (it opens Hermes' own native install
flow).

The page's own UI is Italian; the plugin also ships an English bundle, so it
follows the app locale (`ctx.i18n.register({ en, it })`).

## Translation

Opening an entry translates its description into Italian automatically, trying
three sources in order:

1. **Google Translate** (`translate.googleapis.com`) — free, no key.
2. **MyMemory** (`api.mymemory.translated.net`) — second public source, used
   when Google does not answer.
3. **The user's own local model** — last resort, through the OpenAI-compatible
   endpoint Hermes is already configured against.

A *Mostra originale* / *Traduci descrizione* button toggles between the
translation and the original text.

## How it works

- `dashboard/plugin_api.py` exposes `GET /api-key`, returning the API key Hermes
  generated for the local llama.cpp server
  (`$HERMES_HOME/runtimes/llamacpp/.api_key`) together with the local endpoint
  (`model.base_url` from Hermes' `config.yaml`). The port is never hardcoded:
  the local server changes port on every launch.
- `desktop/plugin.js` fetches the public catalog feed directly, and asks the
  plugin's own backend route for the local endpoint only when it needs the
  translation fallback. The model name comes from the endpoint's `/v1/models`,
  falling back to the configured one.

## Requirements

- Hermes `>=0.21.6` (unified plugin packages with a `dashboard/plugin_api.py`).
- Internet access for the catalog feed and the public translators. The local
  model fallback additionally needs a local server configured in Hermes; without
  it, translations simply come from the public sources.

## Disclosure

- **Network calls:** the public catalog feed
  (`hermes-agent.nousresearch.com`), `translate.googleapis.com`,
  `api.mymemory.translated.net`, and — only as the last fallback — the local
  model endpoint from Hermes' own config. Translated text is the plugin's
  description, i.e. text already published in the public catalog.
- **Credentials:** the only key used is the one Hermes itself generated for the
  local llama.cpp server, read through this plugin's own backend route. No key
  is stored, logged or sent anywhere but that local endpoint.
- **Reads:** the public catalog feed, and (through the backend route) Hermes'
  `config.yaml` for `model.base_url` and the local server's `.api_key` file.
- **No telemetry**, no analytics, no writes outside the plugin's own cache.
- Translation is triggered per opened entry, not for the whole catalog.

## License

MIT — see the repository [LICENSE](../../LICENSE).
