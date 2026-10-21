/**
 * Token Rate Monitor — chip in the Hermes Desktop status bar.
 *
 * Shows the real tokens/sec of the local llama.cpp server. Hermes itself reads
 * `$HERMES_HOME/logs/llama-server.log` and serves it at
 * `/api/plugins/token-rate/`; this chip polls that route once a second. No
 * external process, no extra port, no API key, no network calls.
 *
 * The counter starts OFF: click the chip, or run the palette entry
 * "Token Rate: start/stop the tok/s counter" (Ctrl/Cmd+K), to start it.
 *
 * Plain ESM, uncompiled — jsx() calls only. Allowed imports:
 * @hermes/plugin-sdk, react, react/jsx-runtime.
 */

import { cn, host, PALETTE_AREA, Tip, usePluginI18n, useQuery, useQueryClient, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'token-rate'
const POLL_MS = 1000

const LOCALES = {
  en: {
    title: 'Token Rate Monitor',
    outputLine: (o, i) => `Output: ${o} t/s · Input: ${i} t/s`,
    offLine: 'Monitor off',
    clickStop: 'Click to stop',
    clickStart: 'Click to start',
    off: 'off',
    startTip: 'Click to start the tok/s counter',
    stopTip: 'Click to stop the tok/s counter',
    started: 'Token rate counter started — it updates while the model responds',
    stopped: 'Token rate counter stopped',
    unreachable: (err) => `Token Rate: backend unreachable (${err}). Restart Hermes Desktop and try again.`,
    command: 'Token Rate: start/stop the tok/s counter',
    description: 'Live tokens/sec of the local llama.cpp server, started with one click.'
  },
  it: {
    title: 'Token Rate Monitor',
    outputLine: (o, i) => `Output: ${o} t/s · Input: ${i} t/s`,
    offLine: 'Monitor spento',
    clickStop: 'Clicca per fermare',
    clickStart: 'Clicca per attivare',
    off: 'spento',
    startTip: 'Clicca per attivare il contatore tok/s',
    stopTip: 'Clicca per fermare il contatore tok/s',
    started: 'Contatore tok/s attivato — si aggiorna mentre il modello risponde',
    stopped: 'Contatore tok/s fermato',
    unreachable: (err) => `Token Rate: backend non raggiungibile (${err}). Riavvia Hermes Desktop e riprova.`,
    command: 'Token Rate: attiva/ferma il contatore tok/s',
    description: 'Token/sec in tempo reale del server llama.cpp locale, attivabile con un click.'
  }
}

/** Accorcia il nome del modello per lo spazio dello status bar. */
function shortModel(name) {
  if (!name) return '—'
  const cleaned = String(name).trim()
  if (!cleaned) return '—'
  return cleaned.length > 8 ? cleaned.slice(0, 8) : cleaned
}

/** null/NaN → '—'. */
function fmtRate(v) {
  if (typeof v !== 'number' || Number.isNaN(v)) return '—'
  return String(Math.round(v * 10) / 10)
}

/** Nome del modello attivo (oggetto o stringa). */
function modelLabel(model) {
  if (!model) return '—'
  if (typeof model === 'string') return shortModel(model)
  return shortModel(model.id || model.model_name || model.name || model.model || '')
}

function TokenRateChip({ ctx }) {
  const t = usePluginI18n(ID)
  const busy = useValue(host.state.busy)
  const model = useValue(host.state.model)
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: [ID, 'metrics'],
    queryFn: async () => {
      try {
        return await ctx.rest('/metrics')
      } catch {
        // Backend non montato (plugin disabilitato o app non ancora riavviata)
        return { enabled: false, tps: null, input_tps: null, source: null }
      }
    },
    refetchInterval: POLL_MS,
    retry: false
  })

  const data = query.data ?? {}
  const enabled = data.enabled === true
  const tps = typeof data.tps === 'number' ? data.tps : null
  const inputTps = typeof data.input_tps === 'number' ? data.input_tps : null

  /** Click sul chip: attiva/ferma il contatore. */
  async function toggle() {
    try {
      await ctx.rest(enabled ? '/stop' : '/start', { method: 'POST' })
    } catch (err) {
      host.notify({ kind: 'error', message: t('unreachable', err?.message || 'error') })
      return
    }
    await queryClient.refetchQueries({ queryKey: [ID, 'metrics'] })
    host.notify({ kind: enabled ? 'info' : 'success', message: t(enabled ? 'stopped' : 'started') })
  }

  const rateLabel = !enabled
    ? t('off')
    : (tps != null || inputTps != null)
      ? `↑${fmtRate(tps)}↓${fmtRate(inputTps)}`
      : '↑—↓—'

  return jsx(Tip, {
    label: jsxs('div', {
      className: 'flex flex-col gap-0.5',
      children: [
        jsx('span', { className: 'font-medium', children: t('title') }),
        jsx('span', {
          className: 'text-(--ui-text-tertiary)',
          children: enabled ? t('outputLine', fmtRate(tps), fmtRate(inputTps)) : t('offLine')
        }),
        jsx('span', {
          className: 'text-(--ui-text-quaternary)',
          children: enabled ? t('clickStop') : t('clickStart')
        })
      ]
    }),
    children: jsx('span', {
      role: 'button',
      tabIndex: 0,
      title: enabled ? t('stopTip') : t('startTip'),
      onClick: () => { void toggle() },
      onKeyDown: (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          void toggle()
        }
      },
      className: cn(
        'inline-flex h-full cursor-pointer items-center gap-1 px-1.5 text-[0.6875rem] transition-colors',
        enabled && busy
          ? 'font-medium text-(--ui-text-secondary)'
          : 'text-(--ui-text-tertiary)',
        'hover:bg-(--chrome-action-hover) hover:text-foreground'
      ),
      children: `${modelLabel(model)} ${rateLabel}`
    })
  })
}

let _paletteBusy = false

/** Comando della palette: non c'e' un componente attivo, si interroga il backend. */
async function toggleFromPalette(ctx) {
  if (_paletteBusy) return
  _paletteBusy = true
  try {
    const data = await ctx.rest('/metrics')
    const enabled = data?.enabled === true
    await ctx.rest(enabled ? '/stop' : '/start', { method: 'POST' })
    host.notify({
      kind: enabled ? 'info' : 'success',
      message: ctx.i18n.t(enabled ? 'stopped' : 'started')
    })
  } catch (err) {
    host.notify({
      kind: 'error',
      message: ctx.i18n.t('unreachable', err?.message || 'error')
    })
  } finally {
    _paletteBusy = false
  }
}

export default {
  id: ID,
  name: 'Token Rate Monitor',
  description: LOCALES.en.description,
  defaultEnabled: true,
  register(ctx) {
    ctx.i18n.register(LOCALES)

    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 125,
      render: () => jsx(TokenRateChip, { ctx })
    })

    ctx.register({
      id: 'toggle',
      area: PALETTE_AREA,
      data: {
        id: `${ID}.toggle`,
        label: ctx.i18n.t('command'),
        keywords: ['token', 'rate', 'tok/s', 'tokens', 'counter', 'speed', 'monitor', 'llama'],
        run: () => { void toggleFromPalette(ctx) }
      }
    })
  }
}
