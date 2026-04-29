# 01 — Architecture Contract

## Layer map

```
User interaction
       │
       ▼
┌──────────────────┐
│      Pages       │  Route-owned — renders a Provider, owns Suspense + ErrorBoundary
└────────┬─────────┘
         │ wraps subtree in
         ▼
┌──────────────────┐
│    Providers     │  React Context — inject a controller's output into the subtree
└────────┬─────────┘
         │ components read via useXxxContext()
         ▼
┌──────────────────┐
│    Components    │  Pure UI — consume context, render markup, fire context callbacks
└──────────────────┘
         (components import nothing from the logic layer directly)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ logic layer ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

┌──────────────────────────────────────────┐
│  Controllers    aggregate: queries +     │
│                actions + permissions     │  typed UI API for one section
│  Flows          orchestrate: multi-step  │
│                process state             │
└───────────────────┬──────────────────────┘
                    │ composes
                    ▼
┌──────────────────────────────────────────┐
│  Actions        one discrete operation   │  create / update / delete / send
└───────────────────┬──────────────────────┘
                    │ wraps
                    ▼
┌──────────────────────────────────────────┐
│  Query / Mutation hooks  (TanStack Query)│  server state
└───────────────────┬──────────────────────┘
                    │ calls
                    ▼
┌──────────────────────────────────────────┐
│  API functions   typed fetch calls       │  Zod-validated responses
└───────────────────┬──────────────────────┘
                    │ via
                    ▼
┌──────────────────────────────────────────┐
│  API Client      HTTP wrapper            │  auth headers, error normalization
└───────────────────┬──────────────────────┘
                    │
                 Backend
```

The dividing line is between **Providers** and **Controllers**. Everything below the line is the logic layer — components never cross it directly. A component touches the logic layer only through the context a provider injects.

---

## Hard dependency rules

| Layer | May import | Must NOT import |
|---|---|---|
| `pages/` | `features/*/index`, `components/`, route/global layout helpers | Feature internals, logic layer directly, stores except route-level global pass-through |
| `features/<f>/providers/` | `features/<f>/controllers/`, `features/<f>/flows/`, `types/`, `store/` | `components/`, `api/`, `actions/` directly |
| `features/<f>/components/` | `components/ui/`, `lib/utils` | **Everything in the logic layer** — no api/, actions/, controllers/, flows/, store/ |
| `features/<f>/controllers/` | `features/<f>/actions/`, `features/<f>/api/`, `hooks/`, `store/`, `types/` | `components/`, `providers/`, other features' internals |
| `features/<f>/actions/` | `features/<f>/api/`, `lib/`, `types/`, `store/` | `components/`, `providers/`, `controllers/`, other features' internals |
| `features/<f>/api/` | `lib/api-client`, `types/` | `components/`, `controllers/`, `providers/`, `store/` |
| `components/ui/` | `lib/utils`, `types/` | `features/`, `store/`, logic layer |
| `hooks/` (shared) | `lib/`, `types/`, `store/` | `features/`, `lib/api-client` |
| `store/` | `types/`, `lib/` | `features/`, `components/` |
| `lib/api-client` | `types/api`, stdlib | Everything in `features/`, `components/`, `store/` |

The "must not" column for `features/<f>/components/` is the critical rule: **feature components never import from the logic layer.** They only consume context.

---

## Folder structure

```
src/
├── app/
│   ├── App.tsx              # Root component: providers wrapping router
│   ├── providers.tsx        # Global providers (QueryClient, AuthProvider, etc.)
│   └── router.tsx           # All routes — createBrowserRouter
│
├── pages/                   # Thin route-level components
│   └── <feature>/
│       └── <PageName>Page.tsx   # renders provider + suspense + error boundary
│
├── features/                # Vertical slices — one per business domain
│   └── <feature>/
│       ├── api/             # TanStack Query hooks + query/mutation functions
│       │   ├── <entity>-keys.ts
│       │   ├── fetch-<entity>.ts
│       │   └── use-<entity>.ts
│       ├── actions/         # Action hooks — one file per operation
│       │   ├── use-create-<entity>.ts
│       │   ├── use-update-<entity>.ts
│       │   └── use-delete-<entity>.ts
│       ├── controllers/     # Controller hooks — one file per UI section
│       │   └── use-<entity>-<section>.controller.ts
│       ├── flows/           # Flow hooks — only when a multi-step process exists
│       │   └── use-<process>.flow.ts
│       ├── providers/       # Context providers + context hooks
│       │   └── <Entity><Section>Provider.tsx
│       ├── surfaces.ts      # Optional: lazy Surface Manager registrations
│       ├── preload.ts       # Optional: explicit preload functions for lazy surfaces/routes
│       ├── components/      # Feature UI components — consume context only
│       │   └── <Component>.tsx
│       ├── types.ts         # Zod schemas + inferred types
│       └── index.ts         # Public API
│
├── components/              # Shared, application-agnostic UI primitives
│   └── ui/
│       ├── Button.tsx
│       └── Input.tsx
│   └── surfaces/
│       ├── DrawerSurface.tsx
│       └── ModalSurface.tsx
│
├── hooks/                   # Shared utility hooks (domain-agnostic)
│   └── use-debounce.ts
│
├── providers/               # App-level cross-cutting providers
│   ├── BreakpointProvider.tsx
│   └── SurfaceProvider.tsx
│
├── store/                   # Zustand stores for truly global client state
│   ├── auth.store.ts
│   └── notifications.store.ts
│
├── lib/
│   ├── api-client.ts
│   ├── animation.ts
│   ├── env.ts
│   └── utils.ts
│
└── types/
    ├── api.ts
    └── common.ts
```

