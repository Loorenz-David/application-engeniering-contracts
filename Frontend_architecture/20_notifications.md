# 20 — Message System Contract

## Definition

The message system is the single channel through which the application communicates ephemeral feedback to the user — operation outcomes, warnings, and system alerts. The **logic** (store, configuration, sourcing rules) is identical across every application. The **rendering** (visual shape, position, animation) is app-specific and fully replaceable.

```
Action hook / API client / socket handler
        ↓  calls
notify.success() / notify.error() / ...
        ↓  writes to
Zustand notification store
        ↓  read by
NotificationRenderer  (app-specific UI)
```

No component touches the store directly. Components may use `useNotify()` only for purely local UI events, such as "Copied to clipboard"; mutation, API, auth, and socket outcomes are notified from their owning logic layer.

---

## App-level configuration

Each application sets its own configuration by passing a `NotificationConfig` to `NotificationProvider`. The config object is the only place these values live — nothing in the store or hooks hardcodes them.

```ts
// src/lib/notification-config.ts  (one file per app — varies per project)
import type { NotificationConfig } from '@/store/notifications.store';

export const notificationConfig: NotificationConfig = {
  maxVisible: 4,              // how many notifications can stack at once
  defaultDurations: {
    success: 4000,
    info:    5000,
    warning: 6000,
    error:   8000,
  },
  // persistent: true notifications never auto-dismiss regardless of this config
};
```

Pass it once at the app root:

```tsx
// src/app/providers.tsx
export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <NotificationProvider config={notificationConfig}>
      {children}
      <NotificationRenderer />
    </NotificationProvider>
  );
}
```

---

## Store

```ts
// src/store/notifications.store.ts
import { create } from 'zustand';

export type NotificationType = 'success' | 'error' | 'info' | 'warning';

export type Notification = {
  id:          string;
  type:        NotificationType;
  title:       string;
  message?:    string;
  duration?:   number;    // resolved from config if not provided
  persistent?: boolean;   // if true, never auto-dismisses
};

export type NotificationConfig = {
  maxVisible:       number;
  defaultDurations: Record<NotificationType, number>;
};

type NotificationsState = {
  notifications: Notification[];
  config:        NotificationConfig;
  configure:     (config: Partial<NotificationConfig>) => void;
  notify:        (notification: Omit<Notification, 'id'>) => string;
  dismiss:       (id: string) => void;
  clear:         () => void;
};

// Timer refs live outside the store so they don't cause re-renders
// and can be properly cleared when a notification is dismissed or cleared early.
const _timers = new Map<string, ReturnType<typeof setTimeout>>();

const DEFAULT_CONFIG: NotificationConfig = {
  maxVisible:       4,
  defaultDurations: { success: 4000, info: 5000, warning: 6000, error: 8000 },
};

export const useNotificationsStore = create<NotificationsState>((set, get) => ({
  notifications: [],
  config:        DEFAULT_CONFIG,

  configure: (config) =>
    set((state) => ({ config: { ...state.config, ...config } })),

  notify: (notification) => {
    const { config } = get();
    const id       = crypto.randomUUID();
    const duration = notification.persistent
      ? undefined
      : (notification.duration ?? config.defaultDurations[notification.type]);

    set((state) => {
      // Deduplication — skip if an identical title+type is already visible
      const isDuplicate = state.notifications.some(
        (n) => n.type === notification.type && n.title === notification.title,
      );
      if (isDuplicate) return state;

      const next = [...state.notifications, { ...notification, id, duration }];

      // Enforce max visible — dismiss the oldest when at capacity
      if (next.length > config.maxVisible) {
        const evicted = next.splice(0, next.length - config.maxVisible);
        evicted.forEach((n) => {
          const t = _timers.get(n.id);
          if (t) { clearTimeout(t); _timers.delete(n.id); }
        });
      }

      return { notifications: next };
    });

    if (duration) {
      const timer = setTimeout(() => {
        get().dismiss(id);
        _timers.delete(id);
      }, duration);
      _timers.set(id, timer);
    }

    return id;
  },

  dismiss: (id) => {
    const timer = _timers.get(id);
    if (timer) { clearTimeout(timer); _timers.delete(id); }

    set((state) => ({
      notifications: state.notifications.filter((n) => n.id !== id),
    }));
  },

  clear: () => {
    _timers.forEach((t) => clearTimeout(t));
    _timers.clear();
    set({ notifications: [] });
  },
}));
```

---

## `notify` — global singleton (non-React)

Action hooks, the API client, socket handlers, and any non-component code use the global `notify` object. It reads from the store via `getState()` — no hook required.

```ts
// src/lib/notify.ts
import { useNotificationsStore } from '@/store/notifications.store';

export const notify = {
  success:    (title: string, message?: string) =>
    useNotificationsStore.getState().notify({ type: 'success', title, message }),

  error:      (title: string, message?: string) =>
    useNotificationsStore.getState().notify({ type: 'error', title, message }),

  info:       (title: string, message?: string) =>
    useNotificationsStore.getState().notify({ type: 'info', title, message }),

  warning:    (title: string, message?: string) =>
    useNotificationsStore.getState().notify({ type: 'warning', title, message }),

  persistent: (type: 'error' | 'warning', title: string, message?: string) =>
    useNotificationsStore.getState().notify({ type, title, message, persistent: true }),

  dismiss:    (id: string) =>
    useNotificationsStore.getState().dismiss(id),
};
```

