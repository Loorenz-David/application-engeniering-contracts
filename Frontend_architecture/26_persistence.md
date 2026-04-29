# 26 — Persistence Contract

## Definition

Persistence is the decision of how long data survives and where it is stored between sessions. Every piece of data in the app has a correct storage tier. Choosing the wrong tier creates either security vulnerabilities (sensitive data in localStorage) or poor UX (no data on cold boot). Neither is acceptable in a production app.

---

## Storage tiers

| Tier | Technology | Capacity | Survives refresh | JS-accessible | Use for |
|---|---|---|---|---|---|
| **In-memory** | JS module variables | Session only | No | Yes (this session only) | Access tokens |
| **Cookie (`httpOnly`)** | Browser-managed | ~4 KB | Configurable | No | Refresh token |
| **localStorage** | `localStorage` + Zustand `persist` | ~5 MB | Yes | Yes | Preferences, UI state |
| **TanStack Query cache persister** | localStorage or IndexedDB | ~5–50 MB | Yes | Yes | Non-sensitive server state |
| **IndexedDB** | Dexie or idb | Hundreds of MB | Yes | Yes | Large offline datasets, draft queues |

---

## Decision rule

> Persist anything that makes the app feel instant on cold boot.  
> Never persist anything that would be a security problem if another person sat down at the same machine.

Applied:

| Data | Persist? | Tier | Reason |
|---|---|---|---|
| Access token | ❌ Never | In-memory only | XSS-exfiltration risk across sessions |
| Refresh token | ❌ Never (JS) | `httpOnly` cookie | Browser manages it; JS cannot touch it |
| Theme preference | ✅ Yes | localStorage | Not sensitive; user expects it to survive refresh |
| Sidebar open/collapsed | ✅ Yes | localStorage | UX expectation |
| Column visibility, table density | ✅ Yes | localStorage | UX expectation |
| User profile (name, avatar_file_id) | ✅ Yes | TanStack Query persister | Avoids loading flash on cold boot |
| Workspace member list | ✅ Yes | TanStack Query persister | Speeds up app shell render |
| Invoice list (or any entity list) | ✅ Optional | TanStack Query persister | Shows stale data immediately; fresh data loads in background |
| Sensitive financial summaries | ⚠️ Careful | TanStack Query persister only if single-user device is assumed | Risk: shared machine |
| Auth tokens of any kind | ❌ Never | — | Security boundary |
| Draft form data (multi-step wizard) | ✅ Yes | localStorage (ephemeral) or IndexedDB | User expects draft to survive accidental refresh |
| Offline message queue | ✅ Yes | IndexedDB | Too large for localStorage; needs durability |

---

## Zustand persistence (preferences and UI state)

Use Zustand's `persist` middleware for settings the user has explicitly configured. Keep persisted stores small and non-sensitive.

```ts
// src/store/preferences.store.ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type PreferencesState = {
  theme:       'light' | 'dark' | 'system';
  sidebarOpen: boolean;
  setTheme:    (theme: 'light' | 'dark' | 'system') => void;
  setSidebar:  (open: boolean) => void;
};

export const usePreferencesStore = create<PreferencesState>()(
  persist(
    (set) => ({
      theme:       'system',
      sidebarOpen: true,

      setTheme:   (theme)  => set({ theme }),
      setSidebar: (open)   => set({ sidebarOpen: open }),
    }),
    {
      name:    'app-preferences',   // localStorage key
      version: 1,                   // increment when shape changes — triggers migration
    },
  ),
);
```

Zustand `persist` serialises to `localStorage` by default. The `version` field matters: when you change the shape of the persisted state, bump `version` and supply a `migrate` function so old stored values don't crash the app.

```ts
persist(
  (set) => ({ ... }),
  {
    name:    'app-preferences',
    version: 2,
    migrate: (persisted, fromVersion) => {
      if (fromVersion === 1) {
        // v1 had "darkMode: boolean" — convert to "theme: string"
        const old = persisted as { darkMode: boolean };
        return { ...persisted, theme: old.darkMode ? 'dark' : 'system' };
      }
      return persisted;
    },
  },
)
```

---

## TanStack Query cache persistence

This is the primary senior technique for perceived performance. The query cache is serialized to localStorage (or IndexedDB) on every write. On cold boot, the cache is restored before the first render — the user sees their data instantly while a background refetch updates it.

### Setup (`src/app/providers.tsx`)

```ts
import { QueryClient } from '@tanstack/react-query';
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client';
import { createSyncStoragePersister } from '@tanstack/query-sync-storage-persister';
import { env } from '@/lib/env';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60,          // data is fresh for 1 minute
      gcTime:    1000 * 60 * 60 * 24, // keep in cache/storage for 24 hours
    },
  },
});

const persister = createSyncStoragePersister({
  storage: window.localStorage,
  key:     'app-query-cache',
});

export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{
        persister,
        maxAge:  1000 * 60 * 60 * 24,  // discard cache older than 24 hours
        buster:  env.VITE_APP_VERSION, // invalidate cache on deploy
      }}
    >
      {children}
    </PersistQueryClientProvider>
  );
}
```

`VITE_APP_VERSION` is set at build time (commit hash or semver). When the app is redeployed, the cache buster changes and stale persisted data is discarded — preventing schema mismatches after a backend change.

```ts
// vite.config.ts
import { defineConfig } from 'vite';
export default defineConfig({
  define: {
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(
      process.env.npm_package_version ?? 'dev',
    ),
  },
});
```

