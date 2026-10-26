import { atom, update } from 'claude-code'
import type { Register, EngineInterface } from 'claude-code'

import type { Totals } from '../types'

// The /tokens command is the plugin's skill (skills/tokens), in every app and
// the terminal alike. This module only keeps a short count in the status line
// where the app draws one (the terminal). It registers no command of its own,
// so it never answers /tokens in place of the skill.

const EMPTY: Totals = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, steps: 0 }

const totals = atom({ plugin: 'token-meter', key: 'totals' } as const, EMPTY, {
  shape: 'totals-1',
})

// Effective weight: cache reads 0.1x, cache writes 2x, output 5x, plain input 1x.
const effective = (t: Totals) =>
  t.input + 0.1 * t.cacheRead + 2 * t.cacheWrite + 5 * t.output

const fmt = (n: number) =>
  n >= 1_000_000
    ? `${(n / 1_000_000).toFixed(2)}M`
    : n >= 1_000
      ? `${(n / 1_000).toFixed(1)}k`
      : `${Math.round(n)}`

async function statusLine($: EngineInterface, t: Totals) {
  const usage = await $.session.usage()
  const ctx = usage.context.percent
  const usd = usage.cost?.usd
  return `Tokens: ${fmt(effective(t))} eff · out ${fmt(t.output)}${
    ctx !== undefined ? ` · ctx ${ctx}%` : ''
  }${usd !== undefined ? ` · $${usd.toFixed(2)}` : ''}`
}

export const register: Register = on => {
  // Every model response, main thread and subagents, adds its usage.
  on('turn.step', async function* ($, e, next) {
    const r = yield* next(e)
    const u = r.usage
    if (u) {
      const t = await update($, totals, old => ({
        input: old.input + u.input_tokens,
        output: old.output + u.output_tokens,
        cacheRead: old.cacheRead + u.cache_read_input_tokens,
        cacheWrite: old.cacheWrite + u.cache_creation_input_tokens,
        steps: old.steps + 1,
      }))
      $.ui.status(await statusLine($, t))
    }
    return r
  })
}
