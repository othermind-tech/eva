import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, XCircle, Wrench, ListChecks, ChevronDown, ChevronUp, Plus, Trash2 } from 'lucide-react'
import { useChatStore } from '@/store/chatStore'
import { chatApi } from '@/api/chat'
import { extractErrorDetail, isUnreachableError } from '@/lib/errors'

export function ApprovalGate() {
  const approval         = useChatStore(s => s.approval)
  const clearApproval    = useChatStore(s => s.clearApproval)
  const activeExecId     = useChatStore(s => s.activeExecutionId)

  if (approval.status === 'idle') return null

  return (
    <div
      className="absolute inset-0 z-50 flex items-center justify-center p-6"
      style={{ background: 'rgba(6,9,15,0.8)', backdropFilter: 'blur(8px)' }}
    >
      {approval.status === 'tool' && (
        <ToolApprovalCard
          event={approval.event}
          executionId={activeExecId!}
          onDone={clearApproval}
        />
      )}
      {approval.status === 'plan' && (
        <PlanApprovalCard
          event={approval.event}
          plan={approval.plan}
          executionId={activeExecId!}
          onDone={clearApproval}
        />
      )}
    </div>
  )
}

// ─── Tool Approval ───────────────────────────────────────────
import type { SSEApprovalRequired } from '@/types/api'

