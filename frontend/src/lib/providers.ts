// Section 98 (V6.59) — one place for the rules every provider/model picker shares.
// Selectors offer only chat_ready providers (never is_configured: a provider can
// be configured yet unable to chat) and only agent-usable models (the backend is
// asked for agent_usable_only=true, see providersApi.getModels).
import type { SelectOption } from '@/components/ui/SearchableSelect'
import type { LiveModelOut, ProviderListItem } from '@/types/api'
import { TOKENS_PER_MILLION } from './constants'

export const UNAVAILABLE_META = '⚠ unavailable'

export function providerKind(p: ProviderListItem): string {
  return p.is_local ? 'local' : p.is_native ? 'native' : 'gateway'
}

// SearchableSelect renders its placeholder when `value` matches no option, which
// would make a saved-but-now-unusable choice look like "nothing selected" while
// the saved value is still sent. Keep it visible, flagged, instead.
export function chatReadyProviderOptions(
  providers: ProviderListItem[],
  current?: string,
): SelectOption[] {
  const opts: SelectOption[] = providers
    .filter(p => p.chat_ready)
    .map(p => ({ value: p.provider, label: p.display_name, meta: providerKind(p) }))
  if (current && !opts.some(o => o.value === current)) {
    const known = providers.find(p => p.provider === current)
    opts.unshift({ value: current, label: known?.display_name ?? current, meta: UNAVAILABLE_META })
  }
  return opts
}

export function modelMeta(m: LiveModelOut): string | undefined {
  const parts: string[] = []
  if (m.cost_per_input_token != null) {
    parts.push(`$${(m.cost_per_input_token * TOKENS_PER_MILLION).toFixed(2)}/M`)
  }
  // null = EVA has no capability data for this model (not "can't call tools")
  if (m.supports_tool_calls === null) parts.push('unverified')
  return parts.length ? parts.join(' · ') : undefined
}

// `loaded` must be false while the list is still fetching, otherwise every saved
// model would flash as unavailable until the response lands.
export function modelOptions(
  models: LiveModelOut[],
  current: string | undefined,
  loaded: boolean,
): SelectOption[] {
  const opts: SelectOption[] = models
    .filter(m => m.active)
    .map(m => ({ value: m.model_id, label: m.display_name ?? m.model_id, meta: modelMeta(m) }))
  if (loaded && current && !opts.some(o => o.value === current)) {
    opts.unshift({ value: current, label: current, meta: UNAVAILABLE_META })
  }
  return opts
}
