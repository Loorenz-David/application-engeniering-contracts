# 15 — Feature Structure Contract

## Definition

A feature is a vertical slice of the application that owns one business domain. It contains every layer needed to implement that domain in the UI: types, API hooks, actions, controllers, flows, providers, and components. Features are the primary unit of code organization.

---

## Feature folder layout

```
src/features/<feature>/
├── api/
│   ├── <entity>-keys.ts              # Query key factory
│   ├── fetch-<entity>.ts             # Query function (calls apiClient, Zod-validated)
│   ├── fetch-<entity>s.ts            # List query function
│   ├── create-<entity>.ts            # Mutation function
│   ├── update-<entity>.ts
│   ├── delete-<entity>.ts
│   ├── use-<entity>.ts               # Single-entity query hook
│   └── use-<entity>s.ts              # List query hook
│
├── actions/
│   ├── use-create-<entity>.ts        # One action per operation
│   ├── use-update-<entity>.ts
│   └── use-delete-<entity>.ts
│
├── controllers/
│   ├── use-<entity>-list.controller.ts   # Controller for the list section
│   ├── use-<entity>-detail.controller.ts # Controller for the detail section
│   └── use-<entity>-filters.ts           # Filter state (used inside controllers)
│
├── flows/                             # Only present when multi-step processes exist
│   └── use-<process>.flow.ts
│
├── providers/
│   ├── <Entity>ListProvider.tsx      # Provider + context hook for the list section
│   ├── <Entity>DetailProvider.tsx    # Provider + context hook for the detail section
│   └── <Process>FlowProvider.tsx     # Provider for a flow (if the flow spans components)
│
├── components/
│   ├── <Entity>ListView.tsx          # Top-level layout for the list section
│   ├── <Entity>Table.tsx
│   ├── <Entity>Row.tsx
│   ├── <Entity>Filters.tsx
│   ├── <Entity>DetailView.tsx
│   ├── <Entity>StatusBadge.tsx
│   └── ...
│
├── types.ts                          # Zod schemas + inferred types + view model types
└── index.ts                          # Public API — only what other features/pages need
```

Every subfolder has a strict single responsibility:

| Folder | Contains | Imports from |
|---|---|---|
| `api/` | Query hooks + raw fetch/mutation functions | `lib/api-client`, `types/` |
| `actions/` | Action hooks (one per operation) | `api/`, `lib/`, `types/`, `store/` |
| `controllers/` | Controller hooks + filter state hooks | `actions/`, `api/`, `hooks/`, `store/`, `types/` |
| `flows/` | Flow hooks (multi-step orchestration) | `actions/`, `types/` |
| `providers/` | Provider components + context consumer hooks | `controllers/`, `flows/`, `types/` |
| `components/` | Feature UI components | Providers (via context hook), `components/ui/`, `lib/utils` |

---

## Layer traversal for a complete read path

```
InvoiceTable (component)
  └── useInvoiceListContext()                  ← reads InvoiceViewModel[] from context
        └── useInvoiceListController()         ← calls toInvoiceViewModel on each item
              └── useInvoicesQuery()           ← returns Invoice[] (Response DTO)
                    └── fetchInvoices()        ← parses HTTP response with InvoiceSchema
                          └── apiClient.get()  ← raw JSON from backend
```

DTOs enter the system at `fetchInvoices()` and are immediately validated by Zod. The controller transforms Response DTOs into View Models. Components receive View Models — they never see raw DTOs.

## Layer traversal for a complete write path

```
InvoiceTable (component) — calls context.createInvoice(input)
  └── InvoiceListController.createInvoice     ← receives CreateInvoiceInput (Request DTO)
        └── useCreateInvoice()                ← action hook → useMutation
              └── createInvoice()             ← serialises Request DTO → JSON body
                    └── apiClient.post()      ← HTTP POST → backend
                          onSuccess: receives Invoice (Response DTO) → updates cache
```

The Request DTO (`CreateInvoiceInput`) is validated by the form's `zodResolver` before the action is called. The action serialises it and sends it. The response is a new Response DTO which seeds the cache.

---

## The `index.ts` boundary

