---
name: typescript-react-patterns
description: "Use when working on TypeScript or React codebases: building components,
  writing hooks, managing state with Zustand or TanStack Query, handling forms, or
  adding accessibility. Triggers on phrases like 'build a component', 'write a hook',
  'add state management', 'React', 'TypeScript', or when the user's project has a
  package.json with react or typescript dependencies."
license: MIT
allowed-tools: [read_file, write_file, edit_file, shell]
compatibility: "Applies to React 17+ and TypeScript 4.5+."
---

## TypeScript & React Patterns

You are working on a TypeScript/React codebase. Follow these patterns to produce
idiomatic, maintainable, accessible code.

---

### TypeScript Fundamentals

**Type over interface for unions; interface for object shapes:**
```typescript
// Union type — use type
type Status = "idle" | "loading" | "success" | "error";

// Object shape — use interface (extendable)
interface User {
  id: string;
  name: string;
  email: string;
}
```

**Never use `any` — use `unknown` for truly unknown values:**
```typescript
// Bad
function process(data: any) { ... }

// Good
function process(data: unknown) {
  if (typeof data === "string") { ... }
}
```

**Prefer explicit return types on public functions:**
```typescript
function formatDate(date: Date): string {
  return date.toISOString().split("T")[0];
}
```

**Use `const` assertions for literal types:**
```typescript
const ROLES = ["admin", "editor", "viewer"] as const;
type Role = typeof ROLES[number]; // "admin" | "editor" | "viewer"
```

---

### React Component Patterns

**Function components only — no class components:**
```typescript
interface Props {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}

export function Modal({ title, onClose, children }: Props) {
  return (
    <dialog aria-labelledby="modal-title" role="dialog">
      <h2 id="modal-title">{title}</h2>
      {children}
      <button onClick={onClose} aria-label="Close modal">×</button>
    </dialog>
  );
}
```

**Co-locate types with the component — export if reused elsewhere:**
```typescript
// In the same file as the component
export interface ButtonProps {
  variant: "primary" | "secondary" | "danger";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  children: React.ReactNode;
  onClick?: () => void;
}
```

**Forward refs when the parent needs DOM access:**
```typescript
export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ label, ...props }, ref) => (
    <label>
      {label}
      <input ref={ref} {...props} />
    </label>
  )
);
Input.displayName = "Input";
```

---

### Hook Rules & Custom Hooks

**Rules of Hooks — always followed:**
- Call hooks only at the top level (not inside conditions or loops).
- Call hooks only from React functions or custom hooks.

**Extract reusable logic into custom hooks:**
```typescript
// hooks/useDebounce.ts
export function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
```

**useCallback and useMemo — only when there is a measurable benefit:**
- Use `useCallback` for functions passed as props to memoized children.
- Use `useMemo` for expensive computations, not simple derivations.
- Premature memoization adds complexity without benefit.

---

### State Management — Zustand

```typescript
// stores/authStore.ts
import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  token: string | null;
  user: string | null;
  setAuth: (token: string, user: string) => void;
  clearAuth: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      setAuth: (token, user) => set({ token, user }),
      clearAuth: () => set({ token: null, user: null }),
    }),
    { name: "auth-store" }
  )
);
```

**Select only what you need — avoids unnecessary re-renders:**
```typescript
// Good — component only re-renders when token changes
const token = useAuthStore((s) => s.token);

// Bad — re-renders on any store change
const store = useAuthStore();
```

---

### Server State — TanStack Query v5

```typescript
// Queries (GET)
const { data, isLoading, error } = useQuery({
  queryKey: ["agents", userId],
  queryFn: () => api.get<Agent[]>(`/agents?user=${userId}`),
  staleTime: 30_000,
});

// Mutations (POST/PUT/DELETE)
const mutation = useMutation({
  mutationFn: (agent: AgentPayload) => api.post("/agents", agent),
  onSuccess: () => {
    queryClient.invalidateQueries({ queryKey: ["agents"] });
  },
});
```

**Always specify queryKey at the right granularity:**
- `["agents"]` — all agents
- `["agents", userId]` — agents for a specific user
- `["agents", userId, agentId]` — a specific agent

---

### Accessibility (ARIA)

**Interactive elements must have accessible names:**
```tsx
// Button with icon only — needs aria-label
<button onClick={close} aria-label="Close dialog">
  <XIcon aria-hidden="true" />
</button>

// Icon decorative — hide from screen readers
<img src={logo} alt="EVA Logo" />
<SomeIcon aria-hidden="true" />
```

**Forms — always associate labels:**
```tsx
<label htmlFor="email">Email address</label>
<input id="email" type="email" name="email" autoComplete="email" />
```

**Focus management for modals and dialogs:**
```tsx
// Move focus into modal on open; return focus on close
useEffect(() => {
  if (isOpen) firstFocusableRef.current?.focus();
}, [isOpen]);
```

**Keyboard navigation:**
- All interactive elements must be reachable via Tab.
- Custom interactive components must handle Enter/Space for activation.
- Modals must trap focus while open.

---

### File Organisation

```
src/
  components/       Shared UI components (atomic, no business logic)
  features/         Feature-scoped components + hooks + types
    agents/
      AgentList.tsx
      AgentCard.tsx
      useAgents.ts
      types.ts
  hooks/            App-wide custom hooks
  stores/           Zustand stores
  api/              API client + per-resource request functions
  types/            Global shared TypeScript types
  utils/            Pure utility functions
```

**Rule:** A component should not know how its data is fetched. Data fetching
belongs in hooks (`useAgents`), not in components (`AgentList`).
