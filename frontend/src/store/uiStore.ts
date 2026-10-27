import { create } from 'zustand'

type ProfileTab = 'profile' | 'providers' | 'account'

interface UiState {
  profileModalTab: ProfileTab | null
  openProfileModal: (tab?: ProfileTab) => void
  closeProfileModal: () => void
  // Section 82 — shown when a user tries to send/trigger/enhance without a
  // configured provider. Shared by MessageInput (conversation/agent/daemon
  // sends) and SystemPromptField (enhance) rather than each rolling its own.
  providerRequiredOpen: boolean
  openProviderRequired: () => void
  closeProviderRequired: () => void
}

export const useUiStore = create<UiState>(set => ({
  profileModalTab: null,
  openProfileModal: (tab = 'profile') => set({ profileModalTab: tab }),
  closeProfileModal: () => set({ profileModalTab: null }),
  providerRequiredOpen: false,
  openProviderRequired: () => set({ providerRequiredOpen: true }),
  closeProviderRequired: () => set({ providerRequiredOpen: false }),
}))
