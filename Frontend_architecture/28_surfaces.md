# 28 — Surface Manager Contract

## Definition

A **surface** is a visual container that frames a feature page. The surface provides the chrome — animation, backdrop, header, close button, scroll area. The feature page provides the content. Neither knows about the other's implementation.

The **Surface Manager** is the root-level registry and controller for all overlay surfaces. Features register the surfaces they can appear in. Any other feature triggers them by name — no direct imports, no shared props, no coupling between features.

```
Feature A:  surface.open('invoice-detail', { id })
            ↓
Manager:    looks up 'invoice-detail' → path=/invoices/123 + DrawerSurface + InvoiceDetailPage
            ↓
Router:     navigate('/invoices/123', { state: { surface: 'drawer', background: location } })
            ↓
Renderer:   mounts DrawerSurface containing InvoiceDetailPage
            URL: /invoices/123  ← deep-linkable, shareable, trace-able
```

Every surface with a registered `path` is URI-addressable. The URL is the trace — the full application state can be restored from it, shared, or replayed.

---

## Surface types

| Type | Visual | URL changes | Semantic meaning |
|---|---|---|---|
| **Page** | Full viewport | Yes | Primary task — owns the full screen |
| **Drawer** | Slides from edge, background dimmed | Yes | Secondary focus — context behind it still visible |
| **Modal** | Center + backdrop blur/dark | No | Interruption — decision or quick action required |

Panel (split-view master-detail) is a **layout concern**, not a managed surface. Each app defines its own panel architecture via nested routes and layout components. The Surface Manager does not control panels.

Surfaces can be **stacked**. A modal can open on top of a drawer. Each surface in the stack gets a progressively higher `z-index`. The URL reflects the topmost surface.

---

## Surface registry

Features declare which surfaces they participate in. The app assembles all feature declarations into a single registry — the only file that knows which features exist.

### Per-feature declaration

`path` is a function from props to a URI string. Whether a surface gets a `path` is independent of its visual type (drawer vs modal). The decision is about the **feature's semantics**, not its appearance:

| Has `path` | No `path` |
|---|---|
| Full feature page — create, edit, detail | Ephemeral interruption — confirm, alert, short destructive dialog |
| Worth sharing, bookmarking, or restoring on refresh | The context behind it matters more than the surface itself |
| Makes sense as a standalone page if opened directly | Not a navigable state |

A create invoice form in a center modal popup is just as worth a URL as the same form in a side drawer. The `path` captures the state; the `surface` type controls only the visual treatment.

```ts
// features/invoices/surfaces.ts
import { lazy } from 'react';
import type { SurfaceRegistrations } from '@/providers/SurfaceProvider';

export const invoiceSurfaces = {
  'invoice-list': {
    surface:   'page',
    path:      () => '/invoices',
    component: lazy(() => import('./pages/InvoicesPage')),
  },
  'invoice-detail': {
    surface:   'drawer',
    path:      (p: { id: string }) => `/invoices/${p.id}`,
    component: lazy(() => import('./pages/InvoiceDetailPage')),
  },
  'invoice-create': {
    surface:   'modal',
    path:      () => '/invoices/new',   // ← full feature page — gets a URL
    component: lazy(() => import('./pages/InvoiceCreatePage')),
  },
  'invoice-delete-confirm': {
    surface:   'modal',
    // No path — ephemeral confirm dialog, not a navigable state
    component: lazy(() => import('./pages/InvoiceDeleteConfirmPage')),
  },
} satisfies SurfaceRegistrations;
```

### App-level assembly

```ts
// src/app/surface-registry.ts
import { invoiceSurfaces }  from '@/features/invoices/surfaces';
import { settingsSurfaces } from '@/features/settings/surfaces';
import { clientSurfaces }   from '@/features/clients/surfaces';

export const surfaceRegistry = {
  ...invoiceSurfaces,
  ...settingsSurfaces,
  ...clientSurfaces,
} as const;

export type SurfaceId = keyof typeof surfaceRegistry;
```

No feature imports another feature. The registry is the only join point.

---

## Surface store

