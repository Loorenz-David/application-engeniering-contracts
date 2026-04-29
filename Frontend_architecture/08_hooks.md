# 08 — Hook Taxonomy Contract

## Definition

"Hooks" is not a single category. Every custom hook belongs to one of four distinct types, each with a defined responsibility, location, naming convention, and return shape. Using the wrong type for a job creates the same class of bugs as putting business logic in a router handler.

---

## The four types

| Type | Responsibility | Location | Named |
|---|---|---|---|
| **Action** | One discrete operation (create, update, delete, send) | `features/<f>/actions/` | `use<Verb><Entity>` |
| **Controller** | Aggregate everything a UI section needs into one typed API | `features/<f>/controllers/` | `use<Entity><Section>Controller` |
| **Flow** | Orchestrate a multi-step process with explicit step states | `features/<f>/flows/` | `use<Process>Flow` |
| **Utility** | Domain-agnostic reusable logic | `src/hooks/` | `use<Capability>` |

Controllers are the only hooks that providers inject into context. Components consume controllers through context — they never call action, flow, or query hooks directly.

---

## Type 1 — Action

An action wraps exactly one state-changing operation. It is the frontend equivalent of a backend command: one intent, one outcome.

**Signature:** `{ execute fn, isPending, error }`

**Rules:**
- Wraps one `useMutation` (or one side-effect API call that does not need caching)
- Handles its own optimistic snapshot, rollback, authoritative response seeding, and cache invalidation
- Does not read from queries — it only writes
- Is reusable: the same action can be called from multiple controllers or flows

Optimistic updates are the default for every action. Every action follows the same four-hook lifecycle: `onMutate` (snapshot + apply), `onError` (rollback), `onSuccess` (seed caches), `onSettled` (invalidate). See [05_server_state.md](05_server_state.md) for the full optimistic rationale and when to skip it.

```ts
// features/invoices/actions/use-create-invoice.ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { createInvoice } from '@/features/invoices/api/create-invoice';
import { invoiceKeys } from '@/features/invoices/api/invoice-keys';
import { toOptimisticInvoice } from '@/features/invoices/types';
import { notify } from '@/lib/notify';   // global singleton — safe inside callbacks
import type { CreateInvoiceInput, InvoicePage, InvoiceDetail } from '@/features/invoices/types';
import type { InvoiceId } from '@/types/common';

export function useCreateInvoice() {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: createInvoice,

    onMutate: async (input) => {
      await queryClient.cancelQueries({ queryKey: invoiceKeys.lists() });

      const previousLists = queryClient.getQueriesData<InvoicePage>({
        queryKey: invoiceKeys.lists(),
      });

      queryClient.setQueriesData<InvoicePage>({ queryKey: invoiceKeys.lists() }, (old) => ({
        ...old!,
        items: [...(old?.items ?? []), toOptimisticInvoice(input)],
        total: (old?.total ?? 0) + 1,
      }));

      return { previousLists };
    },

    onSuccess: (invoice, input) => {
      notify.success('Invoice created');
      queryClient.setQueryData(
        invoiceKeys.detail(input.client_id as InvoiceId),
        invoice,
      );
    },

    onError: (err, _input, context) => {
      context?.previousLists.forEach(([key, data]) =>
        queryClient.setQueryData(key, data),
      );
      notify.error(
        'Invoice not saved',
        'Your changes are preserved. Fix the issue and try again.',
      );
    },

    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: invoiceKeys.lists() });
    },
  });

  return {
    createInvoice:      mutation.mutate,
    createInvoiceAsync: mutation.mutateAsync,
    isPending:          mutation.isPending,
    error:              mutation.error,
    variables:          mutation.variables,  // last attempted input — available during and after failure
    reset:              mutation.reset,
  };
}

export type CreateInvoiceAction = ReturnType<typeof useCreateInvoice>;
```

```ts
// features/invoices/actions/use-update-invoice.ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { updateInvoice } from '@/features/invoices/api/update-invoice';
import { invoiceKeys } from '@/features/invoices/api/invoice-keys';
import type { UpdateInvoiceInput, InvoicePage, InvoiceDetail } from '@/features/invoices/types';
import type { InvoiceId } from '@/types/common';

export function useUpdateInvoice() {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: updateInvoice,

    onMutate: async ({ id, ...changes }) => {
      await queryClient.cancelQueries({ queryKey: invoiceKeys.detail(id) });
      await queryClient.cancelQueries({ queryKey: invoiceKeys.lists() });

      const previousDetail = queryClient.getQueryData<InvoiceDetail>(invoiceKeys.detail(id));
      const previousLists  = queryClient.getQueriesData<InvoicePage>({
        queryKey: invoiceKeys.lists(),
      });

      queryClient.setQueryData<InvoiceDetail>(invoiceKeys.detail(id), (old) => ({
        ...old!,
        invoice: { ...old!.invoice, ...changes },
      }));

      queryClient.setQueriesData<InvoicePage>({ queryKey: invoiceKeys.lists() }, (old) => ({
        ...old!,
        items: old?.items.map((inv) =>
          inv.id === id ? { ...inv, ...changes } : inv,
        ) ?? [],
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

  return {
    updateInvoice:      mutation.mutate,
    updateInvoiceAsync: mutation.mutateAsync,
    isPending:          mutation.isPending,
    error:              mutation.error,
    variables:          mutation.variables,  // last attempted input — use to re-populate form after rollback
    reset:              mutation.reset,
  };
}

export type UpdateInvoiceAction = ReturnType<typeof useUpdateInvoice>;
```

