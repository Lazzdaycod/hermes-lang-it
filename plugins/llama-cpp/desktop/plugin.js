/**
 * llama.cpp — desktop plugin (unified with the Python agent plugin in this
 * package). Plain ESM; the runtime loader maps `react*` and
 * `@hermes/plugin-sdk` to the app's shims, so no other imports are allowed.
 *
 * UI: one workspace page —
 *   - backend status header (server state, detected GPU backend, links to
 *     docs and the ROCm build),
 *   - system section (OS, CPU, RAM, GPU — read from the backend),
 *   - Hermes local models (providers with a local base_url in config.yaml),
 *   - model list with load/unload,
 *   - extra model folders: manually add a directory path to scan for .gguf,
 *   - a per-model preset form that POSTs /apply.
 *
 * Backend contract (dashboard/plugin_api.py):
 *   GET  /status  -> {running, backend, build, port, active_model, presets,
 *                     default_options, valid_cache_types, valid_flash_attn,
 *                     system, hermes_local_models, ...}
 *   GET  /models  -> {models: [{id, path, size_human, quant, status,
 *                     active, in_preset}]}   (?dir=<path>&dir=<path>)
 *   POST /apply   body {model, options: {<api_key>: value}, set_active: bool}
 *   POST /load    body {model}      (202, async on the router)
 *   POST /unload  body {model}      (202, async on the router)
 *
 * Preset keys in /status are INI keys (ctx-size, batch-size, ...); /apply
 * expects the API keys (ctx_size, batch_size, ...) — INI_TO_API bridges the
 * two. The INI section name is the model id.
 */

import { jsx, jsxs } from 'react/jsx-runtime'

import { useEffect, useState } from 'react'
import {
  cn,
  host,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  Tip,
  usePluginI18n,
  useQuery,
  useQueryClient
} from '@hermes/plugin-sdk'

// L'utente vuole vedere solo nomi amichevoli (Vulkan / ROCm / CUDA), mai i
// nomi interni di build. "hip" e' la build ROCm (ggml-org). La label viene
// derivata client-side dal variant (o, in fallback, dal nome package), cosi'
// il fix e' efficace anche prima che il backend vada in reload.
const VARIANT_LABEL = {
  vulkan: 'Vulkan', cuda: 'CUDA', rocm: 'ROCm', hip: 'ROCm',
  lemonade: 'ROCm', 'rocm-lemonade': 'ROCm',
  metal: 'Metal', cpu: 'CPU',
}
const VARIANT_META = {
  vulkan: 'vulkan', cuda: 'cuda', rocm: 'rocm', hip: 'hip',
  lemonade: 'rocm', 'rocm-lemonade': 'rocm',
  metal: 'metal', cpu: 'cpu',
}
function _variantKey(b) {
  const v = String(b.variant || '').trim().toLowerCase()
  const first = v.split(/\s+/)[0]
  if (VARIANT_LABEL[first]) return first
  const m = String(b.package || '').match(/^llamacpp-([a-z-]+)/)
  if (m) {
    const k = m[1].replace(/-+$/, '')
    if (VARIANT_LABEL[k]) return k
  }
  return first
}
function variantLabel(b) {
  if (b.display) return b.display
  const k = _variantKey(b)
  return VARIANT_LABEL[k] || k || b.package || 'build'
}

// Le build ufficiali CUDA e Vulkan sono pubblicate nella stessa pagina
// release di ggml-org/llama.cpp (asset per backend).
const REPO_CUDA_URL = 'https://github.com/ggml-org/llama.cpp/releases'
const REPO_VULKAN_URL = 'https://github.com/ggml-org/llama.cpp/releases'
// Link ROCm: build ROCm/HIP ufficiale, stessa pagina release ggml-org.
const REPO_ROCM_URL = 'https://github.com/ggml-org/llama.cpp/releases'

