import { useChatStore } from '@/store/chatStore'
import AgentEditView from './AgentEditView'
import AgentsListView from './AgentsListView'

// ── Agents — 2-level router ───────────────────────────────────────────────────
export default function AgentsMainView() {
  const agentBrowseId      = useChatStore(s => s.agentBrowseId)
  const setViewAgents      = useChatStore(s => s.setViewAgents)
  const setViewAgentDetail = useChatStore(s => s.setViewAgentDetail)

  if (agentBrowseId !== null) {
    return (
      <AgentEditView
        agentName={agentBrowseId === '__new__' ? null : agentBrowseId}
        onBack={setViewAgents}
        onSaved={setViewAgents}
      />
    )
  }
  return (
    <AgentsListView
      onEdit={(name) => setViewAgentDetail(name)}
      onCreate={() => setViewAgentDetail('__new__')}
    />
  )
}
