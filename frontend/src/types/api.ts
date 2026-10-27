// ============================================================
// EVA API — TypeScript types derived from backend Pydantic models
// Covers all 46 documented endpoints
// ============================================================

// ─── Auth ───────────────────────────────────────────────────
// email is REQUIRED by backend (EmailStr validation)
export interface SignupRequest  { username: string; password: string; email: string }
// Login uses OAuth2PasswordRequestForm (form-encoded, not JSON) — handled in api/auth.ts
export interface LoginRequest   { username: string; password: string }
export interface AuthResponse   {
  access_token:  string
  token_type:    string
  expires_in:    number   // seconds until access token expires (24h = 86400)
  refresh_token: string   // opaque 64-char token, valid 7 days; rotated on each refresh
}
export interface UserInfo       { id: string; username: string; email?: string }

// ─── Session config ─────────────────────────────────────────
export type AgentMode        = 'auto' | 'chat' | 'plan_execute'
export type ToolApprovalMode = 'auto' | 'always_ask' | 'per_conversation'
export type SkillsMode       = 'auto' | 'enabled' | 'disabled'
export type Provider         = 'anthropic' | 'openai' | 'groq' | 'litellm' | 'ollama' | string


export interface FallbackEntry { provider: Provider; model: string }

// ─── Tool Hooks (Section 48 — B-053 V4.30 / Section 56 — B-064 V4.45) ────────
export type HookEvent   = 'pre' | 'post' | 'session_start' | 'prompt_submit' | 'stop'
export type HookHandler = 'audit_log' | 'deny_always' | 'path_guard' | 'redact_env_vars' | 'redact_pii'
export type HookType    = 'builtin' | 'command' | 'http'

export interface HookDefinition {
  tool:      string          // exact tool name or "*" for all tools
  event:     HookEvent
  type?:     HookType        // V4.45: builtin (default/compat) | command | http
  // type="builtin"
  handler?:  HookHandler
  config?:   Record<string, unknown>
  // type="command"
  command?:  string          // script name from Script Manager
  timeout?:  number          // seconds, default 30
  async_?:   boolean         // not allowed for pre / prompt_submit
  // type="http"
  url?:      string
  headers?:  Record<string, string>
}

// ─── Hook Scripts (Section 56 — B-064 V4.45) ─────────────────────────────────
export interface HookScript {
  id:          string
  name:        string
  description: string | null
  filename:    string
  size_bytes:  number
  created_at:  string
  updated_at:  string | null
}

export interface HookScriptListResponse {
  scripts: HookScript[]
  total:   number
}

export type ReasoningLevel = 'minimal' | 'low' | 'medium' | 'high' | 'xhigh'
export const REASONING_LEVELS: ReasoningLevel[] = ['minimal', 'low', 'medium', 'high', 'xhigh']

export interface SessionConfig {
  provider?:               Provider
  model?:                  string
  tools_enabled?:          string[]
  system_prompt?:          string
  reasoning?:              ReasoningLevel | null   // null = off; boolean shim accepted by backend during transition
  tool_approval_mode?:     ToolApprovalMode
  allowed_authorities?:    string | string[]  // comma-sep string or array from API response
  agent_mode?:             AgentMode
  fallback_chain?:         FallbackEntry[]
  user_memory_enabled?:    boolean
  agent_memory_enabled?:   boolean
  skills_mode?:            SkillsMode
  tool_hooks?:             HookDefinition[]
  shell_readonly?:         boolean           // V4.44 — block non-read-only shell commands
  user_timezone?:          string | null     // IANA tz, e.g. "Asia/Kolkata" (V5.5)
  user_location?:          string | null     // free text, e.g. "Mumbai, India" (V5.5)
  user_language?:          string | null     // BCP-47, e.g. "en-IN" (V5.5)
  agent_scope_key?:        string | null     // V5.7 — explicit scope key; falls back to project_id when blank
  require_plan_approval?:  boolean           // V6.13 — gate PEV executions behind plan approval
  max_step_extensions?:    number            // V6.13 — how many times a user may extend the ReAct step budget (0 = disabled)
  pii_redaction_enabled?:  boolean           // S-03 (V6.47) — redact PII from chat messages before storage/LLM; default false
  pev_lookahead_skip_enabled?: boolean       // A-21 (Section 93) — skip already-satisfied future PEV steps; default TRUE (opt-out, unlike pii_redaction_enabled)
}

// Full session response from GET /session — superset of SessionConfig
export interface SessionResponse extends SessionConfig {
  status:               string
  allowed_authorities:  string[]   // array form in GET /session response
  warnings?:            string[]
}

// ─── CDP-embedded preview pane (browser automation) ───────────────────────
// Backend: core/mcp/preview_registry.py + api/session.py. Deliberately not
// part of SessionConfig/SessionResponse — this is infrastructure plumbing
// registered once by Electron's own CDP relay, not a user preference, and
// is gone on every backend restart (in-memory only, never persisted).
export interface PreviewEndpointItem {
  name:           string
  cdp_endpoint:   string
  registered_at:  string   // ISO-8601
}
export interface PreviewEndpointsResponse {
  endpoints: Record<string, PreviewEndpointItem>
}

export interface SessionPermissions {
  tool_approval_mode:   ToolApprovalMode
  allowed_authorities:  string
}

// ─── Chat ───────────────────────────────────────────────────
export interface ChatRequest  { message: string; conversation_id?: string; agent_scope_key?: string; project_id?: string }
export interface ChatResponse { conversation_id: string; execution_id: string; status: string; execution_mode?: string }