`index.ts` is the public API of the feature. External code (pages, other features) imports only from `index.ts`. No deep imports are permitted outside the feature.

```ts
// src/features/invoices/index.ts

// Providers (used by pages to wrap sections)
export { InvoiceListProvider, useInvoiceListContext } from './providers/InvoiceListProvider';
export { InvoiceDetailProvider, useInvoiceDetailContext } from './providers/InvoiceDetailProvider';
export { CreateInvoiceFlowProvider } from './providers/CreateInvoiceFlowProvider';

// Top-level view components (used by pages)
export { InvoiceListView } from './components/InvoiceListView';
export { InvoiceDetailView } from './components/InvoiceDetailView';
export { CreateInvoiceWizard } from './components/CreateInvoiceWizard';

// Types used by other features or pages
export type { Invoice, InvoiceViewModel, CreateInvoiceInput } from './types';
export type { InvoiceId } from './types';
```

If something is not in `index.ts`, it is private. Deep imports (`@/features/invoices/components/InvoiceTable`) from outside the feature are forbidden.

---

## Cross-feature imports

When feature B needs something from feature A, it imports from A's `index.ts`:

```ts
// Correct — through the public API
import type { Invoice } from '@/features/invoices';

// Wrong — deep import into another feature
import type { Invoice } from '@/features/invoices/types';
```

If B needs something A does not yet export, the rule is: add it to A's `index.ts` first, then import. Never bypass the boundary.

---

## Feature size

A feature maps to one noun in the business domain. Split when:
- More than 8–10 components, or the folder is difficult to scan
- Two genuinely independent sub-domains share a feature folder

```
features/
  invoices/             ← invoice list, detail, status management
  invoice-payments/     ← payment recording, payment history
  invoice-templates/    ← template CRUD
```

Sub-features are full features — each has its own `index.ts` and complete layer stack.

---

## Types file structure

`types.ts` is the DTO layer for the feature. It contains all four DTO categories in a fixed order. See [24_dto.md](24_dto.md) for the full DTO contract.

```ts
// src/features/invoices/types.ts
import { z } from 'zod';
import type { InvoiceId, CustomerId } from '@/types/common';

// ─── 1. Response DTOs  (what the backend sends → parsed with Zod) ─────────────

export const InvoiceSchema = z.object({
  id:           z.string().uuid().transform((v) => v as InvoiceId),
  number:       z.string(),
  status:       z.enum(['draft', 'sent', 'paid', 'overdue']),
  amount_cents: z.number().int().nonnegative(),
  due_date:     z.string().datetime({ offset: true }),
  customer_id:  z.string().uuid().transform((v) => v as CustomerId),
  created_at:   z.string().datetime({ offset: true }),
});
export type Invoice = z.infer<typeof InvoiceSchema>;

// ─── 2. Request DTOs  (what we send to the backend → validated by form) ───────

export const CreateInvoiceInputSchema = z.object({
  // client_id is a UUID generated by the frontend before the request fires.
  // The backend stores this as the entity's id — enabling optimistic updates
  // without a temp-id swap and immediate navigation to the new entity.
  client_id:   z.string().uuid(),
  customer_id: z.string().uuid({ message: 'Select a customer.' }),
  due_date:    z.string().datetime({ offset: true }),
  line_items:  z.array(z.object({
    description:      z.string().min(1, 'Description is required.'),
    quantity:         z.number().int().positive(),
    unit_price_cents: z.number().int().nonnegative(),
  })).min(1, 'Add at least one line item.'),
  notes: z.string().max(500).optional(),
});
export type CreateInvoiceInput = z.infer<typeof CreateInvoiceInputSchema>;

export const UpdateInvoiceInputSchema = z.object({
  id:         z.string().uuid().transform((v) => v as InvoiceId),
  notes:      z.string().max(500).optional(),
  line_items: CreateInvoiceInputSchema.shape.line_items.optional(),
});
export type UpdateInvoiceInput = z.infer<typeof UpdateInvoiceInputSchema>;

// ─── 3. Query Params DTOs  (URL query string parameters) ──────────────────────

export type ListInvoicesParams = {
  page?:        number;
  per_page?:    number;
  status?:      Invoice['status'];
  search?:      string;
  customer_id?: CustomerId;
};

// ─── 4. View Models  (derived from Response DTO — display-ready, not sent over wire) ──

export type InvoiceViewModel = Invoice & {
  amount_formatted: string;
  status_label:     string;
  status_variant:   'success' | 'danger' | 'warning' | 'default';
  is_overdue:       boolean;
  due_in_days:      number | null;
};

export function toInvoiceViewModel(invoice: Invoice): InvoiceViewModel {
  const now  = new Date();
  const due  = new Date(invoice.due_date);
  const days = Math.ceil((due.getTime() - now.getTime()) / 86_400_000);

  return {
    ...invoice,
    amount_formatted: new Intl.NumberFormat('en-US', {
      style: 'currency', currency: 'USD',
    }).format(invoice.amount_cents / 100),
    status_label:   { draft: 'Draft', sent: 'Sent', paid: 'Paid', overdue: 'Overdue' }[invoice.status],
    status_variant: invoice.status === 'paid'    ? 'success'
                  : invoice.status === 'overdue' ? 'danger'
                  : 'default',
    is_overdue:  invoice.status === 'overdue',
    due_in_days: invoice.status === 'paid' ? null : days,
  };
}

// Pure function that builds a plausible optimistic Invoice from a CreateInvoiceInput.
// Called by useCreateInvoice's onMutate to populate the cache before the server responds.
// Server-assigned fields (number, created_at) use placeholder values that are replaced
// when onSettled fires and the cache is invalidated.
export function toOptimisticInvoice(input: CreateInvoiceInput): Invoice {
  return {
    id:           input.client_id as InvoiceId,
    number:       '—',
    status:       'draft',
    amount_cents: input.line_items.reduce(
      (sum, li) => sum + li.quantity * li.unit_price_cents,
      0,
    ),
    due_date:     input.due_date,
    customer_id:  input.customer_id as CustomerId,
    created_at:   new Date().toISOString(),
  };
}
```

