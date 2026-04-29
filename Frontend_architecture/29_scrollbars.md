# 29 — Scrollbar Contract

## Definition

Custom scrollbars are implemented entirely in CSS. No JavaScript, no virtual scroll libraries, no DOM wrappers. The browser renders the scrollbar natively — the CSS only controls appearance.

Two CSS APIs cover all modern browsers:

| API | Browsers | What it controls |
|---|---|---|
| `::-webkit-scrollbar` pseudo-elements | Chrome, Safari, Edge | Full control: width, track, thumb, hover, corner |
| `scrollbar-width` + `scrollbar-color` | Firefox + W3C standard | Width preset and thumb/track color |

Both are applied together. The result is a consistent thin, modern scrollbar across all browsers with zero runtime cost.

---

## Global styles

Applied once in `src/styles/global.css` inside `@layer base`. Every scrollable element in the app inherits these automatically.

```css
/* src/styles/global.css */
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    /* Scrollbar design tokens — override per app in tailwind.config.ts */
    --scrollbar-size:        5px;
    --scrollbar-thumb:       hsl(var(--muted-foreground) / 0.25);
    --scrollbar-thumb-hover: hsl(var(--muted-foreground) / 0.5);
    --scrollbar-track:       transparent;
    --scrollbar-radius:      9999px;  /* pill shape */
  }

  /* W3C standard — Firefox */
  * {
    scrollbar-width: thin;
    scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);
  }

  /* Webkit — Chrome, Safari, Edge */
  *::-webkit-scrollbar {
    width:  var(--scrollbar-size);   /* vertical scrollbar */
    height: var(--scrollbar-size);   /* horizontal scrollbar */
  }

  *::-webkit-scrollbar-track {
    background:    var(--scrollbar-track);
    border-radius: var(--scrollbar-radius);
  }

  *::-webkit-scrollbar-thumb {
    background:    var(--scrollbar-thumb);
    border-radius: var(--scrollbar-radius);
  }

  *::-webkit-scrollbar-thumb:hover {
    background: var(--scrollbar-thumb-hover);
  }

  *::-webkit-scrollbar-corner {
    background: transparent;
  }
}
```

Because the tokens use HSL variables (`hsl(var(--muted-foreground) / 0.25)`), the scrollbar automatically adapts to dark mode when the `--muted-foreground` token changes — no separate dark mode rule needed.

---

## Design token wiring

`--muted-foreground` must be defined as a bare HSL value (no `hsl()` wrapper) so the `/ 0.25` opacity syntax works:

```css
/* src/styles/global.css — inside :root */
--muted-foreground: 215 16% 47%;   /* bare HSL — no hsl() wrapper */

.dark {
  --muted-foreground: 215 20% 65%;
}
```

This is the same format shadcn/ui uses. If the project uses a different design token format, adjust the `--scrollbar-thumb` value to match.

---

## Scroll container rule

Scroll containers in the app shell and surface components should include `scrollbar-gutter: stable` to prevent layout shift when a scrollbar appears or disappears as content grows.

```tsx
{/* AuthenticatedLayout — main content area */}
<main className="flex-1 overflow-y-auto [scrollbar-gutter:stable]">
  <Outlet />
</main>

{/* DrawerSurface — content area */}
<div className="flex-1 overflow-y-auto [scrollbar-gutter:stable]">
  {children}
</div>
```

`scrollbar-gutter: stable` reserves the 5px scrollbar gutter even when no scrollbar is visible. This prevents the content from shifting when a scrollbar appears. At 5px it is barely perceptible.

---

## `ScrollArea` — the scroll primitive

Making a container scrollable manually means remembering to combine `overflow-y-auto`, `min-h-0`, `overscroll-contain`, and `scrollbar-gutter` every time. Missing any one of them causes subtle layout bugs that are hard to trace. The `ScrollArea` shared primitive applies all of them by default.

```tsx
// src/components/ui/ScrollArea.tsx
import { forwardRef } from 'react';
import { cn } from '@/lib/utils';

type ScrollDirection = 'y' | 'x' | 'both';

type ScrollAreaProps = {
  direction?: ScrollDirection;
  className?: string;
  children:   React.ReactNode;
} & React.HTMLAttributes<HTMLDivElement>;

export const ScrollArea = forwardRef<HTMLDivElement, ScrollAreaProps>(
  ({ direction = 'y', className, children, ...props }, ref) => (
    <div
      ref={ref}
      data-scroll-area={direction}
      className={cn(
        // Overrides flex item min-height/min-width: auto — the #1 source of scroll bugs
        'min-h-0 min-w-0',
        // Scroll direction
        direction === 'y'    && 'overflow-y-auto overflow-x-hidden',
        direction === 'x'    && 'overflow-x-auto overflow-y-hidden',
        direction === 'both' && 'overflow-auto',
        // Prevents scroll chaining to parent when this container reaches its boundary
        direction === 'y'    && 'overscroll-y-contain',
        direction === 'x'    && 'overscroll-x-contain',
        direction === 'both' && 'overscroll-contain',
        // Reserves scrollbar gutter — prevents layout shift when scrollbar appears
        '[scrollbar-gutter:stable]',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  ),
);
ScrollArea.displayName = 'ScrollArea';
```

