/**
 * Catalogo Plugin IT — una pagina nativa dentro Hermes con il catalogo
 * ufficiale dei plugin Hermes, con interfaccia in italiano e descrizioni
 * tradotte al volo (l'utente apre un plugin → la descrizione viene tradotta).
 *
 * Fonte dati: il JSON pubblico del catalogo Hermes (lo stesso che usa la
 * pagina "Plugins" di Hermes). Nessun backend, nessun server: una pagina sola.
 */

import {
  atom,
  Badge,
  Button,
  Codicon,
  host,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  SearchField,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Separator,
  Tip,
  usePluginI18n,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import { useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'catalogo-it'
const CATALOG_URL = 'https://hermes-agent.nousresearch.com/docs/api/plugins.json'

const CATEGORIES = {
  tools: 'Strumenti',
  desktop: 'Desktop',
  models: 'Modelli',
  automation: 'Automazione',
  general: 'Generale',
  memory: 'Memoria',
  platform: 'Piattaforme',
  voice: 'Voce',
  web: 'Web'
}
const TIERS = { official: 'Ufficiale', verified: 'Verificato', community: 'Comunità' }

const LOCALES = {
  en: {
    title: 'Plugin Catalog', subtitle: 'The official Hermes plugin catalog, in Italian',
    search: 'Search plugins…', category: 'Category', tier: 'Tier', all: 'All',
    plugins: 'plugins', loading: 'Loading catalog…', error: 'Could not load the catalog.',
    retry: 'Try again', translate: 'Translate description', translating: 'Translating…',
    showOriginal: 'Show original', original: 'Original', translated: 'Italian',
    by: 'by', repository: 'Repository', docs: 'Documentation', version: 'Version',
    platforms: 'Platforms', noResults: 'No plugins found', categories: 'categories',
    clear: 'Clear', close: 'Close', official: 'Official', community: 'Community',
    verified: 'Verified', open: 'Open catalog', refresh: 'Reload catalog', install: 'Install plugin',
    searchTitle: 'Cerca Plugin', searchSubtitle: 'Digita una parola: vedrai solo i plugin corrispondenti',
    searchPlaceholder: 'Digita per cercare…', searchHint: 'Prova con token, memory, web…', searchIdle: 'Digita una parola per cercare'
  },
  it: {
    title: 'Catalogo Plugin', subtitle: 'Il catalogo ufficiale dei plugin Hermes, in italiano',
    search: 'Cerca plugin…', category: 'Categoria', tier: 'Livello', all: 'Tutti',
    plugins: 'plugin', loading: 'Caricamento catalogo…', error: 'Impossibile caricare il catalogo.',
    retry: 'Riprova', translate: 'Traduci descrizione', translating: 'Traduzione…',
    showOriginal: 'Mostra originale', original: 'Originale', translated: 'Italiano',
    by: 'di', repository: 'Repository', docs: 'Documentazione', version: 'Versione',
    platforms: 'Piattaforme', noResults: 'Nessun plugin trovato', categories: 'categorie',
    clear: 'Pulisci', close: 'Chiudi', official: 'Ufficiale', community: 'Comunità',
    verified: 'Verificato', open: 'Apri catalogo', refresh: 'Ricarica catalogo', install: 'Installa plugin',
    searchTitle: 'Cerca Plugin', searchSubtitle: 'Digita una parola: vedrai solo i plugin corrispondenti',
    searchPlaceholder: 'Digita per cercare…', searchHint: 'Prova con token, memoria, web…', searchIdle: 'Digita una parola per cercare'
  }
}

const CSS = `
.hermes-cat-wrap{display:flex;flex-direction:column;height:100%;min-height:0;color:var(--ui-text-primary);background:var(--ui-bg-editor)}
.hermes-cat-head{display:flex;flex-direction:row;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--ui-stroke-secondary);flex-shrink:0;background:var(--ui-bg-editor)}
.hermes-cat-title{font-size:14px;font-weight:600;line-height:20px;color:var(--ui-text-primary)}
.hermes-cat-sub{font-size:12px;line-height:16px;color:var(--ui-text-secondary)}
.hermes-cat-tools{display:flex;align-items:center;gap:8px;flex:1;min-width:0}
.hermes-cat-search{flex:1;min-width:0}
.hermes-cat-search input{font-size:13px;height:32px;min-width:0}
.hermes-cat-searchbox{display:flex;align-items:center;gap:8px;padding:10px 14px;border:1px solid var(--ui-stroke-secondary);border-radius:10px;background:var(--ui-bg-elevated);flex:1;min-width:280px;min-height:40px}
.hermes-cat-searchbox .hermes-cat-search{flex:1;min-width:0}
.hermes-cat-searchbox:focus-within{border-color:var(--ui-accent)}
.hermes-cat-select{height:30px;min-width:120px;font-size:12px}
.hermes-cat-count{font-size:11px;color:var(--ui-text-tertiary);white-space:nowrap}
.hermes-cat-body{display:flex;min-height:0;flex:1;background:var(--ui-bg-editor)}
.hermes-cat-list{flex:1;min-width:0;overflow-y:auto;padding:8px;overscroll-behavior:contain;background:var(--ui-bg-editor)}
.hermes-cat-row{display:flex;flex-direction:column;gap:6px;padding:10px 12px;border-radius:8px;cursor:pointer;border:1px solid transparent;transition:background .12s,border-color .12s}
.hermes-cat-row:hover{background:var(--chrome-action-hover)}
.hermes-cat-row[data-open=true]{background:var(--ui-fill-tertiary);border-color:var(--ui-stroke-secondary)}
.hermes-cat-row-top{display:flex;align-items:center;gap:8px;min-width:0}
.hermes-cat-name{font-size:13px;font-weight:600;line-height:18px;color:var(--ui-text-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.hermes-cat-desc{font-size:12px;line-height:17px;color:var(--ui-text-secondary);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.hermes-cat-badges{display:flex;align-items:center;gap:6px;flex-shrink:0;margin-left:auto}
.hermes-cat-detail{width:min(420px,46%);min-width:280px;border-left:1px solid var(--ui-stroke-secondary);padding:16px;overflow-y:auto;display:flex;flex-direction:column;gap:10px;background:var(--ui-bg-editor)}
.hermes-cat-detail-title{font-size:15px;font-weight:600;line-height:20px}
.hermes-cat-detail-meta{font-size:11px;color:var(--ui-text-tertiary);line-height:16px}
.hermes-cat-detail-desc{font-size:12px;line-height:18px;color:var(--ui-text-secondary);white-space:pre-wrap}
.hermes-cat-actions{display:flex;align-items:center;gap:8px;margin-top:4px}
.hermes-cat-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;padding:32px;color:var(--ui-text-secondary);text-align:center;font-size:12px}
.hermes-cat-spinner{color:var(--ui-accent)}
`

const state = atom('')

function categoryLabel(key, t) {
  return CATEGORIES[key] ?? (key ? key[0].toUpperCase() + key.slice(1) : t('all'))
}

function tierLabel(key) {
  return TIERS[key] ?? key
}

function matchText(entry, needle) {
  if (!needle) return true
  const hay = `${entry.title ?? ''} ${entry.name} ${entry.maintainer} ${entry.description ?? ''} ${entry.category ?? ''}`.toLowerCase()
  return needle.split(/\s+/).filter(Boolean).every(token => hay.includes(token))
}

// Google Translate: gratuito, nessuna chiave, nessun limite giornaliero.
async function googleTranslate(text, signal) {
  const url = `https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=it&dt=t&q=${encodeURIComponent(text.slice(0, 1500))}`
  const response = await fetch(url, { signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]), credentials: 'omit' })
  if (!response.ok) return null
  const data = await response.json()
  const segments = Array.isArray(data?.[0]) ? data[0] : []
  const out = segments.map(segment => segment?.[0] ?? '').join('')
  if (!out || out.toLowerCase() === text.toLowerCase()) return null
  return out
}