const LOCALES = {
  en: {
    'catalog.notice': 'Catalog build — update, stop/restart and build select/delete are not included to follow the catalog policy. Use Settings → Local models, or install the full version from the Lazzdaycod/hermes-lang-it repository.',
    subtitle: 'Backend server, system info and per-model presets',
    'server.running': 'running',
    'server.idle': 'idle',
    'server.active': 'active model',
    'server.backend': 'backend',
    'server.backendFromConfig': 'backend (from config)',
    'link.cuda': 'CUDA',
    'link.vulkan': 'Vulkan',
    'link.rocm': 'ROCm (HIP) builds',
    'system.header': 'System',
    'system.unavailable': 'System info not available on this backend.',
    'system.os': 'OS',
    'system.cpu': 'CPU',
    'system.cores': 'cores',
    'system.ram': 'RAM',
    'system.gpu': 'GPU',
    'hermes.header': 'Hermes local models',
    'hermes.empty': 'No local providers found in config.yaml.',
    'hermes.offline': 'offline',
    'hermes.default': 'active',
    'models.header': 'Models',
    'models.empty': 'No models found in the models directory.',
    'modelsDirs.header': 'Extra model folders',
    'modelsDirs.placeholder': 'C:\\path\\to\\folder',
    'modelsDirs.add': 'Add folder',
    'modelsDirs.empty': 'No models in this folder.',
    'modelsDirs.remove': 'Remove folder',
    'error.addDir': 'Could not read the folder',
    'status.loaded': 'loaded',
    'status.loading': 'loading',
    'status.unloading': 'unloading',
    'status.unloaded': 'unloaded',
    'status.error': 'error',
    'status.unknown': 'unknown',
    'action.load': 'Load',
    'action.unload': 'Unload',
    'badge.active': 'active',
    'badge.inPreset': 'has preset',
    'form.title': 'Presets',
    'form.empty': 'Select a model above to edit its preset.',
    'form.apply': 'Apply',
    'form.applying': 'Applying…',
    'form.setActive': 'Set as active model',
    'modes.title': 'Quick modes',
    'modes.hint': 'One click: applies only the sampling parameters (temperature, top-k, top-p, repeat-penalty) to the selected model. Model-specific settings (context, batch, cache) are left untouched.',
    'modes.coding': '💻 Coding — precise & strict',
    'modes.chat': '💬 Chat / Creative — natural & fluid',
    'modes.applying': 'Applying…',
    'toast.modeApplied': 'Mode applied',
    'error.modeApply': 'Mode apply failed',
    'field.ctx_size': 'Context size (ctx-size)',
    'field.batch_size': 'Batch size (batch-size)',
    'field.ubatch_size': 'Micro-batch (ubatch-size)',
    'field.n_gpu_layers': 'GPU layers (999 = all in VRAM, 0 = all in RAM)',
    'opts.title': 'Active optimizations',
    'opts.cmd': 'Server launch command',
    'opts.preset': 'Preset for active model',
    'opts.hint': 'These are the flags actually in force. The runtime may rewrite some of them (context, GPU spilling) the next time the server is restarted.',
    'opts.none': 'No server running.',
    'field.cache_type_k': 'KV cache K (cache-type-k)',
    'field.cache_type_v': 'KV cache V (cache-type-v)',
    'field.flash_attn': 'Flash attention (flash-attn)',
    'field.temp': 'Temperature',
    'field.top_k': 'Top-k',
    'field.top_p': 'Top-p',
    'field.repeat_penalty': 'Repeat penalty (repeat-penalty)',
    'toast.applied': 'Preset saved',
    'toast.loading': 'Load started',
    'toast.unloading': 'Unload started',
    'error.apply': 'Apply failed',
    'error.load': 'Load failed',
    'error.unload': 'Unload failed',
    'builds.header': 'Runtime builds',
    'builds.hint': 'Installed llama.cpp builds (read-only). The active one is what the server runs. Selecting or removing a build from this panel will arrive with a public core route (NousResearch/hermes-agent#157878).',
    'builds.empty': 'No builds installed in the store.',
    'builds.active': 'active',
    'builds.deprecated': 'deprecated',
    'controls.header': 'Runtime versions',
    'controls.hint': 'Update, stop and restart the server from Settings → Local models.',
    'controls.gpu': 'GPU',
    'controls.badBackend': 'The active backend is not compatible with this GPU',
    'controls.switchHint': 'switch to a suitable backend.',
    'controls.version': 'Version',
    'controls.notInstalled': 'not installed',
    'controls.notSuitable': 'Not suitable for this GPU',
    'controls.unknown': 'n/a',
    'controls.updateAvailable': 'update available',
    'controls.active': 'active',
    'controls.desc.vulkan': 'Vulkan GPU — AMD, Intel, NVIDIA — source: ggml-org/llama.cpp',
    'controls.desc.rocm': 'AMD RDNA3 GPU (RX 7000, e.g. 7900 XTX) — official ROCm/HIP build, source: ggml-org/llama.cpp',
    'controls.desc.cuda': 'NVIDIA GPU — source: ggml-org/llama.cpp',
    'bench.header': 'Benchmark',
    'bench.model': 'Model',
    'bench.mode': 'Mode',
    'bench.mode.generation': 'generation (tok/s)',
    'bench.mode.prefill': 'prefill (tok/s)',
    'bench.mode.both': 'both',
    'bench.promptTokens': 'Prompt tokens',
    'bench.outputTokens': 'Output tokens',
    'bench.runs': 'Runs',
    'bench.run': 'Run benchmark',
    'bench.running': 'Benchmark running…',
    'bench.col.mode': 'Mode',
    'bench.col.prompt': 'Prompt tok',
    'bench.col.output': 'Output tok',
    'bench.col.ttft': 'TTFT s',
    'bench.col.total': 'Total s',
    'bench.col.tps': 'tok/s',
    'bench.board': 'Leaderboard (best result per model)',
    'bench.col.model': 'Model',
    'bench.col.gen': 'Gen tok/s',
    'bench.col.prefill': 'Prefill tok/s',
    'bench.col.server': 'Server',
    'bench.col.preset': 'Preset',
    'bench.recent': 'Recent sessions',
    'controls.capTitle': 'Context window cap',
    'controls.capLabel': 'Cap context at',
    'controls.capApply': 'Apply cap',
    'controls.capApplied': 'Context cap applied',
    'controls.capNone': 'No preset above the cap.',
    'controls.capChanged': (n) => `${n} preset(s) capped`,
    'controls.capError': 'Could not apply the cap',
    'controls.capResult': (m, f, to) => `${m}: ${f} → ${to}`,
    'controls.capResultError': (m, err) => `${m}: ${err}`
  },
  it: {
    'catalog.notice': 'Versione catalogo — aggiorna, stop/riavvia e seleziona/elimina build non sono inclusi per rispettare le regole del catalogo. Usa Impostazioni → Modelli locali, oppure installa la versione completa dalla repository Lazzdaycod/hermes-lang-it.',
    subtitle: 'Server di backend, info di sistema e preset per modello',
    'server.running': 'attivo',
    'server.idle': 'inattivo',
    'server.active': 'modello attivo',
    'server.backend': 'backend',
    'server.backendFromConfig': 'backend (da config)',
    'link.cuda': 'CUDA',
    'link.vulkan': 'Vulkan',
    'link.rocm': 'Build ROCm (HIP)',
    'system.header': 'Sistema',
    'system.unavailable': 'Info di sistema non disponibili su questo backend.',
    'system.os': 'Sistema operativo',
    'system.cpu': 'CPU',
    'system.cores': 'core',
    'system.ram': 'RAM',
    'system.gpu': 'GPU',
    'hermes.header': 'Modelli locali Hermes',
    'hermes.empty': 'Nessun provider locale trovato in config.yaml.',
    'hermes.offline': 'offline',
    'hermes.default': 'attivo',
    'models.header': 'Modelli',
    'models.empty': 'Nessun modello trovato nella directory.',
    'modelsDirs.header': 'Cartelle modelli extra',
    'modelsDirs.placeholder': 'C:\\path\\to\\folder',
    'modelsDirs.add': 'Aggiungi cartella',
    'modelsDirs.empty': 'Nessun modello in questa cartella.',
    'modelsDirs.remove': 'Rimuovi cartella',
    'error.addDir': 'Impossibile leggere la cartella',
    'status.loaded': 'caricato',
    'status.loading': 'caricamento',
    'status.unloading': 'scaricamento',
    'status.unloaded': 'non caricato',
    'status.error': 'errore',
    'status.unknown': 'sconosciuto',
    'action.load': 'Carica',
    'action.unload': 'Rimuovi',
    'badge.active': 'attivo',
    'badge.inPreset': 'ha preset',
    'form.title': 'Preset',
    'form.empty': 'Seleziona un modello qui sopra per modificare il preset.',
    'form.apply': 'Applica',
    'form.applying': 'Applicazione…',
    'form.setActive': 'Imposta come modello attivo',
    'modes.title': 'Modalità rapide',
    'modes.hint': 'Un click: applica solo i parametri di sampling (temperatura, top-k, top-p, repeat-penalty) al modello selezionato. Le impostazioni specifiche del modello (contesto, batch, cache) restano invariate.',
    'modes.coding': '💻 Coding — precisa e rigida',
    'modes.chat': '💬 Chat / Creativa — naturale e fluida',
    'modes.applying': 'Applicazione…',
    'toast.modeApplied': 'Modalità applicata',
    'error.modeApply': "Errore nell'applicazione della modalità",
    'field.ctx_size': 'Dimensione contesto (ctx-size)',
    'field.batch_size': 'Batch size (batch-size)',
    'field.ubatch_size': 'Micro-batch (ubatch-size)',
    'field.n_gpu_layers': 'Layer GPU (999 = tutto in VRAM, 0 = tutto in RAM)',
    'opts.title': 'Ottimizzazioni attive',
    'opts.cmd': 'Comando di avvio del server',
    'opts.preset': 'Preset modello attivo',
    'opts.hint': 'Questi sono i flag realmente in forza. Il runtime può riscriverne alcuni (contesto, spill su GPU) al prossimo riavvio del server.',
    'opts.none': 'Nessun server in esecuzione.',
    'field.cache_type_k': 'KV cache K (cache-type-k)',
    'field.cache_type_v': 'KV cache V (cache-type-v)',
    'field.flash_attn': 'Flash attention (flash-attn)',
    'field.temp': 'Temperatura',
    'field.top_k': 'Top-k',
    'field.top_p': 'Top-p',
    'field.repeat_penalty': 'Repeat penalty (repeat-penalty)',
    'toast.applied': 'Preset salvato',
    'toast.loading': 'Caricamento avviato',
    'toast.unloading': 'Rimozione avviata',
    'error.apply': 'Errore in applicazione',
    'error.load': 'Errore nel caricamento',
    'error.unload': 'Errore nella rimozione',
    'builds.header': 'Build runtime',
    'builds.hint': 'Build llama.cpp installate (sola lettura). Quella attiva è quella che usa il server. Selezione e rimozione dal pannello arriveranno con una route pubblica del core (NousResearch/hermes-agent#157878).',
    'builds.empty': 'Nessuna build installata nello store.',
    'builds.active': 'attiva',
    'builds.deprecated': 'deprecata',
    'controls.header': 'Versioni runtime',
    'controls.hint': 'Aggiorna, ferma e riavvia il server da Impostazioni → Modelli locali.',
    'controls.gpu': 'GPU',
    'controls.badBackend': 'Il backend attivo non è compatibile con questa GPU',
    'controls.switchHint': 'passa a un backend adatto.',
    'controls.version': 'Versione',
    'controls.notInstalled': 'non installata',
    'controls.notSuitable': 'Non adatto a questa GPU',
    'controls.unknown': 'n/d',
    'controls.updateAvailable': 'aggiornamento disponibile',
    'controls.active': 'attivo',
    'controls.desc.vulkan': 'GPU Vulkan — AMD, Intel, NVIDIA — sorgente: ggml-org/llama.cpp',
    'controls.desc.rocm': 'GPU AMD RDNA3 (RX 7000, es. 7900 XTX) — build ROCm/HIP ufficiale, sorgente: ggml-org/llama.cpp',
    'controls.desc.cuda': 'GPU NVIDIA — sorgente: ggml-org/llama.cpp',
    'bench.header': 'Benchmark',
    'bench.model': 'Modello',
    'bench.mode': 'Modalità',
    'bench.mode.generation': 'generation (tok/s)',
    'bench.mode.prefill': 'prefill (tok/s)',
    'bench.mode.both': 'entrambi',
    'bench.promptTokens': 'Prompt tok',
    'bench.outputTokens': 'Output tok',
    'bench.runs': 'Run',
    'bench.run': 'Avvia benchmark',
    'bench.running': 'Benchmark in corso…',
    'bench.col.mode': 'Modalità',
    'bench.col.prompt': 'Prompt tok',
    'bench.col.output': 'Output tok',
    'bench.col.ttft': 'TTFT s',
    'bench.col.total': 'Tot s',
    'bench.col.tps': 'tok/s',
    'bench.board': 'Classifica (miglior risultato per modello)',
    'bench.col.model': 'Modello',
    'bench.col.gen': 'Gen tok/s',
    'bench.col.prefill': 'Prefill tok/s',
    'bench.col.server': 'Server',
    'bench.col.preset': 'Preset',
    'bench.recent': 'Ultime sessioni',
    'controls.capTitle': 'Limite finestra di contesto',
    'controls.capLabel': 'Limita il contesto a',
    'controls.capApply': 'Applica limite',
    'controls.capApplied': 'Limite contesto applicato',
    'controls.capNone': 'Nessun preset oltre il limite.',
    'controls.capChanged': (n) => `${n} preset limitati`,
    'controls.capError': 'Impossibile applicare il limite',
    'controls.capResult': (m, f, to) => `${m}: ${f} → ${to}`,
    'controls.capResultError': (m, err) => `${m}: ${err}`
  }
}

