import { post } from './client'
import type {
  ChatRequest, ChatResponse, ApproveRequest, RejectRequest,
  PlanDecisionRequest, PlanDecisionResponse,
} from '@/types/api'

export interface ExtendExecutionRequest {
  decision: 'continue' | 'stop'
  step_extension_token: string
}
export interface ExtendExecutionResponse {
  status: 'continuing' | 'stopped'
  execution_id: string
  step_extension_token: string
}

export const chatApi = {
  send: (body: ChatRequest) =>
    post<ChatResponse>('/chat', body),

  cancel: (conversationId: string) =>
    post<void>(`/cancel?conversation_id=${encodeURIComponent(conversationId)}`),

  approveTool: (executionId: string, body: ApproveRequest) =>
    post<void>(`/executions/${executionId}/approve`, body),

  rejectTool: (executionId: string, body: RejectRequest) =>
    post<void>(`/executions/${executionId}/reject`, body),

  // V6.13 — replaces the removed approve-plan / reject-plan endpoints
  planDecision: (executionId: string, body: PlanDecisionRequest) =>
    post<PlanDecisionResponse>(`/executions/${executionId}/plan-decision`, body),

  // V6.13 — grant (or decline to grant) more ReAct steps past the step limit
  extendExecution: (executionId: string, body: ExtendExecutionRequest) =>
    post<ExtendExecutionResponse>(`/executions/${executionId}/extend`, body),
}