// ─── SSE Event types ────────────────────────────────────────
export type SSEEventType =
  | 'init'
  | 'text'
  | 'reasoning'
  | 'tool_call'
  | 'tool_result'
  | 'approval_required'
  | 'plan_approval_required'
  | 'usage'
  | 'error'
  | 'ping'
  | 'finish'
  | 'done'
  | 'subagent'
  | 'step_progress'
  | 'status'
  | 'provider_fallback'
  | 'step_limit_reached'
  | 'cancelled'

export interface SSEBase        { type: SSEEventType; timestamp: number }
export interface SSEText        extends SSEBase { type: 'text';      content: string }
export interface SSEReasoning   extends SSEBase { type: 'reasoning'; content: string }
export interface SSEToolCall    extends SSEBase {
  type: 'tool_call'
  tool_name: string
  tool_input: Record<string, unknown>
  tool_call_id: string
}
export interface SSEToolResult  extends SSEBase {
  type: 'tool_result'
  tool_name: string
  tool_call_id: string
  result: unknown
  is_error?: boolean
}
export interface SSEApprovalRequired extends SSEBase {
  type: 'approval_required'
  execution_id: string
  approval_token: string
  tool_name: string
  tool_input: Record<string, unknown>
  authority: string
  description?: string
}
export interface SSEPlanApprovalRequired extends SSEBase {
  type: 'plan_approval_required'
  execution_id: string
  plan_approval_token: string
  plan: PlanApprovalPlan
}
export interface SSEUsage  extends SSEBase {
  type: 'usage'
  // B-066 / V4.32 field names (backend fixed in stream_buffer.py)
  prompt_tokens:     number
  completion_tokens: number
  reasoning_tokens:  number | null
  total_tokens:      number
  provider?:         string
  model?:            string
}
export interface SSEError  extends SSEBase {
  type:        'error'
  message:     string
  error_type?: string   // 'tool_call_failed' | 'provider_error' | 'timeout'
  provider?:   string
  code?:       string
  details?:    Record<string, unknown>
}
export interface SSEStatus          extends SSEBase { type: 'status'; message: string }
export interface SSEFinish          extends SSEBase { type: 'finish'; stop_reason?: string }
export interface SSEDone            extends SSEBase { type: 'done' }
// Emitted the moment the execution's broker status flips to `cancelled` —
// arrives right before `finish`/`done` on the same connection, carrying the
// reason `done` alone doesn't (e.g. "user" for an explicit Stop click).
export interface SSECancelled       extends SSEBase { type: 'cancelled'; reason?: string }
export interface SSEInit            extends SSEBase { type: 'init';  conversation_id: string }
export interface SSEPing            extends SSEBase { type: 'ping' }
export interface SSEProviderFallback extends SSEBase {
  type:     'provider_fallback'
  provider: string   // provider now actually serving the response
  model:    string   // model now actually serving the response
}

// V6.13 — ReAct step budget exhausted; offers the user a chance to extend it
export interface SSEStepLimitReached extends SSEBase {
  type: 'step_limit_reached'
  execution_id: string
  step_extension_token: string
  steps_used: number
  max_steps: number
  extensions_used: number
  extensions_max: number
}

// G-003 — Subagent delegation events (event: subagent frames)
export interface SSESubagent extends SSEBase {
  type:       'subagent'
  event:      'started' | 'text' | 'tool_call' | 'completed' | 'failed'
  agent_name: string
  content:    string
  tool_name:  string | null
}

// PEV loop — step lifecycle progress event
// V6.14: single source of truth for PEV narration — the backend no longer
// sends "Planning...", "Verifying step...", etc. as markdown TextChunks
// mixed into the answer; all of that detail now arrives structured here.
export interface PlanStepMeta {
  index:        number
  title:        string
  description:  string
  tool_hint?:   string | null
}

export interface SSEStepProgress extends SSEBase {
  type:        'step_progress'
  step_index:  number   // 0-based
  total_steps: number
  step_title:  string
  step_status: 'planning' | 'awaiting_plan_approval' | 'running' | 'verifying'
             | 'completed' | 'failed' | 'replanning' | 'replan_skipped'
             | 'plan_complete' | 'cancelled'
             | 'skipped'   // A-21 — agent never ran for this step; verification_summary says which earlier step covered it. Never sent for a plan's final step.
  goal:        string
  plan_steps?:            PlanStepMeta[] | null
  verification_summary?:  string | null
  verification_issues?:   string | null
  replan_reason?:          string | null
  error_detail?:            string | null
  steps_completed?:         number | null
  steps_failed?:             number | null
  steps_skipped?:            number | null   // A-21 — plan_complete only; never folded into steps_completed
  replan_count?:             number | null
}

export type SSEEvent =
  | SSEInit | SSEText | SSEReasoning | SSEToolCall | SSEToolResult
  | SSEApprovalRequired | SSEPlanApprovalRequired | SSEUsage
  | SSEError | SSEFinish | SSEDone | SSEPing | SSESubagent | SSEStepProgress | SSEStatus
  | SSEProviderFallback | SSEStepLimitReached | SSECancelled

// ─── Conversations ───────────────────────────────────────────
// Backend field: id (not conversation_id)
export interface Conversation {
  id:              string
  user_id?:        string
  session_id?:     string
  title?:          string
  system_prompt?:  string | null   // B-054 — per-conversation persona
  project_id?:     string | null   // B-056 — project linkage
  created_at:      string
  updated_at:      string
}