// MyMemory: seconda fonte, utile se Google non risponde.
async function myMemoryTranslate(text, signal) {
  const url = `https://api.mymemory.translated.net/get?q=${encodeURIComponent(text.slice(0, 450))}&langpair=en|it`
  const response = await fetch(url, { signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]), credentials: 'omit' })
  if (!response.ok) return null
  const data = await response.json()
  const out = data?.responseData?.translatedText
  if (!out || /WARNING: YOU USED ALL/i.test(out)) return null
  if (out.toLowerCase() === text.toLowerCase()) return null
  return String(out)
}

async function translate(text, signal) {
  const clean = (text ?? '').trim()
  if (!clean) return null
  const fromGoogle = await googleTranslate(clean, signal)
  if (fromGoogle) return fromGoogle
  return myMemoryTranslate(clean, signal)
}

// Fallback: il modello locale di Hermes (llama.cpp) traduce senza limiti di quota.
// L'endpoint e la chiave li legge il backend Hermes (base_url del config +
// runtimes/llamacpp/.api_key) e li espone in /api-key: la porta del server
// locale cambia a ogni avvio, quindi non puo' stare qui.
let _local = null
async function localEndpoint(signal) {
  if (_local) return _local
  const response = await fetch('/api/plugins/catalogo-it/api-key', {
    signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
    credentials: 'omit'
  })
  if (!response.ok) throw new Error('no local backend')
  const data = await response.json()
  if (!data?.key || !data?.base_url) throw new Error('no local endpoint')
  _local = {
    key: data.key,
    baseUrl: String(data.base_url).replace(/\/+$/, ''),
    model: data.model || ''
  }
  return _local
}

