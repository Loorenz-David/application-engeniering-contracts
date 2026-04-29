# 11 — Routing Contract

## Definition

React Router v6 with the data router API (`createBrowserRouter`) is the routing layer. All routes are defined in one place, all page components are lazy-loaded, and protected routes are enforced by a guard component.

Route-level lazy loading follows [30_dynamic_loading.md](30_dynamic_loading.md). The router imports page modules only; it never imports feature providers, feature components, or feature internals directly.

---

## Route configuration

All routes are declared in `src/app/router.tsx`. No routes are defined anywhere else.

```tsx
// src/app/router.tsx
import { createBrowserRouter, redirect } from 'react-router-dom';
import { RootRoute } from '@/app/RootRoute';
import { AppShell } from '@/components/ui/AppShell';
import { lazyRoute } from '@/lib/lazy-route';
import { ProtectedRoute } from '@/features/auth';
import { GuestRoute } from '@/features/auth';

export const router = createBrowserRouter([
  {
    element: <RootRoute />,
    children: [
      // Auth routes (public only — redirect to app if already signed in)
      {
        element: <GuestRoute />,
        children: [
          {
            path: '/sign-in',
            element: lazyRoute(() =>
              import('@/pages/auth/SignInPage').then((m) => ({ default: m.SignInPage })),
            ),
          },
        ],
      },

      // App routes (require authentication)
      {
        element: <ProtectedRoute />,
        children: [
          {
            element: <AppShell />,
            children: [
              { path: '/', loader: () => redirect('/invoices') },
              {
                path: '/invoices',
                element: lazyRoute(() =>
                  import('@/pages/invoices/InvoicesPage').then((m) => ({
                    default: m.InvoicesPage,
                  })),
                ),
              },
              {
                path: '/invoices/:invoiceId',
                element: lazyRoute(() =>
                  import('@/pages/invoices/InvoiceDetailPage').then((m) => ({
                    default: m.InvoiceDetailPage,
                  })),
                ),
              },
            ],
          },
        ],
      },

      // Catch-all
      {
        path: '*',
        element: lazyRoute(() =>
          import('@/pages/NotFoundPage').then((m) => ({ default: m.NotFoundPage })),
        ),
      },
    ],
  },
]);
```

The root route is where router-aware providers live. `AuthProvider` and `SurfaceProvider` call router hooks, so they must be descendants of `RouterProvider`; global app providers such as `QueryClientProvider`, `MotionConfig`, and `BreakpointProvider` stay outside the router tree.

Routes that can render inside a drawer or modal may be wrapped by the `SurfaceRouteFrame` described in [28_surfaces.md](28_surfaces.md). They are still declared here in `src/app/router.tsx`; surface support must not create a second route registry.

---

## Lazy loading rules

Every page component is lazy-loaded. No synchronous page imports are permitted. Use `lazyRoute` from [30_dynamic_loading.md](30_dynamic_loading.md).

```ts
// Wrong — synchronous import
import { InvoicesPage } from '@/pages/invoices/InvoicesPage';

// Correct — lazy import
element: lazyRoute(() =>
  import('@/pages/invoices/InvoicesPage').then((m) => ({ default: m.InvoicesPage })),
);
```

The `.then((m) => ({ default: m.InvoicesPage }))` pattern handles named exports — `lazy` requires a default export wrapper.

---

## Protected routes

Authentication guards are implemented as layout route components:

```tsx
// src/features/auth/components/ProtectedRoute.tsx
import { Navigate, Outlet } from 'react-router-dom';
import { useAuthStore, selectIsAuthenticated } from '@/store/auth.store';

export function ProtectedRoute() {
  const isAuthenticated = useAuthStore(selectIsAuthenticated);

  if (!isAuthenticated) {
    return <Navigate to="/sign-in" replace />;
  }

  return <Outlet />;
}
```

```tsx
// src/features/auth/components/GuestRoute.tsx
import { Navigate, Outlet } from 'react-router-dom';
import { useAuthStore, selectIsAuthenticated } from '@/store/auth.store';

export function GuestRoute() {
  const isAuthenticated = useAuthStore(selectIsAuthenticated);

  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  return <Outlet />;
}
```

---

## Route paths

Route paths are defined as constants to avoid typos and enable safe navigation:

```ts
// src/lib/routes.ts
export const ROUTES = {
  signIn: '/sign-in',
  invoices: '/invoices',
  invoiceDetail: (id: string) => `/invoices/${id}`,
  settings: '/settings',
  settingsProfile: '/settings/profile',
} as const;
```

Use `ROUTES` everywhere, never raw string paths:

```ts
// Correct
navigate(ROUTES.invoiceDetail(invoice.id));

// Wrong — will drift if the path changes
navigate(`/invoices/${invoice.id}`);
```

---

## Navigation

Use React Router's `useNavigate` hook in components and hooks. Use the `Link` component for anchor-based navigation.

```tsx
// Programmatic navigation — in a hook
const navigate = useNavigate();
const handleSuccess = (id: InvoiceId) => navigate(ROUTES.invoiceDetail(id));

// Link navigation — in a component
<Link to={ROUTES.invoiceDetail(invoice.id)}>View invoice</Link>
```

Never use `window.location.href` for in-app navigation — it causes a full page reload.

---

## Search params

Filter state and pagination live in URL search params, not in component state. This makes them bookmarkable and shareable:

```ts
import { useSearchParams } from 'react-router-dom';

export function useInvoiceFilters() {
  const [searchParams, setSearchParams] = useSearchParams();

  const status = searchParams.get('status') ?? undefined;
  const page = Number(searchParams.get('page') ?? '1');

  const setStatus = (status: string | undefined) =>
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (status) next.set('status', status); else next.delete('status');
      next.set('page', '1');
      return next;
    });

  return { status, page, setStatus };
}
```

---

## Nested layouts

Use layout routes (routes with `<Outlet>` and no `path`) to share UI across sections:

```tsx
// AppShell — shared nav and sidebar
export function AppShell() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
```

---

## What routing must NOT do

- **Never define routes outside of `src/app/router.tsx`.** Dynamic route additions at runtime are not permitted.
- **Never import page components synchronously.** Every page is lazy-loaded.
- **Never import feature providers, components, or internals from the router.** The router imports pages only.
- **Never hardcode path strings.** Use `ROUTES` constants.
- **Never perform authentication checks inside page components.** Auth gates live in `ProtectedRoute` layout routes.
- **Never store filter/pagination state in component state** when it should be in URL search params. Search params are the first choice for shareable UI state.
- **Never use `window.location.href`** for SPA navigation.
