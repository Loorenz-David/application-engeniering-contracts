# 14 — Styling Contract

## Definition

Tailwind CSS is the styling primitive. `class-variance-authority` (cva) is the component variant system. Together they replace CSS files, CSS modules, and CSS-in-JS. The design token system (Tailwind config) is the single source of truth for spacing, color, and typography.

Animation is governed by [31_animations.md](31_animations.md). CSS handles simple color, border, shadow, and focus transitions; Framer Motion handles structural UI transitions such as surfaces, route transitions, list add/remove, and collapse/expand behavior.

Loading shimmer is governed by [32_loading_skeletons.md](32_loading_skeletons.md). Skeleton gradients and keyframes are centralized in global CSS utilities; components only compose skeleton shapes.

---

## Rules

1. **Utility classes only.** No `.css` files, no CSS modules, no `<style>` tags for component styles.
2. **Design tokens, not raw values.** `text-gray-700` not `text-[#374151]`. `p-4` not `p-[16px]`.
3. **No arbitrary values** (`[...]`) except for one-off layout constraints that cannot be expressed with tokens (e.g., `max-w-[680px]` for a specific content width). Arbitrary values require a comment.
4. **`cn()` for conditional classes.** Never build class strings with template literals.
5. **`cva` for component variants.** Never branch on a prop to return different class strings.

---

## The `cn` utility

```ts
// src/lib/utils.ts
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

`cn` combines `clsx` (conditional class logic) and `tailwind-merge` (deduplication of conflicting Tailwind utilities). Use it everywhere class names are conditional or composed.

```tsx
// Correct
<div className={cn('base-class', isActive && 'text-blue-600', className)} />

// Wrong — template literals don't merge conflicts
<div className={`base-class ${isActive ? 'text-blue-600' : ''} ${className}`} />
```

---

## cva for component variants

```tsx
// src/components/ui/Button.tsx
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  // Base classes applied to every variant
  'inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary: 'bg-blue-600 text-white hover:bg-blue-700 focus-visible:ring-blue-600',
        secondary: 'bg-gray-100 text-gray-900 hover:bg-gray-200 focus-visible:ring-gray-400',
        destructive: 'bg-red-600 text-white hover:bg-red-700 focus-visible:ring-red-600',
        ghost: 'hover:bg-gray-100 text-gray-700',
        link: 'text-blue-600 underline-offset-4 hover:underline',
      },
      size: {
        sm: 'h-8 px-3',
        md: 'h-10 px-4',
        lg: 'h-12 px-6',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: {
      variant: 'primary',
      size: 'md',
    },
  },
);

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants>;

export function Button({ variant, size, className, ...props }: ButtonProps) {
  return (
    <button
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  );
}
```

The `className` prop is always accepted and merged last — it allows callers to add one-off overrides without breaking the component's base styles.

---

## Design tokens in tailwind.config.ts

All colors, spacing, and typography used beyond Tailwind's defaults are defined in the Tailwind config — not hardcoded in component class strings:

```ts
// tailwind.config.ts
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eff6ff',
          500: '#3b82f6',
          600: '#2563eb',
          900: '#1e3a5f',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
};
```

Use `text-brand-600` in components, not `text-[#2563eb]`.

---

## Layout primitives

Common layout patterns use consistent utility combinations:

```
Stack (vertical)   flex flex-col gap-4
Row (horizontal)   flex items-center gap-3
Grid 2-col         grid grid-cols-2 gap-4
Full-width card    rounded-lg border border-gray-200 bg-white p-6
Page container     mx-auto max-w-5xl px-4 py-8
```

Define these as components when they're used more than twice:

```tsx
export function Stack({ children, gap = 4 }: { children: ReactNode; gap?: number }) {
  return <div className={cn('flex flex-col', `gap-${gap}`)}>{children}</div>;
}
```

---

## Dark mode

If dark mode is required, use Tailwind's `dark:` variant. The theme store drives a class on `<html>`:

```ts
// src/store/theme.store.ts applies dark class to <html> element
document.documentElement.classList.toggle('dark', theme === 'dark');
```

```tsx
<p className="text-gray-900 dark:text-gray-100">Content</p>
```

---

## Responsive design

Use Tailwind's responsive prefixes (`sm:`, `md:`, `lg:`) with a mobile-first approach:

```tsx
// Mobile-first: base styles are mobile, larger screens override
<div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
```

---

## What styling must NOT do

- **Never write `.css` files** for component styles. Global resets and `@font-face` declarations in `src/index.css` are the only exception.
- **Never use inline `style` props** for anything expressible as a Tailwind class.
- **Never use CSS transitions for structural UI movement** such as drawers, modals, route transitions, or list reordering. Use the animation contract.
- **Never define skeleton shimmer gradients or keyframes inside components.** Use the centralized skeleton utility.
- **Never hardcode hex colors or pixel values** in className strings. Use design tokens.
- **Never branch on a prop with a ternary to build class strings.** Define the variant in `cva`.
- **Never import Tailwind utility classes from JavaScript variables.** Tailwind's purge scanner cannot detect dynamic class names — use complete class strings.
- **Never use arbitrary values** without a comment explaining why a token does not work here.