// Backend field: id (not message_id), content is Optional[str]
export interface Message {
  id:              string
  conversation_id: string
  role:            'user' | 'assistant' | 'tool'
  content:         string | null
  type?:           'text' | 'tool_call' | 'tool_result' | string
  tool_name?:      string
  tool_call_id?:   string
  tool_args?:      Record<string, unknown> | null
  tool_result?:    Record<string, unknown> | string | null
  is_error?:       boolean
  created_at:      string
}

// ─── Tools ───────────────────────────────────────────────────
export interface ToolParameter {
  name:        string
  type:        string
  description: string
  required?:   boolean
}
export interface Tool {
  name:        string
  description: string
  parameters?: ToolParameter[]
  type:        'default' | 'mcp' | 'custom'
  source?:     string
  authority?:  'read_only' | 'mutating' | 'destructive' | string
}

// ─── Providers ───────────────────────────────────────────────
// Matches backend ModelCapabilityOut (static capability registry)
export interface ModelInfo {
  model:               string    // model ID (e.g. "claude-opus-4-5")
  max_context_tokens:  number | null
  supports_vision?:    boolean
  supports_reasoning?: boolean
  cost_per_input_token?:  number | null
  cost_per_output_token?: number | null
}
// V4.3 (B-016): GET /providers/capabilities — static capability registry
// Note: Ollama is NOT returned here (it has no fixed model catalogue)
export interface ProviderInfo {
  provider:    string   // was "name" in V3; matches backend "provider" field
  model_count: number
  models:      ModelInfo[]
  is_native:   boolean  // EVA has a dedicated native handler
  is_local:    boolean  // local inference, no API cost (ollama)
}

// V4.5: GET /providers/list — the backend's curated provider list for the frontend dropdowns
// Replaces the removed GET /providers endpoint for dropdown population
export interface ProviderListItem {
  provider:      string   // canonical id — pass directly to set-session and /models
  display_name:  string   // human-friendly label (e.g. "Groq", "Ollama (Local)")
  is_native:     boolean  // EVA has a dedicated native handler
  is_local:      boolean  // local inference, no API cost (ollama)
  is_configured: boolean  // API key present on server (or Ollama host reachable)
  // Section 98 (V6.59) — configured AND EVA has a working chat path. Selectors
  // (session / agent / daemon / fallback) filter on THIS, never is_configured.
  chat_ready:    boolean
}

// V4.5: GET /providers/{provider}/models/{model_id}/capabilities
// Per-model capability profile — fetched after model selection for badge display
export interface ModelCapabilities {
  provider:                  string
  model_id:                  string          // bare model name (no provider prefix)
  max_context_tokens:        number | null
  max_output_tokens:         number | null
  supports_streaming:        boolean | null
  supports_tool_calls:       boolean | null
  supports_reasoning:        boolean | null
  reasoning_levels?:         string[] | null   // ordered weakest→strongest; null = no level data
  reasoning_can_disable?:    boolean | null    // false = model always reasons, Off unavailable
  supports_vision:           boolean | null
  supports_json_schema:      boolean | null
  supports_file_upload:      boolean | null
  supports_usage_reporting:  boolean | null
  cost_per_input_token:      number | null   // USD per token; null = unknown
  cost_per_output_token:     number | null
  tool_call_reliability:     'reliable' | 'unreliable' | null
  source:                    'registry' | 'litellm' | 'unknown'
}

// V4.3 (B-024): GET /providers/{provider}/models — live model listing with TTL cache
export interface LiveModelOut {
  model_id:              string
  display_name:          string | null
  context_window:        number | null
  owned_by:              string | null
  active:                boolean
  cost_per_input_token:  number | null  // null if pricing unavailable
  cost_per_output_token: number | null
  // Section 98 (V6.59) — null = unknown (no capability data); agent_usable is
  // false for models the agent loop can't drive (embedding/image/audio, or
  // known to lack tool calling). Unknown counts as usable.
  supports_tool_calls:   boolean | null
  agent_usable:          boolean
}
export interface LiveProviderModelsOut {
  provider:    string
  is_native:   boolean
  is_local:    boolean
  chat_ready:  boolean
  model_count: number
  cached:      boolean   // true = result from TTL cache
  models:      LiveModelOut[]
}

// V4.3 (B-023): GET /providers/costs — aggregated spend per (provider, model)
export interface ProviderCostRow {
  provider:                string
  model:                   string
  total_executions:        number
  total_prompt_tokens:     number
  total_completion_tokens: number
  total_tokens:            number
  total_cost_usd:          number | null   // null if pricing was unavailable
}
export interface ProviderCostsOut {
  user_id:              string
  period_start:         string | null
  period_end:           string | null
  rows:                 ProviderCostRow[]
  grand_total_tokens:   number
  grand_total_cost_usd: number | null   // null if ANY row has no pricing
}

// V4.4 (B-025): POST /secrets/{provider} — runtime API key upsert
export interface SecretWriteResponse {
  provider:   string
  key_name:   string          // e.g. "GROQ_API_KEY" — safe to display; never the key value
  configured: boolean
  valid:      boolean | null  // true=validated; false=rejected by provider; null=skipped
  message:    string
}

