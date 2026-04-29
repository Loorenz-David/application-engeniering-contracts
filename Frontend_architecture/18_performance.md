# 18 — Performance Contract

## Definition

Performance is built in by default, not added later. This contract defines the rules for code splitting, memoization, and bundle discipline that apply to every frontend application.

Dynamic loading boundaries are defined in [30_dynamic_loading.md](30_dynamic_loading.md). This contract explains when performance work is justified and how it is measured.

Animation performance rules are defined in [31_animations.md](31_animations.md). Prefer opacity and transform, and avoid layout-heavy animation unless the interaction is small and bounded.

---

## Code splitting

### Route-level splitting

Every page component is lazy-loaded (see [11_routing.md](11_routing.md) and [30_dynamic_loading.md](30_dynamic_loading.md)). This is the most impactful form of code splitting and is non-negotiable.

```ts
// Every page — mandatory
element: lazyRoute(() =>
  import('@/pages/invoices/InvoicesPage').then((m) => ({ default: m.InvoicesPage })),
);
```

### Heavy library adapters

Large libraries used by only one feature are imported through dynamic adapters:

```ts
// src/lib/dynamic-libs/pdf.ts
export async function loadPdfLib() {
  return import('pdf-lib');
}
```

Do not dynamically import modules that are used on every page (React, TanStack Query, Zustand) — the chunking overhead exceeds the benefit.

---

## Bundle discipline

### Analyze before adding

Before adding a dependency, check its bundle size. Libraries over 50 KB (minified + gzipped) require a justification comment in `package.json`:

```json
{
  "dependencies": {
    "date-fns": "^3.0.0"
  },
  "bundleSizeNotes": {
    "date-fns": "~14KB gzipped — tree-shakeable, only imported functions included"
  }
}
```

### Tree-shaking discipline

Import only what is used. Never import an entire library to use one function:

```ts
// Correct — tree-shakeable
import { format, parseISO } from 'date-fns';

// Wrong — imports the whole library
import dateFns from 'date-fns';
```

---

## Memoization rules

Memoization adds complexity. Apply it only when a measurable performance problem exists, not preemptively.

### When to use `useMemo`

Use `useMemo` when:
1. A computation is demonstrably slow (array sort/filter of 500+ items, heavy calculation)
2. The computed value is used as a dependency in another `useMemo` or `useEffect`

```ts
// Justified — sorting 1000+ items on every render
const sortedInvoices = useMemo(
  () => [...invoices].sort((a, b) => b.amount_cents - a.amount_cents),
  [invoices],
);
```

Do not use `useMemo` for:
- Simple property access or formatting (e.g., `invoice.amount_formatted`)
- Deriving a value that TanStack Query already caches
- Avoiding a "rerender" when the re-render is fast

### When to use `useCallback`

Use `useCallback` when a function is passed as a prop to a memoized component or is used as a `useEffect` dependency.

```ts
// Justified — passed to a memoized child component
const handleSelect = useCallback((id: InvoiceId) => {
  setSelectedId(id);
}, []);  // stable reference
```

Do not add `useCallback` to every function. Most component re-renders are fast; the overhead of maintaining the memoized reference is often larger than the re-render cost.

### When to use `React.memo`

Use `React.memo` on components that:
1. Render frequently as part of a large list
2. Have stable props but an unstable parent

```tsx
export const InvoiceRow = React.memo(function InvoiceRow({ invoice, onSelect }: InvoiceRowProps) {
  return <tr onClick={() => onSelect(invoice.id)}>{/* ... */}</tr>;
});
```

Do not wrap every component in `React.memo`. Unnecessary memoization adds memory overhead and makes the component harder to reason about.

---

## List rendering

For lists with 100+ items that need to be rendered simultaneously, use virtualization:

```tsx
import { useVirtualizer } from '@tanstack/react-virtual';

export function LargeInvoiceList({ invoices }: { invoices: Invoice[] }) {
  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: invoices.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 52,
  });

  return (
    <div ref={parentRef} className="h-[600px] overflow-auto">
      <div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>
        {virtualizer.getVirtualItems().map((virtualItem) => (
          <div
            key={virtualItem.key}
            style={{
              position: 'absolute',
              top: 0,
              transform: `translateY(${virtualItem.start}px)`,
              width: '100%',
            }}
          >
            <InvoiceRow invoice={invoices[virtualItem.index]!} />
          </div>
        ))}
      </div>
    </div>
  );
}
```

The threshold for virtualization is a list that causes a frame drop (> 16ms render time) on mid-range hardware.

---

## Image optimization

- Use `loading="lazy"` on all images below the fold.
- Specify `width` and `height` on all images to prevent layout shift.
- Use WebP with a fallback for user-uploaded images when the backend serves optimized variants.

---

## TanStack Query as a performance tool

TanStack Query already optimizes server data access. Leverage its built-in mechanisms:

- **staleTime** — prevents redundant refetches when data is fresh. Default: 60 seconds.
- **Prefetching** — load the next page before the user navigates (on hover of next page button).
- **`setQueryData`** — seed the detail cache from list data to avoid a second fetch.
- **Background refetch** — data refetches on window focus, keeping the UI fresh without user action.

---

## Performance baseline

Maintain these metrics before and after each feature release:

| Metric | Target |
|---|---|
| Largest Contentful Paint (LCP) | < 2.5 seconds |
| Total Blocking Time (TBT) | < 300 ms |
| Initial JS bundle (parsed) | < 200 KB |
| Per-route lazy chunk | < 100 KB |

Measure with `vite build --report` (rollup-plugin-visualizer) after each major feature.

---

## What performance work must NOT do

- **Never add `useMemo` or `useCallback` preemptively.** Measure first.
- **Never use `React.memo` on every component.** Apply to demonstrated bottlenecks only.
- **Never import a library at the module level if it is used only in one lazy route.** Dynamic import it inside the route component.
- **Never virtualize a list with fewer than 100 items.** The setup cost exceeds the benefit.
- **Never inline large data structures** (product catalogs, config tables) in the bundle. Fetch them at runtime.
