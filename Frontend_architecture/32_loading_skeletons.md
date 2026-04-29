# 32 — Loading Skeleton Contract

## Definition

Loading UI uses skeleton reflections: temporary shapes that match the real card, table, form, or feature section that will render when data arrives.

A skeleton is not a spinner replacement pasted into a blank area. It is a layout-preserving preview of the final UI:

```
Final card layout
        ↓
Same container size, spacing, radius, and major content blocks
        ↓
Neutral skeleton shapes
        ↓
Centralized shimmer overlay
```

The goal is to prevent layout shift, communicate structure, and keep loading states consistent across features.

---

## Skeleton system layers

| Layer | Owns |
|---|---|
| Global CSS | shimmer keyframes, tokens, base skeleton utility |
| Shared primitives | `Skeleton`, `CardSkeleton`, `TableSkeleton`, `PageSkeleton`, `SurfaceSkeleton` |
| Feature skeletons | domain-shaped reflections of real feature cards/sections |
| Pages/surfaces | choose the correct fallback boundary |

Shared primitives are generic. Feature skeletons mirror domain UI.

---

## Global shimmer utility

The shimmer animation is centralized in global styles. Loading components reuse the same class instead of defining their own gradients or keyframes.

```css
/* src/styles/global.css */
@layer base {
  :root {
    --skeleton-base:      hsl(var(--muted) / 0.72);
    --skeleton-highlight: hsl(var(--background) / 0.82);
    --skeleton-radius:    0.5rem;
  }

  .dark {
    --skeleton-base:      hsl(var(--muted) / 0.48);
    --skeleton-highlight: hsl(var(--muted-foreground) / 0.12);
  }
}

@layer utilities {
  @keyframes skeleton-shimmer {
    100% {
      transform: translateX(100%);
    }
  }

  .skeleton-shimmer {
    position: relative;
    overflow: hidden;
    background: var(--skeleton-base);
  }

  .skeleton-shimmer::after {
    content: '';
    position: absolute;
    inset: 0;
    transform: translateX(-100%);
    background: linear-gradient(
      90deg,
      transparent,
      var(--skeleton-highlight),
      transparent
    );
    animation: skeleton-shimmer 1.4s ease-in-out infinite;
  }

  @media (prefers-reduced-motion: reduce) {
    .skeleton-shimmer::after {
      animation: none;
      transform: none;
      opacity: 0.35;
    }
  }
}
```

Do not define skeleton gradients in components. The shimmer belongs in one global utility.

---

## `Skeleton` primitive

All skeleton shapes use the shared primitive.

```tsx
// src/components/ui/Skeleton.tsx
import { cn } from '@/lib/utils';

type SkeletonProps = React.HTMLAttributes<HTMLDivElement>;

export function Skeleton({ className, ...props }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={cn('skeleton-shimmer rounded-[var(--skeleton-radius)]', className)}
      {...props}
    />
  );
}
```

The primitive is intentionally small. Shape, size, and layout are controlled by the caller.

---

## Reflection rule

Every loading skeleton must match the real component's outer container and major content structure.

```tsx
// Real card
export function InvoiceCard({ invoice }: { invoice: InvoiceViewModel }) {
  return (
    <article className="rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-medium">{invoice.number}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{invoice.customerName}</p>
        </div>
        <InvoiceStatusBadge status={invoice.status} />
      </div>
      <div className="mt-4 flex items-end justify-between">
        <p className="text-lg font-semibold">{invoice.amountFormatted}</p>
        <p className="text-xs text-muted-foreground">{invoice.dueLabel}</p>
      </div>
    </article>
  );
}
```

```tsx
// Matching skeleton
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

The skeleton uses the same `article`, border, radius, padding, major flex layout, and spacing as the real card.

---

## Shared skeleton primitives

Use shared skeletons for generic structures:

```tsx
// src/components/ui/CardSkeleton.tsx
import { Skeleton } from './Skeleton';