```ts
// src/providers/SurfaceProvider.tsx
import { create } from 'zustand';

export type SurfaceType = 'page' | 'drawer' | 'modal';

export type SurfaceRegistration = {
  surface:    SurfaceType;
  path?:      (props: Record<string, unknown>) => string;
  component:  React.LazyExoticComponent<React.ComponentType<any>>;
};

export type SurfaceRegistrations = Record<string, SurfaceRegistration>;

type ActiveSurface = SurfaceRegistration & {
  id:    string;
  props: Record<string, unknown>;
};

type SurfaceState = {
  registry:  SurfaceRegistrations;
  stack:     ActiveSurface[];
  navigate?: (path: string, opts: { state: unknown }) => void;

  init:      (registry: SurfaceRegistrations, navigate: SurfaceState['navigate']) => void;
  open:      (id: string, props?: Record<string, unknown>) => void;
  close:     (id: string) => void;
  closeTop:  () => void;
  closeAll:  () => void;
};

export const useSurfaceStore = create<SurfaceState>((set, get) => ({
  registry:  {},
  stack:     [],
  navigate:  undefined,

  init: (registry, navigate) => set({ registry, navigate }),

  open: (id, props = {}) => {
    const { registry, stack, navigate } = get();
    const registration = registry[id];

    if (!registration) {
      if (import.meta.env.DEV) console.warn(`[SurfaceManager] "${id}" is not registered.`);
      return;
    }

    // If already open, bring to top with updated props
    const isOpen = stack.some((s) => s.id === id);
    if (isOpen) {
      set((state) => ({
        stack: [
          ...state.stack.filter((s) => s.id !== id),
          { id, ...registration, props },
        ],
      }));
      return;
    }

    // URI-enabled surface: navigate so the URL reflects the new surface
    if (registration.path && navigate) {
      const path           = registration.path(props);
      const currentLocation = window.location;
      navigate(path, {
        state: {
          surface:    registration.surface,
          background: { pathname: currentLocation.pathname, search: currentLocation.search },
        },
      });
      // The router renders the surface — store tracks it for stack management
    }

    set((state) => ({
      stack: [...state.stack, { id, ...registration, props }],
    }));
  },

  close:    (id) => set((state) => ({ stack: state.stack.filter((s) => s.id !== id) })),
  closeTop: ()   => set((state) => ({ stack: state.stack.slice(0, -1) })),
  closeAll: ()   => set({ stack: [] }),
}));
```

---

## `SurfaceProvider` and `SurfaceRenderer`

The provider initializes the registry and wires in `useNavigate`. The renderer reads the stack and mounts each active overlay surface in a portal at `document.body`.

```tsx
// src/providers/SurfaceProvider.tsx  (continued)
import { createContext, useContext, useState, useEffect, Suspense } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate }  from 'react-router-dom';
import { surfaceRegistry } from '@/app/surface-registry';
import { DrawerSurface }   from '@/components/surfaces/DrawerSurface';
import { ModalSurface }    from '@/components/surfaces/ModalSurface';

// --- Surface props context ---
export const SurfacePropsContext = createContext<Record<string, unknown>>({});

// --- Surface header context ---
type SurfaceHeaderValue = {
  setTitle:   (title: string) => void;
  setActions: (actions: React.ReactNode) => void;
};
export const SurfaceHeaderContext = createContext<SurfaceHeaderValue | null>(null);

// --- Surface shell map ---
type SurfaceShellProps = {
  onClose:  () => void;
  zIndex:   number;
  children: React.ReactNode;
};

const SURFACE_SHELLS: Record<SurfaceType, React.ComponentType<SurfaceShellProps>> = {
  page:   ({ children }) => <>{children}</>,
  drawer: DrawerSurface,
  modal:  ModalSurface,
};

// --- Renderer (non-routed overlays: modals, local drawers) ---
function SurfaceRenderer() {
  const stack = useSurfaceStore((s) => s.stack);
  const close = useSurfaceStore((s) => s.close);

  // Only render surfaces not handled by the router (no path = state-only)
  const stateOverlays = stack.filter((s) => s.surface !== 'page' && !s.path);
  if (stateOverlays.length === 0) return null;

  return createPortal(
    <>
      {stateOverlays.map((entry, index) => {
        const Shell     = SURFACE_SHELLS[entry.surface];
        const Component = entry.component;

        return (
          <Shell
            key={entry.id}
            onClose={() => close(entry.id)}
            zIndex={50 + index * 10}
          >
            <SurfacePropsContext.Provider value={entry.props}>
              <Suspense fallback={<SurfaceSpinner />}>
                <Component />
              </Suspense>
            </SurfacePropsContext.Provider>
          </Shell>
        );
      })}
    </>,
    document.body,
  );
}

function SurfaceSpinner() {
  return (
    <div className="flex h-32 items-center justify-center">
      <span className="text-muted-foreground text-sm">Loading…</span>
    </div>
  );
}

// --- Provider ---
export function SurfaceProvider({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const init     = useSurfaceStore((s) => s.init);

  useEffect(() => {
    init(surfaceRegistry, navigate);
  }, []);

  return (
    <>
      {children}
      <SurfaceRenderer />
    </>
  );
}
```

