# 05 — Server State Contract

## Definition

Server state is any data that originates from the backend. It is asynchronous, potentially stale, and must be kept synchronized with the server. TanStack Query v5 is the only permitted tool for managing server state. `useState` and Zustand are not used for server state.

All entity IDs in query keys, route params, mutation inputs, and cache updates are public client-facing IDs. They may originate as `client_id` during create, but the frontend never handles the backend's internal database primary key.

---

## The rule

If the data comes from the backend, it lives in TanStack Query. Query hooks live inside `features/<feature>/api/`. Mutation network functions also live in `api/`, but mutation hooks live in `features/<feature>/actions/` because they own optimistic updates and cache reconciliation.

```ts
// Wrong — server data in useState
const [invoices, setInvoices] = useState<Invoice[]>([]);

useEffect(() => {
  fetchInvoices().then(setInvoices);
}, []);

// Correct — server data in TanStack Query
const { data, isPending, isError } = useInvoicesQuery({ page: 1 });
```

---

## Query key factories

Every feature that fetches data defines a query key factory. Query keys are the identity of cached data — consistent key structure enables precise invalidation.

```ts
// src/features/invoices/api/invoice-keys.ts
export const invoiceKeys = {
  all: ['invoices'] as const,
  lists: () => [...invoiceKeys.all, 'list'] as const,
  list: (params: ListInvoicesParams) => [...invoiceKeys.lists(), params] as const,
  details: () => [...invoiceKeys.all, 'detail'] as const,
  detail: (id: InvoiceId) => [...invoiceKeys.details(), id] as const,
};
```

Key factory conventions:
- `all` — matches everything for this entity type (used for broad invalidation)
- `lists()` — matches all list queries
- `list(params)` — matches one specific list query (including its filters/pagination)
- `details()` — matches all detail/single-entity queries
- `detail(id)` — matches one specific entity

If the application can hold data for multiple workspaces in the same browser session, scope keys by workspace:

```ts
export const invoiceKeys = {
  all: (workspaceId: WorkspaceId) => ['workspaces', workspaceId, 'invoices'] as const,
  lists: (workspaceId: WorkspaceId) => [...invoiceKeys.all(workspaceId), 'list'] as const,
  list: (workspaceId: WorkspaceId, params: ListInvoicesParams) =>
    [...invoiceKeys.lists(workspaceId), params] as const,
  details: (workspaceId: WorkspaceId) => [...invoiceKeys.all(workspaceId), 'detail'] as const,
  detail: (workspaceId: WorkspaceId, id: InvoiceId) =>
    [...invoiceKeys.details(workspaceId), id] as const,
};
```

For single-workspace sessions, workspace scoping can be omitted if `queryClient.clear()` runs on sign-out and workspace switch. Never let two users or two workspaces share the same persisted query cache.

---

## Query hooks

One hook per query. The hook lives in `features/<feature>/api/` and is named `use-<entity>.ts` or `use-<entity>-<qualifier>.ts`.

```ts
// src/features/invoices/api/use-invoices.ts
import { useQuery } from '@tanstack/react-query';
import { fetchInvoices, type ListInvoicesParams } from './fetch-invoices';
import { invoiceKeys } from './invoice-keys';

export function useInvoicesQuery(params: ListInvoicesParams = {}) {
  return useQuery({
    queryKey: invoiceKeys.list(params),
    queryFn: () => fetchInvoices(params),
  });
}
```

```ts
// src/features/invoices/api/use-invoice.ts
import { useQuery } from '@tanstack/react-query';
import { fetchInvoice } from './fetch-invoice';
import { invoiceKeys } from './invoice-keys';
import type { InvoiceId } from '@/types/common';

export function useInvoiceQuery(id: InvoiceId) {
  return useQuery({
    queryKey: invoiceKeys.detail(id),
    queryFn: () => fetchInvoice(id),
    enabled: Boolean(id),
  });
}
```

The hook returns the full TanStack Query result object. Callers destructure what they need.

---

## Mutation hooks

Mutation hooks live in `features/<feature>/actions/` as action hooks, not in `api/`. Action hooks are the correct location because they own the full optimistic lifecycle: `onMutate` (snapshot + apply), `onError` (rollback), `onSuccess` (seed caches), `onSettled` (invalidate). See [08_hooks.md](08_hooks.md) for the complete action hook patterns with optimistic updates.

The `api/` layer holds only query hooks and the raw async mutation functions (`createInvoice`, `updateInvoice`, etc.) that action hooks call via `mutationFn`.

---

## Cache invalidation rules

| Operation | What to invalidate |
|---|---|
| Create entity | `invoiceKeys.lists()` — all list queries for that type |
| Update entity | `invoiceKeys.detail(id)` + `invoiceKeys.lists()` |
| Delete entity | `invoiceKeys.detail(id)` + `invoiceKeys.lists()` |
| Bulk operation | `invoiceKeys.all` — everything for that entity type |