export function CardSkeleton() {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="mt-2 h-3 w-48" />
        </div>
        <Skeleton className="h-8 w-8 rounded-full" />
      </div>
      <Skeleton className="mt-5 h-24 w-full" />
    </div>
  );
}
```

```tsx
// src/components/ui/TableSkeleton.tsx
import { Skeleton } from './Skeleton';

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="rounded-lg border">
      <div className="border-b p-4">
        <Skeleton className="h-4 w-48" />
      </div>
      <div className="divide-y">
        {Array.from({ length: rows }).map((_, index) => (
          <div key={index} className="grid grid-cols-4 gap-4 p-4">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-4 w-16 justify-self-end" />
          </div>
        ))}
      </div>
    </div>
  );
}
```

Shared skeletons are acceptable for generic loading boundaries. Feature-specific cards should usually define their own skeleton reflection.

---

## Feature skeletons

Feature skeletons live beside the feature components they mirror.

```
features/invoices/components/
├── InvoiceCard.tsx
├── InvoiceCardSkeleton.tsx
├── InvoiceListView.tsx
└── InvoiceListSkeleton.tsx
```

Feature component loading pattern:

```tsx
export function InvoiceListView() {
  const { invoices, isPending, isError } = useInvoiceListContext();

  if (isPending) return <InvoiceListSkeleton />;
  if (isError) return <InvoiceListError />;
  if (invoices.length === 0) return <InvoiceEmptyState />;

  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {invoices.map((invoice) => (
        <InvoiceCard key={invoice.id} invoice={invoice} />
      ))}
    </div>
  );
}
```

```tsx
export function InvoiceListSkeleton() {
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, index) => (
        <InvoiceCardSkeleton key={index} />
      ))}
    </div>
  );
}
```

The skeleton count should reflect the visible layout, not the eventual total result count.

---

## Page and surface skeletons

Page skeletons reflect the route layout: header, toolbar, primary content area.

```tsx
// src/components/ui/PageSkeleton.tsx
import { Skeleton } from './Skeleton';

export function PageSkeleton() {
  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <Skeleton className="h-7 w-48" />
          <Skeleton className="mt-2 h-4 w-72" />
        </div>
        <Skeleton className="h-10 w-32" />
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, index) => (
          <CardSkeleton key={index} />
        ))}
      </div>
    </div>
  );
}
```

Surface skeletons reflect the surface chrome and content area:

```tsx
// src/components/ui/SurfaceSkeleton.tsx
import { Skeleton } from './Skeleton';

export function SurfaceSkeleton({ surface }: { surface: 'drawer' | 'modal' }) {
  return (
    <div className={surface === 'drawer' ? 'space-y-5 p-6' : 'space-y-4 p-4'}>
      <div className="flex items-center justify-between gap-4">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-8 w-8 rounded-md" />
      </div>
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-28 w-full" />
      <Skeleton className="h-10 w-32" />
    </div>
  );
}
```

---

## Accessibility

Skeleton shapes are decorative and use `aria-hidden="true"`. The containing region should expose loading state when useful:

```tsx
<section aria-busy={isPending}>
  {isPending ? <InvoiceListSkeleton /> : <InvoiceList />}
</section>
```

Do not announce every skeleton line to screen readers. If loading takes long enough to need an announcement, announce the region state, not the individual shapes.

---

## Animation and reduced motion

Skeleton shimmer is CSS-based and centralized in `skeleton-shimmer`. It must respect `prefers-reduced-motion`.

Do not use Framer Motion for skeleton shimmer. Framer Motion is for structural UI transitions; skeleton shimmer is a styling utility.

---

## What loading skeletons must NOT do

- **Never use a spinner where a stable skeleton reflection is possible.** Spinners are for unknown-duration operations without predictable layout.
- **Never create a skeleton that does not match the final component's container, spacing, and major content blocks.**
- **Never define shimmer gradients, keyframes, or animation durations inside a component.** Use the global `skeleton-shimmer` utility.
- **Never put fake text content in skeletons.** Use neutral blocks only.
- **Never make skeletons interactive.** They are decorative placeholders.
- **Never let skeletons cause layout shift when real data loads.** Match dimensions and spacing.
- **Never use Framer Motion for skeleton shimmer.** The shimmer is CSS.
- **Never render a large offscreen skeleton list.** Match the visible viewport count.
