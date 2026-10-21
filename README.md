# hermes-lang-it — Italian language pack + Italian plugins

Full Italian translation of the **Hermes Desktop** UI catalog, packaged as a
manifest-only Hermes language pack — plus two ready-to-install Italian
Hermes plugins (`token-rate`, `catalogo-it`) in [`plugins/`](plugins/).

## What it ships

- `locales/it.yaml` — 3437 keys, the core (Python-side) catalog.
- `locales/it.tui.yaml` — 1259 keys, the `hermes --tui` catalog.
- `locales/it.desktop.yaml` — 5030 keys, the desktop catalog
  (`apps/desktop/src/i18n/en.ts` as of the pinned build).

The core and TUI catalogs mirror the in-tree `it.yaml` / `it.tui.yaml` so
the pack stands alone; the desktop catalog is the new piece — it does not
exist in-tree yet. When Hermes ships a desktop `it.desktop.yaml`, the pack
still wins (packs are loaded before the bundled layer) — that is intended,
the community translation stays in the pack until the core one is reviewed.

## Install

```sh
hermes plugins install <this-repo-url>
```

or clone into your Hermes home plugin dir:

```sh
git clone <this-repo-url> ~/.hermes/plugins/hermes-lang-it
```

Then choose the language (the pack never switches it for you):

```sh
hermes config set display.language it
```

or pick **Italiano** in the Desktop / TUI language switcher. Restart running
gateways/TUI/desktop sessions so they pick the pack up.

## Verification

Built and checked against the English desktop catalog:

- 100% key coverage (no missing or extra keys);
- placeholders (`{name}`, `{{x}}`), URLs and CLI flags preserved verbatim;
- hard-parsed UI tokens (menu brackets, `[Y/n]` prompts) kept verbatim;
- parsed round-trip with Hermes' own `i18n_layers.parse_locale_file()` →
  4378 dotted keys, 0 missing / 0 extra / 0 divergent.

## Disclosure

- **Manifest-only**: `provides_locales` is the entire manifest. No Python,
  no code execution, no network access, no environment variables.
- Registering the pack **never changes** `display.language`; opting in is
  always the user's choice via config or the language switcher.
- The catalog contains static UI strings only — agent responses, tool output
  and logs are not affected (as with any Hermes language pack).

## Bundled plugins

Two complete Hermes plugins ship in this repository, each in its own
subdirectory. Install one with the `owner/repo/subdir` form:

```sh
hermes plugins install Lazzdaycod/hermes-lang-it/plugins/token-rate --enable
hermes plugins install Lazzdaycod/hermes-lang-it/plugins/catalogo-it --enable
```

### `token-rate` — live tokens/sec for a local llama.cpp model

Status-bar chip showing the real output/input tokens-per-second of the local
llama.cpp server. The rate is read by Hermes itself from
`$HERMES_HOME/logs/llama-server.log` — no external process, no extra port, no
watchdog, no API key.

The counter starts **off**: click the chip in the status bar, or run
**Token Rate: attiva/ferma il contatore tok/s** from the command palette
(Ctrl/Cmd+K). It also registers the `get_token_rate` agent tool, so the model
can read its own speed.

### `catalogo-it` — the Hermes plugin catalog, in Italian

Native page (sidebar → *Catalogo Plugin*, route `/catalogo`, plus a
command-palette entry) listing the official Hermes plugin catalog with an
Italian interface and on-the-fly Italian descriptions
(Google Translate → MyMemory → local llama.cpp fallback).

### Notes for both

- Unified packages: a FastAPI backend (`dashboard/plugin_api.py`, mounted at
  `/api/plugins/<id>/`) plus a desktop half (`desktop/plugin.js`).
- The desktop half is opt-in — after installing, enable the plugin in
  **Capabilities → Plugins**.
- **Restart Hermes Desktop once** after installing so the backend routes get
  mounted (they are mounted at app start).

## License

MIT — see [LICENSE](LICENSE).