Use `queryClient.invalidateQueries` for most cases. Use `queryClient.setQueryData` to seed the cache with data you already have from a mutation response — avoids redundant fetches.

---

## Optimistic updates

Optimistic updates are the **default** for create, update, and delete operations. The UI reflects the change instantly; the server confirms it in the background. If the server rejects it, the UI rolls back automatically.

The pattern has four lifecycle hooks:
1. `onMutate` — snapshot the current cache, apply the optimistic change, return the snapshot as context
2. `onError` — restore the snapshot from context
3. `onSuccess` — seed any caches the optimistic step couldn't fill (e.g. detail cache on create)
4. `onSettled` — always invalidate to reconcile with the server regardless of success or failure

### Optimistic create

Create uses a `client_id` — a UUID generated by the frontend before the request fires. The backend stores it as the entity's public client-facing identifier while keeping the internal database primary key private. This means the public ID is known upfront, so the detail cache can be seeded immediately and navigation to the new entity is instant. See [24_dto.md](24_dto.md) for the full `client_id` convention.

```ts
useMutation({
  mutationFn: createInvoice,

  onMutate: async (input) => {
    await queryClient.cancelQueries({ queryKey: invoiceKeys.lists() });

    // Snapshot every active list cache for rollback
    const previousLists = queryClient.getQueriesData<InvoicePage>({
      queryKey: invoiceKeys.lists(),
    });

    // Add the optimistic item to every active list cache
    queryClient.setQueriesData<InvoicePage>({ queryKey: invoiceKeys.lists() }, (old) => ({
      ...old!,
      items: [...(old?.items ?? []), toOptimisticInvoice(input)],
      total: (old?.total ?? 0) + 1,
    }));

    return { previousLists };
  },

  onError: (_err, _input, context) => {
    context?.previousLists.forEach(([key, data]) =>
      queryClient.setQueryData(key, data),
    );
  },

  onSuccess: (invoice, input) => {
    // ID is already known — seed the detail cache without an extra fetch
    queryClient.setQueryData(
      invoiceKeys.detail(input.client_id as InvoiceId),
      invoice,
    );
  },

  onSettled: () => {
    queryClient.invalidateQueries({ queryKey: invoiceKeys.lists() });
  },
});
```

The optimistic item uses `input.client_id` as its public `id`. The server response is still authoritative: replace the optimistic object with the returned DTO because the backend may assign numbers, timestamps, status defaults, computed totals, or domain-side relationship changes that the frontend could not predict.

### Authoritative mutation responses

Every mutation response is treated as the source of truth, even when the optimistic update looked correct. On success:

1. Seed the primary entity detail cache with the returned DTO.
2. Patch any active list caches if the returned DTO is enough to do so safely.
3. Invalidate the affected list/detail keys so server-side computed changes are reconciled.
4. If the backend returns related changes from domain logic, seed or invalidate those related entity keys too.

```ts
type UpdateInvoiceResult = {
  invoice: Invoice;
  affected_customers?: Customer[];
};

onSuccess: (result) => {
  queryClient.setQueryData(invoiceKeys.detail(result.invoice.id), result.invoice);

  result.affected_customers?.forEach((customer) => {
    queryClient.setQueryData(customerKeys.detail(customer.id), customer);
  });
},

onSettled: (_data, _err, input) => {
  queryClient.invalidateQueries({ queryKey: invoiceKeys.detail(input.id) });
  queryClient.invalidateQueries({ queryKey: invoiceKeys.lists() });
  queryClient.invalidateQueries({ queryKey: customerKeys.lists() });
},
```

This matches the backend Work Context pattern: the frontend sends the intended mutation, but the backend may return additional domain-owned changes that must be reflected in the cache.

### Optimistic update

```ts
useMutation({
  mutationFn: updateInvoice,

  onMutate: async ({ id, ...changes }) => {
    await queryClient.cancelQueries({ queryKey: invoiceKeys.detail(id) });
    await queryClient.cancelQueries({ queryKey: invoiceKeys.lists() });

    const previousDetail = queryClient.getQueryData<InvoiceDetail>(invoiceKeys.detail(id));
    const previousLists  = queryClient.getQueriesData<InvoicePage>({
      queryKey: invoiceKeys.lists(),
    });

    // Update detail cache in-place
    queryClient.setQueryData<InvoiceDetail>(invoiceKeys.detail(id), (old) => ({
      ...old!,
      invoice: { ...old!.invoice, ...changes },
    }));

    // Update every active list cache in-place
    queryClient.setQueriesData<InvoicePage>({ queryKey: invoiceKeys.lists() }, (old) => ({
      ...old!,
      items: old?.items.map((inv) => inv.id === id ? { ...inv, ...changes } : inv) ?? [],
    }));

    return { previousDetail, previousLists };
  },

  onError: (_err, { id }, context) => {
    queryClient.setQueryData(invoiceKeys.detail(id), context?.previousDetail);
    context?.previousLists.forEach(([key, data]) =>
      queryClient.setQueryData(key, data),
    );
  },

  onSettled: (_data, _err, { id }) => {
    queryClient.invalidateQueries({ queryKey: invoiceKeys.detail(id) });
    queryClient.invalidateQueries({ queryKey: invoiceKeys.lists() });
  },
});
```