```ts
// features/invoices/actions/use-delete-invoice.ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { deleteInvoice } from '@/features/invoices/api/delete-invoice';
import { invoiceKeys } from '@/features/invoices/api/invoice-keys';
import type { InvoicePage } from '@/features/invoices/types';
import type { InvoiceId } from '@/types/common';

export function useDeleteInvoice() {
  const queryClient = useQueryClient();

  const mutation = useMutation({
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

  return {
    deleteInvoice: mutation.mutate,
    isPending:     mutation.isPending,
    error:         mutation.error,
  };
}

export type DeleteInvoiceAction = ReturnType<typeof useDeleteInvoice>;
```

---

## Type 2 — Controller

A controller is the single hook a provider calls to build the full API for one UI section. It aggregates query data, action functions, permissions, and view models into one typed object. Providers inject this object into context; components read from it.

**Signature:** a rich typed object — everything the UI section needs, nothing it does not

**Rules:**
- Reads from query hooks and composes action hooks
- Applies permission checks and filters capabilities accordingly
- Builds view models (derived, display-ready data)
- Returns a stable, typed object — export its type (`ReturnType<typeof useXxxController>`)
- Does not render JSX or manage component lifecycle

```ts
// features/invoices/controllers/use-invoice-list.controller.ts
import { useInvoicesQuery } from '@/features/invoices/api/use-invoices';
import { useCreateInvoice } from '@/features/invoices/actions/use-create-invoice';
import { useDeleteInvoice } from '@/features/invoices/actions/use-delete-invoice';
import { useInvoiceFilters } from '@/features/invoices/controllers/use-invoice-filters';
import { usePermissions } from '@/hooks/use-permissions';
import { invoicePermissions } from '@/features/invoices/permissions';
import { toInvoiceViewModel } from '@/features/invoices/types';

export function useInvoiceListController() {
  const filtersApi = useInvoiceFilters();
  const { data, isPending, isError } = useInvoicesQuery(filtersApi.filters);

  const createAction = useCreateInvoice();
  const deleteAction = useDeleteInvoice();

  const { can } = usePermissions();
  const canCreate = can(invoicePermissions.create);
  const canDelete = can(invoicePermissions.delete);

  const invoices = (data?.items ?? []).map(toInvoiceViewModel);

  return {
    // Data
    invoices,
    total: data?.total ?? 0,
    isPending,
    isError,

    // Filters
    filters: filtersApi.filters,
    setStatus:    filtersApi.setStatus,
    setSearch:    filtersApi.setSearch,
    setPage:      filtersApi.setPage,
    resetFilters: filtersApi.resetFilters,

    // Actions — null when the user lacks the permission
    createInvoice: canCreate ? createAction.createInvoice : null,
    deleteInvoice: canDelete ? deleteAction.deleteInvoice : null,
    isCreating: createAction.isPending,
    isDeleting: deleteAction.isPending,

    // Permissions (for conditional rendering decisions)
    can: {
      create: canCreate,
      delete: canDelete,
    },
  };
}

export type InvoiceListController = ReturnType<typeof useInvoiceListController>;
```

Exporting the `ReturnType` as a named type allows the provider and context hook to type their context value without duplication.

---

## Type 3 — Flow

A flow manages a multi-step process. It tracks the current step, accumulates data across steps, and coordinates the final submission. Flows are used for wizards, onboarding, multi-page forms, and checkout sequences.

**Signature:** `{ currentStep, totalSteps, stepData, canProceed, next, back, submit, isPending }`

**Rules:**
- Owns the step sequence as a typed enum or array
- Accumulates partial data across steps using `useState` or `useReducer`
- Calls action hooks for the final submission — it does not call API functions directly
- Is typically injected via its own provider when multiple components need step state

