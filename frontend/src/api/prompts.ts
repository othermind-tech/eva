// B-055 — System prompt enhancer
import { post } from './client'
import type { PromptEnhanceRequest, PromptEnhanceResponse } from '@/types/api'

export const promptsApi = {
  enhance: (body: PromptEnhanceRequest) =>
    post<PromptEnhanceResponse>('/prompts/enhance', body),
}
