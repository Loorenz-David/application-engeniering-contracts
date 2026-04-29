# 06 — Client State Contract

## Definition

Client state is UI state that does not originate from the backend and does not need to be synchronized with a server. Zustand v4 manages shared client state. The decision of which store to use is strict: if the data is backend-owned, it is server state and belongs in TanStack Query. If the data is local to one component, it belongs in `useState`.

---

## When to use client state vs server state

| Data | Where it lives |
|---|---|
| Current user's identity (id, email, name, roles, permissions) | Zustand auth store |
| Current user's full profile (avatar, timezone, preferences) | TanStack Query — see [25_user_profile.md](25_user_profile.md) |
| Access token | In-memory variable in `auth-token.ts` — not Zustand, not localStorage |
| List of invoices fetched from the backend | TanStack Query |
| Active notification toasts | Zustand notifications store |
| Selected rows in a table | `useState` in the component |
| Which tab is open | `useState` in the component |
| Global theme (light/dark) | Zustand with `persist` middleware — if survives page refresh; `useState` if ephemeral |
| Draft form values | React Hook Form (not Zustand, not TanStack Query) |
| Search filter applied by the user | URL search params (via `useSearchParams`) — not Zustand |

**If in doubt, prefer `useState` locally.** Reach for Zustand only when the state must be shared across routes or components that do not share a common ancestor, or when it must survive navigation.

The auth store is the only allowed exception for backend-originated identity data. It stores the minimum session identity needed by the app shell: user ID, email, display name, roles, effective permissions, workspace ID, and authentication status. It is not a replacement for the user profile query and does not store tokens.

---

## Store definition pattern

One file per store. File is named `<domain>.store.ts` in `src/store/`.

```ts
// src/store/notifications.store.ts
import { create } from 'zustand';

type NotificationType = 'success' | 'error' | 'info' | 'warning';

type Notification = {
  id: string;
  type: NotificationType;
  message: string;
  duration?: number;
};

type NotificationsState = {
  notifications: Notification[];
  add: (notification: Omit<Notification, 'id'>) => void;
  dismiss: (id: string) => void;
  clear: () => void;
};

export const useNotificationsStore = create<NotificationsState>((set) => ({
  notifications: [],

  add: (notification) => {
    const id = crypto.randomUUID();
    set((state) => ({
      notifications: [...state.notifications, { ...notification, id }],
    }));
    return id;
  },

  dismiss: (id) =>
    set((state) => ({
      notifications: state.notifications.filter((n) => n.id !== id),
    })),

  clear: () => set({ notifications: [] }),
}));
```

---

## Slice pattern for complex stores

If a store grows beyond five actions, split it into slices:

```ts
// src/store/ui.store.ts — example of a multi-slice store
import { create } from 'zustand';

type SidebarSlice = {
  sidebarOpen: boolean;
  toggleSidebar: () => void;
};

type ThemeSlice = {
  theme: 'light' | 'dark' | 'system';
  setTheme: (theme: 'light' | 'dark' | 'system') => void;
};

type UIState = SidebarSlice & ThemeSlice;

export const useUIStore = create<UIState>()((set) => ({
  sidebarOpen:   false,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),

  theme:    'system',
  setTheme: (theme) => set({ theme }),
}));
```

The auth store is defined in full in [12_auth.md](12_auth.md). It holds identity only — never the access token (see [04_api_client.md](04_api_client.md)) and never the full profile (see [25_user_profile.md](25_user_profile.md)).

---

## Selectors

Always use selectors to read from the store. Never pass the entire store state to a component:

```ts
// Correct — stable selector, only re-renders when user changes
const user = useAuthStore((state) => state.user);
const isAuthenticated = useAuthStore((state) => state.isAuthenticated);

// Wrong — subscribes to the entire store, re-renders on any change
const { user, isAuthenticated } = useAuthStore();
```

For derived values used in multiple places, define the selector as a named function:

```ts
// src/store/auth.store.ts
export const selectUser = (state: AuthState) => state.user;
export const selectWorkspaceId = (state: AuthState) => state.workspaceId;

// Usage
const user = useAuthStore(selectUser);
```

---

## Persistence

Use Zustand's `persist` middleware for state that should survive page refresh:

```ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type ThemeState = {
  theme: 'light' | 'dark';
  setTheme: (theme: 'light' | 'dark') => void;
};

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      theme: 'light',
      setTheme: (theme) => set({ theme }),
    }),
    { name: 'app-theme' },
  ),
);
```

Only use `persist` for state the user has explicitly configured (theme, density, dismissed local UI hints). Never persist auth tokens through `persist` middleware — use `httpOnly` cookies or the dedicated `auth-token.ts` module.

Persisted stores must be versioned and partialized so accidental fields are not written to storage:

```ts
export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      theme: 'system',
      density: 'comfortable',
      setTheme: (theme) => set({ theme }),
      setDensity: (density) => set({ density }),
    }),
    {
      name: 'app-ui-preferences',
      version: 1,
      partialize: (state) => ({
        theme: state.theme,
        density: state.density,
      }),
    },
  ),
);
```

User-specific client stores must expose their own `clearSessionState()` action and be cleared on sign-out or workspace switch. Do not rely on page reloads to remove selected rows, temporary drafts, wizard progress, or open workspace-specific panels.

---

## Permissions in client state

The auth store may hold `roles` and `permissions` because they are part of the current session identity. Feature authorization logic still reads permissions through `usePermissions()`; components do not branch directly on `user.roles`.

Roles are display and app-shell metadata. Permissions are effective backend-provided capability keys. The frontend never expands roles into permissions and never stores a frontend role-permission map in Zustand.

---

## No derived state in stores

Stores hold raw state. Derived values are computed in selectors or hooks — never stored:

```ts
// Wrong — total is derived, storing it creates sync bugs
const useBudgetStore = create((set) => ({
  items: [],
  total: 0,  // must be manually kept in sync with items
  addItem: (item) => set((s) => ({
    items: [...s.items, item],
    total: s.total + item.amount,
  })),
}));

// Correct — total is computed on read
const useBudgetStore = create((set) => ({
  items: [],
  addItem: (item) => set((s) => ({ items: [...s.items, item] })),
}));

const total = useItemsStore((s) => s.items.reduce((sum, i) => sum + i.amount, 0));
```

---

## What client state must NOT do

- **Never store server data in Zustand.** If it comes from the backend, use TanStack Query.
- **Never store auth tokens using Zustand's `persist` middleware.** Tokens in `localStorage` are accessible to XSS. Use `httpOnly` cookies.
- **Never persist the auth store.** Session restoration comes from the refresh cookie plus current-user request, not browser storage.
- **Never access a store inside another store.** Stores are independent units. Coordination happens in hooks that read from multiple stores.
- **Never import `apiClient`, query hooks, or `queryClient` inside a store.** Stores update local state only; async orchestration belongs in hooks, actions, providers, or controllers.
- **Never put business logic in store actions.** Actions set state. Business logic lives in hooks.
- **Never map roles to permissions in a store.** Effective permissions come from the backend session/current-user payload.
- **Never create a store for state that is only used in one component.** Use `useState`.
- **Never reset the entire store to handle a logout.** Clear only the fields that contain user-specific data, and only in the store's own `clearAuth` action.