// Section 99 (V6.59 Phase 4a): DELETE /secrets/{provider}
export interface SecretInUse {
  session_primary:  boolean
  fallback_entries: number
  agents:           string[]
  daemons:          string[]
}
export interface SecretDeleteResponse {
  provider:   string
  key_name:   string
  deleted:    boolean   // false = there was no stored key (still 200, idempotent)
  configured: boolean   // always false after a delete
  in_use:     SecretInUse
  warnings:   string[]
  message:    string
}

// V4.4 (B-025): GET/POST /secrets/ollama/base-url
export interface OllamaBaseUrlResponse {
  base_url:  string
  reachable: boolean | null   // null if probe errored
}

// ─── Executions ──────────────────────────────────────────────
export type StepStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'

export interface ExecutionStep {
  step_id:     string
  step_number: number
  action:      string
  description: string
  status:      StepStatus
  result?:     string
  error?:      string
  started_at?: string
  ended_at?:   string
  tool_calls?: SSEToolCall[]
}

export interface ExecutionPlan {
  plan_id:     string
  execution_id: string
  goal:        string
  steps:       ExecutionStep[]
  created_at:  string
}

// V6.13 — the plan shape carried by the plan_approval_required SSE event and
// the plan-decision "edit" payload. Deliberately distinct from ExecutionPlan
// (which describes a stored/executed plan) — this is the pre-execution,
// user-editable draft shape, keyed by array position rather than step_id.
export interface PlanApprovalStep {
  index?:       number
  title:        string
  description?: string
  tool_hint?:   string | null
}
export interface PlanApprovalPlan {
  goal:  string
  steps: PlanApprovalStep[]
}

export interface PlanDecisionRequest {
  decision: 'approve' | 'edit' | 'reject'
  plan_approval_token: string
  plan?:   PlanApprovalPlan | null   // required iff decision === 'edit'
  reason?: string | null             // optional, only meaningful when decision === 'reject'
}
export interface PlanDecisionResponse {
  status: 'approved' | 'edited' | 'rejected'
  execution_id: string
  plan_approval_token: string
}

export interface ExecutionTrace {
  execution_id: string
  conversation_id: string
  status:       string
  steps:        ExecutionStep[]
  created_at:   string
  updated_at:   string
}

// Lightweight status (GET /executions/{id}/status) — avoids loading full trace
// 'awaiting_step_extension' (V6.12 step-limit ask-to-continue gate) was
// missing from this union — confirmed via a live daemon run actually
// returning it, not just from reading the backend source.
export type ExecutionStatus = 'pending' | 'running' | 'awaiting_approval' | 'awaiting_plan_approval' | 'awaiting_step_extension' | 'awaiting_wakeup' | 'completed' | 'failed' | 'cancelled' | 'unknown'

export interface ExecutionStatusResponse {
  execution_id:    string
  status:          ExecutionStatus
  conversation_id: string
}

// Section 76 — Gap 8: provider fallback chain entry
export interface FallbackDetail {
  failed_provider:   string
  failed_model:      string
  failure_reason:    string   // "rate_limit" | "provider_down" | "invalid_model"
  fallback_provider: string
  fallback_model:    string
  fallback_index:    number   // 1-based position in chain
}

// Section 76 — Gap 9: per-tool call metrics (replaces scalar tool_calls_count)
export interface ToolCallMetric {
  tool_name:          string
  call_count:         number
  avg_latency_ms:     number | null
  failure_count:      number
  success_count:      number
  last_denial_reason: string | null   // most recent ToolPermissionDenied reason
}

export interface ExecutionMetrics {
  execution_id:       string
  duration_ms:        number | null
  prompt_tokens:      number
  completion_tokens:  number
  // Section 103 (V6.59 Phase 6) — reasoning is a SUBSET of completion and cached is a
  // SUBSET of prompt; total_tokens is prompt + completion. Never add the three together.
  reasoning_tokens?:  number
  cached_tokens?:     number   // 0 on executions recorded before this field existed
  total_tokens:       number
  total_steps:        number
  model:              string
  provider:           string
  // V5.0 — enhanced fields (B-073)
  computed_cost_usd?:  number | null
  provider_used?:      string
  model_used?:         string
  fallback_count?:     number
  // Section 75 — error detail
  termination_reason?: string | null
  error_message?:      string | null
  // Section 76 — Gaps 8–11
  fallback_details?:   FallbackDetail[]   // Gap 8: [] when no fallbacks
  tool_calls?:         ToolCallMetric[]   // Gap 9: per-tool breakdown
  subagent_calls?:     number             // Gap 10: SubagentCompleted events
  subagent_failures?:  number             // Gap 10: subagents where success=false
  started_at?:         string | null      // Gap 11: DB-persisted start time
}

// ─── Usage Metrics (Section 57 — B-073 V5.0) ─────────────────────────────────
export interface MetricsProviderBreakdown {
  provider:        string
  execution_count: number
  total_tokens:    number
  total_cost_usd:  number
}

export interface UsageSummary {
  execution_count:   number
  prompt_tokens:     number
  completion_tokens: number
  // Section 103 — subsets of completion / prompt respectively; total_tokens = prompt + completion
  reasoning_tokens?: number
  cached_tokens?:    number
  total_tokens?:     number
  // null = no priced execution in the range (unknown, not zero)
  total_cost_usd:    number | null
  // true when at least one execution in the range had no price (Ollama Cloud
  // subscription, local Ollama, or a model with no known price)
  cost_is_partial?:  boolean
  provider_breakdown: MetricsProviderBreakdown[]   // array, not a Record
}