```ts
// features/invoices/flows/use-create-invoice-flow.ts
import { useState, useCallback } from 'react';
import { useCreateInvoice } from '@/features/invoices/actions/use-create-invoice';
import { CreateInvoiceInputSchema, type CreateInvoiceInput } from '@/features/invoices/types';
import type { InvoiceId } from '@/types/common';

type Step = 'client' | 'line_items' | 'details' | 'review';

const STEPS: Step[] = ['client', 'line_items', 'details', 'review'];

export function useCreateInvoiceFlow(onComplete: (id: InvoiceId) => void) {
  const [currentStep, setCurrentStep] = useState<Step>('client');
  const [clientId] = useState(() => crypto.randomUUID() as InvoiceId);
  const [draft, setDraft] = useState<Partial<CreateInvoiceInput>>({ client_id: clientId });

  const createAction = useCreateInvoice();

  const stepIndex  = STEPS.indexOf(currentStep);
  const isFirst    = stepIndex === 0;
  const isLast     = stepIndex === STEPS.length - 1;

  const saveAndNext = useCallback((stepData: Partial<CreateInvoiceInput>) => {
    const next = { ...draft, ...stepData };
    setDraft(next);
    if (!isLast) setCurrentStep(STEPS[stepIndex + 1]!);
  }, [draft, stepIndex, isLast]);

  const goBack = useCallback(() => {
    if (!isFirst) setCurrentStep(STEPS[stepIndex - 1]!);
  }, [stepIndex, isFirst]);

  const submit = useCallback(() => {
    // The entity ID was generated when the flow started.
    // The optimistic cache entry uses this ID; so does the navigation target.
    const input = CreateInvoiceInputSchema.parse({ ...draft, client_id: clientId });
    createAction.createInvoice(input);  // fire — do not await
    onComplete(clientId);                // navigate immediately with the known ID
  }, [draft, clientId, createAction, onComplete]);

  return {
    currentStep,
    stepIndex,
    totalSteps: STEPS.length,
    isFirst,
    isLast,
    draft,
    isPending: createAction.isPending,
    error:     createAction.error,
    saveAndNext,
    goBack,
    submit,
  };
}

export type CreateInvoiceFlowController = ReturnType<typeof useCreateInvoiceFlow>;
```

---

## Type 4 — Utility hook

A utility hook is domain-agnostic reusable logic. It could work in any React application. No feature types, no API calls, no store access.

**Location:** `src/hooks/`

```ts
// src/hooks/use-debounce.ts
import { useState, useEffect } from 'react';

export function useDebounce<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
```

```ts
// src/hooks/use-local-storage.ts
export function useLocalStorage<T>(key: string, initialValue: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const item = window.localStorage.getItem(key);
      return item ? (JSON.parse(item) as T) : initialValue;
    } catch {
      return initialValue;
    }
  });

  const set = useCallback((newValue: T) => {
    setValue(newValue);
    window.localStorage.setItem(key, JSON.stringify(newValue));
  }, [key]);

  return [value, set] as const;
}
```

---

## Mutation failure recovery

When an optimistic update fails, the cache rolls back automatically. But the user's input should not be lost — they should end up at the place where the failure happened with their data intact so they can fix the issue and retry.

The pattern differs by context. All cases share the same principle: **the action hook rolls back the cache; the controller or form decides how to recover the user's input.**

### Case 1 — Modal or drawer (primary pattern for single-page apps)

The form never unmounts. RHF preserves the user's values in its own internal state, completely independent of the query cache. The cache rollback reverts the optimistic preview; it does not touch the form. On failure: show the error, leave the modal open — nothing else needed.

The `client_id` is generated when the modal **opens**, stored as a hidden form value, and preserved through every retry. This makes retries idempotent — the backend receives the same ID and returns the existing entity if it was partially created.

```tsx
// features/invoices/components/CreateInvoiceDrawer.tsx
export function CreateInvoiceDrawer({ isOpen, onClose }: Props) {
  const { createInvoice, isPending, error } = useCreateInvoiceContext();

  const form = useForm<CreateInvoiceInput>({
    resolver: zodResolver(CreateInvoiceInputSchema),
    defaultValues: {
      client_id:   crypto.randomUUID() as InvoiceId,  // generated once, preserved on retry
      customer_id: '',
      due_date:    '',
      line_items:  [{ description: '', quantity: 1, unit_price_cents: 0 }],
    },
  });

  const onSubmit = form.handleSubmit((input) => {
    createInvoice(input, {
      onSuccess: () => {
        onClose();
        // Reset with a fresh client_id so the next open starts clean
        form.reset({ client_id: crypto.randomUUID() as InvoiceId, customer_id: '', due_date: '', line_items: [] });
      },
      // onError: drawer stays open. RHF retains user's values.
      // error prop shows the error message. User can fix and resubmit.
    });
  });

  return (
    <Drawer open={isOpen} onClose={onClose}>
      <form onSubmit={onSubmit}>
        {error && <ErrorAlert error={error} />}
        {/* form fields — values preserved on failure */}
      </form>
    </Drawer>
  );
}
```