---

## Router integration — URI-enabled surfaces

URI-enabled surfaces (those with a `path`) use React Router's background-location pattern. The router renders the background page AND the surface simultaneously — the background stays visible behind the surface.

```tsx
// src/app/AppRoutes.tsx
import { useLocation, Routes, Route } from 'react-router-dom';
import { DrawerSurface } from '@/components/surfaces/DrawerSurface';
import { useSurfaceStore } from '@/providers/SurfaceProvider';

type SurfaceLocationState = {
  surface?:    SurfaceType;
  background?: { pathname: string; search: string };
};

export function AppRoutes() {
  const location              = useLocation();
  const state                 = (location.state ?? {}) as SurfaceLocationState;
  const backgroundLocation    = state.background;
  const activeSurfaceType     = state.surface;

  return (
    <>
      {/* Background routes — always render the underlying page */}
      <Routes location={backgroundLocation ? { ...location, ...backgroundLocation } : location}>
        <Route element={<AuthenticatedLayout />}>
          <Route path="/invoices"         element={<InvoicesPage />} />
          <Route path="/invoices/:id"     element={<InvoiceDetailPage />} />
          <Route path="/settings"         element={<SettingsPage />} />
          {/* ... all routes */}
        </Route>
      </Routes>

      {/* Surface routes — only when there is a background */}
      {backgroundLocation && activeSurfaceType && (() => {
        const Shell = SURFACE_SHELLS[activeSurfaceType];
        const close = useSurfaceStore.getState().closeTop;

        return (
          <Shell onClose={close} zIndex={50}>
            <Routes location={location}>
              <Route path="/invoices/:id" element={<InvoiceDetailPage />} />
              {/* all routes that can appear in a surface */}
            </Routes>
          </Shell>
        );
      })()}
    </>
  );
}
```

**Direct navigation to a surface URL** (e.g. `/invoices/123` opened in a new tab) renders the feature as a full page — no background, no surface chrome. The feature page works identically in both contexts.

---

## `useSurface()` — the feature-facing API

```ts
// src/hooks/use-surface.ts
import { useSurfaceStore } from '@/providers/SurfaceProvider';

export function useSurface() {
  const open     = useSurfaceStore((s) => s.open);
  const close    = useSurfaceStore((s) => s.close);
  const closeTop = useSurfaceStore((s) => s.closeTop);
  const closeAll = useSurfaceStore((s) => s.closeAll);
  const stack    = useSurfaceStore((s) => s.stack);

  return {
    open,
    close,
    closeTop,
    closeAll,
    isOpen: (id: string) => stack.some((s) => s.id === id),
  };
}
```

Feature usage — no imports of other features, no knowledge of surface type or URL:

```tsx
// features/invoices/components/InvoiceRow.tsx
import { useSurface } from '@/hooks/use-surface';

export function InvoiceRow({ invoice }: { invoice: Invoice }) {
  const surface = useSurface();

  return (
    <tr>
      <td>{invoice.number}</td>
      <td>
        <button onClick={() => surface.open('invoice-detail', { id: invoice.id })}>
          View
        </button>
        <button onClick={() => surface.open('invoice-delete-confirm', { id: invoice.id })}>
          Delete
        </button>
      </td>
    </tr>
  );
}
```