// ─── Workspace Promote (Section 58 — B-075 V5.2 redesign) ───────────────────
// Scenario 1: attach an unlinked conversation to a project
export interface PromoteAttachResponse {
  conversation_id:  string
  project_id:       string
  scenario:         'attach'
  already_attached: boolean
  workspace_moved:  boolean
  files_moved:      number
}
// Scenario 2: copy specific scratch files into the project workspace
export interface PromoteFilesResponse {
  conversation_id: string
  project_id:      string
  scenario:        'files'
  promoted:        string[]
  errors:          Array<{ file: string; error: string }>
}
export type PromoteResponse = PromoteAttachResponse | PromoteFilesResponse

// ─── Approval ────────────────────────────────────────────────
export interface ApproveRequest {
  approval_token: string
  feedback?:      string
}
export interface RejectRequest {
  approval_token: string
  reason?:        string
}

// ─── RAG ─────────────────────────────────────────────────────
export interface RAGFile {
  file_name:     string
  source?:       'rag' | 'library'   // B-066: unified file list; undefined = legacy RAG
  rag_indexed?:  boolean
  size_bytes?:   number
  file_size?:    number
  chunk_count?:  number
  ingested_at?:  string
  storage_path?: string              // absolute OS path — present in Electron, undefined in browser
}

// B-066: unified file entry from GET /rag/files (V4.32+)
export interface UnifiedFileEntry {
  filename:    string
  source:      'rag' | 'library'
  rag_indexed: boolean
  size_bytes?: number
}
export interface RAGSearchResult {
  content:   string
  score:     number
  file_name: string
  chunk_id?: string
}

// ─── Memory ──────────────────────────────────────────────────
export interface ConversationSummary {
  conversation_id: string
  summary:         string
  created_at:      string
}
export interface UserMemoryFact {
  id:          string
  fact:        string
  category?:   string
  slot_key?:   string   // V6.0 — SINGULAR_SLOT key, e.g. "user.name", "user.full_name"
  source?:     string
  confidence?: number
  created_at:  string
  updated_at:  string | null
}

// ─── User Profile (V6.0 — Section 71) ──────────────────────────────────────
export interface UserProfile {
  user_id:     string
  username:    string
  email:       string
  full_name:   string | null
  description: string | null
}

export interface UpdateProfilePayload {
  full_name?:      string | null   // omit = no change; null/"" clears the field
  preferred_name?: string | null   // maps to user.name memory slot (casual name)
  description?:    string | null   // omit = no change; null/"" clears the field
}

export interface ChangePasswordPayload {
  current_password: string
  new_password:     string   // min 8 characters
}
export interface AgentMemoryEntry {
  id:          string
  scope_key?:  string
  content:     string
  key?:        string
  entry_type?: string
  sensitive?:  boolean   // G-004 — entry visible only to user in panel; agent sees placeholder
  created_at:  string
  updated_at:  string | null
}
// Agent scope discovery (GET /memory/agent)
export interface AgentScopeInfo {
  scope_key:   string
  entry_count: number
}

// Sensitive memory grant-access (B-033)
// GET /memory/agent/{scope_key}/{entry_id}/grant-access
export interface GrantAccessStatus {
  granted:            boolean
  expires_in_seconds: number
}

export interface MemorySummary {
  chat_summaries_count: number
  user_facts_count:     number
  agent_entries_count:  number
}

// ─── MCP ─────────────────────────────────────────────────────
export interface MCPConfig {
  id:             string           // backend returns "id", not "config_id"
  name:           string
  transport:      'stdio' | 'sse' | 'http'
  enabled:        boolean
  project_id:     string | null    // V5.7 — null = global MCP (available everywhere)
  project_paused: boolean          // V5.7 — true = cascade-disabled by project pause/archive
  created_at:     string
  updated_at:     string
  // STDIO fields
  command?:       string
  args?:          string[]
  env?:           Record<string, string>
  cwd?:           string
  env_file?:      string
  // HTTP / SSE fields
  url?:           string
  headers?:       Record<string, string>
}
// Section 91 (M-01/M-02, V6.50) — PUT /mcp/{id} response. Distinct from
// MCPConfig (no project_id/project_paused/created_at/updated_at), plus the
// sync outcome from re-connecting when the config is left/made enabled.
export interface UpdateMCPConfigResponse {
  id:          string
  name:        string
  transport:   'stdio' | 'sse' | 'http'
  enabled:     boolean
  command?:    string | null
  args?:       string[] | null
  env?:        Record<string, string> | null
  cwd?:        string | null
  env_file?:   string | null
  url?:        string | null
  headers?:    Record<string, string> | null
  tool_count?: number | null   // null if disabled or the server was unreachable
  sync_error?: string | null   // set when tool_count is null because sync failed
}
// Section 91 — POST /mcp/seed-defaults response
export interface SeedDefaultMCPsPendingInstall {
  name:    string
  missing: string[]   // e.g. ["npx"]
}
export interface SeedDefaultMCPsResponse {
  status:                string
  mcps_seeded:            number
  mcps_skipped:           number
  mcps_pending_install:   SeedDefaultMCPsPendingInstall[]
  errors:                 string[]
}
export interface MCPHealth {
  config_id:   string           // health endpoints return config_id (= MCPConfig.id)
  name:        string
  status:      'healthy' | 'unhealthy' | 'unknown' | 'disabled' | 'project_paused'  // V5.7
  latency_ms?: number
  tool_count?: number | null
  error?:      string | null
}
export interface MCPResource {
  uri:          string
  name?:        string
  description?: string
  mime_type?:   string          // backend returns snake_case
}
export interface MCPPrompt {
  name:         string
  description?: string
  arguments?:   Record<string, unknown>
}
export interface MCPTool {
  name:          string
  description:   string | null
  input_schema:  Record<string, unknown> | null
  output_schema: Record<string, unknown> | null
}

