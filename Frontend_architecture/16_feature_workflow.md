# 16 — Feature Workflow Contract

## Definition

This contract describes the exact sequence for building a new feature from scratch. The order is non-negotiable: each layer depends on the one below it. Building components before controllers produces components that reach into the logic layer directly — the main mistake this architecture prevents.

---

## Build order principle

```
Types → API → Actions → Controllers/Flows → Providers → Components → Pages → Dynamic loading → Routes
```

The logic layer is built bottom-up. The UI layer is assembled top-down on top of it.

---

## Step-by-step

### Step 1 — Define types (`types.ts`)

Before anything else, define:
1. The API response Zod schema (what the backend returns)
2. The input Zod schemas (what forms will submit)
3. The view model type and its transformer function

**Contract:** [02_types.md](02_types.md)

```ts
export const InvoiceSchema = z.object({ ... });
export type Invoice = z.infer<typeof InvoiceSchema>;

export const CreateInvoiceInputSchema = z.object({ ... });
export type CreateInvoiceInput = z.infer<typeof CreateInvoiceInputSchema>;

export type InvoiceViewModel = Invoice & { amount_formatted: string; ... };
export function toInvoiceViewModel(invoice: Invoice): InvoiceViewModel { ... }
```

Stop and confirm the schema matches the backend contract before proceeding.

---

### Step 2 — Query key factory (`api/<entity>-keys.ts`)

Define the query key structure before any hooks. Correct keys now prevent cache invalidation bugs later.

**Contract:** [05_server_state.md](05_server_state.md)

```ts
export const invoiceKeys = {
  all: ['invoices'] as const,
  lists: () => [...invoiceKeys.all, 'list'] as const,
  list: (params) => [...invoiceKeys.lists(), params] as const,
  details: () => [...invoiceKeys.all, 'detail'] as const,
  detail: (id) => [...invoiceKeys.details(), id] as const,
};
```

---

### Step 3 — API functions and query hooks (`api/`)

Write the typed fetch/mutation functions, then wrap them in TanStack Query hooks.

**Contract:** [04_api_client.md](04_api_client.md), [05_server_state.md](05_server_state.md)

```ts
// fetch-invoices.ts — plain async function
export async function fetchInvoices(params): Promise<InvoicePage> { ... }

// use-invoices.ts — TanStack Query hook
export function useInvoicesQuery(params) {
  return useQuery({ queryKey: invoiceKeys.list(params), queryFn: () => fetchInvoices(params) });
}
```

---

### Step 4 — Actions (`actions/`)

Build one action hook per write operation. Each action wraps one `useMutation` and handles its own cache invalidation.

**Contract:** [08_hooks.md](08_hooks.md) — Action type

```ts
// actions/use-create-invoice.ts
export function useCreateInvoice() {
  const mutation = useMutation({
    mutationFn: createInvoice,
    onMutate: async (input) => {
      // Snapshot + optimistic cache update.
    },
    onError: (_err, _input, context) => {
      // Roll back from the snapshot returned by onMutate.
    },
    onSuccess: (invoice) => {
      // Seed authoritative server response.
      queryClient.setQueryData(invoiceKeys.detail(invoice.id), invoice);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: invoiceKeys.lists() });
    },
  });
  return { createInvoice: mutation.mutate, isPending: mutation.isPending, error: mutation.error };
}
```

---

### Step 5 — Controllers (`controllers/`)

Build one controller per UI section. The controller aggregates query data, action functions, permissions, and view models into a single typed object.

**Contract:** [08_hooks.md](08_hooks.md) — Controller type