function ToolApprovalCard({
  event, executionId, onDone
}: { event: SSEApprovalRequired; executionId: string; onDone: () => void }) {
  const [showArgs, setShowArgs] = useState(false)
  // Section 95 — approve/reject previously had no onError at all: a failed
  // request just left the gate sitting there, spinner-free, with no
  // explanation and no way forward other than reloading the app.
  const [actionError, setActionError] = useState<string | null>(null)
  const setWorkerDown = useChatStore(s => s.setWorkerDown)

  const handleActionError = (e: unknown) => {
    if (isUnreachableError(e)) {
      setWorkerDown(true)
      setActionError("Couldn't reach the server — check your connection and try again.")
    } else {
      setActionError(extractErrorDetail(e, 'Something went wrong. Try again.'))
    }
  }

  const { mutate: approve, isPending: approving } = useMutation({
    mutationFn: () => chatApi.approveTool(executionId, { approval_token: event.approval_token }),
    onSuccess:  onDone,
    onError:    handleActionError,
  })
  const { mutate: reject, isPending: rejecting } = useMutation({
    mutationFn: () => chatApi.rejectTool(executionId, { approval_token: event.approval_token }),
    onSuccess:  onDone,
    onError:    handleActionError,
  })

  const loading = approving || rejecting

  return (
    <div
      className="w-full max-w-md rounded-2xl overflow-hidden animate-fade-up"
      style={{
        background: 'var(--bg-surface1)',
        border: '1px solid rgba(245,158,11,0.35)',
        boxShadow: '0 24px 80px rgba(0,0,0,0.6), 0 0 40px rgba(245,158,11,0.1)',
      }}
    >
      {/* Amber gradient top bar */}
      <div
        className="h-[2px] w-full"
        style={{ background: 'linear-gradient(90deg, var(--amber), rgba(245,158,11,0.3))' }}
      />

      <div className="p-6">
        {/* Header */}
        <div className="flex items-start gap-3 mb-5">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'var(--amber-dim)', border: '1px solid rgba(245,158,11,0.3)' }}
          >
            <AlertTriangle size={18} style={{ color: 'var(--amber)' }} />
          </div>
          <div>
            <h3 className="font-semibold text-base" style={{ color: 'var(--text-primary)' }}>
              Tool Approval Required
            </h3>
            <p className="text-sm mt-0.5" style={{ color: 'var(--text-secondary)' }}>
              EVA wants to call a tool
            </p>
          </div>
        </div>

        {/* Tool info */}
        <div
          className="rounded-xl p-4 mb-4"
          style={{ background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)' }}
        >
          <div className="flex items-center gap-2 mb-2">
            <Wrench size={14} style={{ color: 'var(--neural)' }} />
            <span
              className="text-sm font-semibold"
              style={{ color: 'var(--neural)', fontFamily: 'JetBrains Mono, monospace' }}
            >
              {event.tool_name}
            </span>
            <span
              className="ml-auto text-xs px-2 py-0.5 rounded-full"
              style={{
                background: 'var(--amber-dim)',
                border: '1px solid rgba(245,158,11,0.2)',
                color: 'var(--amber)',
              }}
            >
              {event.authority}
            </span>
          </div>
          {event.description && (
            <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
              {event.description}
            </p>
          )}
        </div>

        {/* Args toggle */}
        <button
          onClick={() => setShowArgs(v => !v)}
          className="flex items-center gap-2 text-xs mb-3 transition-colors duration-150"
          style={{ color: 'var(--text-muted)' }}
        >
          {showArgs ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {showArgs ? 'Hide' : 'View'} arguments
        </button>

        {showArgs && (
          <pre
            className="text-xs rounded-xl p-3 overflow-x-auto mb-4"
            style={{
              background: 'var(--bg-base)',
              border: '1px solid var(--border-dim)',
              color: 'var(--text-secondary)',
            }}
          >
            {JSON.stringify(event.tool_input, null, 2)}
          </pre>
        )}

        {actionError && (
          <div
            className="rounded-lg p-2.5 mb-3 text-xs"
            style={{ background: 'var(--danger-dim)', border: '1px solid rgba(255,77,109,0.3)', color: 'var(--danger)' }}
          >
            {actionError}
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={() => { setActionError(null); reject() }}
            disabled={loading}
            className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-semibold transition-all duration-150"
            style={{
              background: 'var(--danger-dim)',
              border: '1px solid rgba(255,77,109,0.3)',
              color: 'var(--danger)',
            }}
          >
            <XCircle size={15} />
            Reject
          </button>
          <button
            onClick={() => { setActionError(null); approve() }}
            disabled={loading}
            className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-semibold transition-all duration-150"
            style={{
              background: 'var(--amber)',
              border: '1px solid var(--amber)',
              color: 'var(--bg-base)',
              boxShadow: '0 0 20px rgba(245,158,11,0.3)',
            }}
          >
            <CheckCircle2 size={15} />
            Approve
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Plan Approval ───────────────────────────────────────────
import type { SSEPlanApprovalRequired, PlanApprovalPlan, PlanApprovalStep } from '@/types/api'

function PlanApprovalCard({
  event, plan, executionId, onDone
}: { event: SSEPlanApprovalRequired; plan: PlanApprovalPlan; executionId: string; onDone: () => void }) {

  const [editedGoal,  setEditedGoal]  = useState(plan.goal)
  const [editedSteps, setEditedSteps] = useState<PlanApprovalStep[]>(plan.steps)
  const [showReason,  setShowReason]  = useState(false)
  const [reason,      setReason]      = useState('')
  const [validationError, setValidationError] = useState<string | null>(null)

  const isDirty = editedGoal !== plan.goal || JSON.stringify(editedSteps) !== JSON.stringify(plan.steps)
  const setWorkerDown = useChatStore(s => s.setWorkerDown)

  const { mutate: decide, isPending } = useMutation({
    mutationFn: (body: import('@/types/api').PlanDecisionRequest) => chatApi.planDecision(executionId, body),
    onSuccess:  onDone,
    onError:    (e: unknown) => {
      // Section 95 — this already had a working fallback (validationError),
      // it just never distinguished "unreachable" to also flip the shared
      // banner, unlike a real 422 validation message which needs no banner.
      if (isUnreachableError(e)) setWorkerDown(true)
      setValidationError(extractErrorDetail(e, 'Could not save the plan — check the fields below.'))
    },
  })

  const approveAsIs = () => {
    setValidationError(null)
    decide({ decision: 'approve', plan_approval_token: event.plan_approval_token })
  }
  const saveEditsAndApprove = () => {
    setValidationError(null)
    decide({
      decision: 'edit',
      plan_approval_token: event.plan_approval_token,
      plan: { goal: editedGoal, steps: editedSteps.map((s, i) => ({ ...s, index: i })) },
    })
  }
  const rejectPlan = () => {
    setValidationError(null)
    decide({ decision: 'reject', plan_approval_token: event.plan_approval_token, reason: reason.trim() || null })
  }

  const updateStep = (i: number, patch: Partial<PlanApprovalStep>) =>
    setEditedSteps(steps => steps.map((s, idx) => (idx === i ? { ...s, ...patch } : s)))
  const removeStep = (i: number) =>
    setEditedSteps(steps => steps.filter((_, idx) => idx !== i))
  const addStep = () =>
    setEditedSteps(steps => [...steps, { title: '', description: '' }])

  const loading = isPending

  return (
    <div
      className="w-full max-w-lg rounded-2xl overflow-hidden animate-fade-up"
      style={{
        background: 'var(--bg-surface1)',
        border: '1px solid rgba(124,108,243,0.35)',
        boxShadow: '0 24px 80px rgba(0,0,0,0.6), 0 0 40px rgba(124,108,243,0.1)',
      }}
    >
      {/* Neural gradient top bar */}
      <div
        className="h-[2px] w-full"
        style={{ background: 'linear-gradient(90deg, var(--neural), rgba(124,108,243,0.3))' }}
      />

      <div className="p-6">
        {/* Header */}
        <div className="flex items-start gap-3 mb-5">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'var(--neural-dim)', border: '1px solid rgba(124,108,243,0.3)' }}
          >
            <ListChecks size={18} style={{ color: 'var(--neural)' }} />
          </div>
          <div>
            <h3 className="font-semibold text-base" style={{ color: 'var(--text-primary)' }}>
              Execution Plan
            </h3>
            <p className="text-sm mt-0.5" style={{ color: 'var(--text-secondary)' }}>
              Review, edit, and approve before EVA executes
            </p>
          </div>
        </div>

        {/* Goal — editable */}
        <div
          className="rounded-xl p-3 mb-4 text-sm"
          style={{
            background: 'var(--neural-dim)',
            border: '1px solid rgba(124,108,243,0.2)',
          }}
        >
          <span className="font-semibold text-xs uppercase tracking-widest block mb-1" style={{ color: 'var(--neural)' }}>Goal</span>
          <textarea
            value={editedGoal}
            onChange={e => setEditedGoal(e.target.value)}
            rows={2}
            className="w-full resize-none outline-none bg-transparent text-sm"
            style={{ color: 'var(--text-secondary)', fontFamily: 'inherit' }}
          />
        </div>

        {/* Steps — editable */}
        <div className="flex flex-col gap-2 mb-3 max-h-64 overflow-y-auto pr-1">
          {editedSteps.map((step, i) => (
            <div
              key={i}
              className="flex gap-3 p-3 rounded-lg"
              style={{
                background: 'var(--bg-surface2)',
                border: '1px solid var(--border-dim)',
              }}
            >
              <span
                className="w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold shrink-0 mt-0.5"
                style={{
                  background: 'var(--neural-dim)',
                  border: '1px solid rgba(124,108,243,0.3)',
                  color: 'var(--neural)',
                }}
              >
                {i + 1}
              </span>
              <div className="flex-1 min-w-0 flex flex-col gap-1.5">
                <input
                  value={step.title}
                  onChange={e => updateStep(i, { title: e.target.value })}
                  placeholder="Step title"
                  className="w-full outline-none bg-transparent text-sm font-medium"
                  style={{ color: 'var(--text-primary)' }}
                />
                <textarea
                  value={step.description ?? ''}
                  onChange={e => updateStep(i, { description: e.target.value })}
                  placeholder="Description (optional)"
                  rows={1}
                  className="w-full resize-none outline-none bg-transparent text-xs"
                  style={{ color: 'var(--text-secondary)', fontFamily: 'inherit' }}
                />
              </div>
              <button
                onClick={() => removeStep(i)}
                disabled={loading}
                title="Remove step"
                className="shrink-0 self-start"
                style={{ color: 'var(--text-muted)' }}
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
          <button
            onClick={addStep}
            disabled={loading}
            className="flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-medium transition-colors duration-150"
            style={{ background: 'transparent', border: '1px dashed var(--border-mid)', color: 'var(--text-muted)' }}
          >
            <Plus size={12} />
            Add step
          </button>
        </div>

        {validationError && (
          <div
            className="rounded-lg p-2.5 mb-3 text-xs"
            style={{ background: 'var(--danger-dim)', border: '1px solid rgba(255,77,109,0.3)', color: 'var(--danger)' }}
          >
            {validationError}
          </div>
        )}

        {/* Reject reason */}
        {showReason && (
          <input
            value={reason}
            onChange={e => setReason(e.target.value)}
            placeholder="Reason for rejecting (optional)"
            className="w-full mb-3 text-xs rounded-lg px-3 py-2 outline-none"
            style={{ background: 'var(--bg-base)', border: '1px solid var(--border-dim)', color: 'var(--text-primary)' }}
          />
        )}

        {/* Actions */}
        <div className="flex gap-2">
          <button
            onClick={() => (showReason ? rejectPlan() : setShowReason(true))}
            disabled={loading}
            className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-semibold transition-all duration-150"
            style={{
              background: 'var(--danger-dim)',
              border: '1px solid rgba(255,77,109,0.3)',
              color: 'var(--danger)',
            }}
          >
            <XCircle size={15} />
            {showReason ? 'Confirm Reject' : 'Reject'}
          </button>
          {isDirty ? (
            <button
              onClick={saveEditsAndApprove}
              disabled={loading}
              className="flex-[2] flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-semibold transition-all duration-150"
              style={{
                background: 'var(--neural)',
                border: '1px solid var(--neural)',
                color: '#fff',
                boxShadow: '0 0 20px rgba(124,108,243,0.3)',
              }}
            >
              <CheckCircle2 size={15} />
              Save Edits &amp; Approve
            </button>
          ) : (
            <button
              onClick={approveAsIs}
              disabled={loading}
              className="flex-[2] flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-semibold transition-all duration-150"
              style={{
                background: 'var(--neural)',
                border: '1px solid var(--neural)',
                color: '#fff',
                boxShadow: '0 0 20px rgba(124,108,243,0.3)',
              }}
            >
              <CheckCircle2 size={15} />
              Approve Plan
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