### Optimistic delete

```ts
useMutation({
  mutationFn: deleteInvoice,

  onMutate: async (id) => {
    await queryClient.cancelQueries({ queryKey: invoiceKeys.lists() });

    const previousLists = queryClient.getQueriesData<InvoicePage>({
      queryKey: invoiceKeys.lists(),
    });

    queryClient.setQueriesData<InvoicePage>({ queryKey: invoiceKeys.lists() }, (old) => ({
      ...old!,
      items: old?.items.filter((inv) => inv.id !== id) ?? [],
      total: Math.max((old?.total ?? 1) - 1, 0),
    }));

    return { previousLists };
  },

  onError: (_err, _id, context) => {
    context?.previousLists.forEach(([key, data]) =>
      queryClient.setQueryData(key, data),
    );
  },

  onSettled: (_data, _err, id) => {
    queryClient.removeQueries({ queryKey: invoiceKeys.detail(id) });
    queryClient.invalidateQueries({ queryKey: invoiceKeys.lists() });
  },
});
```

### When NOT to use optimistic updates

Skip `onMutate` and fall back to normal invalidation for operations where a rollback would be confusing or where the server response is authoritative in a way the frontend cannot predict:

| Operation | Reason to skip optimistic |
|---|---|
| Payment / charge | User must not see "paid" before the gateway confirms |
| Status transitions with compliance rules | Server enforces allowed transitions; client cannot predict the result |
| Bulk operations (50+ rows) | Rolling back large cache mutations is expensive |
| Operations that trigger server-side side effects the client cannot model | The optimistic state would diverge too much from reality |

For these, use the simple pattern: `onSuccess` invalidates the relevant queries and the UI shows a pending state while the server responds.

---

### Optimistic delete with navigation

When the user deletes an entity while viewing its detail page, the delete removes it from the list but nothing automatically navigates them away. The controller surfaces a `navigate` call through `onSuccess` (or immediately, optimistically). Navigation belongs at the controller level — not inside the action hook.

```ts
// features/invoices/controllers/use-invoice-detail.controller.ts
export function useInvoiceDetailController(id: InvoiceId) {
  const navigate  = useNavigate();
  const deleteAction = useDeleteInvoice();

  const handleDelete = useCallback(() => {
    deleteAction.deleteInvoice(id, {
      onSuccess: () => navigate(ROUTES.invoiceList, { replace: true }),
      // Optimistic: navigate immediately if you trust the delete will succeed
      // onSettled: () => navigate(ROUTES.invoiceList, { replace: true }),
    });
    // For truly optimistic delete: navigate before the request resolves
    // navigate(ROUTES.invoiceList, { replace: true });
    // deleteAction.deleteInvoice(id);
  }, [id, deleteAction, navigate]);

  return { handleDelete, isDeleting: deleteAction.isPending };
}
```

The optimistic variant (navigate before the request resolves) works best in SPAs where the list page is already cached and the user lands immediately on visible data. The conservative variant (navigate in `onSuccess`) is safer for operations where "delete" has compliance implications.

---

### Optimistic create with paginated lists

`setQueriesData` affects every active list cache — including paginated pages that are currently in memory. Appending the new item to page 3's cache when it logically belongs at the top of page 1 produces a visually wrong result.

**Guidance by sort order:**

| List sort | Optimistic strategy |
|---|---|
| Most-recent-first (default for most entities) | Add the new item to page 1's cache only; skip other pages |
| User-defined order / alphabetical | Skip optimistic list insertion entirely — let `onSettled` refetch reconcile |
| Flat unordered list (no pagination) | Append to the single list cache as normal |

For most-recent-first lists:

```ts
onMutate: async (input) => {
  await queryClient.cancelQueries({ queryKey: invoiceKeys.lists() });

  // Snapshot all active list caches for rollback
  const previousLists = queryClient.getQueriesData<InvoicePage>({
    queryKey: invoiceKeys.lists(),
  });

  // Only prepend to page 1 (page: 1 or undefined in the params)
  // Other pages are untouched — their data stays correct
  queryClient.setQueriesData<InvoicePage>({ queryKey: invoiceKeys.lists() }, (old, key) => {
    const params = key[key.length - 1] as ListInvoicesParams | undefined;
    if (params?.page && params.page > 1) return old;  // skip pages 2+
    return old
      ? { ...old, items: [toOptimisticInvoice(input), ...old.items], total: old.total + 1 }
      : old;
  });

  return { previousLists };
},
```