// ─── Skills (V5.6 overhaul — multi-file, recursive tree) ────
export interface SkillInfo {
  name:           string
  description?:   string
  path?:          string                      // skill directory (V5.6+)
  allowed_tools?: string[]                    // tools bundled with skill (V5.6)
  tools?:         string[]                    // legacy alias (pre-V5.6)
  version?:       string                      // kept for display (pre-V5.6 skills)
  author?:        string
  license?:       string | null
  compatibility?: string | null
  metadata?:      Record<string, unknown>     // orchestrator skill metadata (V5.6)
}

// V5.6: recursive file tree node (GET /skills/{name}/files)
export interface SkillFileEntry {
  path:     string                            // relative to skill root, forward slashes
  type:     'file' | 'directory'
  size:     number | null                     // null for directories
  children: SkillFileEntry[] | null           // null for files; array for directories
}

export interface SkillFilesResponse {
  name:      string
  skill_dir: string                           // absolute server path — display only
  files:     SkillFileEntry[]
}

export interface SkillFileContentResponse {
  name:     string
  filepath: string
  content:  string
}

export interface SkillFileWriteResponse {
  name:              string
  filepath:          string
  size:              number
  registry_reloaded: boolean                  // true when SKILL.md was edited — refresh skill list
}

// ─── Paginated messages (B-013) ──────────────────────────────
export interface PaginatedMessagesResponse {
  messages:    Message[]
  has_more:    boolean
  next_cursor: string | null
}

// ─── RAG ingest job (B-018) ──────────────────────────────────
export interface IngestJobResponse {
  job_id:   string
  status:   'queued'
  filename: string
}
export interface IngestJobStatus {
  job_id:       string
  status:       'queued' | 'processing' | 'completed' | 'failed'
  filename:     string
  chunks_done:  number
  total_chunks: number
  error:        string | null
}

// ─── Execution list item (B-021 + B-061) ─────────────────────
export interface ExecutionSummary {
  execution_id:       string
  conversation_id:    string
  status:             ExecutionStatus
  triggered_by?:      'conversation' | 'daemon' | null   // B-061
  daemon_name?:       string | null                       // B-061
  project_id?:        string | null                       // B-061
  model_used?:        string
  provider_used?:     string
  duration_ms?:       number
  created_at:         string
  completed_at?:      string
}

// ─── Live execution summary (app-close cancellation safety) ──
// GET /executions/running — in-memory ExecutionRegistry, not the DB (a
// still-running execution has no execution_metrics row yet). Covers both
// chat and daemon executions, including ones with no open SSE connection.
export interface RunningExecutionSummary {
  conversation_id: string
  execution_id:    string
  execution_mode:  string   // "conversational" | "plan_execute" | "daemon" | "subagent_background" | "background_process" (Section 90)
  status:          string
  started_at?:     string | null
  // Section 90 (V6.49 Phase 3) — only set when execution_mode === 'background_process'
  pid?:             number
  log_path?:        string
  // Added 2026-09-17 per a frontend request — the process's argv, joined
  // into a single display string server-side; full string, not truncated,
  // so any truncation/"see full command" affordance stays UI-side.
  command?:         string
  // Section 96 (V6.58 Phase 2) — only meaningful when execution_mode ===
  // 'background_process'. Default false (session-scoped): the process is
  // killed on app quit unless the user pins it via PATCH /keep-alive.
  keep_alive_on_app_shutdown?: boolean
}

// ─── Daemon execution history (B-062) ────────────────────────
export type DaemonRunStatus = 'running' | 'completed' | 'failed' | 'cancelled' | 'aborted'

export interface DaemonExecutionSummary {
  execution_id:        string
  status:              DaemonRunStatus
  triggered_by?:       'daemon' | null
  project_id?:         string | null
  termination_reason?: string | null
  error_message?:      string | null    // Section 75: full str(exc) text; null on success/cancel
  duration_ms?:        number | null
  created_at?:         string | null
  completed_at?:       string | null
  // Section 75: now populated for daemon runs (were always null/0 before)
  model_used?:         string | null
  provider_used?:      string | null
  prompt_tokens?:      number | null
  completion_tokens?:  number | null
  reasoning_tokens?:   number | null
}

// ─── Health (B-017) ──────────────────────────────────────────
export interface HealthReadyResponse {
  status: 'ready' | 'degraded'
  checks: {
    db:           string
    redis:        string
    vector_store: string   // V5.1: renamed from 'qdrant' (ChromaDB migration)
    worker:       string
  }
}

// ─── EVA error envelope (B-014) ──────────────────────────────
export interface EVAError {
  code:    string   // e.g. "EVA_NOT_FOUND"
  detail:  string
  fields?: Array<{ loc: string[]; msg: string; type: string }>
}

// ─── Subagents (G-003) ───────────────────────────────────────
export interface SubagentConfigRequest {
  name:                  string
  description?:          string
  system_prompt?:        string
  model?:                string
  provider?:             string
  tools?:                string[]
  max_steps?:            number
  allowed_mcp_servers?:  string[] | null   // B-053 — null = inherit all user MCP servers
  project_id?:           string            // B-056 — scope agent to a project
}

