# 10 — Page Component Contract

## Definition

A page component is a thin wrapper that owns its route's data Suspense boundary and post-load error boundary, then delegates all rendering to feature components. Route chunk loading is handled by `lazyRoute` in [30_dynamic_loading.md](30_dynamic_loading.md), which also catches chunk-load failures before the page module exists. Loading fallback structure follows [32_loading_skeletons.md](32_loading_skeletons.md). A page does not contain business logic, API calls, or significant JSX.

---

## Page structure

```tsx
// src/pages/invoices/InvoicesPage.tsx
import { Suspense } from 'react';
import { RouteErrorBoundary } from '@/components/ui/RouteErrorBoundary';
import { InvoiceList } from '@/features/invoices';
import { PageSkeleton } from '@/components/ui/PageSkeleton';

export function InvoicesPage() {
  return (
    <RouteErrorBoundary>
      <Suspense fallback={<PageSkeleton />}>
        <InvoiceList />
      </Suspense>
    </RouteErrorBoundary>
  );
}
```

The page:
1. Wraps the content in a `RouteErrorBoundary` — so errors in this route do not crash the app shell
2. Wraps feature content in `Suspense` — so route data loading shows a reflected skeleton instead of nothing
3. Renders the feature's primary component — all logic lives there

---

## Pages are thin

A page component's entire JSX should fit in under 30 lines. If it does not, logic is leaking from the feature into the page.

```tsx
// Wrong — page doing too much
export function InvoicesPage() {
  const [filters, setFilters] = useState({ status: undefined });
  const { data } = useInvoicesQuery(filters);

  return (
    <div>
      <h1>Invoices</h1>
      <FilterBar filters={filters} onChange={setFilters} />
      {data?.items.map((invoice) => (
        <InvoiceRow key={invoice.id} invoice={invoice} />
      ))}
    </div>
  );
}

// Correct — page delegates to feature
export function InvoicesPage() {
  return (
    <RouteErrorBoundary>
      <Suspense fallback={<PageSkeleton />}>
        <InvoiceList />
      </Suspense>
    </RouteErrorBoundary>
  );
}
```

---

## Loading states with Suspense

Use React's `Suspense` to handle loading states at the route level. TanStack Query integrates with Suspense when `suspense: true` is set:

```ts
// Query hook with suspense enabled
export function useInvoicesQuery(params: ListInvoicesParams) {
  return useSuspenseQuery({
    queryKey: invoiceKeys.list(params),
    queryFn: () => fetchInvoices(params),
  });
}
```

`useSuspenseQuery` throws a Promise when loading, which Suspense catches. The component that calls `useSuspenseQuery` never needs to handle `isPending` — it only renders when data is ready.

Use `useSuspenseQuery` in feature components that are wrapped in a Suspense boundary. Use `useQuery` in components that need to show an inline reflected skeleton or manage the loading state themselves.

---

## Route parameters

Page components read route parameters via React Router hooks and pass them to feature components:

```tsx
// src/pages/invoices/InvoiceDetailPage.tsx
import { useParams } from 'react-router-dom';
import { RouteErrorBoundary } from '@/components/ui/RouteErrorBoundary';
import { InvoiceDetail } from '@/features/invoices';

export function InvoiceDetailPage() {
  const { invoiceId } = useParams<{ invoiceId: string }>();

  if (!invoiceId) return null;  // router should prevent this, but types require handling

  return (
    <RouteErrorBoundary>
      <Suspense fallback={<PageSkeleton />}>
        <InvoiceDetail id={invoiceId as InvoiceId} />
      </Suspense>
    </RouteErrorBoundary>
  );
}
```

Route parameters are always `string | undefined` from React Router. Validate and cast them in the page before passing downstream.

---

## Document title

Each page sets the document title using a shared hook:

```tsx
export function InvoicesPage() {
  useDocumentTitle('Invoices');

  return (
    <RouteErrorBoundary>
      <Suspense fallback={<PageSkeleton />}>
        <InvoiceList />
      </Suspense>
    </RouteErrorBoundary>
  );
}
```

---

## Nested pages

For routes with nested children, use React Router's `<Outlet>`:

```tsx
// src/pages/settings/SettingsPage.tsx
import { Outlet } from 'react-router-dom';
import { SettingsNav } from '@/features/settings';

export function SettingsPage() {
  return (
    <div className="flex gap-8">
      <SettingsNav />
      <main className="flex-1">
        <RouteErrorBoundary>
          <Suspense fallback={<PageSkeleton />}>
            <Outlet />
          </Suspense>
        </RouteErrorBoundary>
      </main>
    </div>
  );
}
```

---

## RouteErrorBoundary component

A minimal error boundary component used at every route boundary:

```tsx
// src/components/ui/RouteErrorBoundary.tsx
import { Component, type ErrorInfo } from 'react';
import { ErrorFallback } from './ErrorFallback';

type Props = { children: React.ReactNode };
type State = { error: Error | null };

export class RouteErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Log to error reporting service (Sentry, etc.)
    reportError(error, info);
  }

  render() {
    if (this.state.error) {
      return <ErrorFallback error={this.state.error} onReset={() => this.setState({ error: null })} />;
    }
    return this.props.children;
  }
}
```

---

## What pages must NOT do

- **Never contain business logic.** Logic belongs in feature hooks.
- **Never call TanStack Query hooks directly.** Pages delegate to feature components that own their data.
- **Never access Zustand stores directly** unless it is to pass a value from global state as a prop to a feature component.
- **Never skip the error boundary.** Every page must be wrapped in `RouteErrorBoundary`.
- **Never skip `Suspense`** when using `useSuspenseQuery`. A missing Suspense boundary causes a runtime error.
- **Never use a generic spinner as the page fallback** when the route structure is known. Use `PageSkeleton` or a feature-specific page skeleton.
- **Never put more than 30 lines of JSX in a page.** If you exceed this, move logic to the feature.