`ScrollArea` deliberately does **not** set a height. The caller controls the height constraint — that is the only thing that varies per use case. Everything else is standardized.

```tsx
// Fixed height
<ScrollArea className="h-64">
  {list}
</ScrollArea>

// Fill remaining flex space
<div className="flex flex-col h-full">
  <header className="flex-shrink-0 h-14">...</header>
  <ScrollArea className="flex-1">   {/* min-h-0 is already inside — no need to add it */}
    {content}
  </ScrollArea>
</div>

// Viewport-relative max height
<ScrollArea className="max-h-[60vh]">
  {content}
</ScrollArea>

// Horizontal scroll (table, carousel)
<ScrollArea direction="x" className="w-full">
  <table className="w-max">...</table>
</ScrollArea>

// Named — agent-addressable
<ScrollArea className="flex-1" data-scroll-area="invoice-list">
  {invoices}
</ScrollArea>
```

The `data-scroll-area` attribute is forwarded via `...props` — pass it as a regular prop when the area needs to be agent-addressable.

---

## Scroll conflict rules

These are the five patterns that cause scroll to break. Each has one correct fix.

### 1 — Flex column without `min-h-0`

The most common bug. Flex items have `min-height: auto` by default, which allows them to grow past their container and prevents scrolling.

```tsx
// Wrong — flex child expands past container, never scrolls
<div className="flex flex-col h-screen">
  <header className="h-14 flex-shrink-0" />
  <div className="flex-1 overflow-y-auto">   {/* expands to content height, ignores overflow */}
    {longContent}
  </div>
</div>

// Correct — ScrollArea has min-h-0 built in
<div className="flex flex-col h-screen">
  <header className="h-14 flex-shrink-0" />
  <ScrollArea className="flex-1">
    {longContent}
  </ScrollArea>
</div>

// Correct — if writing raw Tailwind (no ScrollArea)
<div className="flex flex-col h-screen">
  <header className="h-14 flex-shrink-0" />
  <div className="flex-1 min-h-0 overflow-y-auto overscroll-y-contain">
    {longContent}
  </div>
</div>
```

### 2 — Scroll chaining to parent

When the user reaches the end of a scroll container, the scroll event propagates to the parent — scrolling the page or an outer container. `overscroll-contain` stops this at the boundary.

```tsx
// Wrong — inner list scrolls the page when it hits the end
<ScrollArea className="flex-1">       {/* page-level scroll */}
  <div className="overflow-y-auto h-48">   {/* chains to page on boundary */}
    {innerList}
  </div>
</ScrollArea>

// Correct — nested scroll areas both use ScrollArea
<ScrollArea className="flex-1">
  <ScrollArea className="h-48">       {/* overscroll-contain stops here */}
    {innerList}
  </ScrollArea>
</ScrollArea>
```

### 3 — `overflow-hidden` on an ancestor clipping the scrollbar

`overflow: hidden` creates a new block formatting context and clips everything — including child scrollbars. Use `overflow-clip` when you only need visual clipping (e.g. for `border-radius`) but still want child scroll to work.

```tsx
// Wrong — rounded card clips the inner scrollbar
<div className="rounded-xl overflow-hidden">
  <ScrollArea className="h-64">      {/* scrollbar clipped, may not scroll */}
    {content}
  </ScrollArea>
</div>

// Correct — overflow-clip clips visually without blocking child scroll
<div className="rounded-xl overflow-clip">
  <ScrollArea className="h-64">      {/* scrollbar works */}
    {content}
  </ScrollArea>
</div>

// Also correct — if the rounded element has no scrollable children, overflow-hidden is fine
<div className="rounded-xl overflow-hidden">
  <img src="..." />   {/* no scroll container, overflow-hidden is correct here */}
</div>
```

### 4 — `position: fixed` inside a scroll container

`fixed` elements inside a scroll container are positioned relative to the viewport, not the scroll container. They appear to "escape" the container and don't scroll with the content. Use `sticky` instead.

```tsx
// Wrong — fixed header escapes the scroll container, covers unrelated content
<ScrollArea className="flex-1">
  <div className="fixed top-0 z-10 bg-background">   {/* positioned to viewport */}
    Sticky section header
  </div>
  {sectionContent}
</ScrollArea>

// Correct — sticky header stays inside the scroll container
<ScrollArea className="flex-1">
  <div className="sticky top-0 z-10 bg-background">  {/* positioned within scroll parent */}
    Sticky section header
  </div>
  {sectionContent}
</ScrollArea>
```

### 5 — Missing height on the scroll container

A scroll container with no height constraint and no flex parent will always expand to fit its content. It will never scroll.

```tsx
// Wrong — no height defined, grows to content size
<ScrollArea>        {/* expands forever, overflow never triggers */}
  {longContent}
</ScrollArea>

// Correct — height constraint is always the caller's responsibility
<ScrollArea className="h-96">           {/* fixed */}
<ScrollArea className="flex-1">         {/* flex fill — parent must have a height */}
<ScrollArea className="max-h-[50vh]">   {/* viewport-relative cap */}
```

