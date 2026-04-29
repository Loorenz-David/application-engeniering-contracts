# 27 — Responsive Layout Contract

## Definition

Responsive design is divided across two levels with a clear boundary between them:

| Level | Concern | Approach |
|---|---|---|
| **Root** | App shell, navigation structure | `BreakpointProvider` + layout components |
| **Feature / component** | Content arrangement within a page | Tailwind responsive classes or `useBreakpoint()` |

The data layer — controllers, action hooks, queries, stores — is **never device-aware**. A controller returns the same data object on a phone and a desktop. Only the UI layer branches.

---

## The CSS-first rule

Tailwind responsive prefixes (`sm:`, `md:`, `lg:`) are the default. If rearranging elements, changing spacing, or showing/hiding is sufficient, use CSS only — no JavaScript, no hook, no conditional render.

```tsx
// Correct — pure CSS, no DOM overhead
<div className="flex flex-col md:flex-row gap-4">
  <Sidebar />
  <main className="flex-1" />
</div>

// Wrong — unnecessary JS for something CSS handles
const { isMobile } = useBreakpoint();
return isMobile ? <StackedLayout /> : <SideLayout />;
```

Use `useBreakpoint()` only when the two layouts are **structurally different component trees** that are expensive to keep in the DOM simultaneously, or when the behavior (not just appearance) changes.

---

## `BreakpointProvider` — single source of truth

One `matchMedia` listener runs at the app root. Every component that calls `useBreakpoint()` reads from the same context value — they all update together on the same tick when the viewport crosses a breakpoint.

```tsx
// src/providers/BreakpointProvider.tsx
import { createContext, useContext, useState, useEffect } from 'react';

// Breakpoints match Tailwind's defaults — update QUERIES here if the project overrides them
const QUERIES = {
  tablet:  '(min-width: 768px)',   // md
  desktop: '(min-width: 1024px)',  // lg
} as const;

type Breakpoint = 'mobile' | 'tablet' | 'desktop';

type BreakpointValue = {
  breakpoint: Breakpoint;
  isMobile:   boolean;
  isTablet:   boolean;
  isDesktop:  boolean;
};

function getBreakpoint(): Breakpoint {
  if (window.matchMedia(QUERIES.desktop).matches) return 'desktop';
  if (window.matchMedia(QUERIES.tablet).matches)  return 'tablet';
  return 'mobile';
}

const BreakpointContext = createContext<BreakpointValue>({
  breakpoint: 'desktop',
  isMobile:   false,
  isTablet:   false,
  isDesktop:  true,
});

export function BreakpointProvider({ children }: { children: React.ReactNode }) {
  const [bp, setBp] = useState<Breakpoint>(
    typeof window !== 'undefined' ? getBreakpoint() : 'desktop',
  );

  useEffect(() => {
    const desktop = window.matchMedia(QUERIES.desktop);
    const tablet  = window.matchMedia(QUERIES.tablet);
    const update  = () => setBp(getBreakpoint());

    desktop.addEventListener('change', update);
    tablet.addEventListener('change', update);
    return () => {
      desktop.removeEventListener('change', update);
      tablet.removeEventListener('change', update);
    };
  }, []);

  const value: BreakpointValue = {
    breakpoint: bp,
    isMobile:   bp === 'mobile',
    isTablet:   bp === 'tablet',
    isDesktop:  bp === 'desktop',
  };

  return (
    <BreakpointContext.Provider value={value}>
      {children}
    </BreakpointContext.Provider>
  );
}

export function useBreakpoint(): BreakpointValue {
  return useContext(BreakpointContext);
}
```

Mount it once at the app root, outside the router:

```tsx
// src/app/providers.tsx
import { BreakpointProvider } from '@/providers/BreakpointProvider';

export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <BreakpointProvider>
      <QueryClientProvider client={queryClient}>
        <NotificationProvider config={notificationConfig}>
          {children}
        </NotificationProvider>
      </QueryClientProvider>
    </BreakpointProvider>
  );
}
```

All components import from the same path — the implementation detail (context vs standalone) is invisible to consumers:

```ts
import { useBreakpoint } from '@/providers/BreakpointProvider';
```

---

