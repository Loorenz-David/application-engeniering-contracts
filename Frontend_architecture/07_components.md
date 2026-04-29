# 07 — Component Contract

## Definition

A component is a pure UI unit: it receives data from context or props, renders markup, and fires callbacks. It contains no business logic, no API calls, no state derivations, and no direct imports from the logic layer. All of that lives in controllers, actions, and flows, surfaced through providers.

Loading states follow [32_loading_skeletons.md](32_loading_skeletons.md). Feature components render skeleton reflections that match the real UI structure instead of generic spinners.

---

## Two categories of components

### Shared UI primitives (`src/components/ui/`)

Generic, application-agnostic building blocks. `Button`, `Input`, `Modal`, `Badge`, `Table`, `Skeleton`.

- Receive all data via props — no context consumption
- No knowledge of any feature, domain, or business rule
- Composed by feature components
- May include progress primitives for unknown-duration operations, but predictable page/card/table loading uses skeleton reflections

### Feature components (`features/<f>/components/`)

Domain-specific UI pieces. `InvoiceTable`, `InvoiceFilters`, `InvoiceStatusBadge`.

- Consume data and callbacks from the feature's context hook (e.g., `useInvoiceListContext()`)
- Never receive data props that could come from context — that causes prop drilling
- Never import from `api/`, `actions/`, `controllers/`, `store/`, or `lib/api-client`
- Props are limited to: composition slots (`children`), layout variants, stable IDs needed for rendering a specific sub-item

---

## Feature component signature

```tsx
// features/invoices/components/InvoiceTable.tsx
import { useInvoiceListContext } from '@/features/invoices/providers/InvoiceListProvider';
import { InvoiceRow } from './InvoiceRow';
import { TableSkeleton } from '@/components/ui/TableSkeleton';
import { EmptyState } from '@/components/ui/EmptyState';

export function InvoiceTable() {
  const { invoices, isPending, isError } = useInvoiceListContext();

  if (isPending) return <TableSkeleton rows={5} />;  // shared reflected table skeleton
  if (isError)   return <EmptyState message="Failed to load invoices." />;
  if (invoices.length === 0) return <EmptyState message="No invoices yet." />;

  return (
    <table className="w-full text-sm">
      <thead>
        <tr>
          <th>Number</th>
          <th>Amount</th>
          <th>Status</th>
          <th>Due</th>
        </tr>
      </thead>
      <tbody>
        {invoices.map((invoice) => (
          <InvoiceRow key={invoice.id} invoice={invoice} />
        ))}
      </tbody>
    </table>
  );
}
```

The component does not know where `invoices` comes from. It does not call `useInvoicesQuery`. It reads from context and renders.

For card grids or domain-specific layouts, prefer a feature skeleton beside the real component:

```tsx
// features/invoices/components/InvoiceCardSkeleton.tsx
export function InvoiceCardSkeleton() {
  return (
    <article className="rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="mt-2 h-3 w-40" />
        </div>
        <Skeleton className="h-6 w-20 rounded-full" />
      </div>
      <div className="mt-4 flex items-end justify-between">
        <Skeleton className="h-6 w-28" />
        <Skeleton className="h-3 w-16" />
      </div>
    </article>
  );
}
```

---

## Shared UI primitive signature

```tsx
// src/components/ui/Badge.tsx
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium',
  {
    variants: {
      variant: {
        default:  'bg-gray-100 text-gray-800',
        success:  'bg-green-100 text-green-800',
        warning:  'bg-yellow-100 text-yellow-800',
        danger:   'bg-red-100 text-red-800',
        info:     'bg-blue-100 text-blue-800',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

type BadgeProps = React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>;

export function Badge({ variant, className, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
```

No context. No feature knowledge. Props only.

---

## Props discipline

### Feature components: props for composition, not data