`notify` is a plain object — it can be imported anywhere, including inside `onSuccess`, `onError`, `onSettled` callbacks where hooks cannot be called.

---

## `useNotify` — React hook

Components that need to trigger notifications based on local events (button clicks, keyboard shortcuts) use `useNotify`. It wraps the same `notify` singleton but is more idiomatic inside JSX.

```ts
// src/hooks/use-notify.ts
import { notify } from '@/lib/notify';

// Re-export the singleton as a hook for consistent import patterns in components.
// All methods are stable references — no re-renders triggered.
export function useNotify() {
  return notify;
}
```

---

## `NotificationProvider`

The provider applies app-level configuration to the store. It renders nothing — the renderer is separate.

```tsx
// src/features/notifications/NotificationProvider.tsx
import { useEffect } from 'react';
import { useNotificationsStore } from '@/store/notifications.store';
import type { NotificationConfig } from '@/store/notifications.store';

type Props = {
  config?: Partial<NotificationConfig>;
  children: React.ReactNode;
};

export function NotificationProvider({ config, children }: Props) {
  const configure = useNotificationsStore((s) => s.configure);

  useEffect(() => {
    if (config) configure(config);
  }, []);  // intentionally runs once — config is static per app

  return <>{children}</>;
}
```

---

## `NotificationRenderer` — app-specific

The renderer is the only part that varies between applications. It reads `notifications` from the store and renders them using whatever UI primitives the app provides. Replace this file entirely per project.

```tsx
// src/features/notifications/NotificationRenderer.tsx
import { useNotificationsStore } from '@/store/notifications.store';
// Toast is a shared UI primitive defined in this project's design system
import { Toast } from '@/components/ui/Toast';

export function NotificationRenderer() {
  const notifications = useNotificationsStore((s) => s.notifications);
  const dismiss       = useNotificationsStore((s) => s.dismiss);

  return (
    // Position, z-index, and spacing are app-specific — adjust per project
    <div
      role="region"
      aria-label="Notifications"
      aria-live="polite"
      aria-atomic="false"
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none"
    >
      {notifications.map((n) => (
        <Toast
          key={n.id}
          notification={n}
          onDismiss={() => dismiss(n.id)}
          className="pointer-events-auto"
        />
      ))}
    </div>
  );
}
```

Mount the renderer outside the router so it is always visible regardless of the current route:

```tsx
// src/app/providers.tsx
export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <NotificationProvider config={notificationConfig}>
      {children}
      <NotificationRenderer />  {/* outside router — always visible */}
    </NotificationProvider>
  );
}
```

---

## Message sourcing rules

Every message has exactly one canonical source. The same event is never notified from two places.

| Source | What it notifies | How |
|---|---|---|
| **Action hook** `onSuccess` | Operation confirmed | `notify.success(...)` |
| **Action hook** `onError` | Operation failed + data preserved hint | `notify.error(...)` |
| **`AuthProvider`** session expiry | Session ended | `notify.warning(...)` before redirect |
| **`SocketProvider`** `connect_error` | Connection lost | `notify.warning(...)` |
| **Real-time event handler** | Server-pushed alert | `notify.info(...)` |
| **`NotificationRenderer`** | Never — renderer only reads, never writes | — |
| **Component** | Only for user-triggered local events (e.g. "Copied to clipboard") | `useNotify()` |

Action hooks are the primary source. They are called from every mutation in the app and are the correct layer for communicating outcome.

```ts
// features/invoices/actions/use-create-invoice.ts
import { notify } from '@/lib/notify';

const mutation = useMutation({
  mutationFn: createInvoice,
  onMutate:   async (input) => { /* optimistic update */ },
  onSuccess:  ()    => notify.success('Invoice created'),
  onError:    (err) => notify.error('Failed to create invoice', err.message),
  onSettled:  ()    => queryClient.invalidateQueries({ queryKey: invoiceKeys.lists() }),
});
```

For failure recovery, pair the error notification with the recovery hint (see [08_hooks.md](08_hooks.md)):

```ts
onError: (err, input, context) => {
  // 1. Roll back cache
  context?.previousLists.forEach(([key, data]) => queryClient.setQueryData(key, data));
  // 2. Notify with recovery hint
  notify.error('Invoice not saved', 'Your changes are preserved. Fix the issue and try again.');
},
```

---

## Persistent notifications

Use persistent notifications for conditions that require deliberate user action — not for transient errors.

```ts
// Session expired — user must sign in again
notify.persistent('warning', 'Session expired', 'Sign in to continue.');

// Maintenance mode alert from server
notify.persistent('warning', 'Scheduled maintenance in 10 minutes', 'Save your work.');
```

Persistent notifications render with an explicit close button. They do not auto-dismiss.

---

## What the message system must NOT do

- **Never call `notify` from a component in response to a mutation.** Mutations fire from action hooks; notifications fire there too.
- **Never notify from `NotificationRenderer`.** The renderer reads; it never writes.
- **Never hardcode durations or `maxVisible` in the store.** They come from `notificationConfig` via the provider.
- **Never fire two notifications for the same event from two different layers.** If the action hook fires `notify.error`, the controller must not also fire one.
- **Never use persistent notifications for success messages.** Users should not need to dismiss confirmations.
- **Never show validation errors through the notification system.** Field-level errors belong on the form via `form.setError` (see [09_forms.md](09_forms.md)).
- **Never access `useNotificationsStore` directly from a component.** Use `useNotify()`.
- **Never call `useNotify()` inside a mutation callback.** Callbacks are not hook call sites — use the `notify` singleton instead.
