import InlineDisclosure from './InlineDisclosure'

interface Props {
  content:   string
  collapsed: boolean
  onToggle:  () => void
}

export default function ReasoningBlock({ content, collapsed, onToggle }: Props) {
  return (
    <InlineDisclosure
      summary={<span style={{ color: 'var(--text-muted)' }}>Reasoning</span>}
      open={!collapsed}
      onOpenChange={onToggle}
    >
      <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-dim)', fontStyle: 'italic', lineHeight: 1.6 }}>
        {content}
      </p>
    </InlineDisclosure>
  )
}
