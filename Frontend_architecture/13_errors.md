# 13 — Error Contract

## Definition

Errors are typed, layered, and surfaced in the appropriate UI context. Network errors, validation errors, and domain errors each have a distinct handling path. An unhandled error in one feature must never crash another feature or the app shell.

---

## Error hierarchy

```
Error
└── ApiRequestError           — all errors from the API client (see 04_api_client.md)
    ├── status: number        — HTTP status code
    ├── code: string          — machine-readable error code from the backend
    ├── message: string       — human-readable message
    └── fieldErrors?          — field-level validation errors (for validation_failed)
```

All thrown errors in the frontend are `ApiRequestError`. Raw `Error` objects are only thrown for programming errors (invalid arguments, unexpected state) — they should never reach the user.

---

## Error boundary placement

Error boundaries are placed at three levels:

```
App
└── RouteErrorBoundary (per route)   ← isolates feature failures
    └── SectionErrorBoundary         ← optional, for independent sections within a page
        └── Component tree
```

Every route has a `RouteErrorBoundary` (see `10_pages.md`). This is non-negotiable.

A `SectionErrorBoundary` is used when a page renders two or more independent data sections and failure of one should not hide the other:

```tsx
export function DashboardPage() {
  return (
    <RouteErrorBoundary>
      <Suspense fallback={<PageSkeleton />}>
        <div className="grid grid-cols-2 gap-4">
          <SectionErrorBoundary fallback={<MetricsSectionError />}>
            <MetricsSection />
          </SectionErrorBoundary>
          <SectionErrorBoundary fallback={<ActivityFeedError />}>
            <ActivityFeed />
          </SectionErrorBoundary>
        </div>
      </Suspense>
    </RouteErrorBoundary>
  );
}
```

---

## Error handling by error type

| Error code | User-visible action | UI pattern |
|---|---|---|
| `validation_failed` | Show field-level errors on the form | Map to `form.setError` (see `09_forms.md`) |
| `forbidden` | "You don't have permission to do this." | Inline error message or toast |
| `not_found` | "This item no longer exists." | Redirect to list or inline message |
| `conflict` | "This change conflicts with a recent update." | Inline error with refresh suggestion |
| `unauthorized` | "Session expired. Please sign in again." | `clearAuth()` + redirect to `/sign-in` |
| `server_error` | "Something went wrong on our end." | Toast + optional retry button |
| `network_error` | "Check your connection and try again." | Toast + retry button |
| `invalid_response` | "Unexpected response from the server." | Log to error reporter, generic toast |

---

## Handling errors in mutation hooks

Mutations catch errors in `onError` and decide how to surface them:

```ts
const { mutate } = useCreateInvoiceMutation();

mutate(data, {
  onError: (err) => {
    if (!(err instanceof ApiRequestError)) {
      showErrorToast('An unexpected error occurred.');
      return;
    }

    switch (err.code) {
      case 'validation_failed':
        // field errors — handled by mapping back onto the form (see 09_forms.md)
        break;
      case 'forbidden':
        showErrorToast('You don't have permission to create invoices.');
        break;
      case 'conflict':
        showErrorToast('This invoice number already exists.');
        break;
      default:
        showErrorToast(err.message);
    }
  },
});
```

---

## Handling errors in query hooks

Query errors are handled in the component using TanStack Query's error state, not try/catch:

```tsx
const { data, isPending, isError, error } = useInvoiceQuery(id);

if (isError) {
  if (error instanceof ApiRequestError && error.code === 'not_found') {
    return <Navigate to={ROUTES.invoices} replace />;
  }
  return <QueryErrorFallback error={error} />;
}
```

For `useSuspenseQuery`, errors are caught by the nearest `RouteErrorBoundary`. The error boundary's `componentDidCatch` logs and the fallback renders.

---

## Error fallback components

```tsx
// src/components/ui/ErrorFallback.tsx
type ErrorFallbackProps = {
  error: Error;
  onReset: () => void;
};

export function ErrorFallback({ error, onReset }: ErrorFallbackProps) {
  const message =
    error instanceof ApiRequestError && error.code !== 'server_error'
      ? error.message
      : 'Something went wrong. Please try again.';

  return (
    <div role="alert" className="flex flex-col items-center gap-4 p-8 text-center">
      <p className="text-sm text-gray-600">{message}</p>
      <button onClick={onReset} className="text-sm text-blue-600 underline">
        Try again
      </button>
    </div>
  );
}
```

---

## Error reporting

Every `RouteErrorBoundary` reports errors to the error reporting service (Sentry or equivalent):

```tsx
componentDidCatch(error: Error, info: ErrorInfo) {
  reportError(error, { componentStack: info.componentStack });
}
```

`reportError` is defined in `src/lib/error-reporter.ts` and wraps the Sentry SDK. No component imports Sentry directly.

---

## What error handling must NOT do

- **Never swallow errors silently.** An error that is caught must either be shown to the user or logged to the error reporter.
- **Never show raw error messages from the API to the user without mapping.** Always map error codes to user-friendly messages.
- **Never skip the error boundary at route level.** Every route must be wrapped in `RouteErrorBoundary`.
- **Never use `window.alert()` or `console.error()` to surface errors to users.** Use the toast notification system (see `20_notifications.md`).
- **Never retry a mutation automatically on error.** Mutations must be explicitly retried by user action.
- **Never catch errors inside a component render body** — that pattern prevents error boundaries from catching them. Let errors propagate to the nearest boundary.