export interface SubagentConfigResponse {
  id:                    string
  user_id:               string
  name:                  string
  description:           string | null
  system_prompt:         string | null
  model:                 string | null
  provider:              string | null
  tools:                 string[] | null
  max_steps:             number
  allowed_mcp_servers?:  string[] | null   // B-053
  project_id?:           string | null
  is_default:            boolean           // V5.7
}

export interface SubagentDeleteResponse {
  status: 'deleted'
  name:   string
}

// ─── Daemons (G-008) ─────────────────────────────────────────
export type DaemonLifecycleStatus =
  | 'idle'
  | 'running'
  | 'paused'
  | 'retrying'
  | 'permanently_failed'
  | 'project_paused'     // V5.7 — daemon suspended because its project is paused

export interface DaemonConfig {
  id:                      string
  name:                    string         // slug (e.g. "daily-reporter")
  description?:            string | null
  system_prompt:           string
  trigger_message?:        string | null  // default: "You have been activated..."
  model?:                  string | null
  provider?:               string | null
  tools?:                  string[] | null  // null/[] = all tools
  max_steps:               number
  run_interval_seconds?:   number | null  // null = manual only; ≥60 for scheduled
  enabled:                 boolean
  last_run_at?:            string | null  // ISO UTC timestamp
  next_run_at?:            string | null  // ISO UTC timestamp (computed from interval)
  created_at:              string
  updated_at:              string
  // Lifecycle / retry fields
  lifecycle_status?:       DaemonLifecycleStatus
  failure_count?:          number
  last_error?:             string | null
  last_failure_at?:        string | null
  max_retries?:            number | null
  retry_on_failure?:       boolean
  retry_delay_seconds?:    number | null
  current_execution_id?:   string | null
  event_subscriptions?:    string[] | null
  project_id?:             string | null     // V5.7 — project linkage (from backend)
  // V6.38 (Section 86) — 'system' is server-reserved (auto-created daemons
  // only, e.g. a project's sync daemon); never settable via the API
  kind?:                   'standard' | 'system' | 'supervisor'
}

export interface DaemonConfigRequest {
  name:                    string
  description?:            string
  system_prompt:           string
  trigger_message?:        string
  model?:                  string
  provider?:               string
  tools?:                  string[]
  max_steps?:              number
  // V6.38 (Section 86) — 'system' omitted from this union deliberately: the
  // backend 422s if a client sends it. Omit the field entirely when saving
  // an existing 'system' daemon rather than sending its current kind back.
  kind?:                   'standard' | 'supervisor'
  run_interval_seconds?:   number | null
  enabled?:                boolean
  retry_on_failure?:       boolean
  max_retries?:            number
  retry_delay_seconds?:    number
  event_subscriptions?:    string[] | null
  project_id?:             string            // B-056 — scope daemon to a project
}

// ─── Project Metrics (Section 70 / V6.0) ─────────────────────
export interface ProjectMetricsResponse {
  project_id:        string
  project_name:      string
  status:            string
  created_at:        string | null
  last_active_at:    string | null

  executions: {
    total:        number
    successful:   number
    failed:       number
    cancelled:    number
    success_rate: number | null
    by_trigger: {
      conversation: number
      daemon:       number
    }
  }

  tokens: {
    prompt:     number
    completion: number
    reasoning:  number
    total:      number
  }

  cost_usd:          number | null
  cost_is_partial:   boolean
  duration_total_ms: number | null

  provider_breakdown: Array<{
    provider:     string
    model:        string
    executions:   number
    total_tokens: number
    cost_usd:     number | null
  }>

  conversation_count: number
  agent_count:        number

  daemons: {
    count:               number
    healthy:             number
    degraded:            number
    failed:              number
    total_failure_count: number
    last_error:          string | null
  }

  tasks: {
    pending: number
    active:  number
    blocked: number
    done:    number
    open:    number
    total:   number
  }
}

// ─── Project sub-resources (B-056) ───────────────────────────
export interface ProjectAgent {
  id:          string
  name:        string
  description: string | null
  model:       string | null
  provider:    string | null
}

export interface ProjectDaemon {
  id:               string
  name:             string
  description:      string | null
  enabled:          boolean
  lifecycle_status: string
  last_run_at:      string | null
}

export interface DaemonWebhook {
  daemon_name:       string
  webhook_url:       string
  trigger_count:     number
  last_triggered_at: string | null
  created_at:        string
}

export interface DaemonTriggerResponse {
  triggered:    boolean
  daemon_name:  string
  execution_id: string
  note:         string
}

export interface DaemonDeleteResponse {
  status: 'deleted'
  name:   string
}

