/**
 * GPU Monitor — chip in the Hermes Desktop status bar.
 *
 * Reads Windows performance counters through the mounted backend at
 * /api/plugins/gpu-monitor/metrics. No external process, no port, no network.
 * Polls every 2s; click the chip (or use the palette) for a detailed readout.
 *
 * Plain ESM, uncompiled — jsx() calls only. Allowed imports:
 * @hermes/plugin-sdk, react, react/jsx-runtime.
 */

import { cn, host, PALETTE_AREA, Tip, usePluginI18n, useQuery } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'gpu-monitor'
const POLL_MS = 2000

const LOCALES = {
  en: {
    title: 'GPU Monitor',
    unavailable: 'GPU counters unavailable',
    utilLine: (v) => `GPU utilization: ${v}%`,
    vramLine: (v) => `Dedicated VRAM in use: ${v}`,
    vramNone: 'Dedicated VRAM: n/a',
    tempLine: (v) => `GPU temperature: ${v}°C`,
    topLine: (v) => `Busiest: ${v}`,
    sourceLine: (v) => `Source: ${v}`,
    multiEngine: 'Engines are summed, so the total can exceed 100%.',
    clickTip: 'Click for GPU details',
    details: (v, m, t) => `GPU ${v}% · VRAM ${m}${t != null ? ` · ${t}°` : ''}`,
    command: 'GPU Monitor: show GPU usage',
    description: 'Live GPU utilization and dedicated VRAM in the status bar.'
  },
  it: {
    title: 'GPU Monitor',
    unavailable: 'Contatori GPU non disponibili',
    utilLine: (v) => `Utilizzo GPU: ${v}%`,
    vramLine: (v) => `VRAM dedicata in uso: ${v}`,
    vramNone: 'VRAM dedicata: n/d',
    tempLine: (v) => `Temperatura GPU: ${v}°C`,
    topLine: (v) => `Processi piu' attivi: ${v}`,
    sourceLine: (v) => `Fonte: ${v}`,
    multiEngine: 'I motori vengono sommati: il totale puo superare 100%.',
    clickTip: 'Clicca per i dettagli GPU',
    details: (v, m, t) => `GPU ${v}% · VRAM ${m}${t != null ? ` · ${t}°` : ''}`,
    command: 'GPU Monitor: mostra utilizzo GPU',
    description: 'Utilizzo GPU e VRAM dedicata in tempo reale nella barra di stato.'
  }
}

function fmtPct(v) {
  if (typeof v !== 'number' || Number.isNaN(v)) return '—'
  return String(Math.round(v * 10) / 10)
}

function fmtGb(v) {
  if (typeof v !== 'number' || Number.isNaN(v)) return null
  return `${(v / (1 << 30)).toFixed(1)} GB`
}

function topProcesses(data) {
  if (!Array.isArray(data.engines) || data.engines.length === 0) return '—'
  return data.engines.slice(0, 3).map((e) => {
    const pid = e && e.pid != null ? `#${e.pid}` : ''
    const name = e && e.engine ? e.engine : 'GPU'
    const pct = e && typeof e.percent === 'number' ? `${Math.round(e.percent)}%` : '—'
    return `${name}${pid} ${pct}`
  }).join(' · ')
}

async function fetchMetrics(ctx) {
  try {
    return await ctx.rest('/metrics')
  } catch {
    return { available: false }
  }
}

function GpuChip({ ctx }) {
  const t = usePluginI18n(ID)
  const query = useQuery({
    queryKey: [ID, 'metrics'],
    queryFn: () => fetchMetrics(ctx),
    refetchInterval: POLL_MS,
    retry: false
  })

  const data = query.data ?? {}
  const available = data.available === true
  const util = typeof data.util_percent === 'number' ? data.util_percent : null
  const vram = fmtGb(data.vram_used_bytes)
  const temp = typeof data.temp_c === 'number' ? Math.round(data.temp_c) : null
  const pctLabel = util == null ? '—' : String(Math.min(100, Math.round(util)))
  const label = !available
    ? 'GPU —'
    : `GPU ${pctLabel}%${vram ? ` · ${Math.round(parseFloat(vram))}Gvram` : ''}${temp != null ? ` · ${temp}°` : ''}`

  function showDetails() {
    host.notify({
      kind: 'info',
      message: available
        ? t('details', `${fmtPct(util)}%`, vram || '—', temp)
        : t('unavailable')
    })
  }

  return jsx(Tip, {
    label: jsxs('div', {
      className: 'flex flex-col gap-0.5',
      children: [
        jsx('span', { className: 'font-medium', children: t('title') }),
        jsx('span', {
          className: 'text-(--ui-text-tertiary)',
          children: available ? t('utilLine', fmtPct(util)) : t('unavailable')
        }),
        jsx('span', {
          className: 'text-(--ui-text-tertiary)',
          children: available ? (vram ? t('vramLine', vram) : t('vramNone')) : ''
        }),
        jsx('span', {
          className: 'text-(--ui-text-tertiary)',
          children: available && temp != null ? t('tempLine', temp) : ''
        }),
        jsx('span', {
          className: 'text-(--ui-text-quaternary)',
          children: available ? t('topLine', topProcesses(data)) : ''
        }),
        jsx('span', {
          className: 'text-(--ui-text-quaternary)',
          children: available ? t('sourceLine', data.source || '—') : ''
        })
      ]
    }),
    children: jsx('span', {
      role: 'button',
      tabIndex: 0,
      title: t('clickTip'),
      onClick: () => { showDetails() },
      onKeyDown: (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          showDetails()
        }
      },
      className: cn(
        'inline-flex h-full cursor-pointer items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) transition-colors',
        'hover:bg-(--chrome-action-hover) hover:text-foreground'
      ),
      children: label
    })
  })
}

let _paletteBusy = false

async function showFromPalette(ctx) {
  if (_paletteBusy) return
  _paletteBusy = true
  try {
    const data = await fetchMetrics(ctx)
    const vram = fmtGb(data.vram_used_bytes)
    const temp = typeof data.temp_c === 'number' ? Math.round(data.temp_c) : null
    host.notify({
      kind: data.available === true ? 'info' : 'error',
      message: data.available === true
        ? ctx.i18n.t('details', `${fmtPct(data.util_percent)}%`, vram || '—', temp)
        : ctx.i18n.t('unavailable')
    })
  } finally {
    _paletteBusy = false
  }
}

export default {
  id: ID,
  name: 'GPU Monitor',
  description: LOCALES.en.description,
  defaultEnabled: true,
  register(ctx) {
    ctx.i18n.register(LOCALES)

    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 130,
      render: () => jsx(GpuChip, { ctx })
    })

    ctx.register({
      id: 'detail',
      area: PALETTE_AREA,
      data: {
        id: `${ID}.detail`,
        label: ctx.i18n.t('command'),
        keywords: ['gpu', 'utilization', 'vram', 'usage', 'monitor', 'amd', 'nvidia'],
        run: () => { void showFromPalette(ctx) }
      }
    })
  }
}