## Root concern — app shell layout

The app shell (navigation, layout frame) is handled by route-level layout components. These wrap the authenticated portion of the app and are defined in the router. The shell switches navigation pattern based on screen size.

```tsx
// src/app/layouts/AuthenticatedLayout.tsx
import { Outlet } from 'react-router-dom';
import { Sidebar }    from '@/components/shell/Sidebar';
import { TopBar }     from '@/components/shell/TopBar';
import { BottomNav }  from '@/components/shell/BottomNav';

export function AuthenticatedLayout() {
  return (
    <div className="flex h-dvh overflow-hidden bg-background">

      {/* Desktop: persistent sidebar — hidden on mobile */}
      <Sidebar className="hidden md:flex w-64 flex-shrink-0 border-r" />

      <div className="flex flex-col flex-1 min-w-0">

        {/* Mobile: top bar with hamburger — hidden on desktop */}
        <TopBar className="md:hidden border-b" />

        {/* Page content — scrolls independently */}
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>

        {/* Mobile: bottom tab bar — hidden on desktop */}
        <BottomNav className="md:hidden border-t" />

      </div>
    </div>
  );
}
```

This is a CSS-only shell switch — both navigation components are in the DOM, but only one is visible at a time. For very heavy navigation trees (many items, complex rendering), use `useBreakpoint()` to conditionally mount them instead.

The layout component renders in the router as a parent route:

```tsx
// src/app/router.tsx
const router = createBrowserRouter([
  {
    element: <AuthenticatedLayout />,   // shell wraps all protected routes
    children: [
      { path: ROUTES.invoices, element: <InvoicesPage /> },
      { path: ROUTES.settings, element: <SettingsPage /> },
    ],
  },
]);
```

---

## Feature concern — content within a page

Within a feature, each component handles its own responsive arrangement. The controller and context are identical on every device.

### Table → card list (common pattern)

```tsx
// features/invoices/components/InvoiceListView.tsx
import { useBreakpoint } from '@/providers/BreakpointProvider';
import { useInvoiceListContext } from '../providers/InvoiceListProvider';
import { InvoiceTable }    from './InvoiceTable';
import { InvoiceCardList } from './InvoiceCardList';

export function InvoiceListView() {
  const { isMobile } = useBreakpoint();
  const ctx = useInvoiceListContext();

  // Different component trees — not just rearrangement — justify useBreakpoint
  return isMobile
    ? <InvoiceCardList invoices={ctx.invoices} onDelete={ctx.deleteInvoice} />
    : <InvoiceTable    invoices={ctx.invoices} onDelete={ctx.deleteInvoice} />;
}
```

Both `InvoiceTable` and `InvoiceCardList` receive the same props from the same context. The controller does not know which one rendered.

### Form layout (pure CSS)

```tsx
// Simple rearrangement — Tailwind only, no JS
<form className="grid grid-cols-1 md:grid-cols-2 gap-4">
  <Field name="first_name" />
  <Field name="last_name" />
  <Field name="email" className="md:col-span-2" />
</form>
```

---

## Shared primitives — built-in responsiveness

UI primitives that behave differently across devices encapsulate the breakpoint logic internally. Callers use a single component — they never branch on device in the feature layer. Structural enter/exit animation for dialogs, drawers, and sheets follows [31_animations.md](31_animations.md).

### Dialog: modal on desktop, bottom sheet on mobile

```tsx
// src/components/ui/Dialog.tsx
import { m } from 'framer-motion';
import { useBreakpoint } from '@/providers/BreakpointProvider';
import { transitions } from '@/lib/animation';

type DialogProps = {
  open:     boolean;
  onClose:  () => void;
  title:    string;
  children: React.ReactNode;
};

export function Dialog({ open, onClose, title, children }: DialogProps) {
  const { isMobile } = useBreakpoint();

  if (isMobile) {
    return (
      <m.div
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        className="fixed inset-x-0 bottom-0 z-50 rounded-t-2xl bg-background p-6 shadow-lg"
        initial={{ y: '100%' }}
        animate={{ y: open ? 0 : '100%' }}
        transition={transitions.surface}
      >
        <h2 id="dialog-title" className="text-lg font-semibold mb-4">{title}</h2>
        {children}
      </m.div>
    );
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="dialog-title"
      className={cn(
        'fixed inset-0 z-50 flex items-center justify-center',
        open ? 'pointer-events-auto' : 'pointer-events-none opacity-0',
      )}
    >
      <div className="bg-background rounded-xl shadow-xl w-full max-w-lg p-6">
        <h2 id="dialog-title" className="text-lg font-semibold mb-4">{title}</h2>
        {children}
      </div>
    </div>
  );
}
```

