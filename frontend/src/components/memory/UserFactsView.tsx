import { useState } from 'react'
import { Loader2, Search, X, Check, Edit3, Trash2, Brain, Plus } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { memoryApi } from '@/api/memory'
import { formatDate } from '@/lib/utils'
import { getCatStyle, CAT_STYLES } from '@/lib/memoryCategories'
import { useChatStore } from '@/store/chatStore'

// ── User facts view ───────────────────────────────────────────────────────────
export default function UserFactsView() {
  const qc              = useQueryClient()
  const memoryFilter    = useChatStore(s => s.memoryFilter)
  const setMemoryFilter = useChatStore(s => s.setMemoryFilter)
  const memoryAdding    = useChatStore(s => s.memoryAdding)
  const setMemoryAdding = useChatStore(s => s.setMemoryAdding)

  const [search,        setSearch]      = useState('')
  const [newFact,       setNewFact]     = useState('')
  const [newCategory,   setNewCat]      = useState('')
  const [editId,        setEditId]      = useState<string | null>(null)
  const [editText,      setEditText]    = useState('')
  const [editCat,       setEditCat]     = useState('')
  const [confirmFactId, setConfirmFact] = useState<string | null>(null)

  const { data: facts = [], isLoading } = useQuery({
    queryKey: ['memory-user-facts'],
    queryFn:  memoryApi.getUserFacts,
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['memory-user-facts'] })
    qc.invalidateQueries({ queryKey: ['memory-summary'] })
  }

  const { mutate: create } = useMutation({
    mutationFn: () => memoryApi.createUserFact(newFact.trim(), newCategory.trim() || undefined),
    onSuccess: () => { setNewFact(''); setNewCat(''); setMemoryAdding(false); invalidate() },
  })
  const { mutate: update } = useMutation({
    mutationFn: (id: string) => memoryApi.updateUserFact(id, editText, editCat.trim() || undefined),
    onSuccess: () => { setEditId(null); qc.invalidateQueries({ queryKey: ['memory-user-facts'] }) },
  })
  const { mutate: remove } = useMutation({
    mutationFn: (id: string) => memoryApi.deleteUserFact(id),
    onSuccess: invalidate,
  })

  // Filter by category then by search
  const byCat     = memoryFilter === 'all' ? facts : facts.filter(f => f.category === memoryFilter)
  const displayed = search.trim()
    ? byCat.filter(f => f.fact.toLowerCase().includes(search.toLowerCase()))
    : byCat

  const categories = ['all', ...Object.keys(CAT_STYLES)]

  return (
    <div className="detail-body" style={{ maxWidth: 'none' }}>
      {/* Search */}
      <div className="search-field" style={{ marginBottom: 12 }}>
        <Search size={13} />
        <input
          placeholder="Search facts…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        {search && (
          <button className="icon-btn" style={{ width: 18, height: 18 }} onClick={() => setSearch('')}>
            <X size={11} />
          </button>
        )}
      </div>

      {/* Category pills */}
      <div className="flex flex-wrap gap-1.5 mb-4">
        {categories.map(cat => {
          const cs   = getCatStyle(cat)
          const Icon = cs.icon
          const active = memoryFilter === cat
          return (
            <button
              key={cat}
              onClick={() => setMemoryFilter(cat)}
              className="flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium transition-all capitalize"
              style={active
                ? { background: cs.bg, color: cs.color, border: `1px solid ${cs.color}50` }
                : { background: 'var(--bg-surface2)', color: 'var(--text-muted)', border: '1px solid var(--border-dim)' }
              }
            >
              <Icon size={10} />
              {cat === 'all' ? 'All' : cat}
            </button>
          )
        })}
      </div>

      {/* Add Fact inline form */}
      {memoryAdding && (
        <div
          className="kn-card mb-1"
          style={{ border: '1px solid rgba(0,201,255,0.25)', boxShadow: '0 0 20px rgba(0,201,255,0.05)' }}
        >
          <div style={{ padding: '12px 14px' }}>
            <p style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--eva-primary)', marginBottom: 8 }}>
              New Fact
            </p>
            <textarea
              value={newFact}
              onChange={e => setNewFact(e.target.value)}
              placeholder="Enter a fact about yourself…"
              rows={3}
              className="w-full resize-none text-sm outline-none bg-transparent leading-relaxed"
              style={{ color: 'var(--text-primary)' }}
              autoFocus
            />
            <input
              type="text"
              value={newCategory}
              onChange={e => setNewCat(e.target.value)}
              placeholder="Category (optional) — e.g. skills, preferences…"
              className="w-full text-xs outline-none bg-transparent"
              style={{ color: 'var(--text-secondary)', borderTop: '1px solid var(--border-dim)', paddingTop: 8, marginTop: 8 }}
            />
            <div className="flex gap-2 pt-3">
              <button
                onClick={() => create()}
                disabled={!newFact.trim()}
                className="btn primary"
                style={{ fontSize: 11 }}
              >
                <Check size={11} /> Save
              </button>
              <button
                onClick={() => { setMemoryAdding(false); setNewFact(''); setNewCat('') }}
                className="btn ghost"
                style={{ fontSize: 11 }}
              >
                <X size={11} /> Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center gap-2 py-10" style={{ color: 'var(--text-muted)', fontSize: 13 }}>
          <Loader2 size={16} className="animate-spin" /> Loading…
        </div>
      )}

      {/* Empty */}
      {!isLoading && displayed.length === 0 && (
        <div className="flex flex-col items-center gap-3 py-14 text-center">
          <Brain size={28} style={{ color: 'var(--text-muted)' }} />
          <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            {search
              ? `No facts match "${search}"`
              : memoryFilter === 'all'
              ? 'No user facts stored yet.'
              : `No facts in category "${memoryFilter}".`}
          </p>
          {!search && memoryFilter === 'all' && (
            <button className="btn ghost" onClick={() => setMemoryAdding(true)}>
              <Plus size={12} /> Add your first fact
            </button>
          )}
        </div>
      )}

      {/* Fact rows */}
      {!isLoading && displayed.map(f => {
        const cs   = getCatStyle(f.category ?? 'all')
        const Icon = cs.icon
        return (
          <div key={f.id} className="kn-card">
            {editId === f.id ? (
              /* ── Edit mode ── */
              <div style={{ padding: '12px 14px' }}>
                <textarea
                  value={editText}
                  onChange={e => setEditText(e.target.value)}
                  rows={3}
                  className="w-full resize-none text-sm outline-none bg-transparent leading-relaxed"
                  style={{ color: 'var(--text-primary)' }}
                  autoFocus
                />
                <input
                  type="text"
                  value={editCat}
                  onChange={e => setEditCat(e.target.value)}
                  placeholder="Category…"
                  className="w-full text-xs outline-none bg-transparent"
                  style={{ color: 'var(--text-secondary)', borderTop: '1px solid var(--border-dim)', paddingTop: 8, marginTop: 8 }}
                />
                <div className="flex gap-2 pt-3">
                  <button className="btn primary" style={{ fontSize: 11 }} onClick={() => update(f.id)}>
                    <Check size={11} /> Save
                  </button>
                  <button className="btn ghost" style={{ fontSize: 11 }} onClick={() => setEditId(null)}>
                    <X size={11} /> Cancel
                  </button>
                </div>
              </div>
            ) : (
              /* ── Read mode ── */
              <div className="kn-head">
                {/* Category icon */}
                <div className="row-icon" style={{ background: cs.bg, color: cs.color, width: 32, height: 32, borderRadius: 9, flexShrink: 0 }}>
                  <Icon size={14} />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <p style={{ fontSize: 13, color: 'var(--text-primary)', lineHeight: 1.6 }}>{f.fact}</p>
                  <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                    {f.category
                      ? <span className="capitalize">{f.category}</span>
                      : <span style={{ fontStyle: 'italic' }}>uncategorised</span>
                    }
                    {' · '}{formatDate(f.updated_at ?? f.created_at)}
                  </p>
                </div>
                {/* Actions */}
                <div className="flex items-center" style={{ gap: 1 }} onClick={e => e.stopPropagation()}>
                  {confirmFactId === f.id ? (
                    <>
                      <span style={{ fontSize: 11, color: 'var(--danger)', marginRight: 4 }}>Delete?</span>
                      <button className="btn danger" style={{ fontSize: 11 }} onClick={() => { remove(f.id); setConfirmFact(null) }}>
                        <Check size={10} /> Yes
                      </button>
                      <button className="btn ghost" style={{ fontSize: 11 }} onClick={() => setConfirmFact(null)}>Cancel</button>
                    </>
                  ) : (
                    <>
                      <button
                        className="icon-btn"
                        title="Edit"
                        style={{ width: 26, height: 26, padding: 0 }}
                        onClick={() => { setEditId(f.id); setEditText(f.fact); setEditCat(f.category ?? '') }}
                      ><Edit3 size={13} /></button>
                      <button
                        className="icon-btn"
                        title="Delete"
                        style={{ width: 26, height: 26, padding: 0 }}
                        onMouseEnter={e => (e.currentTarget.style.color = 'var(--danger)')}
                        onMouseLeave={e => (e.currentTarget.style.color = '')}
                        onClick={() => setConfirmFact(f.id)}
                      ><Trash2 size={13} /></button>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