// Il modello configurato puo' non esistere sul server: in quel caso si usa il
// primo che il server dichiara in /v1/models.
let _localModel = ''
async function localModelName(baseUrl, signal) {
  if (_localModel) return _localModel
  try {
    const response = await fetch(`${baseUrl}/models`, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
      credentials: 'omit'
    })
    if (response.ok) {
      const data = await response.json()
      const first = data?.data?.[0]?.id
      if (first) _localModel = String(first)
    }
  } catch { /* il modello resta quello del config */ }
  return _localModel
}

async function translateLocal(text, signal) {
  const info = await localEndpoint(signal)
  const model = (await localModelName(info.baseUrl, signal)) || info.model
  if (!model) throw new Error('no local model')
  const response = await fetch(`${info.baseUrl}/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${info.key}` },
    signal: AbortSignal.any([signal, AbortSignal.timeout(90000)]),
    body: JSON.stringify({
      model,
      messages: [
        { role: 'system', content: 'Traduci in italiano. Rispondi SOLO con la traduzione, senza spiegazioni.' },
        { role: 'user', content: text }
      ],
      max_tokens: 800,
      temperature: 0.1,
      chat_template_kwargs: { enable_thinking: false }
    })
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const data = await response.json()
  const message = data?.choices?.[0]?.message
  const out = message?.content || message?.reasoning_content
  return typeof out === 'string' && out.trim() ? out.trim() : null
}

async function translateEntry(text, signal) {
  try {
    const out = await translate(text, signal)
    if (out) return out
  } catch { /* passa al fallback */ }
  return translateLocal(text, signal)
}

function PluginRow({ entry, open, onOpen, t }) {
  const [text, setText] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const translated = text !== null

  const handleTranslate = async () => {
    if (busy) return
    setBusy(true)
    setError(false)
    try {
      const out = await translateEntry(entry.description ?? '')
      if (out) setText(out)
    } catch {
      setError(true)
    } finally {
      setBusy(false)
    }
  }

  const name = entry.name
  const desc = translated ? text : entry.description ?? ''

  return jsxs('div', {
    className: 'hermes-cat-row',
    'data-open': open,
    onClick: () => onOpen(entry),
    children: [
      jsxs('div', { className: 'hermes-cat-row-top', children: [
        jsx(Codicon, { name: 'package', size: 14 }),
        jsx('span', { className: 'hermes-cat-name', children: name }),
        jsxs('div', { className: 'hermes-cat-badges', children: [
          entry.category && jsx(Badge, { variant: 'muted', children: categoryLabel(entry.category, t) }),
          jsx(Badge, { variant: entry.tier === 'official' ? 'default' : 'muted', children: tierLabel(entry.tier) })
        ] })
      ] }),
      jsx('span', { className: 'hermes-cat-desc', children: desc }),
      jsxs('div', { className: 'hermes-cat-actions', children: [
        jsx(Button, {
          variant: 'ghost', size: 'micro',
          onClick: event => { event.stopPropagation(); void handleTranslate() },
          children: translated ? t('showOriginal') : busy ? t('translating') : t('translate')
        }),
        error && jsx('span', { style: { fontSize: 11, color: 'var(--ui-text-tertiary)' }, children: t('retry') })
      ] })
    ]
  })
}

