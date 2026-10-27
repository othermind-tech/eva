import { useChatStore } from '@/store/chatStore'
import SkillFileEditor from './SkillFileEditor'
import SkillFolderView from './SkillFolderView'
import SkillsListView from './SkillsListView'

// ── Skills — 3-level router ───────────────────────────────────────────────────
export default function SkillsMainView() {
  const skillBrowseId      = useChatStore(s => s.skillBrowseId)
  const skillFilePath      = useChatStore(s => s.skillFilePath)
  const setViewSkillDetail = useChatStore(s => s.setViewSkillDetail)
  const setViewSkillFile   = useChatStore(s => s.setViewSkillFile)
  const setViewSkills      = useChatStore(s => s.setViewSkills)

  // Level 3: file editor
  if (skillBrowseId && skillFilePath) {
    return (
      <SkillFileEditor
        skillName={skillBrowseId}
        fileName={skillFilePath}
        onBack={() => setViewSkillDetail(skillBrowseId)}
      />
    )
  }
  // Level 2: folder view
  if (skillBrowseId) {
    return (
      <SkillFolderView
        name={skillBrowseId}
        onBack={setViewSkills}
        onOpenFile={setViewSkillFile}
      />
    )
  }
  // Level 1: skill list
  return <SkillsListView onPick={setViewSkillDetail} />
}