/** INI key (preset files /status) -> API key (/apply options). */
const INI_TO_API = {
  'ctx-size': 'ctx_size',
  'batch-size': 'batch_size',
  'ubatch-size': 'ubatch_size',
  'n-gpu-layers': 'n_gpu_layers',
  'cache-type-k': 'cache_type_k',
  'cache-type-v': 'cache_type_v',
  'flash-attn': 'flash_attn',
  temp: 'temp',
  'top-k': 'top_k',
  'top-p': 'top_p',
  'repeat-penalty': 'repeat_penalty',
  'cache-ram': 'cache_ram'
}

/** Form fields, in order. Selects read their options from /status. */
const FIELDS = [
  { key: 'ctx_size', type: 'number', placeholder: '98304' },
  { key: 'batch_size', type: 'number', placeholder: '2048' },
  { key: 'ubatch_size', type: 'number', placeholder: '2048' },
  { key: 'n_gpu_layers', type: 'number', placeholder: '999' },
  { key: 'cache_type_k', type: 'select', options: 'cache' },
  { key: 'cache_type_v', type: 'select', options: 'cache' },
  { key: 'flash_attn', type: 'select', options: 'flash' },
  { key: 'temp', type: 'number', placeholder: '0.8' },
  { key: 'top_k', type: 'number', placeholder: '40', integer: true },
  { key: 'top_p', type: 'number', placeholder: '0.95' },
  { key: 'repeat_penalty', type: 'number', placeholder: '1.0' },
  { key: 'cache_ram', type: 'number', placeholder: '8192' }
]

/** Pre-configured sampling modes. Only sampling parameters are touched
 *  (temperature, top-k, top-p, repeat-penalty); model-specific settings
 *  (context, batch, cache, GPU layers) are left untouched. */
const MODES = {
  coding: {
    temp: 0.1,
    top_k: 20,
    top_p: 0.9,
    repeat_penalty: 1.1
  },
  chat: {
    temp: 0.9,
    top_k: 40,
    top_p: 0.95,
    repeat_penalty: 1.0
  }
}

/** FastAPI raises HTTPException(detail=str); surface the message whatever
 *  the IPC bridge attached to the error. */
function apiError(err) {
  const detail = err && (err.body && err.body.detail)
  if (detail != null) {
    return typeof detail === 'string' ? detail : JSON.stringify(detail)
  }
  const message = (err && err.message) || String(err)
  const m = /^(\d{3}):\s*(.*)$/.exec(message)
  return m && m[2] ? m[2] : message
}

/** Seed the form for a model: default_options first, then the model's
 *  per-model preset (INI keys mapped to API keys), then its active state. */
function initialForm(status, modelId) {
  const form = { ...(status.default_options || {}) }
  const preset = (status.presets || {})[modelId] || {}
  for (const [iniKey, apiKey] of Object.entries(INI_TO_API)) {
    const value = preset[iniKey]
    if (value !== undefined && value !== '') form[apiKey] = value
  }
  form.set_active = status.active_model === modelId
  return form
}

const KNOWN_STATUSES = new Set(['loaded', 'loading', 'unloading', 'unloaded', 'error'])

