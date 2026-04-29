# 31 — Animation Contract

## Definition

Framer Motion is the default animation system for application UI transitions. CSS handles simple state transitions. Custom JavaScript animation is reserved for cases Framer Motion or CSS cannot model cleanly.

Animation is a UI concern only. Controllers, actions, API functions, stores, and DTO transformers expose state; components and surfaces decide how that state moves.

```
Logic layer returns state: isOpen, isPending, selectedId, items
        ↓
Provider exposes state through context
        ↓
Component or surface chooses animation
```

---

## Default tool choice

| Use case | Tool |
|---|---|
| Hover/focus color, border, shadow | CSS / Tailwind |
| Button press micro-interactions | CSS or Framer Motion inside the shared primitive |
| Modal/drawer enter and exit | Framer Motion |
| Route/page transitions | Framer Motion, restrained |
| List item add/remove/reorder | Framer Motion |
| Accordion/collapse height animation | Framer Motion |
| Skeleton shimmer | CSS |
| Loading spinner/progress | CSS |
| Canvas/3D/data visualization animation | JS/library-specific |
| Highly custom gesture/physics interaction | Framer Motion first, custom JS only if needed |

Use the simplest tool that preserves the interaction contract. Do not use Framer Motion for color-only hover states that Tailwind handles cleanly.

Skeleton loading shimmer is defined in [32_loading_skeletons.md](32_loading_skeletons.md). It is CSS-based and shared across all loading components.

---

## App-level setup

Framer Motion is shared UI infrastructure. It is imported statically at the app root, not dynamically loaded per feature.

Use `LazyMotion` with `domAnimation` by default. This keeps the Framer Motion feature bundle smaller while preserving the common animation APIs used by surfaces, lists, and transitions.

```tsx
// src/app/providers.tsx
import { LazyMotion, MotionConfig, domAnimation } from 'framer-motion';

export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <MotionConfig reducedMotion="user">
      <LazyMotion features={domAnimation}>
        {children}
      </LazyMotion>
    </MotionConfig>
  );
}
```

`reducedMotion="user"` is mandatory. It respects the user's OS-level reduced-motion preference.

Use `m` components with `LazyMotion`:

```tsx
import { m } from 'framer-motion';

<m.div
  initial={{ opacity: 0, y: 8 }}
  animate={{ opacity: 1, y: 0 }}
  exit={{ opacity: 0, y: 8 }}
/>
```

Do not import `motion` directly unless the component has a specific reason and the bundle impact is understood.

---

## Animation tokens

Durations and easings are centralized. Components do not invent one-off timing values.

```ts
// src/lib/animation.ts
export const durations = {
  instant: 0.08,
  fast:    0.12,
  base:    0.18,
  slow:    0.28,
} as const;

export const easings = {
  standard:   [0.2, 0, 0, 1],
  emphasized: [0.16, 1, 0.3, 1],
} as const;

export const transitions = {
  fast: {
    duration: durations.fast,
    ease:     easings.standard,
  },
  base: {
    duration: durations.base,
    ease:     easings.standard,
  },
  surface: {
    duration: durations.slow,
    ease:     easings.emphasized,
  },
} as const;
```

Use these tokens in variants:

```ts
import { transitions } from '@/lib/animation';

const fadeIn = {
  hidden:  { opacity: 0 },
  visible: { opacity: 1, transition: transitions.base },
  exit:    { opacity: 0, transition: transitions.fast },
};
```

---

## Variants live near the UI they animate

Animation variants belong beside the component or surface using them. Shared variants are allowed only when multiple components use the same animation for the same semantic purpose.

```tsx
// src/components/surfaces/ModalSurface.tsx
const backdropVariants = {
  hidden:  { opacity: 0 },
  visible: { opacity: 1 },
  exit:    { opacity: 0 },
};

const panelVariants = {
  hidden:  { opacity: 0, scale: 0.96, y: 8 },
  visible: { opacity: 1, scale: 1, y: 0 },
  exit:    { opacity: 0, scale: 0.98, y: 8 },
};
```

Do not define animation variants in controllers, stores, API files, or DTO files.

---

## Mount and unmount transitions

Use `AnimatePresence` when an element needs an exit animation before it unmounts.

```tsx
import { AnimatePresence, m } from 'framer-motion';

export function InlineError({ message }: { message?: string }) {
  return (
    <AnimatePresence initial={false}>
      {message ? (
        <m.p
          key="error"
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          className="text-sm text-destructive"
        >
          {message}
        </m.p>
      ) : null}
    </AnimatePresence>
  );
}
```

Use `initial={false}` when animating UI that may already be present on first render and should not play an entrance animation.

