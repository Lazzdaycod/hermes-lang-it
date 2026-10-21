/**
 * Token Rate Monitor — chip nella status bar di Hermes Desktop.
 *
 * Mostra i token/sec reali del server llama.cpp locale, letti da Hermes
 * stesso (router FastAPI montato su /api/plugins/token-rate/). Nessun
 * processo esterno, nessuna porta, nessun watchdog: il backend gira dentro
 * l'app.
 *
 * Il contatore parte SPENTO: si attiva con un click sul chip oppure con il
 * comando "Token Rate: attiva/ferma il contatore" nella palette (Ctrl+K).
 *
 * Plain ESM, non compilato — solo jsx() calls. Import consentiti:
 * @hermes/plugin-sdk, react, react/jsx-runtime.
 */

import { cn, host, PALETTE_AREA, Tip, useQuery, useQueryClient, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'token-rate'
const POLL_MS = 1000

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
      host.notify({
        kind: 'error',
        message: `Token Rate: backend non raggiungibile (${err?.message || 'errore'}). Riavvia Hermes Desktop e riprova.`
      })
      return
    }
    await queryClient.refetchQueries({ queryKey: [ID, 'metrics'] })
    host.notify({
      kind: enabled ? 'info' : 'success',
      message: enabled
        ? 'Contatore tok/s fermato'
        : 'Contatore tok/s attivato — si aggiorna mentre il modello risponde'
    })
  }

  const rateLabel = !enabled
    ? 'off'
    : (tps != null || inputTps != null)
      ? `↑${fmtRate(tps)}↓${fmtRate(inputTps)}`
      : '↑—↓—'

  return jsx(Tip, {
    label: jsxs('div', {
      className: 'flex flex-col gap-0.5',
      children: [
        jsx('span', { className: 'font-medium', children: 'Token Rate Monitor' }),
        jsx('span', {
          className: 'text-(--ui-text-tertiary)',
          children: enabled
            ? `Output: ${fmtRate(tps)} t/s · Input: ${fmtRate(inputTps)} t/s`
            : 'Monitor spento'
        }),
        jsx('span', {
          className: 'text-(--ui-text-quaternary)',
          children: enabled ? 'Clicca per fermare' : 'Clicca per attivare'
        })
      ]
    }),
    children: jsx('span', {
      role: 'button',
      tabIndex: 0,
      title: enabled
        ? 'Clicca per fermare il contatore tok/s'
        : 'Clicca per attivare il contatore tok/s',
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
      message: enabled
        ? 'Contatore tok/s fermato'
        : 'Contatore tok/s attivato — si aggiorna mentre il modello risponde'
    })
  } catch {
    host.notify({
      kind: 'error',
      message: 'Token Rate: backend non disponibile. Riavvia Hermes e riprova.'
    })
  } finally {
    _paletteBusy = false
  }
}

export default {
  id: ID,
  name: 'Token Rate Monitor',
  description: 'Token/sec in tempo reale del server llama.cpp locale, attivabile con un click.',
  defaultEnabled: true,
  register(ctx) {
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
        label: 'Token Rate: attiva/ferma il contatore tok/s',
        keywords: ['token', 'rate', 'tok/s', 'tokens', 'contatore', 'velocita', 'monitor', 'llama'],
        run: () => { void toggleFromPalette(ctx) }
      }
    })
  }
}