function Page({ ctx }) {
  const t = usePluginI18n('llama-cpp')
  const qc = useQueryClient()
  const [selected, setSelected] = useState(null)
  const [formModel, setFormModel] = useState(null)
  const [form, setForm] = useState(null)
  const [busy, setBusy] = useState(null) // {kind: 'load'|'unload'|'apply', model}
  const [dirInput, setDirInput] = useState('')
  // Manually added folders: [{dir, models: [...]|null, error: string|null}]
  const [extraDirs, setExtraDirs] = useState([])
  const [benchModel, setBenchModel] = useState(null)
  const [benchMode, setBenchMode] = useState('both')
  const [benchPromptTokens, setBenchPromptTokens] = useState(2048)
  const [benchOutputTokens, setBenchOutputTokens] = useState(128)
  const [benchRuns, setBenchRuns] = useState(1)
  const [benchBusy, setBenchBusy] = useState(false)
  const [benchResults, setBenchResults] = useState([])
  const [ctxCap, setCtxCap] = useState(65000)
  const [ctxCapResult, setCtxCapResult] = useState(null)

  const statusQuery = useQuery({
    queryKey: ['llama-cpp', 'status'],
    queryFn: () => ctx.rest('/status'),
    refetchInterval: 4000
  });

  // Aggiornamento: versione corrente + ultima disponibile (compatibile con la
  // GPU installata, risolta dal PM; il backend non ri-interroga l'upstream a
  // ogni poll). Poll rapido solo durante un aggiornamento in corso.
  const updateQuery = useQuery({
    queryKey: ['llama-cpp', 'update'],
    queryFn: () => ctx.rest('/update'),
    refetchInterval: 30000
  })

  const modelsQuery = useQuery({
    queryKey: ['llama-cpp', 'models'],
    queryFn: () => ctx.rest('/models'),
    refetchInterval: (query) => {
      const models = (query.state.data && query.state.data.models) || []
      const inFlight = models.some((m) => m.status === 'loading' || m.status === 'unloading')
      return inFlight ? 1500 : 5000
    }
  })

  // Build runtime installate nel PM store: elenco + selezione/rimozione.
  const buildsQuery = useQuery({
    queryKey: ['llama-cpp', 'builds'],
    queryFn: () => ctx.rest('/builds'),
    refetchInterval: 30000
  })

  const benchHistoryQuery = useQuery({
    queryKey: ['llama-cpp', 'bench-history'],
    queryFn: () => ctx.rest('/bench/history'),
    refetchInterval: 30000
  })

  const benchBoardQuery = useQuery({
    queryKey: ['llama-cpp', 'bench-leaderboard'],
    queryFn: () => ctx.rest('/bench/leaderboard'),
    refetchInterval: 30000
  })

  const status = statusQuery.data
  const mainModels = (modelsQuery.data && modelsQuery.data.models) || []
  const mainIds = new Set(mainModels.map((m) => m.id))
  const extraModels = extraDirs
    .filter((e) => e.models)
    .flatMap((e) => e.models)
    .filter((m) => !mainIds.has(m.id))
  const models = [...mainModels, ...extraModels]

  // Seed when the selection changes or when /status arrives late. Never
  // re-seed while the user is typing (formModel tracks the seeded model).
  useEffect(() => {
    if (selected && status && formModel !== selected) {
      setFormModel(selected)
      setForm(initialForm(status, selected))
    }
  }, [status, selected, formModel])

  const selectModel = (id) => {
    if (id === selected) return
    setSelected(id)
    if (status) setForm(initialForm(status, id))
  }

  const invalidate = () => qc.invalidateQueries({ queryKey: ['llama-cpp'] })

  // Azione esplicita: applica il soffitto di contesto ai preset. Non gira mai
  // al montaggio del plugin.
  const runCtxCap = async () => {
    if (busy) return
    const cap = Number(ctxCap)
    if (!Number.isFinite(cap) || cap <= 0) return
    setBusy({ kind: 'ctxCap' })
    try {
      const res = await ctx.rest('/ctx-cap', { method: 'POST', body: { cap: Math.round(cap) } })
      const changed = (res && res.changed) || []
      setCtxCapResult(changed)
      host.notify({
        kind: 'success',
        message: changed.length ? t('controls.capChanged', changed.length) : t('controls.capNone')
      })
      invalidate()
    } catch (err) {
      host.notify({ kind: 'error', message: `${t('controls.capError')}: ${apiError(err)}` })
    } finally {
      setBusy(null)
    }
  }

  const runLoadUnload = async (kind, model) => {
    if (busy) return
    setBusy({ kind, model })
    try {
      await ctx.rest(kind === 'load' ? '/load' : '/unload', { method: 'POST', body: { model } })
      host.notify({ kind: 'info', message: kind === 'load' ? t('toast.loading') : t('toast.unloading') })
    } catch (err) {
      host.notify({
        kind: 'error',
        message: `${kind === 'load' ? t('error.load') : t('error.unload')}: ${apiError(err)}`
      })
    } finally {
      setBusy(null)
    }
  }

  const inputCls =
    'h-8 rounded-md border border-(--ui-stroke-secondary) bg-(--ui-bg-quinary) px-2 text-sm text-(--ui-text-primary) outline-none focus:border-(--ui-accent) disabled:opacity-50'
  const actionBtnCls =
    'h-7 shrink-0 rounded-md border border-(--ui-stroke-secondary) bg-(--ui-bg-elevated) px-2.5 text-xs font-medium hover:bg-(--ui-bg-quinary) disabled:cursor-not-allowed disabled:opacity-40'
  const linkCls =
    'shrink-0 rounded-md border border-(--ui-stroke-secondary) bg-(--ui-bg-elevated) px-2.5 py-1 text-xs font-medium text-(--ui-text-secondary) underline-offset-2 hover:bg-(--ui-bg-quinary) hover:underline'
  const sectionCls = 'rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-bg-elevated) p-4'
  const sectionTitleCls =
    'text-xs font-semibold uppercase tracking-wide text-(--ui-text-secondary)'

  // Dropdown custom: i <select> nativi aprono menu disegnati dal sistema,
  // che nel tema scuro restano bianchi e poco leggibili.
  const MiniSelect = ({ value, onChange, options, disabled }) => {
    const [open, setOpen] = useState(false)
    const norm = options.map((o) => (typeof o === 'string' ? { value: o, label: o } : o))
    const current = norm.find((o) => o.value === value)
    return jsxs('div', {
      className: 'relative',
      children: [
        jsx('button', {
          type: 'button',
          disabled,
          onClick: () => setOpen((o) => !o),
          className: `${inputCls} flex items-center justify-between`,
          children: [
            current ? current.label : '—',
            jsx('span', { className: 'ml-2 text-[10px]', children: '▾' }),
          ],
        }),
        open
          ? jsx('div', { className: 'fixed inset-0 z-10', onClick: () => setOpen(false) })
          : null,
        open
          ? jsx('div', {
              className:
                'absolute z-20 mt-1 max-h-56 w-full min-w-[120px] overflow-auto rounded-md border border-(--ui-stroke-secondary) bg-(--ui-bg-elevated) py-1 shadow-lg',
              children: norm.map((o) => jsx('button', {
                type: 'button',
                key: o.value,
                onClick: () => {
                  onChange(o.value)
                  setOpen(false)
                },
                className: `block w-full px-3 py-1.5 text-left text-xs hover:bg-(--ui-bg-quinary) ${o.value === value ? 'text-(--ui-text-primary)' : 'text-(--ui-text-secondary)'}`,
                children: o.label,
              })),
            })
          : null,
      ],
    })
  }

  const updateData = updateQuery.data || null
  const updateVariants = (updateData && updateData.variants) || {}
  const gpu = (updateData && updateData.gpu) || null

  // Ogni backend ha il suo riquadro (nome, descrizione, versione): sola
  // lettura. Stop/restart e installazioni sono nel core (Impostazioni →
  // Modelli locali); il plugin non esegue azioni di lifecycle.
  const variantMeta = [
    { key: 'vulkan', label: 'Vulkan', descKey: 'controls.desc.vulkan' },
    { key: 'rocm', label: 'ROCm', descKey: 'controls.desc.rocm' },
    { key: 'cuda', label: 'CUDA', descKey: 'controls.desc.cuda' }
  ]
  const isActiveVariant = (key) => {
    if (!updateData || !key) return false
    if (updateData.active_key) return updateData.active_key === key
    const v = (updateData.variants || {})[key] || {}
    if (!v.package) return false
    return !!(updateData.active && String(updateData.active).startsWith(v.package + '-'))
  }
  // Backend attivo ma non compatibile con la GPU (es. CUDA 13 su Pascal):
  // avvisa in modo evidente, altrimenti il crash al benchmark sembra un mistero.
  const activeBadVariant = variantMeta.find(
    (vm) => isActiveVariant(vm.key) && (updateVariants[vm.key] || {}).suitable === false
  )
  const activeBadNote = activeBadVariant
    ? (updateVariants[activeBadVariant.key] || {}).note || ''
    : ''

  const controlSection = jsxs('section', {
    className: sectionCls,
    children: [
      jsx('h2', { className: `${sectionTitleCls} mb-3`, children: t('controls.header') }),
      gpu
        ? jsx('div', {
            className: 'mb-3 text-xs text-(--ui-text-secondary)',
            children: `${t('controls.gpu')}: ${gpu.name || t('controls.unknown')}${gpu.vendor ? ` · ${gpu.vendor}` : ''}`
          })
        : null,
      activeBadVariant
        ? jsx('div', {
            className: 'mb-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-600',
            children: `${t('controls.badBackend')} (${activeBadVariant.label}): ${activeBadNote || t('controls.switchHint')}`
          })
        : null,
      jsx('div', {
        className: 'flex flex-col gap-2',
        children: variantMeta.map((vm) => {
          const v = updateVariants[vm.key] || {}
          const active = isActiveVariant(vm.key)
          let versionLine
          if (v.error) versionLine = `${t('controls.version')}: ${v.error}`
          else if (v.suitable === false) versionLine = v.note || t('controls.notSuitable')
          else if (v.latest)
            versionLine = `${t('controls.version')}: ${v.current ? v.current : t('controls.notInstalled')} → ${v.latest}${v.update_available ? ` · ${t('controls.updateAvailable')}` : ''}${v.source ? ` · ${v.source}` : ''}`
          else versionLine = `${t('controls.version')}: ${t('controls.unknown')}`
          return jsxs('div', {
            key: vm.key,
            className: 'flex items-center justify-between gap-3 rounded-md border border-(--ui-border) px-3 py-2',
            children: [
              jsxs('div', {
                className: 'min-w-0',
                children: [
                  jsxs('div', {
                    className: 'flex items-center gap-2 text-sm font-medium',
                    children: [
                      vm.label,
                      active
                        ? jsx('span', { className: 'rounded-full bg-(--ui-bg-quinary) px-1.5 py-0.5 text-[10px] text-(--ui-text-secondary)', children: t('controls.active') })
                        : null
                    ]
                  }),
                  jsx('div', { className: 'text-xs text-(--ui-text-secondary)', children: t(vm.descKey) }),
                  jsx('div', { className: 'text-xs text-(--ui-text-secondary)', children: versionLine }),
                  v.suitable !== false && v.note
                    ? jsx('div', { className: 'text-[11px] text-(--ui-text-secondary)', children: v.note })
                    : null
                ]
              })
            ]
          })
        })
      }),
      jsxs('div', {
        className: 'mt-3 rounded-lg border border-(--ui-stroke-secondary) p-3',
        children: [
          jsx('div', {
            className: 'mb-2 text-xs font-medium text-(--ui-text-secondary)',
            children: t('controls.capTitle')
          }),
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx('label', {
                className: 'text-xs text-(--ui-text-secondary)',
                children: t('controls.capLabel')
              }),
              jsx('input', {
                type: 'number',
                min: '1024',
                step: '1024',
                value: ctxCap,
                onChange: (e) => setCtxCap(e.target.value),
                className: inputCls
              }),
              jsx('button', {
                type: 'button',
                disabled: !!busy || !Number.isFinite(Number(ctxCap)) || Number(ctxCap) <= 0,
                onClick: runCtxCap,
                className: actionBtnCls,
                children: busy && busy.kind === 'ctxCap' ? '…' : t('controls.capApply')
              })
            ]
          }),
          ctxCapResult && ctxCapResult.length
            ? jsx('ul', {
                className: 'mt-2 space-y-1 text-[11px] text-(--ui-text-secondary)',
                children: ctxCapResult.map((c, i) =>
                  jsx('li', {
                    key: `${c.model}-${i}`,
                    children: c.error
                      ? t('controls.capResultError', c.model, c.error)
                      : t('controls.capResult', c.model, c.from, c.to)
                  })
                )
              })
            : null
        ]
      }),
      jsx('p', {
        className: 'mt-3 text-[11px] text-(--ui-text-secondary)',
        children: t('controls.hint')
      })
    ]
  });

  const addDir = async () => {
    const dir = dirInput.trim().replace(/\/$/, '')
    if (!dir) return
    const entry = { dir, models: null, error: null }
    setExtraDirs((prev) => [...prev.filter((e) => e.dir !== dir), entry])
    setDirInput('')
    try {
      const data = await ctx.rest(`/models?dir=${encodeURIComponent(dir)}`)
      setExtraDirs((prev) =>
        prev.map((e) => (e.dir === dir ? { ...e, models: (data && data.models) || [] } : e))
      )
    } catch (err) {
      setExtraDirs((prev) =>
        prev.map((e) => (e.dir === dir ? { ...e, error: `${t('error.addDir')}: ${apiError(err)}` } : e))
      )
    }
  }

  const removeDir = (dir) => setExtraDirs((prev) => prev.filter((e) => e.dir !== dir))

  const onApply = async (e) => {
    e.preventDefault()
    if (!selected || !form || busy) return
    const options = {}
    for (const f of FIELDS) {
      const raw = String(form[f.key] != null ? form[f.key] : '').trim()
      if (raw === '') continue
      if (f.type === 'number') {
        const n = Number(raw)
        if (!Number.isFinite(n)) continue
        options[f.key] = f.integer ? Math.round(n) : n
      } else {
        options[f.key] = raw
      }
    }
    setBusy({ kind: 'apply', model: selected })
    try {
      await ctx.rest('/apply', {
        method: 'POST',
        body: { model: selected, options, set_active: !!form.set_active }
      })
      host.notify({ kind: 'success', message: t('toast.applied') })
      invalidate()
    } catch (err) {
      host.notify({ kind: 'error', message: `${t('error.apply')}: ${apiError(err)}` })
    } finally {
      setBusy(null)
    }
  }

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }))

  /** One-click pre-configured mode: applies only the sampling parameters of
   *  the mode to the selected model (model-specific settings untouched). */
  const onApplyMode = async (modeKey) => {
    const mode = MODES[modeKey]
    if (!selected || !mode || busy) return
    setBusy({ kind: 'apply', model: selected })
    try {
      await ctx.rest('/apply', {
        method: 'POST',
        body: { model: selected, options: { ...mode }, set_active: false }
      })
      host.notify({ kind: 'success', message: t('toast.modeApplied') })
      invalidate()
    } catch (err) {
      host.notify({ kind: 'error', message: `${t('error.modeApply')}: ${apiError(err)}` })
    } finally {
      setBusy(null)
    }
  }

  const runBench = async () => {
    const model = benchModel || (models[0] && models[0].id)
    if (!model || benchBusy) return
    setBenchBusy(true)
    try {
      const data = await ctx.rest('/bench', {
        method: 'POST',
        body: {
          model,
          mode: benchMode,
          prompt_tokens: Number(benchPromptTokens),
          output_tokens: Number(benchOutputTokens),
          runs: Number(benchRuns),
        },
      })
      setBenchResults((data && data.results) || [])
      invalidate()
    } catch (err) {
      host.notify({ kind: 'error', message: apiError(err) })
    } finally {
      setBenchBusy(false)
    }
  }

  const statusLabel = (s) =>
    KNOWN_STATUSES.has(s) ? t(`status.${s}`) : `${t('status.unknown')} (${s})`

  const pillStyle = (s) => {
    switch (s) {
      case 'loaded':
        return 'bg-emerald-500/15 text-emerald-600'
      case 'loading':
      case 'unloading':
        return 'bg-amber-500/15 text-amber-600'
      case 'error':
        return 'bg-red-500/15 text-red-600'
      default:
        return 'bg-(--ui-bg-quinary) text-(--ui-text-secondary)'
    }
  }

  const backendLabel =
    status && status.backend && status.backend !== 'unknown'
      ? status.backend_from_config
        ? t('server.backendFromConfig')
        : `${t('server.backend')}: ${status.backend}`
      : null
  const backendMeta = [
    backendLabel,
    status && status.build ? `build ${status.build}` : null,
    status && status.running ? `port ${status.port}` : null,
    status && status.running && status.active_model
      ? `${t('server.active')}: ${status.active_model}`
      : null
  ]
    .filter(Boolean)
    .join('  ·  ')


  // ---- system section ---------------------------------------------------
  const sys = status && status.system
  const systemRows = []
  if (sys) {
    if (sys.os) systemRows.push([t('system.os'), sys.os])
    if (sys.cpu) {
      const clock = sys.clock_mhz ? ` · ${sys.clock_mhz} MHz` : ''
      const cores = sys.cores_physical ? ` · ${sys.cores_physical} ${t('system.cores')}` : ''
      systemRows.push([t('system.cpu'), `${sys.cpu}${cores}${clock}`])
    }
    if (sys.ram_total_gb != null) {
      systemRows.push([
        t('system.ram'),
        sys.ram_free_gb != null
          ? `${sys.ram_free_gb} / ${sys.ram_total_gb} GB`
          : `${sys.ram_total_gb} GB`
      ])
    }
    if (sys.gpus && sys.gpus.length) {
      systemRows.push([t('system.gpu'), sys.gpus.join(' · ')])
    }
  }
  const systemSection = jsx(
    'section',
    {
      className: sectionCls,
      children: jsxs('div', {
        children: [
          jsx('h2', { className: `${sectionTitleCls} mb-3`, children: t('system.header') }),
          systemRows.length === 0
            ? jsx('div', {
                className: 'py-2 text-sm text-(--ui-text-secondary)',
                children: t('system.unavailable')
              })
            : jsxs('dl', {
                className: 'space-y-1.5',
                children: systemRows.map(([label, value]) =>
                  jsxs('div', {
                    key: label,
                    className: 'flex gap-2 text-sm',
                    children: [
                      jsx('dt', {
                        className: 'w-24 shrink-0 text-(--ui-text-secondary)',
                        children: label
                      }),
                      jsx('dd', {
                        className: 'min-w-0 flex-1 break-words',
                        children: value
                      })
                    ]
                  })
                )
              })
        ]
      })
    }
  )

  // ---- Hermes local models section --------------------------------------
  const hermesModels = (status && status.hermes_local_models) || []
  const hermesSection = jsx(
    'section',
    {
      className: sectionCls,
      children: jsxs('div', {
        children: [
          jsx('h2', { className: `${sectionTitleCls} mb-3`, children: t('hermes.header') }),
          hermesModels.length === 0
            ? jsx('div', {
                className: 'py-2 text-sm text-(--ui-text-secondary)',
                children: t('hermes.empty')
              })
            : jsxs('div', {
                className: 'space-y-2',
                children: hermesModels.map((p) =>
                  jsxs('div', {
                    key: p.base_url,
                    className: 'rounded-lg border border-(--ui-stroke-secondary) px-3 py-2',
                    children: [
                      jsxs('div', {
                        className: 'flex flex-wrap items-center gap-2 text-sm font-medium',
                        children: [
                          jsx('span', { children: p.provider }),
                          p.default_model
                            ? jsx('span', {
                                className:
                                  'rounded-full bg-(--ui-accent) px-2 py-0.5 text-[10px] font-semibold leading-none text-white',
                                children: t('hermes.default')
                              })
                            : null,
                          p.error
                            ? jsx('span', {
                                className: 'text-[11px] text-red-500',
                                children: t('hermes.offline')
                              })
                            : null
                        ]
                      }),
                      p.models.length
                        ? jsx('div', {
                            className: 'mt-1 break-words text-xs text-(--ui-text-secondary)',
                            children: p.models.join(', ')
                          })
                        : p.error
                          ? jsx('div', {
                              className: 'mt-1 truncate text-xs text-(--ui-text-secondary)',
                              children: p.error
                            })
                          : null
                    ]
                  })
                )
              })
        ]
      })
    }
  )

  // ---- extra folders section --------------------------------------------
  const extraSection = jsxs(
    'section',
    {
      className: sectionCls,
      children: [
        jsx('h2', { className: `${sectionTitleCls} mb-3`, children: t('modelsDirs.header') }),
        jsxs('div', {
          className: 'flex gap-2',
          children: [
            jsx('input', {
              type: 'text',
              value: dirInput,
              placeholder: t('modelsDirs.placeholder'),
              onChange: (e) => setDirInput(e.target.value),
              className: `${inputCls} min-w-0 flex-1`
            }),
            jsx('button', {
              type: 'button',
              disabled: !dirInput.trim(),
              onClick: () => void addDir(),
              className: actionBtnCls,
              children: t('modelsDirs.add')
            })
          ]
        }),
        extraDirs.length
          ? jsxs('div', {
              className: 'mt-3 space-y-2',
              children: extraDirs.map((e) =>
                jsxs('div', {
                  key: e.dir,
                  className: 'rounded-lg border border-(--ui-stroke-secondary) px-3 py-2',
                  children: [
                    jsxs('div', {
                      className: 'flex items-center gap-2',
                      children: [
                        jsx('span', {
                          className: 'min-w-0 flex-1 truncate text-xs font-medium',
                          children: e.dir
                        }),
                        jsx('button', {
                          type: 'button',
                          onClick: () => removeDir(e.dir),
                          className: actionBtnCls,
                          children: t('modelsDirs.remove')
                        })
                      ]
                    }),
                    e.error
                      ? jsx('div', {
                          className: 'mt-1 break-words text-xs text-red-500',
                          children: e.error
                        })
                      : e.models === null
                        ? jsx('div', {
                            className: 'mt-1 text-xs text-(--ui-text-secondary)',
                            children: '…'
                          })
                        : e.models.length === 0
                          ? jsx('div', {
                              className: 'mt-1 text-xs text-(--ui-text-secondary)',
                              children: t('modelsDirs.empty')
                            })
                          : jsx('div', {
                              className: 'mt-1 text-xs text-(--ui-text-secondary)',
                              children: `${e.models.length} — ${e.models.map((m) => m.id).join(', ')}`
                            })
                  ]
                })
              )
            })
          : null
      ]
    }
  )

  // ---- runtime builds section --------------------------------------------
  const builds = (buildsQuery.data && buildsQuery.data.builds) || []
  const buildRow = (b) => {
    const name = variantLabel(b)
    const label = b.build ? `${name} b${b.build}` : name
    const meta = [
      VARIANT_META[_variantKey(b)] || b.variant,
      b.platform,
      b.size_mb != null ? `${b.size_mb} MB` : null
    ].filter(Boolean).join(' · ')
    return jsxs('div', {
      key: b.entry,
      className: cn(
        'flex items-center gap-3 rounded-lg border px-4 py-3',
        b.active
          ? 'border-(--ui-accent) bg-(--ui-bg-quinary)'
          : 'border-(--ui-stroke-secondary)'
      ),
      children: [
        jsxs('div', {
          className: 'min-w-0 flex-1',
          children: [
            jsxs('div', {
              className: 'flex flex-wrap items-center gap-2',
              children: [
                jsx('span', { className: 'truncate text-sm font-medium', children: label }),
                b.active
                  ? jsx('span', {
                      className:
                        'shrink-0 rounded-full bg-(--ui-accent) px-2 py-0.5 text-[10px] font-semibold leading-none text-white',
                      children: t('builds.active')
                    })
                  : null,
                b.deprecated
                  ? jsx('span', {
                      className: 'shrink-0 text-[10px] font-medium text-amber-500',
                      children: t('builds.deprecated')
                    })
                  : null
              ]
            }),
            meta
              ? jsx('div', {
                  className: 'mt-0.5 truncate text-xs text-(--ui-text-secondary)',
                  children: meta
                })
              : null
          ]
        })
      ]
    })
  }
  const buildsSection = jsxs('section', {
    className: sectionCls,
    children: [
      jsx('h2', { className: `${sectionTitleCls} mb-2`, children: t('builds.header') }),
      jsx('p', {
        className: 'mb-3 text-xs text-(--ui-text-secondary)',
        children: t('builds.hint')
      }),
      buildsQuery.data
        ? builds.length === 0
          ? jsx('div', {
              className: 'py-4 text-center text-sm text-(--ui-text-secondary)',
              children: t('builds.empty')
            })
          : jsx('div', {
              className: 'space-y-2',
              children: builds.map(buildRow)
            })
        : jsx('div', {
            className: 'py-4 text-center text-sm text-(--ui-text-secondary)',
            children: '…'
          })
    ]
  })

  // ---- model rows ---------------------------------------------------------
  const modelRow = (m) => {
    const isSelected = m.id === selected
    const rowBusy = !!busy && (busy.model === m.id || busy.kind === 'apply')
    const canLoad = m.status !== 'loaded' && m.status !== 'loading'
    const canUnload = m.status === 'loaded'
    const meta = [m.size_human, m.quant].filter(Boolean).join(' · ')
    return jsxs('div', {
      key: m.id,
      onClick: () => selectModel(m.id),
      className: cn(
        'flex cursor-pointer items-center gap-3 rounded-lg border px-4 py-3 transition-colors',
        isSelected
          ? 'border-(--ui-accent) bg-(--ui-bg-quinary)'
          : 'border-(--ui-stroke-secondary) hover:bg-(--ui-bg-quinary)'
      ),
      children: [
        jsxs('div', {
          className: 'min-w-0 flex-1',
          children: [
            jsxs('div', {
              className: 'flex items-center gap-2',
              children: [
                jsx('span', { className: 'truncate text-sm font-medium', children: m.id }),
                m.active
                  ? jsx('span', {
                      className:
                        'shrink-0 rounded-full bg-(--ui-accent) px-2 py-0.5 text-[10px] font-semibold leading-none text-white',
                      children: t('badge.active')
                    })
                  : null,
                m.in_preset
                  ? jsx('span', {
                      className: 'shrink-0 text-[10px] text-(--ui-text-secondary)',
                      children: t('badge.inPreset')
                    })
                  : null
              ]
            }),
            meta
              ? jsx('div', {
                  className: 'mt-0.5 truncate text-xs text-(--ui-text-secondary)',
                  children: meta
                })
              : null
          ]
        }),
        jsx(
          'span',
          {
            className: cn(
              'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium',
              pillStyle(m.status)
            ),
            children: statusLabel(m.status)
          }
        ),
        jsx(
          Tip,
          {
            label: t('action.load'),
            children: jsx('button', {
              type: 'button',
              disabled: !canLoad || rowBusy,
              onClick: (e) => {
                e.stopPropagation()
                void runLoadUnload('load', m.id)
              },
              className: actionBtnCls,
              children: t('action.load')
            })
          }
        ),
        jsx(
          Tip,
          {
            label: t('action.unload'),
            children: jsx('button', {
              type: 'button',
              disabled: !canUnload || rowBusy,
              onClick: (e) => {
                e.stopPropagation()
                void runLoadUnload('unload', m.id)
              },
              className: actionBtnCls,
              children: t('action.unload')
            })
          }
        )
      ]
    })
  }

  const field = (f) => {
    const value = form ? String(form[f.key] != null ? form[f.key] : '') : ''
    const options =
      f.options === 'flash'
        ? (status && status.valid_flash_attn) || []
        : (status && status.valid_cache_types) || []
    const control =
      f.type === 'select' ? (
        jsx(MiniSelect, {
          value,
          disabled: !form || !!busy,
          onChange: (v) => setField(f.key, v),
          options: ['', ...options],
        })
      ) : (
        jsx('input', {
          type: 'text',
          inputMode: 'decimal',
          value,
          disabled: !form || !!busy,
          placeholder: f.placeholder,
          onChange: (e) => setField(f.key, e.target.value),
          className: inputCls
        })
      )
    return jsx(
      'div',
      {
        key: f.key,
        className: 'flex flex-col gap-1.5',
        children: [
          jsx(
            'span',
            {
              className: 'text-xs font-medium text-(--ui-text-secondary)',
              children: t(`field.${f.key}`)
            }
          ),
          control
        ]
      }
    )
  }

  const formCard = jsx(
    'form',
    {
      onSubmit: onApply,
      children: [
        jsxs('div', {
          className: 'mb-3 flex items-center justify-between gap-2',
          children: [
            jsx('h2', {
              className: sectionTitleCls,
              children: t('form.title')
            }),
            selected
              ? jsx('span', {
                  className: 'truncate text-xs text-(--ui-text-secondary)',
                  children: selected
                })
              : null
          ]
        }),
        !selected ? (
          jsx('div', {
            className: 'py-8 text-center text-sm text-(--ui-text-secondary)',
            children: t('form.empty')
          })
        ) : (
          jsxs('div', {
            className: 'flex flex-col gap-4',
            children: [
              jsxs('div', {
                className: 'rounded-lg border border-(--ui-stroke-secondary) p-3',
                children: [
                  jsx('div', {
                    className: 'mb-1 text-xs font-medium text-(--ui-text-secondary)',
                    children: t('modes.title')
                  }),
                  jsx('div', {
                    className: 'mb-2 text-[11px] leading-snug text-(--ui-text-secondary)',
                    children: t('modes.hint')
                  }),
                  jsxs('div', {
                    className: 'flex flex-wrap gap-2',
                    children: [
                      jsx('button', {
                        type: 'button',
                        disabled: !!busy,
                        onClick: () => onApplyMode('coding'),
                        className:
                          'h-8 rounded-md border border-(--ui-stroke-secondary) bg-(--ui-bg-quinary) px-3 text-sm font-medium hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40',
                        children: t('modes.coding')
                      }),
                      jsx('button', {
                        type: 'button',
                        disabled: !!busy,
                        onClick: () => onApplyMode('chat'),
                        className:
                          'h-8 rounded-md border border-(--ui-stroke-secondary) bg-(--ui-bg-quinary) px-3 text-sm font-medium hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40',
                        children: t('modes.chat')
                      })
                    ]
                  })
                ]
              }),
              jsx('div', {
                className: 'grid grid-cols-2 gap-3 md:grid-cols-3',
                children: FIELDS.map(field)
              }),
              jsxs('div', {
                className: 'flex items-center justify-between border-t border-(--ui-stroke-secondary) pt-3',
                children: [
                  jsxs('label', {
                    className: 'flex cursor-pointer items-center gap-2 text-xs text-(--ui-text-secondary)',
                    children: [
                      jsx('input', {
                        type: 'checkbox',
                        checked: !!(form && form.set_active),
                        disabled: !form || !!busy,
                        onChange: (e) => setField('set_active', e.target.checked),
                        className: 'h-3.5 w-3.5 accent-(--ui-accent)'
                      }),
                      t('form.setActive')
                    ]
                  }),
                  jsx('button', {
                    type: 'submit',
                    disabled: !form || !!busy,
                    className:
                      'h-8 rounded-md bg-(--ui-accent) px-4 text-sm font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40',
                    children: busy && busy.kind === 'apply' ? t('form.applying') : t('form.apply')
                  })
                ]
              })
            ]
          })
        )
      ]
    }
  )

  return jsx(
    'div',
    {
      className: 'h-full overflow-y-auto',
      children: jsxs('div', {
        className: 'mx-auto flex w-full max-w-3xl flex-col gap-4 px-6 py-6',
        children: [
          jsx('div', {
            className:
              'rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs text-amber-600',
            children: t('catalog.notice')
          }),
          jsxs('div', {
            className:
              'flex flex-wrap items-center gap-3 rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-bg-elevated) px-4 py-3',
            children: [
              jsx('span', {
                className: cn(
                  'h-2.5 w-2.5 shrink-0 rounded-full',
                  status && status.running ? 'bg-emerald-500' : 'bg-(--ui-stroke)'
                )
              }),
              jsx('div', {
                className: 'min-w-0 flex-1',
                children: jsxs('div', {
                  className: 'flex flex-wrap items-baseline gap-x-2',
                  children: [
                    jsx('span', {
                      className: 'text-sm font-semibold',
                      children: `Server: ${(status && status.backend) || '—'}`
                    }),
                    jsx('span', {
                      className: 'text-xs text-(--ui-text-secondary)',
                      children: `port: ${status && status.port != null ? status.port : '—'}`
                    }),
                    jsx('span', {
                      className: 'text-xs text-(--ui-text-secondary)',
                      children: (() => {
                        const u = updateQuery.data
                        if (!u) return null
                        const vs = updateVariants
                        const fmt = (label, s) => {
                          if (!s || s.error) return null
                          if (s.update_available && s.current && s.latest)
                            return `${label} ${s.current} → ${s.latest}`
                          if (s.latest) return `${label} ${s.latest}`
                          return null
                        }
                        const parts = [fmt('Vulkan', vs.vulkan), fmt('ROCm', vs.rocm), fmt('CUDA', vs.cuda)]
                        if (parts.length) return parts.filter(Boolean).join(' · ')
                        return null
                      })()
                    })
                  ]
                })
              }),
              jsx('a', {
                href: REPO_CUDA_URL,
                target: '_blank',
                rel: 'noreferrer',
                className: linkCls,
                children: t('link.cuda')
              }),
              jsx('a', {
                href: REPO_VULKAN_URL,
                target: '_blank',
                rel: 'noreferrer',
                className: linkCls,
                children: t('link.vulkan')
              }),
              jsx('a', {
                href: REPO_ROCM_URL,
                target: '_blank',
                rel: 'noreferrer',
                className: linkCls,
                children: t('link.rocm')
              }),
              jsx(
                'span', {
                  className:
                    'shrink-0 rounded-full bg-(--ui-bg-quinary) px-2.5 py-1 text-[11px] font-medium text-(--ui-text-secondary)',
                  children: status && status.running ? t('server.running') : t('server.idle')
                }
              )
            ]
          }),
          systemSection,
          hermesSection,
          controlSection,
          buildsSection,
          jsx(
            'section',
            {
              className: sectionCls,
              children:
                models.length === 0 ? (
                  jsxs('div', {
                    children: [
                      jsx('h2', {
                        className: `${sectionTitleCls} mb-2`,
                        children: t('models.header')
                      }),
                      jsx('div', {
                        className: 'py-6 text-center text-sm text-(--ui-text-secondary)',
                        children: t('models.empty')
                      })
                    ]
                  })
                ) : (
                  jsxs('div', {
                    children: [
                      jsx('h2', {
                        className: `${sectionTitleCls} mb-3`,
                        children: t('models.header')
                      }),
                      jsx('div', {
                        className: 'space-y-2',
                        children: models.map(modelRow)
                      })
                    ]
                  })
                )
            }
          ),
          extraSection,
          jsxs('section', {
            className: sectionCls,
            children: [
              jsx('h2', { className: `${sectionTitleCls} mb-3`, children: t('bench.header') }),
              jsxs('div', {
                className: 'flex flex-wrap items-end gap-3',
                children: [
                  jsxs('label', {
                    className: 'flex flex-col gap-1 text-xs text-(--ui-text-secondary)',
                    children: [
                      jsx('span', { children: t('bench.model') }),
                      jsx(MiniSelect, {
                        value: benchModel || (models[0] && models[0].id) || '',
                        disabled: !!benchBusy,
                        onChange: setBenchModel,
                        options: models.map((m) => ({ value: m.id, label: m.id })),
                      }),
                    ],
                  }),
                  jsxs('label', {
                    className: 'flex flex-col gap-1 text-xs text-(--ui-text-secondary)',
                    children: [
                      jsx('span', { children: t('bench.mode') }),
                      jsx(MiniSelect, {
                        value: benchMode,
                        disabled: !!benchBusy,
                        onChange: setBenchMode,
                        options: [
                          { value: 'generation', label: 'generation (tok/s)' },
                          { value: 'prefill', label: 'prefill (tok/s)' },
                          { value: 'both', label: t('bench.mode.both') },
                        ],
                      }),
                    ],
                  }),
                  benchMode !== 'generation'
                    ? jsxs('label', {
                        className: 'flex flex-col gap-1 text-xs text-(--ui-text-secondary)',
                        children: [
                          jsx('span', { children: t('bench.promptTokens') }),
                          jsx('input', {
                            type: 'number', min: 16, max: 65536, value: benchPromptTokens,
                            disabled: !!benchBusy,
                            onChange: (e) => setBenchPromptTokens(e.target.value),
                            className: inputCls,
                          }),
                        ],
                      })
                    : null,
                  benchMode !== 'prefill'
                    ? jsxs('label', {
                        className: 'flex flex-col gap-1 text-xs text-(--ui-text-secondary)',
                        children: [
                          jsx('span', { children: t('bench.outputTokens') }),
                          jsx('input', {
                            type: 'number', min: 1, max: 4096, value: benchOutputTokens,
                            disabled: !!benchBusy,
                            onChange: (e) => setBenchOutputTokens(e.target.value),
                            className: inputCls,
                          }),
                        ],
                      })
                    : null,
                  jsxs('label', {
                    className: 'flex flex-col gap-1 text-xs text-(--ui-text-secondary)',
                    children: [
                      jsx('span', { children: t('bench.runs') }),
                      jsx('input', {
                        type: 'number', min: 1, max: 5, value: benchRuns,
                        disabled: !!benchBusy,
                        onChange: (e) => setBenchRuns(e.target.value),
                        className: inputCls,
                      }),
                    ],
                  }),
                  jsx('button', {
                    type: 'button',
                    disabled: !!benchBusy,
                    onClick: runBench,
                    className: actionBtnCls,
                    children: benchBusy ? t('bench.running') : t('bench.run'),
                  }),
                ],
              }),
              benchResults.length
                ? jsxs('div', {
                    className: 'mt-3 overflow-x-auto',
                    children: [
                      jsx('table', {
                        className: 'w-full text-xs',
                        children: [
                          jsx('thead', {
                            children: jsx('tr', {
                              children: [
                                jsx('th', { className: 'px-2 py-1 text-left text-(--ui-text-secondary) font-medium', children: t('bench.col.mode') }),
                                jsx('th', { className: 'px-2 py-1 text-center text-(--ui-text-secondary) font-medium', children: t('bench.col.prompt') }),
                                jsx('th', { className: 'px-2 py-1 text-center text-(--ui-text-secondary) font-medium', children: t('bench.col.output') }),
                                jsx('th', { className: 'px-2 py-1 text-center text-(--ui-text-secondary) font-medium', children: t('bench.col.ttft') }),
                                jsx('th', { className: 'px-2 py-1 text-center text-(--ui-text-secondary) font-medium', children: t('bench.col.total') }),
                                jsx('th', { className: 'px-2 py-1 text-center text-(--ui-text-secondary) font-medium', children: t('bench.col.tps') }),
                              ],
                            }),
                          }),
                          jsx('tbody', {
                            children: benchResults.map((r, i) =>
                              jsx('tr', {
                                children: [
                                  jsx('td', { className: 'px-2 py-1 text-left', children: r.mode }),
                                  jsx('td', { className: 'px-2 py-1 text-center', children: r.prompt_tokens }),
                                  jsx('td', { className: 'px-2 py-1 text-center', children: r.completion_tokens }),
                                  jsx('td', { className: 'px-2 py-1 text-center', children: r.ttft_s }),
                                  jsx('td', { className: 'px-2 py-1 text-center', children: r.total_s }),
                                  jsx('td', { className: 'px-2 py-1 text-center font-medium', children: r.mode === 'prefill' ? r.prefill_tok_per_s : r.tok_per_s }),
                                ],
                                key: i,
                              })
                            ),
                          }),
                        ],
                      }),
                    ],
                  })
                : null,
              jsxs('div', {
                className: 'mt-4',
                children: [
                  jsx('div', {
                    className: 'mb-1 text-[11px] font-medium text-(--ui-text-secondary)',
                    children: t('bench.board'),
                  }),
                  jsx('div', {
                    className: 'overflow-x-auto',
                    children: jsx('table', {
                      className: 'w-full text-xs',
                      children: [
                        jsx('thead', {
                          children: jsx('tr', {
                            children: [
                              jsx('th', { className: 'px-2 py-1 text-left text-(--ui-text-secondary) font-medium', children: '#' }),
                              jsx('th', { className: 'px-2 py-1 text-left text-(--ui-text-secondary) font-medium', children: t('bench.col.model') }),
                              jsx('th', { className: 'px-2 py-1 text-center text-(--ui-text-secondary) font-medium', children: t('bench.col.gen') }),
                              jsx('th', { className: 'px-2 py-1 text-center text-(--ui-text-secondary) font-medium', children: t('bench.col.prefill') }),
                              jsx('th', { className: 'px-2 py-1 text-left text-(--ui-text-secondary) font-medium', children: t('bench.col.server') }),
                              jsx('th', { className: 'px-2 py-1 text-left text-(--ui-text-secondary) font-medium', children: t('bench.col.preset') }),
                            ],
                          }),
                        }),
                        jsx('tbody', {
                          children: ((benchBoardQuery.data && benchBoardQuery.data.rows) || []).map((r, i) => {
                            const p = r.preset || {}
                            const parts = []
                            if (p['ctx-size']) parts.push(`ctx ${p['ctx-size']}`)
                            if (p['n-gpu-layers']) parts.push(`ngl ${p['n-gpu-layers']}`)
                            if (p['cache-type-k']) parts.push(p['cache-type-k'])
                            if (p['flash-attn']) parts.push(`fa ${p['flash-attn']}`)
                            if (p['temp'] != null) parts.push(`temp ${p['temp']}`)
                            const server = r.server || {}
                            const serverLabel = (server.package || server.build)
                              ? `${server.package || ''} ${server.build || ''}`.trim()
                              : ''
                            return jsxs('tr', {
                              children: [
                                jsx('td', { className: 'px-2 py-1 text-center', children: i + 1 }),
                                jsx('td', { className: 'px-2 py-1 text-left', children: r.model }),
                                jsx('td', { className: 'px-2 py-1 text-center font-medium', children: r.generation_tok_per_s || '—' }),
                                jsx('td', { className: 'px-2 py-1 text-center', children: r.prefill_tok_per_s || '—' }),
                                jsx('td', { className: 'px-2 py-1 text-left text-[11px]', children: serverLabel || '—' }),
                                jsx('td', { className: 'px-2 py-1 text-left text-[11px]', children: parts.length ? parts.join(' · ') : '—' }),
                              ],
                              key: r.model,
                            })
                          }),
                        }),
                      ],
                    }),
                  }),
                ],
              }),
              (() => {
                const entries = (benchHistoryQuery.data && benchHistoryQuery.data.entries) || []
                if (!entries.length) return null
                const last = entries.slice(-5).reverse()
                return jsxs('div', {
                  className: 'mt-3',
                  children: [
                    jsx('div', {
                      className: 'mb-1 text-[11px] font-medium text-(--ui-text-secondary)',
                      children: t('bench.recent'),
                    }),
                    jsx('ul', {
                      className: 'space-y-1 text-[11px] text-(--ui-text-secondary)',
                      children: last.map((e, i) =>
                        jsx('li', {
                          children: `${new Date(e.ts * 1000).toLocaleString()} · ${e.model} · ${e.mode} · ${(e.runs || []).map((r) => `${r.mode} ${r.mode === 'prefill' ? r.prefill_tok_per_s : r.tok_per_s} tok/s`).join(', ')}`,
                        }, i)
                      ),
                    }),
                  ],
                })
              })(),
            ],
          }),
          jsx(
            'section',
            {
              className: sectionCls,
              children: [
                jsx('h2', {
                  className: `${sectionTitleCls} mb-2`,
                  children: t('opts.title')
                }),
                jsx('p', {
                  className: 'mb-3 text-xs text-(--ui-text-secondary)',
                  children: t('opts.hint')
                }),
                status && status.server_command ? (
                  jsxs('div', {
                    children: [
                      jsx('div', {
                        className: 'mb-1 text-[11px] font-medium text-(--ui-text-secondary)',
                        children: t('opts.cmd')
                      }),
                      jsx('pre', {
                        className: 'mb-3 overflow-x-auto rounded-lg border border-(--ui-stroke-secondary) bg-(--ui-bg-quinary) p-3 text-[11px] leading-relaxed',
                        children: status.server_command
                      })
                    ]
                  })
                ) : (
                  jsx('div', {
                    className: 'mb-3 text-xs text-(--ui-text-secondary)',
                    children: t('opts.none')
                  })
                ),
                jsxs('div', {
                  children: [
                    jsx('div', {
                      className: 'mb-1 text-[11px] font-medium text-(--ui-text-secondary)',
                      children: t('opts.preset')
                    }),
                    (() => {
                      // Valori effettivi del modello: default + preset salvato.
                      // Mostra il modello selezionato nel form (se e' locale),
                      // altrimenti il modello locale attivo. Includi sempre i
                      // default, cosi' n-gpu-layers = 999 e' visibile per tutti.
                      const defs = (status && status.default_options) || {}
                      const apiToIni = {}
                      for (const [ini, api] of Object.entries(INI_TO_API)) apiToIni[api] = ini
                      const base = {}
                      for (const [api, ini] of Object.entries(apiToIni)) {
                        if (defs[api] !== undefined && defs[api] !== null) base[ini] = defs[api]
                      }
                      const local = models.find((m) => m.active) || models[0]
                      const shown =
                        selected && models.some((m) => m.id === selected)
                          ? selected
                          : local
                            ? local.id
                            : null
                      const p = status && status.presets && shown ? status.presets[shown] : null
                      const merged = { ...base, ...(p || {}) }
                      if (!Object.keys(merged).length)
                        return jsx('div', {
                          className: 'text-xs text-(--ui-text-secondary)',
                          children: t('opts.none')
                        })
                      return jsx('div', {
                        className: 'flex flex-wrap gap-1.5',
                        children: Object.entries(merged).map(([k, v]) =>
                          jsx('span', {
                            key: k,
                            className:
                              'rounded-md bg-(--ui-bg-quinary) px-2 py-0.5 text-[11px] font-medium',
                            children: `${k} = ${v}`
                          })
                        )
                      })
                    })()
                  ]
                })
              ]
            }
          ),
          jsx(
            'div',
            {
              className: sectionCls,
              children: formCard
            }
          )
        ]
      })
    }
  )
}



export default {
  id: 'llama-cpp',
  name: 'llama.cpp',
  description:
    'llama.cpp backend manager: server status, system info, model load/unload, extra model folders and per-model presets.',
  defaultEnabled: true,
  register(ctx) {
    ctx.i18n.register(LOCALES)
    ctx.register({
      id: 'page',
      area: ROUTES_AREA,
      data: { path: '/llama-cpp' },
      render: () => jsx(Page, { ctx })
    })
    ctx.register({
      id: 'nav',
      area: SIDEBAR_NAV_AREA,
      order: 50,
      data: { codicon: 'circuit-board', label: 'llama.cpp', path: '/llama-cpp' }
    })
    ctx.register({
      id: 'open',
      area: PALETTE_AREA,
      data: {
        id: 'llama-cpp.open',
        label: 'llama.cpp: open the model manager',
        keywords: ['llama', 'llama.cpp', 'llamacpp', 'gguf', 'model', 'models', 'backend', 'preset', 'rocm'],
        run: () => host.navigate('/llama-cpp')
      }
    })
  }
}