---

## Surface animations

Drawers and modals use Framer Motion. Surface shells own the animation; features only provide content.

```tsx
// src/components/surfaces/DrawerSurface.tsx
import { AnimatePresence, m } from 'framer-motion';
import { transitions } from '@/lib/animation';

type Props = {
  open:     boolean;
  onClose:  () => void;
  zIndex:   number;
  children: React.ReactNode;
};

export function DrawerSurface({ open, onClose, zIndex, children }: Props) {
  return (
    <AnimatePresence>
      {open ? (
        <>
          <m.button
            type="button"
            aria-label="Close drawer"
            className="fixed inset-0 bg-black/40"
            style={{ zIndex }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={transitions.base}
            onClick={onClose}
          />
          <m.aside
            role="dialog"
            aria-modal="true"
            className="fixed inset-y-0 right-0 flex w-full max-w-xl flex-col bg-background shadow-xl"
            style={{ zIndex: zIndex + 1 }}
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={transitions.surface}
          >
            {children}
          </m.aside>
        </>
      ) : null}
    </AnimatePresence>
  );
}
```

Modal surfaces follow the same pattern: backdrop and panel are separate motion elements, and the feature content is rendered as children.

---

## Route transitions

Route transitions are allowed, but they must be restrained. Operational apps should prioritize perceived speed and stability over expressive page motion.

Use route transitions only when they clarify hierarchy:

- auth page → app shell
- list → detail
- parent section → nested section
- mobile bottom-tab transitions

Avoid route transitions for high-frequency workflows, tables, forms, and dashboards where motion slows repeated use.

---

## List animation

Use Framer Motion for small or medium lists where add/remove/reorder feedback improves comprehension.

```tsx
import { AnimatePresence, m } from 'framer-motion';

export function InvoiceRows({ invoices }: { invoices: InvoiceViewModel[] }) {
  return (
    <AnimatePresence initial={false}>
      {invoices.map((invoice) => (
        <m.tr
          key={invoice.id}
          layout
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          {/* cells */}
        </m.tr>
      ))}
    </AnimatePresence>
  );
}
```

Do not animate every row in a large virtualized list. Virtualization and layout animation compete for control over positioning.

---

## Reduced motion

Reduced motion is not optional. `MotionConfig reducedMotion="user"` handles most cases, but components with large movement should also provide a low-motion variant.

```tsx
import { m, useReducedMotion } from 'framer-motion';

export function SlideInPanel({ children }: { children: React.ReactNode }) {
  const reduceMotion = useReducedMotion();

  return (
    <m.div
      initial={reduceMotion ? { opacity: 0 } : { opacity: 0, x: 24 }}
      animate={reduceMotion ? { opacity: 1 } : { opacity: 1, x: 0 }}
    >
      {children}
    </m.div>
  );
}
```

Reduced motion should remove large spatial movement, parallax, bouncing, and repeated loops. Fades are usually acceptable.

---

## Performance rules

Prefer animating properties that avoid layout recalculation:

| Prefer | Avoid unless justified |
|---|---|
| `opacity` | `width` |
| `transform` / `x` / `y` / `scale` | `height` |
| `filter` sparingly | `top` / `left` |
| `layout` for small bounded sets | `layout` on large lists |

Height animation is acceptable for accordions and disclosure panels when the content is small and the interaction is infrequent.

Animation must never block navigation, form submission, error display, or permission changes. State changes happen immediately; animation follows state.

---

## Dynamic loading interaction

Framer Motion is core UI infrastructure and stays in the main UI bundle. Do not dynamically import Framer Motion per feature.

Lazy-loaded features and surfaces may use Framer Motion after their chunk loads. If a lazy import fails, the error boundary renders immediately; do not wait for an exit animation to show a load failure.

---

## What animations must NOT do

- **Never put animation logic in controllers, actions, API files, stores, DTOs, or query hooks.** Those layers expose state only.
- **Never use custom JavaScript animation when Framer Motion or CSS can model the behavior cleanly.**
- **Never use Framer Motion for simple color, border, shadow, or focus transitions.** Use CSS/Tailwind.
- **Never ignore reduced-motion preferences.** Use `MotionConfig reducedMotion="user"` and low-motion variants for large movement.
- **Never animate layout-heavy properties casually.** Prefer opacity and transform.
- **Never delay critical feedback for animation.** Errors, disabled states, pending states, and navigation must update immediately.
- **Never animate large virtualized lists with `layout`.** Virtualization owns positioning.
- **Never define surface animation inside a feature.** Surface shells own drawer/modal animation.
- **Never dynamically import Framer Motion per feature.** It is shared UI infrastructure.