### The two component categories

This project has two distinct kinds of components with different rules:

**Shared UI primitives** (`src/components/ui/`)
- Generic: `Button`, `Input`, `Modal`, `Badge`, `Table`
- Receive all data via props
- Zero knowledge of any feature or domain
- Zero context consumption

**Feature components** (`features/<f>/components/`)
- Domain-specific: `InvoiceTable`, `InvoiceFilters`, `InvoiceStatusBadge`
- Consume data and callbacks exclusively from their feature's context hook
- Zero direct imports from the logic layer
- Zero props for data that comes from context (only layout/composition props are passed)

---

## How a feature section flows end-to-end

```
InvoicesPage
  └── <InvoiceListProvider>           ← runs useInvoiceListController(), injects result
        └── <InvoiceListView>
              ├── <InvoiceFilters>    ← useInvoiceListContext() → { filters, setStatus, setSearch }
              ├── <InvoiceTable>      ← useInvoiceListContext() → { invoices, isPending }
              └── <InvoicePagination> ← useInvoiceListContext() → { total, page, setPage }
```

The controller runs once, in the provider. Every component in the tree reads from the same context. No prop drilling. No repeated hook calls.

---

## Technology stack

| Concern | Library | Version |
|---|---|---|
| Build tool | Vite | 5.x |
| Language | TypeScript | 5.x strict |
| UI | React | 18.x |
| Routing | React Router | 6.x (data router) |
| Server state | TanStack Query | 5.x |
| Client state | Zustand | 4.x |
| Forms | React Hook Form + Zod | 7.x + 3.x |
| Styling | Tailwind CSS + cva | 3.x + latest |
| Animation | Framer Motion | 11.x |
| Schema validation | Zod | 3.x |
| Testing | Vitest + @testing-library/react + MSW | 2.x + 14.x + 2.x |
| HTTP | Native fetch (wrapped in api-client) | — |

---

## App bootstrap

```tsx
// src/app/App.tsx
import { RouterProvider } from 'react-router-dom';
import { AppProviders } from './providers';
import { router } from './router';

export function App() {
  return (
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  );
}
```

```tsx
// src/app/providers.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LazyMotion, MotionConfig, domAnimation } from 'framer-motion';
import { NotificationProvider } from '@/features/notifications';
import { NotificationRenderer } from '@/features/notifications';
import { notificationConfig } from '@/lib/notification-config';
import { BreakpointProvider } from '@/providers/BreakpointProvider';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 1000 * 60, retry: 1 },
    mutations: { retry: 0 },
  },
});

export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <MotionConfig reducedMotion="user">
      <LazyMotion features={domAnimation}>
        <BreakpointProvider>
          <QueryClientProvider client={queryClient}>
            <NotificationProvider config={notificationConfig}>
              {children}
              <NotificationRenderer />
            </NotificationProvider>
          </QueryClientProvider>
        </BreakpointProvider>
      </LazyMotion>
    </MotionConfig>
  );
}
```

Providers that call React Router hooks must live inside the router tree. Put them in a root layout route, not in `AppProviders`:

```tsx
// src/app/RootRoute.tsx
import { Outlet } from 'react-router-dom';
import { AuthProvider } from '@/features/auth';
import { SurfaceProvider } from '@/providers/SurfaceProvider';

export function RootRoute() {
  return (
    <AuthProvider>
      <SurfaceProvider>
        <Outlet />
      </SurfaceProvider>
    </AuthProvider>
  );
}
```

`AppProviders` owns infrastructure that does not require router context. `RootRoute` owns providers that need `useNavigate()`, `useLocation()`, or route params.

---

## What is NOT in scope for this contract

- Backend application structure — governed by [`Backend_architecture/`](../Backend_architecture/README.md)
- AI agent layer — governed by [`AI_Architecture/`](../AI_Architecture/README.md)
- Infrastructure provisioning (Docker, CDN, CI/CD)
- Native mobile apps (React Native) — this contract targets browser SPA only
