# 30 — Dynamic Loading Contract

## Definition

Dynamic loading is the architecture for loading feature code, surface code, and heavy libraries only when the user can actually reach them.

It is not an optimization trick sprinkled into components. It is a boundary rule:

| Boundary | Loads |
|---|---|
| Route | Page + the feature code imported by that page |
| Surface | Drawer/modal content opened through the Surface Manager |
| Heavy adapter | Large third-party library used by one feature or one operation |
| User intent | Optional preload for a likely next action |

The app shell, auth, router, query client, stores, shared primitives, Framer Motion, and common utilities stay static. Feature-specific and expensive code is split behind route, surface, or adapter boundaries.

---

## Loading boundary rule

```
If a feature is reachable by route, the route loads it.
If a feature is reachable by drawer or modal, the surface loads it.
If a library is used by one feature or one operation, a dynamic adapter loads it.
If code is needed by the app shell or shared UI primitives, it stays static.
```

Do not dynamically import randomly inside leaf components. Dynamic imports belong at ownership boundaries where loading, fallback UI, and error handling are obvious.

---

## Route-level loading

Every page is lazy-loaded from the router. The page then statically imports its feature's public API. Because the page itself is lazy, the feature is pulled into the route chunk instead of the app shell chunk.

```tsx
// src/app/router.tsx
import { createBrowserRouter } from 'react-router-dom';
import { lazyRoute } from '@/lib/lazy-route';
import { ROUTES } from '@/lib/routes';

export const router = createBrowserRouter([
  {
    path: ROUTES.invoices,
    element: lazyRoute(() =>
      import('@/pages/invoices/InvoicesPage').then((m) => ({
        default: m.InvoicesPage,
      })),
    ),
  },
]);
```

The page imports the feature normally:

```tsx
// src/pages/invoices/InvoicesPage.tsx
import { InvoiceListProvider, InvoiceListView } from '@/features/invoices';

export function InvoicesPage() {
  return (
    <RouteErrorBoundary>
      <Suspense fallback={<PageSkeleton />}>
        <InvoiceListProvider>
          <InvoiceListView />
        </InvoiceListProvider>
      </Suspense>
    </RouteErrorBoundary>
  );
}
```

Correct: the router lazy-loads `InvoicesPage`, and `InvoicesPage` pulls `features/invoices` into the invoice route chunk.

Wrong: the router imports `InvoiceListProvider`, `InvoiceListView`, or any feature internal directly. That leaks feature code into the app shell.

---

## `lazyRoute`

Use one helper for route lazy loading so every route has the same fallback and named-export wrapper.

```tsx
// src/lib/lazy-route.tsx
import { lazy, Suspense } from 'react';
import type { ComponentType } from 'react';
import { PageSkeleton } from '@/components/ui/PageSkeleton';
import { RouteErrorBoundary } from '@/components/ui/RouteErrorBoundary';

type LazyComponent = ComponentType<Record<string, never>>;

export function lazyRoute(importer: () => Promise<{ default: LazyComponent }>) {
  const Component = lazy(importer);

  return (
    <RouteErrorBoundary>
      <Suspense fallback={<PageSkeleton />}>
        <Component />
      </Suspense>
    </RouteErrorBoundary>
  );
}
```

If a page needs route params, it reads them inside the page with React Router hooks. Do not pass route params through `lazyRoute`.

The boundary inside `lazyRoute` catches route chunk load failures. Page components still keep their own route/section boundary for errors that happen after the chunk has loaded.

---

## Feature chunk ownership

A feature's `index.ts` remains the public API, but it must stay free of eager side effects and heavy top-level imports.

```ts
// src/features/invoices/index.ts
export { InvoiceListProvider, useInvoiceListContext } from './providers/InvoiceListProvider';
export { InvoiceListView } from './components/InvoiceListView';
export type { Invoice, InvoiceViewModel } from './types';
```

The public API may export providers, top-level views, types, permission keys, and preload functions. It must not initialize SDKs, start subscriptions, read from browser storage, or import heavy libraries at module scope.

Feature chunk rule:

- `pages/<feature>/...` imports from `features/<feature>/index.ts`.
- `src/app/router.tsx` imports page modules only through `lazyRoute`.
- Other features import only from the target feature's `index.ts`.
- Heavy feature-only libraries are hidden behind dynamic adapters, not exported from `index.ts`.

---

## Surface-level loading

Surfaces are dynamic boundaries. Drawer and modal content is registered with a lazy component so expensive forms, validators, file upload widgets, rich editors, and previews are loaded only when the surface opens.

```ts
// features/invoices/surfaces.ts
import { lazy } from 'react';
import type { SurfaceRegistrations } from '@/providers/SurfaceProvider';

export const invoiceSurfaces = {
  'invoice-create': {
    surface: 'drawer',
    path:    () => '/invoices/new',
    component: lazy(() =>
      import('./pages/InvoiceCreatePage').then((m) => ({
        default: m.InvoiceCreatePage,
      })),
    ),
  },
  'invoice-delete-confirm': {
    surface: 'modal',
    component: lazy(() =>
      import('./pages/InvoiceDeleteConfirmPage').then((m) => ({
        default: m.InvoiceDeleteConfirmPage,
      })),
    ),
  },
} satisfies SurfaceRegistrations;
```

Every URI-enabled surface must have a matching route registration for the same path. Lazy loading does not replace routing: the surface registry decides presentation, while the router decides whether the URL can be restored after refresh, direct entry, browser back/forward, or a shared link.