`onSettled` always invalidates every list — so even pages that were skipped get reconciled once the server responds.

---

### Concurrent mutations on the same entity

If two mutations target the same entity simultaneously (e.g. rapid sequential saves), each `onMutate` snapshots the cache at its own moment in time. If mutation A fails after mutation B has already succeeded and been applied, mutation A's rollback restores the cache to A's snapshot — which predates B's successful change.

`onSettled` always fires a refetch, which reconciles the cache with the server's authoritative state. The window of inconsistency is brief (the time between A's failure and A's `onSettled` refetch completing), but it exists.

**Mitigation:** debounce or throttle rapid mutations on the same field. For auto-save forms, wait at least 500ms after the last keypress before firing the mutation. This keeps concurrent-mutation scenarios rare in practice.

---

## Prefetching

Prefetch data when user intent is predictable (hovering a link, entering a list view):

```ts
const queryClient = useQueryClient();

const prefetchInvoice = (id: InvoiceId) => {
  queryClient.prefetchQuery({
    queryKey: invoiceKeys.detail(id),
    queryFn: () => fetchInvoice(id),
    staleTime: 1000 * 60,
  });
};
```

---

## Dependent queries

When one query depends on another, gate the query until the required ID exists:

```ts
import { skipToken, useQuery } from '@tanstack/react-query';

const { data: workspace } = useWorkspaceQuery();
const workspaceId = workspace?.id ?? null;

const { data: members } = useQuery({
  queryKey: memberKeys.list(workspaceId),
  queryFn: workspaceId ? () => fetchMembers(workspaceId) : skipToken,
});
```

Never fake dependency values to make TypeScript happy. If the ID is not available yet, the query function must not run.

---

## Global QueryClient defaults

Set in `src/app/providers.tsx`:

```ts
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60,         // data is fresh for 1 minute
      gcTime: 1000 * 60 * 5,        // garbage-collect unused cache after 5 minutes
      retry: 1,                      // retry once on failure
      refetchOnWindowFocus: true,    // re-sync when user returns to tab
    },
    mutations: {
      retry: 0,                      // never auto-retry mutations
    },
  },
});
```

Do not override `staleTime` or `retry` in individual query hooks unless there is a documented reason.

---

## Loading states

Query loading states render skeleton reflections, not generic card spinners. A list renders a list-shaped skeleton. A detail page renders a detail-shaped skeleton. A lazy feature route combines Suspense fallback with the same skeleton component the query uses.

```tsx
const { data, isPending, isError, error } = useInvoicesQuery(params);

if (isPending) return <InvoiceListSkeleton />;
if (isError) return <ErrorState error={error} />;

return <InvoiceTable invoices={data.items} />;
```

Skeleton components live beside the feature components they reflect and use the shared shimmer utilities defined in [32_loading_skeletons.md](32_loading_skeletons.md).

---

## What server state must NOT do

- **Never store server state in `useState`.** Use TanStack Query.
- **Never store server state in Zustand.** Zustand is for client state only (see `06_client_state.md`).
- **Never call the API client directly from a component.** The chain is: component → hook → query function → API client.
- **Never manually manage loading/error state for API calls.** TanStack Query provides `isPending`, `isError`, `error` — use them.
- **Never write query keys as inline strings.** Always use the key factory. Inline strings cannot be invalidated reliably.
- **Never mutate query cache data directly.** Use `setQueryData` with a function that returns the new value, never direct mutation.
- **Never write a mutation without `onMutate` + `onError` + `onSettled` unless it is explicitly in the "When NOT to use optimistic" list.** The default for all create, update, and delete operations is optimistic. Omitting `onMutate` is an intentional exception, not a default.
- **Never skip `onSettled` invalidation.** Even when `onSuccess` seeds the cache, `onSettled` is the safety net that reconciles the server's authoritative state — including cases where the optimistic item was wrong, a concurrent mutation raced, or the server applied additional changes.
- **Never navigate away from a deleted entity inside the action hook.** Navigation belongs in the controller. Action hooks are pure data — they roll back caches, never redirect.
- **Never assume optimistic state is final.** Backend mutation responses and follow-up invalidation are authoritative, especially for domain-owned relationship changes.
- **Never put backend database IDs in query keys.** Query keys use public branded IDs only.
- **Never share persisted query cache across users or workspaces.** Clear it on sign-out and workspace switch, or include the workspace in every scoped key.