function DetailPanel({ entry, t, ctx }) {
  const [text, setText] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const translated = text !== null

  // Traduzione automatica appena si apre il plugin.
  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    setBusy(true)
    setError(false)
    translateEntry(entry.description ?? '', controller.signal)
      .then(out => { if (!cancelled && out) setText(out) })
      .catch(() => { if (!cancelled) setError(true) })
      .finally(() => { if (!cancelled) setBusy(false) })
    return () => { cancelled = true; controller.abort() }
  }, [entry.name])

  const handleTranslate = async () => {
    if (busy) return
    setBusy(true)
    setError(false)
    try {
      const out = await translateEntry(entry.description ?? '')
      if (out) setText(out)
    } catch {
      setError(true)
    } finally {
      setBusy(false)
    }
  }

  const name = entry.name

  return jsxs('div', { className: 'hermes-cat-detail', children: [
    jsx('div', { className: 'hermes-cat-detail-title', children: name }),
    jsxs('div', { className: 'hermes-cat-detail-meta', children: [
      `${t('by')} ${entry.maintainer} · ${tierLabel(entry.tier)} · ${categoryLabel(entry.category, t)}`
    ] }),
    jsxs('div', { className: 'hermes-cat-actions', children: [
      jsx(Button, {
        variant: 'ghost', size: 'micro', disabled: busy,
        onClick: () => void handleTranslate(),
        children: busy ? t('translating') : translated ? t('showOriginal') : t('translate')
      }),
      error && jsx('span', { style: { fontSize: 11, color: 'var(--ui-text-tertiary)' }, children: t('retry') })
    ] }),
    jsx('div', { className: 'hermes-cat-detail-desc', children: translated ? text : entry.description ?? '' }),
    entry.version && jsxs('div', { className: 'hermes-cat-detail-meta', children: [`${t('version')}: ${entry.version}`] }),
    (entry.platforms ?? []).length > 0 && jsxs('div', { className: 'hermes-cat-detail-meta', children: [`${t('platforms')}: ${entry.platforms.join(', ')}`] }),
    jsxs('div', { className: 'hermes-cat-actions', children: [
      entry.repo && jsx(Button, {
        variant: 'ghost', size: 'sm',
        onClick: () => void ctx.os.openExternal(entry.repo),
        children: `${t('repository')} ↗`
      }),
      entry.docsUrl && jsx(Button, {
        variant: 'ghost', size: 'sm',
        onClick: () => void ctx.os.openExternal(entry.docsUrl),
        children: `${t('docs')} ↗`
      })
    ] }),
    jsx(Button, {
      variant: 'default', size: 'sm',
      onClick: () => void ctx.os.openExternal(`hermes://plugin/install?catalog=${encodeURIComponent(entry.name)}`),
      children: t('install')
    })
  ] })
}


function useCatalog() {
  return useQuery({
    queryKey: [ID, 'catalog'],
    queryFn: async ({ signal }) => {
      const response = await fetch(CATALOG_URL, {
        signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]),
        credentials: 'omit'
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      const rows = Array.isArray(data) ? data : data?.entries ?? []
      return rows.filter(row => row && typeof row.name === 'string')
    },
    staleTime: 3600000,
    retry: 1
  })
}