```ts
// controllers/use-invoice-list.controller.ts
export function useInvoiceListController() {
  const filtersApi  = useInvoiceFilters();
  const { data, isPending, isError } = useInvoicesQuery(filtersApi.filters);
  const createAction = useCreateInvoice();
  const deleteAction = useDeleteInvoice();
  const { can } = usePermissions();

  return {
    invoices:      (data?.items ?? []).map(toInvoiceViewModel),
    total:         data?.total ?? 0,
    isPending,     isError,
    filters:       filtersApi.filters,
    setStatus:     filtersApi.setStatus,
    setSearch:     filtersApi.setSearch,
    setPage:       filtersApi.setPage,
    resetFilters:  filtersApi.resetFilters,
    createInvoice: can(invoicePermissions.create) ? createAction.createInvoice : null,
    deleteInvoice: can(invoicePermissions.delete) ? deleteAction.deleteInvoice : null,
    isCreating:    createAction.isPending,
    isDeleting:    deleteAction.isPending,
    can: {
      create: can(invoicePermissions.create),
      delete: can(invoicePermissions.delete),
    },
  };
}
export type InvoiceListController = ReturnType<typeof useInvoiceListController>;
```

---

### Step 6 — Flows (`flows/`) — only if a multi-step process exists

If the feature has a wizard, multi-page form, or checkout-style sequence, build the flow hook.

**Contract:** [08_hooks.md](08_hooks.md) — Flow type

```ts
// flows/use-create-invoice-flow.ts
export function useCreateInvoiceFlow(onComplete: (id: InvoiceId) => void) {
  const [currentStep, setCurrentStep] = useState<Step>('client');
  const [draft, setDraft] = useState<Partial<CreateInvoiceInput>>({});
  const createAction = useCreateInvoice();
  // ... step navigation + submit
  return { currentStep, totalSteps, draft, isPending, saveAndNext, goBack, submit };
}
```

---

### Step 7 — Providers (`providers/`)

Wrap each controller (and flow, if applicable) in a provider. Export the provider component and the context consumer hook.

**Contract:** [23_providers.md](23_providers.md)

```tsx
// providers/InvoiceListProvider.tsx
const InvoiceListContext = createContext<InvoiceListController | null>(null);

export function InvoiceListProvider({ children }) {
  const controller = useInvoiceListController();
  return <InvoiceListContext.Provider value={controller}>{children}</InvoiceListContext.Provider>;
}

export function useInvoiceListContext(): InvoiceListController {
  const ctx = useContext(InvoiceListContext);
  if (!ctx) throw new Error('useInvoiceListContext must be used within <InvoiceListProvider>');
  return ctx;
}
```

---

### Step 8 — Components (`components/`)

Build components from smallest leaf to largest view. Each feature component consumes the context hook — never imports from `api/`, `actions/`, or `controllers/` directly.

**Contract:** [07_components.md](07_components.md)

Build order within components:
1. Leaf display components (`InvoiceStatusBadge`, `InvoiceRow`)
2. Section components (`InvoiceTable`, `InvoiceFilters`, `InvoicePagination`)
3. Top-level view component (`InvoiceListView`) — assembles the section layout

```tsx
// Leaf
export function InvoiceStatusBadge({ status }: { status: Invoice['status'] }) { ... }

// Section — consumes context
export function InvoiceTable() {
  const { invoices, isPending, isError } = useInvoiceListContext();
  ...
}

// View — assembles layout, no logic
export function InvoiceListView() {
  return (
    <div>
      <InvoiceFilters />
      <InvoiceTable />
      <InvoicePagination />
    </div>
  );
}
```

---

### Step 9 — Forms (if the feature has a form)

Build the form hook that wires RHF to the Zod schema and calls an action. Then build the form component that consumes the context or receives the form hook result as props.

**Contract:** [09_forms.md](09_forms.md)

```ts
// controllers/use-create-invoice-form.ts  (treated as a controller for the form section)
export function useCreateInvoiceForm(onSuccess: (id: InvoiceId) => void) {
  const createAction = useCreateInvoice();
  const form = useForm<CreateInvoiceInput>({ resolver: zodResolver(CreateInvoiceInputSchema) });

  const onSubmit = form.handleSubmit((data) =>
    createAction.createInvoiceAsync(data, { onSuccess: (invoice) => onSuccess(invoice.id) })
  );
  return { form, onSubmit, isPending: createAction.isPending };
}
```

---

### Step 10 — Page component (`pages/<feature>/`)

The page wraps the provider in a Suspense boundary and error boundary. It is thin by design.