View model transformation functions (`toXxxViewModel`) live in `types.ts`. They are pure functions — no async, no side effects.

---

## Naming conventions

| Layer | Convention | Example |
|---|---|---|
| Query key factory | `<entity>Keys` | `invoiceKeys` |
| Query function | `fetch<Entity>` / `fetch<Entity>s` | `fetchInvoice`, `fetchInvoices` |
| Mutation function | `create<Entity>`, `update<Entity>`, `delete<Entity>` | `createInvoice` |
| Query hook | `use<Entity>Query` / `use<Entity>sQuery` | `useInvoiceQuery` |
| Action hook | `use<Verb><Entity>` | `useCreateInvoice`, `useDeleteInvoice` |
| Controller hook | `use<Entity><Section>Controller` | `useInvoiceListController` |
| Flow hook | `use<Process>Flow` | `useCreateInvoiceFlow` |
| Provider component | `<Entity><Section>Provider` | `InvoiceListProvider` |
| Context hook | `use<Entity><Section>Context` | `useInvoiceListContext` |
| View component | `<Entity><Section>View` | `InvoiceListView`, `InvoiceDetailView` |
| Leaf component | `<Entity><Role>` | `InvoiceRow`, `InvoiceStatusBadge` |
| Schema | `<Entity>Schema`, `<Action><Entity>InputSchema` | `InvoiceSchema`, `CreateInvoiceInputSchema` |
| View model transformer | `to<Entity>ViewModel` | `toInvoiceViewModel` |

---

## What features must NOT do

- **Never deep-import from another feature.** Import from `index.ts` only.
- **Never import `lib/api-client` inside `components/`.** Components never touch the data layer.
- **Never put shared UI primitives inside a feature.** A component used by two features goes in `src/components/ui/`.
- **Never skip `index.ts`.** Every feature must have one, even if it exports only one thing.
- **Never name a feature after a UI pattern** (`modals`, `forms`, `tables`). Name it after the domain entity.
- **Never create circular feature dependencies.** If A depends on B and B depends on A, extract the shared types to `src/types/`.
