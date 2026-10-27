import { useChatStore } from '@/store/chatStore'
import DaemonsListView from './DaemonsListView'
import DaemonEditView from './DaemonEditView'

// ── Daemons — 2-level router ──────────────────────────────────────────────────
export default function DaemonsMainView() {
  const daemonBrowseId      = useChatStore(s => s.daemonBrowseId)
  const setViewDaemons      = useChatStore(s => s.setViewDaemons)
  const setViewDaemonDetail = useChatStore(s => s.setViewDaemonDetail)

  if (daemonBrowseId !== null) {
    return (
      <DaemonEditView
        daemonName={daemonBrowseId === '__new__' ? null : daemonBrowseId}
        onBack={setViewDaemons}
        onSaved={setViewDaemons}
      />
    )
  }
  return (
    <DaemonsListView
      onEdit={(name) => setViewDaemonDetail(name)}
      onCreate={() => setViewDaemonDetail('__new__')}
    />
  )
}