**Rule:** Never call `form.reset()` in `onError`. Resetting on failure discards the user's work.

---

### Case 2 — Optimistic page navigation (create → navigate → fail → back)

When a create action fires optimistically and the user is navigated to the new entity's page before the server confirms, a failure must send them back to the create form with their input intact.

Pass the failed input through React Router's `state` object. It is in-memory, type-safe, and automatically cleared when the user leaves the route.

```ts
// features/invoices/controllers/use-invoice-list.controller.ts
const handleCreate = useCallback((formData: Omit<CreateInvoiceInput, 'client_id'>) => {
  const id    = crypto.randomUUID() as InvoiceId;
  const input = { ...formData, client_id: id };

  createAction.createInvoice(input, {
    onError: (err) => {
      // Take the user back to the create form with their data pre-filled
      navigate(ROUTES.invoiceCreate, {
        state:   { prefill: input },  // failed input — client_id preserved for idempotent retry
        replace: true,
      });
      showErrorToast(err.message);
    },
  });

  // Navigate immediately — optimistic
  navigate(ROUTES.invoiceDetail(id));
}, [createAction, navigate]);
```

The create page reads the `state.prefill` on mount and initialises the form with it:

```ts
// features/invoices/hooks/use-create-invoice-form.ts
export function useCreateInvoiceForm() {
  const { state }  = useLocation();
  const prefill    = state?.prefill as CreateInvoiceInput | undefined;

  // If prefill is present (returned from a failed optimistic create), use the same
  // client_id so the retry is idempotent. Otherwise generate a fresh one.
  const form = useForm<CreateInvoiceInput>({
    resolver:      zodResolver(CreateInvoiceInputSchema),
    defaultValues: prefill ?? {
      client_id:   crypto.randomUUID() as InvoiceId,
      customer_id: '',
      due_date:    '',
      line_items:  [{ description: '', quantity: 1, unit_price_cents: 0 }],
    },
  });

  return { form, prefill };
}
```

---

### Case 3 — Update failure (cache rolls back, input is lost)

When an update fails, `onError` rolls back the cache to the server's last known state. If the form reads its display values from the cache (e.g. a detail page form), those values will revert — the user's attempted edits appear to vanish.

The action exposes `mutation.variables` — the input to the last `mutate()` call, available after failure until `reset()` is called. The form re-populates from `variables` on error:

```ts
// features/invoices/hooks/use-edit-invoice-form.ts
export function useEditInvoiceForm(invoice: Invoice) {
  const updateAction = useUpdateInvoiceContext();

  const form = useForm<UpdateInvoiceInput>({
    resolver:      zodResolver(UpdateInvoiceInputSchema),
    defaultValues: { id: invoice.id, notes: invoice.notes, line_items: invoice.line_items },
  });

  // When a failure occurs and the cache rolls back, restore what the user tried to submit.
  // variables holds the last attempted input. Without this, the form would silently revert
  // to the server state and the user would have to retype their changes.
  useEffect(() => {
    if (updateAction.error && updateAction.variables) {
      form.reset(updateAction.variables);
    }
  }, [updateAction.error, updateAction.variables]);

  const onSubmit = form.handleSubmit((input) => {
    updateAction.updateInvoice(input, {
      onSuccess: () => updateAction.reset(),
    });
  });

  return { form, onSubmit, isPending: updateAction.isPending, error: updateAction.error };
}
```

**Rule:** Never use `useEffect` to sync form values from the query cache after the initial mount. If you write `queryData → form.reset()` on every cache change, the rollback will wipe the user's edits. Initialize from cache once; use `variables` for failure recovery.

---

## Decision guide: which type do I need?

```
Is it a one-shot write operation (create, delete, send)?
  → Action

Does it aggregate queries + actions into a full UI section API?
  → Controller

Does it manage a multi-step process with explicit step transitions?
  → Flow

Is it domain-agnostic utility logic that could ship in a library?
  → Utility hook

Does it only read data and expose no mutations or step management?
  → It probably belongs directly in the controller
```

---

## What hooks must NOT do

- **Actions must never call other actions.** Compose at the controller or flow level.
- **Controllers must never render JSX.** They return data and functions — never elements.
- **Flows must never call API functions directly.** They call action hooks.
- **Utility hooks must never import feature-specific types or modules.**
- **No hook of any type may import from `features/<f>/components/`.**
- **Never call any hook conditionally.** All hook calls must be at the top level of the hook function.