---

## `useSurfaceProps()` — reading props inside a surface-opened page

Pages opened via the surface manager receive their input through `SurfacePropsContext`. Pages opened via direct URL navigation read from `useParams()`. A page that supports both declares both sources clearly.

```ts
// src/hooks/use-surface-props.ts
import { useContext } from 'react';
import { SurfacePropsContext } from '@/providers/SurfaceProvider';

export function useSurfaceProps<T extends Record<string, unknown>>(): Partial<T> {
  return useContext(SurfacePropsContext) as Partial<T>;
}
```

```tsx
// features/invoices/pages/InvoiceDetailPage.tsx
export function InvoiceDetailPage() {
  const { id: routeId }       = useParams();
  const { id: surfaceId }     = useSurfaceProps<{ id: InvoiceId }>();
  const id                    = (routeId ?? surfaceId) as InvoiceId;

  return (
    <InvoiceDetailProvider id={id}>
      <InvoiceDetailView />
    </InvoiceDetailProvider>
  );
}
```

---

## `useSurfaceHeader()` — dynamic titles and actions

Feature pages set their own surface header after data loads. If `useSurfaceHeader()` returns `null`, the page is rendering as a full route — use `<title>` or the router's meta system instead.

```ts
// src/hooks/use-surface-header.ts
import { useContext } from 'react';
import { SurfaceHeaderContext } from '@/providers/SurfaceProvider';

export function useSurfaceHeader() {
  return useContext(SurfaceHeaderContext);
}
```

```tsx
// features/invoices/pages/InvoiceDetailPage.tsx  (continued)
export function InvoiceDetailPage() {
  const { id: routeId }   = useParams();
  const { id: surfaceId } = useSurfaceProps<{ id: InvoiceId }>();
  const id                = (routeId ?? surfaceId) as InvoiceId;
  const surfaceHeader     = useSurfaceHeader();
  const { invoice }       = useInvoiceDetailContext();

  useEffect(() => {
    if (!invoice) return;
    surfaceHeader?.setTitle(`Invoice #${invoice.number}`);
    surfaceHeader?.setActions(<DeleteInvoiceButton id={invoice.id} />);
  }, [invoice]);

  return <InvoiceDetailView />;
}
```

---

## Optimistic behavior + surface recovery

When an optimistic mutation fails, the controller rolls back both the **data** (via TanStack Query cache) and the **surface stack** (via the surface manager). The action hook stays data-only. The controller orchestrates both.

### Optimistic create — surface transition on failure

```tsx
// features/invoices/controllers/use-invoice-list.controller.ts
export function useInvoiceListController() {
  const surface      = useSurface();
  const createAction = useCreateInvoice();

  const handleCreate = useCallback((input: CreateInvoiceInput) => {
    // Optimistic: immediately open the detail surface
    surface.open('invoice-detail', { id: input.client_id as InvoiceId });

    createAction.createInvoice(input, {
      onSuccess: () => {
        // Surface stays open — the detail page is now showing real data
      },
      onError: () => {
        // Roll back: close the detail surface, reopen create with input preserved
        surface.close('invoice-detail');
        surface.open('invoice-create', { prefill: input });
      },
    });
  }, [surface, createAction]);

  return { handleCreate };
}
```

The user is never stranded. On failure they land back in the create surface with their input intact — the same recovery principle from [08_hooks.md](08_hooks.md), applied at the surface level.

### Recovery principle

| What failed | Data recovery | Surface recovery |
|---|---|---|
| Create (modal → detail drawer) | Cache rolls back via `onMutate` snapshot | `surface.close(detail)` + `surface.open(create, { prefill })` |
| Update (form in drawer) | RHF preserves input; form stays open | Drawer never closes — no surface action needed |
| Delete (modal confirm) | Cache rolls back | `surface.close(confirm)` — user returns to drawer/page |

The controller decides the surface recovery. The action hook never calls `surface.*`.

---

## URI as trace

Because every drawer surface produces a real URL, the browser's history stack is the trace of the user's path through the application. No additional instrumentation is required.

```
/invoices                 → user lands on list
/invoices/123             → user opens invoice detail (drawer)
/invoices/123             → user closes drawer (navigate(-1) → back to /invoices)
/invoices/456             → user opens a different invoice
```

To **restore any state**: navigate to the URL. The router renders the background page and the surface in the correct configuration.

To **share a state**: copy the URL. The recipient opens to the same surface stack.

To **replay a session programmatically**, record `surface.open()` calls with their props and timestamps:

```ts
// src/lib/surface-trace.ts
type TraceEntry = {
  surfaceId: string;
  props:     Record<string, unknown>;
  timestamp: number;
};