**Contract:** [10_pages.md](10_pages.md)

```tsx
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

---

### Step 11 — Dynamic loading registration

Register any lazy boundaries the feature owns:

1. Route page loaded through `lazyRoute`
2. Drawer/modal page registered in `surfaces.ts`
3. Heavy one-off library loaded through `src/lib/dynamic-libs/*`
4. Optional preload function in `preload.ts`

**Contract:** [30_dynamic_loading.md](30_dynamic_loading.md)

```ts
// features/invoices/preload.ts
export function preloadInvoiceCreateSurface() {
  return import('./pages/InvoiceCreatePage');
}
```

```ts
// features/invoices/surfaces.ts
export const invoiceSurfaces = {
  'invoice-create': {
    surface: 'drawer',
    path:    () => '/invoices/new',
    component: lazy(() =>
      import('./pages/InvoiceCreatePage').then((m) => ({ default: m.InvoiceCreatePage })),
    ),
  },
} satisfies SurfaceRegistrations;
```

Do this before route registration so the feature's loading boundaries are explicit.

---

### Step 12 — Route registration

Add the lazy-loaded page to `src/app/router.tsx` and the path constant to `src/lib/routes.ts`.

**Contract:** [11_routing.md](11_routing.md)

---

### Step 13 — Public API (`index.ts`)

Export only what other features and pages need. Everything internal stays private.

**Contract:** [15_feature_structure.md](15_feature_structure.md)

---

### Step 14 — Tests

Test the logic layer (actions, controllers) and the render layer (form components, list components) separately.

**Contract:** [17_testing.md](17_testing.md)

Priority:
1. Action hooks — success path, error path, cache invalidation
2. Controller hooks — verify it aggregates correctly
3. Form components — valid submit, validation errors, server error mapping
4. List/detail components — render from mocked context

---

## Checklist

Use before marking the feature complete:

- [ ] `types.ts`: all schemas use `z.infer` — no standalone interfaces
- [ ] Query key factory covers `all`, `lists()`, `list(params)`, `details()`, `detail(id)`
- [ ] Every API response parsed through Zod schema
- [ ] One action hook per write operation — optimistic lifecycle plus authoritative response reconciliation
- [ ] Controller aggregates all queries + actions + permissions into one typed object
- [ ] `ReturnType<typeof useXxxController>` exported as a named type
- [ ] Provider exports: the component + the context consumer hook only
- [ ] No feature component imports from `api/`, `actions/`, `controllers/`, `flows/`, or `store/`
- [ ] Page component: `RouteErrorBoundary` + `Suspense` + provider + view — nothing else
- [ ] Route lazy-loaded; path constant in `ROUTES`
- [ ] `index.ts`: exports providers, top-level view components, and public types only
- [ ] At least one test per action and one per controller

---

## Contract reference per step

| Step | Primary contract | Secondary |
|---|---|---|
| 1 — Types | [02_types.md](02_types.md) | — |
| 2 — Query keys | [05_server_state.md](05_server_state.md) | — |
| 3 — API functions + query hooks | [04_api_client.md](04_api_client.md) | 05 |
| 4 — Actions | [08_hooks.md](08_hooks.md) | 05 |
| 5 — Controllers | [08_hooks.md](08_hooks.md) | 06, 19 |
| 6 — Flows (if needed) | [08_hooks.md](08_hooks.md) | 09 |
| 7 — Providers | [23_providers.md](23_providers.md) | 08 |
| 8 — Components | [07_components.md](07_components.md) | 14, 23 |
| 9 — Forms | [09_forms.md](09_forms.md) | 02, 08 |
| 10 — Page | [10_pages.md](10_pages.md) | 13, 23 |
| 11 — Dynamic loading | [30_dynamic_loading.md](30_dynamic_loading.md) | 11, 18, 28 |
| 12 — Route | [11_routing.md](11_routing.md) | 18 |
| 13 — Public API | [15_feature_structure.md](15_feature_structure.md) | — |
| 14 — Tests | [17_testing.md](17_testing.md) | 08, 23 |