Feature components use `<Dialog>` without caring about device type:

```tsx
// features/invoices/components/DeleteInvoiceDialog.tsx
<Dialog open={isOpen} onClose={onClose} title="Delete invoice?">
  {/* same content on mobile and desktop */}
</Dialog>
```

### Drawer: side panel on desktop, full-screen on mobile

```tsx
// src/components/ui/Drawer.tsx
import { m } from 'framer-motion';
import { useBreakpoint } from '@/providers/BreakpointProvider';
import { transitions } from '@/lib/animation';

export function Drawer({ open, onClose, title, children }: DrawerProps) {
  const { isMobile } = useBreakpoint();

  return (
    <m.div
      className={cn(
        'fixed z-40 bg-background shadow-xl',
        isMobile
          ? 'inset-0'                                    // full screen on mobile
          : 'right-0 top-0 h-full w-[480px] border-l',  // side panel on desktop
      )}
      initial={isMobile ? { y: '100%' } : { x: '100%' }}
      animate={open ? { x: 0, y: 0 } : isMobile ? { y: '100%' } : { x: '100%' }}
      transition={transitions.surface}
    >
      {children}
    </m.div>
  );
}
```

---

## Testing

In tests, wrap the component under test with a `BreakpointContext.Provider` directly — no media query mocking needed:

```tsx
// Override the context value for a specific test
import { BreakpointContext } from '@/providers/BreakpointProvider';

const mobileValue = { breakpoint: 'mobile', isMobile: true, isTablet: false, isDesktop: false };

render(
  <BreakpointContext.Provider value={mobileValue}>
    <InvoiceListView />
  </BreakpointContext.Provider>
);

// Assert the card list renders, not the table
expect(screen.getByTestId('invoice-card-list')).toBeInTheDocument();
```

Export `BreakpointContext` from `BreakpointProvider.tsx` for test use only — feature code never imports it directly.

---

## Decision guide

```
Does it only rearrange, resize, or show/hide elements?
  → Tailwind responsive classes only. No JS.

Is it a fundamentally different component tree (table vs cards, sidebar vs bottom nav)?
  → useBreakpoint() from BreakpointProvider context. Conditionally render.

Is it a shared primitive (Dialog, Drawer, Menu)?
  → Encapsulate useBreakpoint() inside the primitive.
    Callers never branch on device.

Is it in the data layer (controller, action hook, query, store)?
  → Never device-aware. Same code on all devices.
```

---

## What responsive design must NOT do

- **Never branch on device in a controller, action hook, query, or store.** The data layer is device-agnostic. Only the component layer renders differently.
- **Never use `useBreakpoint()` for simple rearrangement.** If Tailwind responsive classes solve it, use them — they are zero-JS.
- **Never keep large component trees in the DOM with `hidden` and `block` when one branch is always unnecessary.** Use `useBreakpoint()` + conditional render for heavy components. Use CSS `hidden/block` for lightweight shell elements (nav icons, labels).
- **Never duplicate controller logic for mobile and desktop versions of a feature.** One controller serves both. Branching happens only in the view layer.
- **Never define breakpoints in JavaScript that differ from Tailwind's config.** Keep both in sync — if you override Tailwind's `md` breakpoint, update the `QUERIES` object in `BreakpointProvider.tsx` to match.
- **Never let a feature component import `useBreakpoint()` to decide which *data* to fetch.** Fetch all the data the section needs; the mobile component uses a subset of it.
- **Never call `useBreakpoint()` outside of `BreakpointProvider`.** Mount the provider at the app root before any component that needs it.
- **Never import `BreakpointContext` directly in feature or UI code.** It is exported for tests only — all production code uses `useBreakpoint()`.