```tsx
// Correct — invoice comes from context, not a prop
export function InvoiceRow({ invoice }: { invoice: InvoiceViewModel }) {
  // InvoiceRow is a leaf — its parent InvoiceTable passes the specific item
  // The data came from context into InvoiceTable, then as a prop into InvoiceRow
  return <tr>...</tr>;
}
```

There is one legitimate case for data props in feature components: a leaf component rendering one item from a list. The list is fetched from context; the individual item is passed as a prop from the list component. This is not prop drilling — it is composition.

### Shared components: all data via props

```tsx
// Correct — all data via props, no context
<Button variant="destructive" onClick={onDelete} disabled={isDeleting}>
  {isDeleting ? 'Deleting…' : 'Delete'}
</Button>
```

### No logic in props computation

Derive values in the controller or action hook, not inline in JSX:

```tsx
// Wrong — ternary domain logic in JSX
<Badge variant={invoice.status === 'paid' ? 'success' : invoice.status === 'overdue' ? 'danger' : 'default'} />

// Correct — derived in the controller or view model
<Badge variant={invoice.statusVariant} />
```

---

## Component rules

### Named exports only

Default exports are forbidden for all components.

```tsx
// Correct
export function InvoiceTable() { ... }

// Wrong
export default function InvoiceTable() { ... }
```

### One file, one component (with small private helpers)

A component file exports one public component. Small, file-private helper components may be defined in the same file but must not be exported.

```tsx
// InvoiceTable.tsx — public component
export function InvoiceTable() { ... }

// Private helper, not exported
function EmptyTableBody() { ... }
```

### PascalCase name, matching file name

`InvoiceTable.tsx` exports `InvoiceTable`. No mismatches.

### No nested component definitions

Never define a component inside another component's function body. It re-creates the inner component on every render and breaks React's reconciliation.

```tsx
// Wrong — defined inside render function
export function InvoiceTable() {
  function EmptyRow() { return <tr><td>No items</td></tr>; }  // recreated every render
  return <table><EmptyRow /></table>;
}

// Correct — defined at module level
function EmptyRow() { return <tr><td>No items</td></tr>; }

export function InvoiceTable() {
  return <table><EmptyRow /></table>;
}
```

---

## Variant pattern with `cva`

Shared UI components use `cva` for variants — no `if/else` class strings:

```tsx
const buttonVariants = cva(
  'inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary:     'bg-blue-600 text-white hover:bg-blue-700',
        secondary:   'bg-gray-100 text-gray-900 hover:bg-gray-200',
        destructive: 'bg-red-600 text-white hover:bg-red-700',
        ghost:       'hover:bg-gray-100 text-gray-700',
      },
      size: {
        sm: 'h-8 px-3',
        md: 'h-10 px-4',
        lg: 'h-12 px-6',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
);
```

---

## Forwarding refs

Shared UI primitives that wrap HTML elements must forward refs so forms and third-party libraries can access the DOM node:

```tsx
export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ error, className, ...props }, ref) => (
    <div>
      <input ref={ref} className={cn('...', error && 'border-red-500', className)} {...props} />
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  ),
);
Input.displayName = 'Input';
```

---

## What components must NOT do

- **Feature components must never import from the logic layer.** No `api/`, `actions/`, `controllers/`, `flows/`, `store/`, or `lib/api-client` imports inside `features/<f>/components/`.
- **Feature components must never call `useQuery`, `useMutation`, or `useXxxStore` directly.** That is the controller's job.
- **Shared UI components (`components/ui/`) must never consume feature context.** They are domain-blind.
- **Never use default exports.** Named exports only.
- **Never use `React.FC`.** Write plain typed function components.
- **Never define a component inside another component's function body.**
- **Never compute domain logic inline in JSX.** Move it to the controller's view model.
- **Never use mismatched loading placeholders.** Loading components must reflect the final component's container, spacing, and major content blocks.
- **Never build class strings with template literals.** Use `cn()` from `@/lib/utils`.