function CatalogPage({ ctx }) {
  const t = usePluginI18n(ID)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')
  const [tier, setTier] = useState('')
  const [openName, setOpenName] = useState(null)
  const listRef = useRef(null)
  const searchRef = useRef(null)

  const query = useCatalog()

  const entries = query.data ?? []
  const needle = search.trim().toLowerCase()
  const filtered = useMemo(() => {
    return entries.filter(entry =>
      (!needle || matchText(entry, needle)) &&
      (!category || entry.category === category) &&
      (!tier || entry.tier === tier)
    )
  }, [entries, search, category, tier])

  const openEntry = entries.find(entry => entry.name === openName) ?? null

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = 0
  }, [search, category, tier])

  // Aprire da Cmd+K (catalogo.search) mette il cursore subito sulla ricerca.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('focus') === '1') {
      setTimeout(() => searchRef.current?.focus(), 80)
    }
  }, [])

  const style = useRef(null)

  return jsxs('div', { className: 'hermes-cat-wrap', children: [
    jsxs('div', { className: 'hermes-cat-head', children: [
      jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 2, flexShrink: 0 }, children: [
        jsx('div', { className: 'hermes-cat-title', children: t('title') }),
        jsx('div', { className: 'hermes-cat-sub', children: t('subtitle') })
      ] }),
      jsxs('div', { className: 'hermes-cat-tools', children: [
        jsx('div', { className: 'hermes-cat-searchbox', children: jsx(SearchField, {
          value: search,
          onChange: setSearch,
          placeholder: t('searchPlaceholder'),
          containerClassName: 'hermes-cat-search',
          inputRef: searchRef,
          hints: [t('searchHint')]
        }) }),
        !needle && jsx(Select, { value: category, onValueChange: setCategory, children: [
          jsx(SelectTrigger, { className: 'hermes-cat-select', children: jsx(SelectValue, { placeholder: `${t('category')}: ${t('all')}` }) }),
          jsx(SelectContent, { children: [
            jsx(SelectItem, { value: '', children: t('all') }),
            ...Object.keys(CATEGORIES).map(key => jsx(SelectItem, { value: key, children: CATEGORIES[key] }, key))
          ] })
        ] }),
        !needle && jsx(Select, { value: tier, onValueChange: setTier, children: [
          jsx(SelectTrigger, { className: 'hermes-cat-select', children: jsx(SelectValue, { placeholder: `${t('tier')}: ${t('all')}` }) }),
          jsx(SelectContent, { children: [
            jsx(SelectItem, { value: '', children: t('all') }),
            ...Object.keys(TIERS).map(key => jsx(SelectItem, { value: key, children: TIERS[key] }, key))
          ] })
        ] }),
        jsx('span', { className: 'hermes-cat-count', children: `${filtered.length} ${t('plugins')}` })
      ] })
    ] }),
    jsxs('div', { className: 'hermes-cat-body', children: [
      jsxs('div', { ref: listRef, className: 'hermes-cat-list', children: [
        query.isLoading && jsx('div', { className: 'hermes-cat-empty', children: jsx(Codicon, { name: 'loading', size: 16 }) }),
        query.isError && jsxs('div', { className: 'hermes-cat-empty', children: [
          jsx('span', { children: t('error') }),
          jsx(Button, { variant: 'outline', size: 'sm', onClick: () => void query.refetch(), children: t('retry') })
        ] }),
        query.isSuccess && filtered.length === 0 && jsx('div', { className: 'hermes-cat-empty', children: t('noResults') }),
        filtered.map(entry => jsx(PluginRow, {
          key: entry.name, entry, t, ctx,
          open: entry.name === openName,
          onOpen: () => setOpenName(entry.name)
        }))
      ] }),
      openEntry && jsx(DetailPanel, { entry: openEntry, t, ctx })
    ] })
  ] })
}

export default {
  id: ID,
  name: 'Catalogo Plugin IT',
  description: 'The official Hermes plugin catalog in a native page, with descriptions translated into Italian on the fly.',
  defaultEnabled: true,
  register(ctx) {
    ctx.i18n.register(LOCALES)

    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    ctx.onDispose(() => style.remove())

    ctx.register({ id: 'page', area: ROUTES_AREA, data: { path: '/catalogo' }, render: () => jsx(CatalogPage, { ctx }) })
    ctx.register({ id: 'nav', area: SIDEBAR_NAV_AREA, order: 55, data: { codicon: 'extensions', label: 'Catalogo Plugin', path: '/catalogo' } })
    ctx.register({ id: 'open', area: PALETTE_AREA, data: { id: 'catalogo.open', label: 'Apri catalogo plugin', keywords: ['catalogo', 'plugin', 'catalog'], run: () => host.navigate('/catalogo') } })
    ctx.register({ id: 'search', area: PALETTE_AREA, data: { id: 'catalogo.search', label: 'Cerca nel catalogo plugin…', keywords: ['cerca', 'catalogo', 'plugin', 'search'], run: () => host.navigate('/catalogo?focus=1') } })
  }
}
