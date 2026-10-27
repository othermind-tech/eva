import { create } from 'zustand'

type UpdateStatus = 'idle' | 'downloading' | 'ready'

interface UpdateState {
  status:    UpdateStatus
  percent:   number
  setStatus: (status: UpdateStatus, percent?: number) => void
}

export const useUpdateStore = create<UpdateState>((set) => ({
  status:    'idle',
  percent:   0,
  setStatus: (status, percent = 0) => set({ status, percent }),
}))
