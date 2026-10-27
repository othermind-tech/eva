import { create } from 'zustand'
import type { AppEvent } from '@/types/api'

export interface Notification {
  id:    string
  event: AppEvent
  read:  boolean
  ts:    Date
}

interface NotificationState {
  notifications: Notification[]
  unreadCount:   number
  push:          (evt: AppEvent) => void
  markAllRead:   () => void
  clear:         () => void
  remove:        (id: string) => void
}

export const useNotificationStore = create<NotificationState>((set) => ({
  notifications: [],
  unreadCount:   0,

  push: (evt) =>
    set((state) => {
      const next: Notification = {
        id:    crypto.randomUUID(),
        event: evt,
        read:  false,
        ts:    new Date(),
      }
      // Prepend newest-first, cap at 50
      const notifications = [next, ...state.notifications].slice(0, 50)
      const unreadCount   = notifications.filter((n) => !n.read).length
      return { notifications, unreadCount }
    }),

  markAllRead: () =>
    set((state) => ({
      notifications: state.notifications.map((n) => ({ ...n, read: true })),
      unreadCount:   0,
    })),

  clear: () => set({ notifications: [], unreadCount: 0 }),

  remove: (id) =>
    set((state) => {
      const notifications = state.notifications.filter((n) => n.id !== id)
      const unreadCount   = notifications.filter((n) => !n.read).length
      return { notifications, unreadCount }
    }),
}))