```tsx
// src/app/router.tsx
{
  path: ROUTES.invoiceCreate,
  element: lazyRoute(() =>
    import('@/pages/invoices/InvoiceCreatePage').then((m) => ({
      default: m.InvoiceCreatePage,
    })),
  ),
}
```

Surface fallback UI is not the same as route fallback UI. A drawer uses a surface-sized skeleton; a modal uses a compact modal skeleton. Skeleton structure and shimmer rules are defined in [32_loading_skeletons.md](32_loading_skeletons.md).

```tsx
// SurfaceRenderer
<Suspense fallback={<SurfaceSkeleton surface={entry.surface} />}>
  <Component />
</Suspense>
```

---

## Heavy library adapters

Any large third-party library used by one feature, one surface, or one operation is loaded through a dynamic adapter.

```ts
// src/lib/dynamic-libs/pdf.ts
export async function loadPdfLib() {
  return import('pdf-lib');
}
```

```ts
// features/invoices/actions/use-export-invoice-pdf.ts
import { loadPdfLib } from '@/lib/dynamic-libs/pdf';

export function useExportInvoicePdf() {
  async function exportPdf(invoice: InvoiceViewModel) {
    const { PDFDocument } = await loadPdfLib();
    const pdf = await PDFDocument.create();
    // build and download file
  }

  return { exportPdf };
}
```

Good candidates for dynamic library adapters:

- rich text editors
- PDF generation or parsing
- spreadsheet import/export
- charting libraries used by one dashboard
- maps
- image crop/compression tools
- code editors
- drag-and-drop builders
- markdown renderers
- payment SDKs
- analytics SDKs
- file preview libraries

Do not dynamically import libraries used on every route, such as React, React Router, TanStack Query, Zustand, Framer Motion, shared date utilities, or design-system primitives.

---

## Preloading

Preloading is allowed when the user shows clear intent. It is not a license to load every feature after boot.

Use preload functions owned by the feature:

```ts
// features/invoices/preload.ts
export function preloadInvoiceCreateSurface() {
  return import('./pages/InvoiceCreatePage');
}
```

Export preload functions from the feature public API only when another feature or shell component needs them:

```ts
// features/invoices/index.ts
export { preloadInvoiceCreateSurface } from './preload';
```

Use them on strong intent:

```tsx
<Button
  onPointerEnter={preloadInvoiceCreateSurface}
  onFocus={preloadInvoiceCreateSurface}
  onClick={() => surface.open('invoice-create')}
>
  Create invoice
</Button>
```

Good preload triggers:

- pointer enter or focus on a primary action
- opening a menu that contains a lazy action
- navigating to the step before a heavy step
- idle time after the current route is stable and interactive

Bad preload triggers:

- app startup
- after every sign-in regardless of user intent
- preloading all route siblings
- preloading unauthorized features

---

## Permission-aware loading

Do not preload or render lazy feature code for actions the user cannot access.

```tsx
const { can } = useInvoiceListContext();

return can.create ? (
  <Button
    onPointerEnter={preloadInvoiceCreateSurface}
    onFocus={preloadInvoiceCreateSurface}
    onClick={() => surface.open('invoice-create')}
  >
    Create invoice
  </Button>
) : null;
```

The permission check is still UX only. The backend must enforce authorization when the lazy-loaded surface submits a request.

---

## Loading fallback taxonomy

Use the smallest fallback that matches the boundary:

| Boundary | Fallback |
|---|---|
| Initial app boot | `AppBootSkeleton` or app shell skeleton |
| Route chunk | `PageSkeleton` |
| Route data | `PageSkeleton` or feature-specific page skeleton |
| Drawer surface | `SurfaceSkeleton surface="drawer"` |
| Modal surface | `SurfaceSkeleton surface="modal"` |
| Inline widget | `InlineSkeleton` |
| Button-triggered library operation | Button pending state |

Never use one global spinner for every dynamic import. A route, drawer, modal, and button operation have different layout constraints.

---

## Error handling

A failed dynamic import is a route or surface error, not an empty state.

- Route lazy failures are caught by the route error boundary.
- Surface lazy failures are caught by the surface error boundary.
- Library adapter failures are handled by the action/controller that initiated the operation and surfaced through `notify.error`.

For deploys, a lazy import can fail when the user has an old shell loaded and the server no longer has the old chunk. The app should offer a reload action:

```tsx
<ErrorFallback
  title="This screen could not load."
  actionLabel="Reload"
  onAction={() => window.location.reload()}
/>
```

---

## Bundle review

After adding a feature with a new route, surface, or heavy library:

1. Run the production build.
2. Inspect the bundle report.
3. Confirm the feature code is not in the initial app chunk.
4. Confirm heavy libraries are in their own async chunks or inside the feature chunk that owns them.
5. Record bundle-size justification for dependencies over the threshold in [18_performance.md](18_performance.md).

---

## What dynamic loading must NOT do

- **Never import page components synchronously in the router.** Use `lazyRoute`.
- **Never import feature internals from the router.** The router imports pages only.
- **Never dynamically import shared primitives used across most routes.** Keep common UI static.
- **Never dynamically import inside render without `lazy`, `Suspense`, or an explicit user action.**
- **Never preload all features at app boot.** Preload only on strong user intent or carefully chosen idle moments.
- **Never preload features the user cannot access.** Check permissions first.
- **Never put heavy libraries in a feature `index.ts`.** Use a dynamic adapter or load inside the owning action/surface.
- **Never treat a lazy surface registration as a route replacement.** Every URI-enabled surface still needs a matching route.
- **Never hide dynamic import failures.** Show an error boundary fallback with a reload path.
- **Never use dynamic loading to bypass architecture boundaries.** Lazy code still follows the same feature, provider, controller, and component rules.