const _trace: TraceEntry[] = [];

export const surfaceTrace = {
  record: (surfaceId: string, props: Record<string, unknown>) => {
    _trace.push({ surfaceId, props, timestamp: Date.now() });
  },
  get:    () => [..._trace],
  replay: (trace: TraceEntry[], surface: ReturnType<typeof useSurface>) => {
    trace.forEach((entry) => surface.open(entry.surfaceId, entry.props));
  },
  clear:  () => _trace.splice(0),
};
```

Wire recording into the store's `open` action during development or when session recording is needed. Replay by iterating the trace and calling `surface.open()` in sequence.

---

## Surface components

### `DrawerSurface`

Slides from the right on desktop, from the bottom on mobile via `BreakpointProvider`. Animates in/out. Provides `SurfaceHeaderContext`.

```tsx
// src/components/surfaces/DrawerSurface.tsx
import { useState, useEffect }     from 'react';
import { useBreakpoint }           from '@/providers/BreakpointProvider';
import { SurfaceHeaderContext }    from '@/providers/SurfaceProvider';
import { cn } from '@/lib/utils';

type Props = { onClose: () => void; zIndex: number; children: React.ReactNode };

export function DrawerSurface({ onClose, zIndex, children }: Props) {
  const { isMobile }          = useBreakpoint();
  const [title,   setTitle]   = useState('');
  const [actions, setActions] = useState<React.ReactNode>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => { requestAnimationFrame(() => setVisible(true)); }, []);

  const handleClose = () => {
    setVisible(false);
    setTimeout(onClose, 300);
  };

  return (
    <SurfaceHeaderContext.Provider value={{ setTitle, setActions }}>
      <div
        className={cn(
          'fixed inset-0 bg-black/40 transition-opacity duration-300',
          visible ? 'opacity-100' : 'opacity-0',
        )}
        style={{ zIndex }}
        onClick={handleClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="surface-drawer-title"
        className={cn(
          'fixed bg-background shadow-xl flex flex-col transition-transform duration-300',
          isMobile
            ? 'inset-x-0 bottom-0 rounded-t-2xl max-h-[90dvh]'
            : 'right-0 top-0 h-full w-[480px] border-l',
          visible
            ? 'translate-x-0 translate-y-0'
            : isMobile ? 'translate-y-full' : 'translate-x-full',
        )}
        style={{ zIndex: zIndex + 1 }}
      >
        <header className="flex items-center justify-between px-6 py-4 border-b flex-shrink-0">
          <h2 id="surface-drawer-title" className="text-lg font-semibold truncate">{title}</h2>
          <div className="flex items-center gap-2">
            {actions}
            <button onClick={handleClose} aria-label="Close" className="rounded-md p-1 hover:bg-muted">✕</button>
          </div>
        </header>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </div>
    </SurfaceHeaderContext.Provider>
  );
}
```

### `ModalSurface`

Centered with backdrop blur. Closes on `Escape` and backdrop click. No URL change.

```tsx
// src/components/surfaces/ModalSurface.tsx
import { useState, useEffect }  from 'react';
import { SurfaceHeaderContext } from '@/providers/SurfaceProvider';
import { cn } from '@/lib/utils';

type Props = { onClose: () => void; zIndex: number; children: React.ReactNode };