### What gets persisted vs excluded

By default, every query in the cache is eligible for persistence. Exclude sensitive queries explicitly with query `meta` and `shouldDehydrateQuery`:

```ts
// features/billing/api/use-payment-methods.ts
// Payment data stays in-memory only — too sensitive to persist across sessions
export function usePaymentMethodsQuery() {
  return useQuery({
    queryKey: paymentKeys.list(),
    queryFn:  fetchPaymentMethods,
    meta:     { persist: false },   // excluded from persisted cache
  });
}
```

Alternatively, whitelist which queries are persisted using the `dehydrateOptions` field:

```ts
persistOptions={{
  persister,
  dehydrateOptions: {
    shouldDehydrateQuery: (query) => {
      if (query.meta?.persist === false) return false;
      // Only persist profile and workspace data
      const key = query.queryKey[0];
      return key === 'profile' || key === 'workspace' || key === 'members';
    },
  },
}}
```

### The stale-while-revalidate effect

After restoring the cache on boot, TanStack Query immediately treats all restored data as stale (because the restore happens before `staleTime` is checked). It will refetch in the background. The user sees:

```
t=0ms  → Page renders with cached profile, workspace, member list (from localStorage)
t=200ms → Background refetches complete, UI updates silently if data changed
```

Without the persister:
```
t=0ms  → Page renders with skeleton reflections everywhere
t=200ms → Data arrives, UI renders
```

---

## IndexedDB

Use IndexedDB when:
- The dataset exceeds 5 MB (localStorage limit)
- The app must function offline (offline-first features)
- You need transactional writes (draft sync queues, message queues)
- You are storing binary data (images, files)

Do **not** use IndexedDB for a standard SaaS app that requires a network connection. The complexity is not justified.

### When to reach for IndexedDB

```
Standard SaaS (invoices, CRM, project management, dashboards)
  → localStorage + TanStack Query persister is sufficient

Field app, logistics, medical (works offline, syncs when connected)
  → IndexedDB via Dexie for offline queue + sync engine

Collaborative editor (Google Docs-style)
  → IndexedDB for CRDT state + Yjs or Automerge

Messaging app (WhatsApp Web-style, large history)
  → IndexedDB for message store
```

### IndexedDB via Dexie (if needed)

```ts
// src/lib/db.ts
import Dexie, { type Table } from 'dexie';

type DraftInvoice = {
  id:         string;
  data:       unknown;
  updated_at: number;
};

class AppDatabase extends Dexie {
  drafts!: Table<DraftInvoice>;

  constructor() {
    super('app-db');
    this.version(1).stores({
      drafts: 'id, updated_at',
    });
  }
}

export const db = new AppDatabase();
```

For TanStack Query with an IndexedDB persister, replace the `createSyncStoragePersister` with `createAsyncStoragePersister` from `@tanstack/query-async-storage-persister`:

```ts
import { createAsyncStoragePersister } from '@tanstack/query-async-storage-persister';
import { get, set, del } from 'idb-keyval';

const persister = createAsyncStoragePersister({
  storage: { getItem: get, setItem: set, removeItem: del },
});
```

Use the async persister when the localStorage 5 MB limit is a concern.

---

## Cache invalidation on sign-out

On sign-out, clear both the TanStack Query cache and the persisted storage. This prevents the next user from seeing the previous user's data on a shared machine:

```ts
// features/auth/api/use-sign-out.ts
async function signOut() {
  await apiClient.post('/api/v1/auth/sign-out', z.object({}), {});
  setAccessToken(null);
  useAuthStore.getState().clearAuth();
}

export function useSignOutMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: signOut,
    onSettled: () => {
      queryClient.clear();                              // wipes in-memory cache
      localStorage.removeItem('app-query-cache');       // wipes persisted cache
    },
  });
}
```

---

## Summary: what the contracts use

| Layer | What it stores | Where |
|---|---|---|
| `auth-token.ts` | Access token | In-memory variable |
| Browser | Refresh token | `httpOnly` cookie |
| `auth.store.ts` | User identity (id, email, name, roles, permissions) | Zustand — in-memory only |
| `preferences.store.ts` | Theme, sidebar state | Zustand + `persist` → localStorage |
| TanStack Query | Server data (profile, workspace, entities) | In-memory + optional localStorage persister |
| `features/profile/` | Full user profile | TanStack Query (persisted) |
| IndexedDB | Offline queues, large datasets | Dexie — only if offline support is a feature |

---

## What persistence must NOT do

- **Never persist the access token to any storage.** In-memory only — it dies when the tab closes and is restored by the refresh cookie.
- **Never persist the Zustand auth store.** If the auth store is persisted, a user who signs out on a shared machine leaves their identity in localStorage for the next user.
- **Never persist sensitive TanStack Query data.** Mark sensitive queries with `meta: { persist: false }` and enforce that in `shouldDehydrateQuery`.
- **Never skip the cache buster on the TanStack Query persister.** Without it, a backend schema change can hydrate stale data that crashes the app on boot.
- **Never use localStorage for tokens of any kind.** Any XSS script can read it and exfiltrate it to an attacker's server — unlike in-memory variables, which are only readable during the current session.
- **Never forget to clear persisted cache on sign-out.** `queryClient.clear()` clears the in-memory cache; `localStorage.removeItem('app-query-cache')` clears the persisted copy.
- **Never reach for IndexedDB without a concrete requirement.** It adds schema management, migration complexity, and a learning curve. Use it only when localStorage capacity or offline support makes it necessary.