---

## Utility classes

Define these in `@layer utilities` for components that need to override the global defaults.

```css
@layer utilities {
  /* Completely hide the scrollbar (still scrollable) */
  .scrollbar-hidden {
    scrollbar-width: none;
  }
  .scrollbar-hidden::-webkit-scrollbar {
    display: none;
  }

  /* Restore browser default scrollbar */
  .scrollbar-default {
    scrollbar-width: auto;
    scrollbar-color: auto;
  }
  .scrollbar-default::-webkit-scrollbar {
    width:  revert;
    height: revert;
  }

  /* Wider scrollbar for data-heavy areas (tables, code blocks) */
  .scrollbar-wide {
    scrollbar-width: auto;
    scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);
  }
  .scrollbar-wide::-webkit-scrollbar {
    width:  10px;
    height: 10px;
  }
}
```

Usage:

```tsx
{/* Horizontal scroll — hide bar, swipe to scroll */}
<div className="overflow-x-auto scrollbar-hidden">
  <table>...</table>
</div>

{/* Code block — slightly wider for horizontal scroll visibility */}
<pre className="overflow-x-auto scrollbar-wide">
  <code>...</code>
</pre>
```

---

## Horizontal scrollbars

The global `--scrollbar-size` applies to both vertical (width) and horizontal (height). A 5px horizontal scrollbar is visible and functional. For touch devices, horizontal scroll is handled by native momentum scrolling — the scrollbar is rarely visible.

For carousels and horizontal scroll areas intended for mouse users, the scrollbar should be visible:

```tsx
{/* Horizontal carousel — scrollbar intentionally visible */}
<div className="overflow-x-auto pb-2">  {/* pb-2 gives the scrollbar breathing room */}
  <div className="flex gap-4 w-max">
    {items.map(item => <Card key={item.id} {...item} />)}
  </div>
</div>
```

The `pb-2` padding prevents the scrollbar from overlapping the bottom of card content.

---

## Performance

CSS scrollbar styling has zero JavaScript overhead. The browser composites the scrollbar on the GPU layer alongside the scroll container — it does not trigger layout or paint on scroll.

Things that DO affect scroll performance (avoid these):

```tsx
// Wrong — onScroll fires on every frame, blocks the main thread
<div onScroll={handleScroll} className="overflow-y-auto">

// Wrong — reading scrollTop causes layout recalculation
element.addEventListener('scroll', () => {
  const pos = element.scrollTop;  // forces reflow
});

// Correct — use IntersectionObserver for scroll-triggered effects
const observer = new IntersectionObserver(callback, { threshold: 0.1 });
observer.observe(targetElement);

// Correct — use scroll-driven animations (CSS) for scroll-linked effects
// No JS at all for scroll position → visual change mappings
```

---

## Agent interface consideration

Scrollable areas that an AI agent may need to interact with (scroll to a position, scroll to find an element) should be identifiable. Add a `data-scroll-area` attribute to named scroll containers:

```tsx
<main
  data-scroll-area="page-content"
  className="flex-1 overflow-y-auto [scrollbar-gutter:stable]"
>
  <Outlet />
</main>

<div
  data-scroll-area="drawer-content"
  className="flex-1 overflow-y-auto [scrollbar-gutter:stable]"
>
  {children}
</div>
```

An agent interface can then target `[data-scroll-area="page-content"]` to scroll programmatically without coupling to DOM structure.

---

## What scrollbars must NOT do

- **Never use a JavaScript scrollbar library** (`simplebar`, `perfect-scrollbar`, `overlayscrollbars`) for visual styling alone. CSS handles it with zero overhead.
- **Never set `overflow: hidden` to hide a scrollbar** if the area should still be scrollable. Use `scrollbar-hidden` utility class instead.
- **Never listen to `scroll` events to drive visual effects.** Use `IntersectionObserver` for element visibility, or CSS scroll-driven animations for scroll-position-linked visuals.
- **Never hardcode scrollbar colors.** Use the CSS token variables so the scrollbar adapts to dark mode and per-app theming automatically.
- **Never apply custom scrollbar styles inline on a component.** All scrollbar appearance is global (`@layer base`) or utility class (`@layer utilities`) — never inline.
- **Never set `scrollbar-width: none` on the `<body>` or `<html>`.** Hiding the document-level scrollbar causes layout shift on pages that need it.
- **Never use raw `overflow-y-auto` on a flex child without `min-h-0`.** It will not scroll. Use `ScrollArea` instead — `min-h-0` is built in.
- **Never use `overflow-hidden` on a parent of a `ScrollArea`.** It clips the scrollbar. Use `overflow-clip` for visual clipping, `overflow-hidden` only when there are no scrollable descendants.
- **Never use `position: fixed` inside a scroll container.** Use `sticky` instead.
- **Never create a `ScrollArea` without a height constraint.** The caller is always responsible for providing `h-*`, `flex-1`, or `max-h-*`.