// ─── Event stream (B-051) ────────────────────────────────────
export interface EvtDaemonCompleted {
  event:        'daemon.completed'
  daemon_name:  string
  execution_id: string
  user_id:      string
  ts:           string
  summary?:     string
}
export interface EvtDaemonFailed {
  event:        'daemon.failed'
  daemon_name:  string
  execution_id: string
  user_id:      string
  ts:           string
  error?:       string
}
export interface EvtDaemonTriggered {
  event:        'daemon.triggered'
  daemon_name:  string
  execution_id: string
  user_id:      string
  ts:           string
  trigger:      string
}
export interface EvtExecutionCompleted {
  event:           'execution.completed'
  execution_id:    string
  user_id:         string
  ts:              string
  conversation_id: string
}
// Section 85 (P-19) — execution.failed had no type/wiring at all before this;
// shape assumed to mirror execution.completed until confirmed against a real
// payload (backend doc's generic envelope disagrees slightly — no
// conversation_id there, ts as number not string — don't trust either
// description over what actually arrives on the wire).
export interface EvtExecutionFailed {
  event:           'execution.failed'
  execution_id:    string
  user_id:         string
  ts:              string
  conversation_id: string
  error?:          string
  // Section 102 (V6.59 Phase 5) — a run refused at the worker because its provider/
  // model had gone stale carries the reason here as a ready-to-show sentence.
  summary?:        string
}
// Section 102 (V6.59 Phase 5) — the session's primary provider was unusable at run
// time and a fallback is answering instead. `summary` is a ready-to-show sentence;
// the saved session is NOT changed. No conversation_id on this payload — match on
// execution_id.
export interface EvtProviderFallbackPromoted {
  event:        'provider.fallback_promoted'
  execution_id: string
  user_id:      string
  daemon_name:  string | null
  summary:      string
  ts:           number
}
export interface EvtExecutionAwaitingWakeup {
  event:        'execution.awaiting_wakeup'
  execution_id: string
  user_id:      string
  daemon_name:  string | null
  summary:      string
  ts:           number
}
export interface EvtWakeupResolved {
  event:        'wakeup.resolved'
  execution_id: string   // the ORIGINAL paused execution, not the continuation
  user_id:      string
  daemon_name:  string | null
  summary:      string
  ts:           number
}
export interface EvtConversationTitled {
  event:           'conversation.titled'
  conversation_id: string
  title:           string
  user_id:         string
  ts:              number
}
// Section 90 (V6.49 Phase 3) — background_process lifecycle events, same
// envelope shape as EvtWakeupResolved. execution_id here is the process id.
export interface EvtProcessStopped {
  event:        'process.stopped'
  execution_id: string
  user_id:      string
  daemon_name:  string | null
  summary:      string
  ts:           number
}
export interface EvtProcessCrashed {
  event:        'process.crashed'
  execution_id: string
  user_id:      string
  daemon_name:  string | null
  summary:      string
  ts:           number
}
export type AppEvent =
  | EvtDaemonCompleted
  | EvtDaemonFailed
  | EvtDaemonTriggered
  | EvtExecutionCompleted
  | EvtExecutionFailed
  | EvtProviderFallbackPromoted
  | EvtExecutionAwaitingWakeup
  | EvtWakeupResolved
  | EvtConversationTitled
  | EvtProcessStopped
  | EvtProcessCrashed

// ─── Background wakeups (P-19 / Section 84) ───────────────────
export interface WakeupSummary {
  id:                  string   // wakeup id, not the execution id
  execution_id:        string   // the paused execution
  conversation_id:     string
  conversation_title:  string | null
  reason:              string
  wake_at:             string | null
  watch_execution_id:  string | null
  watched_status:      string | null
  watch_daemon_name:   string | null
  created_at:          string | null
}

// ─── Prompt enhancer (B-055) ─────────────────────────────────
export interface PromptEnhanceRequest {
  raw_prompt: string
  context:    'conversation' | 'agent' | 'project'
}
export interface PromptEnhanceResponse {
  enhanced_prompt: string
  original_prompt: string
}

// ─── Generic API responses ────────────────────────────────────
export interface APIError {
  code?:   string   // EVA error code (B-014)
  // V6.22 (Section 82) — POST /chat, POST /prompts/enhance, PATCH /session/permissions
  // return a 424 shaped { error_code: "provider_not_configured" | "api_key_not_configured", message }
  detail:  string | { msg: string; type: string }[] | { error_code: string; message: string }
  status?: number
}
export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page:  number
  size:  number
}

// ─── Attachments (B-066 / V4.32) ─────────────────────────────
export interface AttachmentResponse {
  attachment_id:   string
  attachment_type: 'file' | 'image'
  filename:        string | null
  mime_type:       string | null
  token_estimate:  number | null
  size_bytes:      number | null
  project_id:      string | null
  created_at:      string | null
}

export type ConvAttachmentType = 'file' | 'image' | 'conversation_ref' | 'execution_ref'

export interface ConversationAttachmentResponse {
  conversation_attachment_id: string
  conversation_id:            string
  attachment_type:            ConvAttachmentType
  attachment_id:              string | null
  ref_conversation_id:        string | null
  ref_execution_id:           string | null
  message_id:                 string | null
  rag_indexed:                boolean
  rag_job_id:                 string | null
  created_at:                 string | null
}

export interface LinkAttachmentBody {
  attachment_id?:       string
  conversation_ref_id?: string
  execution_ref_id?:    string
}

// ─── Coordinator result (B-034) ──────────────────────────────────────────────
// Shape returned by the `coordinate` tool when it completes.
// All fields are optional — backend may omit any of them; render defensively.
export interface CoordinatePhaseAgentResult {
  agent_name: string
  status:     'completed' | 'failed'
  output?:    string
  error?:     string
}

export interface CoordinatePhaseDetail {
  phase:     number
  execution: 'parallel' | 'sequential' | string
  results:   CoordinatePhaseAgentResult[]
}

export interface CoordinateResult {
  goal_summary?:  string
  strategy?:      'adaptive' | 'parallel' | 'sequential' | string
  phases_run?:    number
  total_tasks?:   number
  completed?:     number
  failed?:        number
  synthesis?:     string
  phase_details?: CoordinatePhaseDetail[]
}
