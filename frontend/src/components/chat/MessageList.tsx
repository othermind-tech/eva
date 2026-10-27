import type { ChatMessageUI } from '@/store/chatStore'
import MessageItem from './MessageItem'
import ReActStepCard from './ReActStepCard'

interface Props {
  messages:    ChatMessageUI[]
  onEditSend?: (msgId: string, newText: string) => void
  onRetry?:    (msgId: string) => void
}

type DisplayGroup =
  | { type: 'message'; msg: ChatMessageUI }
  | { type: 'react';   steps: ChatMessageUI[] }

function groupMessages(messages: ChatMessageUI[]): DisplayGroup[] {
  const groups: DisplayGroup[] = []
  let i = 0
  while (i < messages.length) {
    const m = messages[i]
    if (m.msgType === 'tool_call' || m.msgType === 'tool_result') {
      const steps: ChatMessageUI[] = [m]
      i++
      while (
        i < messages.length &&
        (messages[i].msgType === 'tool_call' || messages[i].msgType === 'tool_result')
      ) {
        steps.push(messages[i])
        i++
      }
      groups.push({ type: 'react', steps })
    } else {
      groups.push({ type: 'message', msg: m })
      i++
    }
  }
  return groups
}

export default function MessageList({ messages, onEditSend, onRetry }: Props) {
  const groups = groupMessages(messages)

  return (
    <div className="flex flex-col max-w-3xl mx-auto w-full gap-1">
      {groups.map((g, idx) =>
        g.type === 'message' ? (
          <MessageItem
            key={g.msg.id}
            message={g.msg}
            onEditSend={onEditSend}
            onRetry={onRetry}
          />
        ) : (
          <ReActStepCard key={`react-${idx}`} steps={g.steps} />
        )
      )}
    </div>
  )
}