export function ModalSurface({ onClose, zIndex, children }: Props) {
  const [title,   setTitle]   = useState('');
  const [actions, setActions] = useState<React.ReactNode>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => { requestAnimationFrame(() => setVisible(true)); }, []);

  const handleClose = () => {
    setVisible(false);
    setTimeout(onClose, 200);
  };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') handleClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);

  return (
    <SurfaceHeaderContext.Provider value={{ setTitle, setActions }}>
      <div
        className={cn(
          'fixed inset-0 bg-black/50 backdrop-blur-sm transition-opacity duration-200',
          visible ? 'opacity-100' : 'opacity-0',
        )}
        style={{ zIndex }}
        onClick={handleClose}
        aria-hidden="true"
      />
      <div
        className={cn(
          'fixed inset-0 flex items-center justify-center p-4 transition-all duration-200',
          visible ? 'opacity-100 scale-100' : 'opacity-0 scale-95',
        )}
        style={{ zIndex: zIndex + 1 }}
      >
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="surface-modal-title"
          className="bg-background rounded-xl shadow-2xl w-full max-w-lg max-h-[85dvh] flex flex-col"
          onClick={(e) => e.stopPropagation()}
        >
          <header className="flex items-center justify-between px-6 py-4 border-b flex-shrink-0">
            <h2 id="surface-modal-title" className="text-lg font-semibold">{title}</h2>
            <div className="flex items-center gap-2">
              {actions}
              <button onClick={handleClose} aria-label="Close" className="rounded-md p-1 hover:bg-muted">✕</button>
            </div>
          </header>
          <div className="flex-1 overflow-y-auto p-6">{children}</div>
        </div>
      </div>
    </SurfaceHeaderContext.Provider>
  );
}
```

---

## Surface stacking

Stacking is automatic. Each surface in the stack gets a progressively higher `z-index` (+10 per layer). URI-enabled surfaces push a new history entry; state-only surfaces (modals without `path`) do not.

```tsx
// Open invoice detail in a drawer (URL: /invoices/123)
surface.open('invoice-detail', { id: invoice.id });

// Open a delete confirmation modal on top (URL unchanged)
surface.open('invoice-delete-confirm', { id: invoice.id });

// Stack: [drawer(invoice-detail, z=50), modal(invoice-delete-confirm, z=60)]

surface.closeTop();  // closes modal, drawer remains
surface.closeAll();  // closes everything
```

---

## File structure

```
src/
  app/
    surface-registry.ts         ← assembles all feature surface declarations
    AppRoutes.tsx               ← router integration for URI-enabled surfaces
  providers/
    SurfaceProvider.tsx         ← store, renderer, contexts (Props, Header)
  components/
    surfaces/
      DrawerSurface.tsx
      ModalSurface.tsx
  hooks/
    use-surface.ts              ← open, close, closeTop, closeAll, isOpen
    use-surface-props.ts        ← read props passed by the manager
    use-surface-header.ts       ← set dynamic title and actions
  lib/
    surface-trace.ts            ← optional: record + replay surface sequences

features/
  invoices/
    surfaces.ts                 ← surface declarations for this feature
    pages/
      InvoicesPage.tsx
      InvoiceDetailPage.tsx     ← reads id from useParams() OR useSurfaceProps()
      InvoiceCreatePage.tsx     ← reads prefill from useSurfaceProps()
```

---

## What the surface manager must NOT do

- **Never import a feature page directly from another feature.** Use `surface.open(id)` — the registry is the only join point.
- **Never register surfaces inside a component or hook.** Registrations live in `features/<f>/surfaces.ts` and are assembled in `src/app/surface-registry.ts` at startup.
- **Never open an unregistered surface id.** Silent failure in production; console warning in development.
- **Never manage overlay open/closed state with local `useState` in a feature component.** All overlay lifecycle lives in the surface store.
- **Never let a surface shell import or reference the feature it contains.** Shells receive `children` only.
- **Never define surface appearance (size, animation, backdrop) inside a feature.** Features declare which *type* they belong to. The surface component owns the appearance.
- **Never call `surface.open()` or `surface.close()` from an action hook.** Action hooks are data-only. Surface transitions belong in the controller.
- **Never open a Page-type surface via the surface manager.** Page surfaces are React Router routes — navigate to them with `useNavigate()` directly.
- **Never decide `path` based on surface type.** A modal can have a URI. A drawer can omit one. The question is always: is this state worth sharing, bookmarking, or restoring? If yes, add `path`. If it's an ephemeral interruption, omit it.
