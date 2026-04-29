# 21 — Real-Time Contract

## Definition

Real-time updates are pushed from the backend via Socket.io. Backend workers emit events when entities change. The frontend reacts by invalidating the relevant TanStack Query cache entries — TanStack Query then decides whether to fetch immediately (if a component is actively observing that data) or lazily (if the user has navigated away).

Event payload IDs are public client-facing IDs, matching API responses, route params, and query keys. Socket events never expose backend database primary keys.

```
Backend worker
      ↓  emits
Socket.io event  ('invoice:updated', { id })
      ↓  received by
SocketProvider   (one connection, all handlers registered here)
      ↓  calls
Event handler    (declared by the feature, assembled at app level)
      ↓  calls
queryClient.invalidateQueries({ queryKey, refetchType: 'active' })
      ↓
TanStack Query:  user is viewing that invoice → refetch now
                 user is on another page      → mark stale, refetch on next visit
```

The frontend never fetches speculatively. It reacts to events and lets TanStack Query decide when the network round-trip is worth making.

---

## Typed events

All events are defined in one file. This is the contract between the backend and the frontend — every event the server can emit and every event the client can emit must appear here.

```ts
// src/lib/socket-types.ts
import type { InvoiceId, WorkspaceId, UserId } from '@/types/common';

// Events the server (workers + API) emits to the client
export type ServerToClientEvents = {
  // Single-entity events — one change, one ID
  'invoice:created': (payload: { id: InvoiceId; workspace_id: WorkspaceId }) => void;
  'invoice:updated': (payload: { id: InvoiceId; workspace_id: WorkspaceId }) => void;
  'invoice:deleted': (payload: { id: InvoiceId; workspace_id: WorkspaceId }) => void;
  'payment:processed': (payload: { invoice_id: InvoiceId; status: string })  => void;

  // Batch events — worker processed many entities at once, one event with N ids
  'invoice:batch-updated': (payload: { ids: InvoiceId[] }) => void;
  'invoice:batch-deleted': (payload: { ids: InvoiceId[] }) => void;

  // Notification events — emitted by workers to surface alerts
  'notification:new': (payload: { id: string; type: string; title: string; message?: string }) => void;

  // System events — connection and session management
  'auth:session-expired': (payload: Record<string, never>) => void;
};

// Events the client emits to the server
export type ClientToServerEvents = {
  'room:join':  (payload: { room: string }, ack: (ok: boolean) => void) => void;
  'room:leave': (payload: { room: string }) => void;
};

export type AppSocket = import('socket.io-client').Socket<
  ServerToClientEvents,
  ClientToServerEvents
>;
```

---

## Socket event registry

Each feature declares its socket event handlers in a `socket-events.ts` file. Handlers receive the typed event payload and a context object with `queryClient` and `notify` — everything needed to react to an event without importing feature internals.

```ts
// src/lib/socket-registry-types.ts
import type { QueryClient }          from '@tanstack/react-query';
import type { ServerToClientEvents } from '@/lib/socket-types';
import type { notify }               from '@/lib/notify';

export type SocketHandlerContext = {
  queryClient: QueryClient;
  notify:      typeof notify;
};

// Each handler matches the payload type of its event from ServerToClientEvents
export type SocketEventHandlers = {
  [K in keyof ServerToClientEvents]?: (
    payload: Parameters<ServerToClientEvents[K]>[0],
    ctx:     SocketHandlerContext,
  ) => void;
};
```

### Per-feature declaration

```ts
// features/invoices/socket-events.ts
import { invoiceKeys }         from './api/invoice-keys';
import type { SocketEventHandlers } from '@/lib/socket-registry-types';
import type { InvoiceId }      from '@/types/common';

export const invoiceSocketEvents: SocketEventHandlers = {
  'invoice:created': (_payload, { queryClient }) => {
    // New invoice — invalidate all lists (refetches only if list is currently viewed)
    queryClient.invalidateQueries({
      queryKey:    invoiceKeys.lists(),
      refetchType: 'active',
    });
  },

  'invoice:updated': ({ id }, { queryClient }) => {
    // Invalidate detail + lists — refetches only the ones currently observed
    queryClient.invalidateQueries({
      queryKey:    invoiceKeys.detail(id as InvoiceId),
      refetchType: 'active',
    });
    queryClient.invalidateQueries({
      queryKey:    invoiceKeys.lists(),
      refetchType: 'active',
    });
  },

  'invoice:deleted': ({ id }, { queryClient }) => {
    // Remove the detail cache entirely — no point keeping deleted data
    queryClient.removeQueries({ queryKey: invoiceKeys.detail(id as InvoiceId) });
    queryClient.invalidateQueries({
      queryKey:    invoiceKeys.lists(),
      refetchType: 'active',
    });
  },

  'payment:processed': ({ invoice_id }, { queryClient, notify }) => {
    // Payment is a critical state change — notify the user regardless of their position
    notify.success('Payment processed', `Invoice ${invoice_id} has been paid.`);
    queryClient.invalidateQueries({
      queryKey:    invoiceKeys.detail(invoice_id as InvoiceId),
      refetchType: 'active',
    });
  },
};
```

### App-level assembly

```ts
// src/app/socket-registry.ts
import { invoiceSocketEvents }  from '@/features/invoices/socket-events';
import { settingsSocketEvents } from '@/features/settings/socket-events';
import { clientSocketEvents }   from '@/features/clients/socket-events';
import type { SocketEventHandlers } from '@/lib/socket-registry-types';

export const socketRegistry: SocketEventHandlers = {
  ...invoiceSocketEvents,
  ...settingsSocketEvents,
  ...clientSocketEvents,

  // System-level handlers defined at the app level, not in any feature
  'notification:new': ({ type, title, message }, { notify }) => {
    if (type === 'success' || type === 'error' || type === 'info' || type === 'warning') {
      notify[type](title, message);
    }
  },

  'auth:session-expired': (_payload, _ctx) => {
    window.dispatchEvent(new CustomEvent('auth:session-expired'));
  },
};
```

No feature imports another feature's event handlers. The registry is the only join point.

---

## `SocketProvider`

One socket connection per authenticated session. The provider creates the connection, exposes it via context, tracks connection status, joins the global rooms, applies every handler from the registry, and tears everything down on sign-out.

```tsx
// src/providers/SocketProvider.tsx
import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { io }                        from 'socket.io-client';
import { useQueryClient }            from '@tanstack/react-query';
import { env }                       from '@/lib/env';
import { getAccessToken, refreshAccessToken } from '@/lib/auth-token';
import { useAuthStore, selectIsAuthenticated } from '@/store/auth.store';
import { socketRegistry }            from '@/app/socket-registry';
import { notify }                    from '@/lib/notify';
import type { AppSocket }            from '@/lib/socket-types';

// --- Contexts ---

const SocketContext = createContext<AppSocket | null>(null);

export type SocketStatus = {
  connected:    boolean;
  reconnecting: boolean;
};

const SocketStatusContext = createContext<SocketStatus>({
  connected:    false,
  reconnecting: false,
});

// --- Hooks ---

/** Returns the socket instance. Use only to emit events — never to attach listeners. */
export function useSocket(): AppSocket | null {
  return useContext(SocketContext);
}

/** Returns live connection status for UI indicators ("Live" / "Reconnecting…"). */
export function useSocketStatus(): SocketStatus {
  return useContext(SocketStatusContext);
}

// --- Provider ---

export function SocketProvider({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore(selectIsAuthenticated);
  const workspaceId     = useAuthStore((s) => s.workspaceId);
  const userId          = useAuthStore((s) => s.user?.id);
  const queryClient     = useQueryClient();

  // socketRef — stable reference for use inside callbacks (avoids stale closure)
  // socket state — triggers context re-render when socket connects or disconnects
  const socketRef = useRef<AppSocket | null>(null);
  const [socket,  setSocket] = useState<AppSocket | null>(null);
  const [status,  setStatus] = useState<SocketStatus>({ connected: false, reconnecting: false });

  useEffect(() => {
    if (!isAuthenticated) {
      socketRef.current?.disconnect();
      socketRef.current = null;
      setSocket(null);
      setStatus({ connected: false, reconnecting: false });
      return;
    }

    const s = io(env.VITE_WS_URL, {
      // Function form — called on every connection attempt (initial + reconnects).
      // Ensures the socket always uses the current in-memory token, not a snapshot
      // captured at creation time that becomes stale after a JWT refresh.
      auth:                 (cb) => cb({ token: getAccessToken() }),
      transports:           ['websocket'],
      reconnectionAttempts: 10,
      reconnectionDelay:    1_000,
      reconnectionDelayMax: 30_000,
    }) as AppSocket;

    socketRef.current = s;
    setSocket(s);

    // --- Auth error handling ---
    s.on('connect_error', async (err) => {
      if (err.message === 'unauthorized') {
        const refreshed = await refreshAccessToken();
        if (!refreshed) {
          window.dispatchEvent(new CustomEvent('auth:session-expired'));
        }
      }
    });

    // --- Connection status ---
    s.on('connect', () => {
      setStatus({ connected: true, reconnecting: false });

      // Global rooms — joined on every connect, including after reconnect.
      // Rooms are server-side and lost on disconnect.
      if (workspaceId) {
        s.emit('room:join', { room: `workspace:${workspaceId}` }, (ok) => {
          if (!ok && import.meta.env.DEV) console.warn('[Socket] Failed to join workspace room');
        });
      }
      if (userId) {
        s.emit('room:join', { room: `user:${userId}` }, () => {});
      }

      // Missed events: events emitted by the server while the client was disconnected
      // are lost — Socket.io has no replay mechanism. Invalidating active queries on
      // reconnect is the mitigation: the UI catches up to server state immediately.
      queryClient.invalidateQueries({ refetchType: 'active' });
    });

    s.on('disconnect', () => {
      setStatus({ connected: false, reconnecting: true });
    });

    s.on('reconnect_failed', () => {
      // All reconnection attempts exhausted — socket will not retry automatically.
      setStatus({ connected: false, reconnecting: false });
      notify.persistent('warning', 'Connection lost', 'Refresh the page to reconnect.');
    });

    // --- Event registry ---
    // All feature handlers registered in one loop. Wrapped in try/catch so a
    // handler bug cannot crash the provider or silence subsequent events.
    const ctx = { queryClient, notify };

    Object.entries(socketRegistry).forEach(([event, handler]) => {
      s.on(event as string, (payload: unknown) => {
        try {
          (handler as (p: unknown, c: typeof ctx) => void)(payload, ctx);
        } catch (err) {
          if (import.meta.env.DEV) {
            console.error(`[Socket] Handler error for "${event}":`, err);
          }
        }
      });
    });

    return () => {
      s.disconnect();
      socketRef.current = null;
      setSocket(null);
      setStatus({ connected: false, reconnecting: false });
    };
  }, [isAuthenticated, workspaceId, userId, queryClient]);

  return (
    <SocketContext.Provider value={socket}>
      <SocketStatusContext.Provider value={status}>
        {children}
      </SocketStatusContext.Provider>
    </SocketContext.Provider>
  );
}
```

Add `SocketProvider` inside `QueryClientProvider` and inside the auth lifecycle — it needs `useQueryClient()` and authenticated identity to be available. If `AuthProvider` lives in the router root route, `SocketProvider` usually lives inside it:

```tsx
// src/app/RootRoute.tsx
export function RootRoute() {
  return (
    <AuthProvider>
      <SocketProvider>
        <SurfaceProvider>
          <Outlet />
        </SurfaceProvider>
      </SocketProvider>
    </AuthProvider>
  );
}
```

The outer app providers still wrap `RouterProvider` with `QueryClientProvider`, `MotionConfig`, and other router-independent providers:

```tsx
// src/app/providers.tsx
export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <MotionConfig reducedMotion="user">
        <LazyMotion features={domAnimation}>
          <BreakpointProvider>
            {children}
            <NotificationRenderer />
          </BreakpointProvider>
        </LazyMotion>
      </MotionConfig>
    </QueryClientProvider>
  );
}
```

---

## Socket token lifecycle

```
App boots → initSession() → access token stored in auth-token.ts (in-memory)
         → SocketProvider mounts → socket connects → auth fn returns current token ✓

JWT expires → HTTP 401 → refreshAccessToken() → new token stored in auth-token.ts
           → socket auto-reconnects → auth fn returns new token ✓

Token expired before handshake → connect_error('unauthorized') → refreshAccessToken()
           → Socket.io retries → auth fn returns refreshed token ✓

Refresh fails → auth:session-expired dispatched → AuthProvider redirects
             → isAuthenticated → false → SocketProvider disconnects ✓

Sign-out → isAuthenticated → false → SocketProvider disconnects immediately ✓
```

The socket never holds a token value. Only the function that reads the current one.

---

## The "frontend decides" mechanism

`refetchType: 'active'` is the key. When a socket event arrives and a handler calls `invalidateQueries`, TanStack Query checks whether any mounted component is currently observing that cache entry:

| Observer state | What happens |
|---|---|
| Active observer (component is mounted and using this query) | Refetch fires immediately |
| No observer (user is on another page or surface is closed) | Cache marked stale — refetches when next observed |
| Query is disabled (`enabled: false`) | Marked stale but not refetched |

This means event handlers never need to check "is the user currently on this page?" — TanStack Query handles it. The handler always invalidates; the query layer decides whether the network round-trip is worth making now.

```ts
// This is all you need in a handler — TanStack Query does the rest
queryClient.invalidateQueries({
  queryKey:    invoiceKeys.detail(id),
  refetchType: 'active',   // refetch now if observed, mark stale if not
});
```

For critical state changes where the user needs to know regardless of their position (payment confirmed, task assigned to them), combine invalidation with a notification:

```ts
'payment:processed': ({ invoice_id }, { queryClient, notify }) => {
  notify.success('Payment confirmed');                          // always shown
  queryClient.invalidateQueries({ queryKey: ..., refetchType: 'active' });  // refetch if viewing
},
```

---

## Debouncing high-frequency events

Background workers processing bulk operations (50 invoices updated at once) emit 50 events in rapid succession. Each invalidation would trigger a refetch. Use `debouncedInvalidation` for events that are high-frequency by nature.

```ts
// src/lib/socket-debounce.ts
const _timers = new Map<string, ReturnType<typeof setTimeout>>();

export function debouncedInvalidation(
  queryClient: QueryClient,
  queryKey:    unknown[],
  delay = 300,
): void {
  const key = JSON.stringify(queryKey);
  const existing = _timers.get(key);
  if (existing) clearTimeout(existing);

  _timers.set(key, setTimeout(() => {
    queryClient.invalidateQueries({ queryKey, refetchType: 'active' });
    _timers.delete(key);
  }, delay));
}
```

Use it in handlers for entity types that bulk operations target:

```ts
'invoice:updated': ({ id }, { queryClient }) => {
  // Detail is always immediate — only one thing changes at a time
  queryClient.invalidateQueries({
    queryKey:    invoiceKeys.detail(id as InvoiceId),
    refetchType: 'active',
  });

  // Lists may receive many rapid invalidations from bulk ops — debounce
  debouncedInvalidation(queryClient, invoiceKeys.lists(), 300);
},
```

---

## Batch event handling

A batch event carries an array of IDs from a single worker operation. The handler must not blindly refetch all of them — most of those IDs are not in view. There are three possible states for each ID and the handler must act differently for each:

| Cache state | `refetchType` | Effect |
|---|---|---|
| In cache + active observer | `'active'` (default) | Refetch now — user is looking at it |
| In cache + no observer | `'none'` | Mark stale only — refetch when they navigate to it |
| Not in cache at all | Skip | Nothing to update — will fetch fresh on first access |

```ts
// src/lib/socket-batch.ts
import type { QueryClient, QueryKey } from '@tanstack/react-query';

type BatchInvalidationOptions = {
  queryClient: QueryClient;
  ids:         string[];
  toQueryKey:  (id: string) => QueryKey;   // maps an ID to its detail query key
  listKey:     QueryKey;                   // the list key to always invalidate
};

export function batchInvalidation({
  queryClient,
  ids,
  toQueryKey,
  listKey,
}: BatchInvalidationOptions): void {
  const cache = queryClient.getQueryCache();

  ids.forEach((id) => {
    const queryKey = toQueryKey(id);
    const query    = cache.find({ queryKey, exact: true });

    if (!query) return;  // not in cache — nothing to do, will fetch fresh on access

    if (query.getObserversCount() > 0) {
      // Active observer — user is currently viewing this entity, refetch now
      queryClient.invalidateQueries({ queryKey });
    } else {
      // In cache but not viewed — mark stale without triggering a network request
      queryClient.invalidateQueries({ queryKey, refetchType: 'none' });
    }
  });

  // The list always gets invalidated actively — it may show any of the changed items
  queryClient.invalidateQueries({ queryKey: listKey, refetchType: 'active' });
}
```

Usage in the invoice socket events:

```ts
// features/invoices/socket-events.ts
import { batchInvalidation }   from '@/lib/socket-batch';
import { invoiceKeys }         from './api/invoice-keys';
import type { SocketEventHandlers } from '@/lib/socket-registry-types';
import type { InvoiceId }      from '@/types/common';

export const invoiceSocketEvents: SocketEventHandlers = {
  // ... single-entity handlers ...

  'invoice:batch-updated': ({ ids }, { queryClient }) => {
    batchInvalidation({
      queryClient,
      ids,
      toQueryKey: (id) => invoiceKeys.detail(id as InvoiceId),
      listKey:    invoiceKeys.lists(),
    });
  },

  'invoice:batch-deleted': ({ ids }, { queryClient }) => {
    // Remove deleted entries from cache entirely — no point marking them stale
    ids.forEach((id) => {
      queryClient.removeQueries({ queryKey: invoiceKeys.detail(id as InvoiceId) });
    });
    queryClient.invalidateQueries({ queryKey: invoiceKeys.lists(), refetchType: 'active' });
  },
};
```

### Single event vs batch event — which to use

| Scenario | Event type | Reason |
|---|---|---|
| User creates one invoice | `invoice:created` (single) | One change, notify immediately |
| Worker reconciles 3 invoices | `invoice:batch-updated` (batch) | Small batch, still worth IDs |
| Worker imports 500 invoices | `invoice:batch-updated` with all IDs OR a broader signal | IDs useful; `batchInvalidation` skips what's not in cache |
| Nightly reconciliation job | Broad signal (`invoice:invalidate-all`) | Too many to enumerate — just invalidate the whole entity |

For very broad worker operations, the backend can emit a signal without IDs:

```ts
// In ServerToClientEvents — for operations too broad to enumerate
'invoice:invalidate-all': (payload: { workspace_id: WorkspaceId }) => void;
```

```ts
// Handler — broad sweep, active only
'invoice:invalidate-all': (_payload, { queryClient }) => {
  queryClient.invalidateQueries({ queryKey: invoiceKeys.all, refetchType: 'active' });
},
```

---

## Feature room subscription

Global rooms (`workspace:*`, `user:*`) are joined by the SocketProvider automatically. Feature-specific rooms (collaborative editing on a single entity) are joined by a hook in the controller — they're only relevant when a specific surface is open.

```ts
// src/hooks/use-socket-room.ts
import { useEffect } from 'react';
import { useSocket } from '@/providers/SocketProvider';

export function useSocketRoom(room: string | null) {
  const socket = useSocket();

  useEffect(() => {
    if (!socket || !room) return;

    socket.emit('room:join', { room }, () => {});

    return () => {
      socket.emit('room:leave', { room });
    };
  }, [socket, room]);
}
```

Usage in a controller when the user opens a collaborative document:

```ts
// features/invoices/controllers/use-invoice-detail.controller.ts
export function useInvoiceDetailController(id: InvoiceId) {
  // Join the invoice-specific room while this controller is mounted
  useSocketRoom(`invoice:${id}`);

  // ... rest of controller
}
```

When the surface closes, the controller unmounts and the hook cleanup leaves the room automatically.

---

## Using `useSocket()` to emit events

`useSocket()` is for emitting client events only — presence announcements, typing indicators, explicit room joins. Features never attach `socket.on()` listeners via this hook.

```ts
// Usage — emitting a client event (presence, typing indicator)
export function useInvoicePresence(id: InvoiceId) {
  const socket = useSocket();

  const announceViewing = useCallback(() => {
    socket?.emit('room:join', { room: `invoice:${id}:presence` }, () => {});
  }, [socket, id]);

  return { announceViewing };
}
```

## Using `useSocketStatus()` for connection indicators

```tsx
// src/components/shell/ConnectionStatus.tsx
import { useSocketStatus } from '@/providers/SocketProvider';

export function ConnectionStatus() {
  const { connected, reconnecting } = useSocketStatus();

  if (connected)    return <span className="text-green-500 text-xs">● Live</span>;
  if (reconnecting) return <span className="text-yellow-500 text-xs">● Reconnecting…</span>;
  return                   <span className="text-red-500   text-xs">● Offline</span>;
}
```

Mount this in the app shell (`TopBar`, `Sidebar`) where it is always visible.

---

## Agent interface consideration

Socket events are a natural trace of application state changes. For agent control, the registry handlers can optionally record events to the surface trace system:

```ts
'invoice:updated': ({ id }, { queryClient }) => {
  queryClient.invalidateQueries({ queryKey: invoiceKeys.detail(id as InvoiceId), refetchType: 'active' });

  // Optional: record for agent replay/debugging
  if (import.meta.env.DEV) {
    surfaceTrace.record('socket:invoice:updated', { id });
  }
},
```

---

## What real-time must NOT do

- **Never attach `socket.on()` listeners inside a component or hook.** All incoming event handling is declared in `features/<f>/socket-events.ts` and applied by the SocketProvider once. Components never touch listeners.
- **Never call `useSocket()` to subscribe to events.** `useSocket()` is for emitting only. Subscriptions are the registry's job.
- **Never pass `auth: { token: getAccessToken() }` as a plain object.** Use the function form so every reconnect gets the current token, not a stale snapshot captured at creation time.
- **Never ignore `connect_error`.** A rejected handshake means the token may be expired — attempt a refresh or dispatch `auth:session-expired`.
- **Never use socket events as the sole source of truth for UI state.** Socket events trigger cache invalidations. TanStack Query holds the truth — the socket only signals that the truth may have changed.
- **Never invalidate without `refetchType: 'active'`** unless you explicitly want to wake up all cached data regardless of whether the user can see it.
- **Never emit socket events without acknowledgment callbacks** for operations requiring server confirmation.
- **Never connect to the socket before the user is authenticated.** The SocketProvider checks `isAuthenticated` before creating the connection.
- **Never handle the same event in two different registry files.** Each event has exactly one handler. If two features care about the same event, merge the handlers into the app-level registry entry.
- **Never iterate batch IDs and call `invalidateQueries` with the default `refetchType`** for all of them. Use `batchInvalidation()` — it checks observer count and uses `refetchType: 'none'` for unobserved cache entries, preventing unnecessary network requests.
- **Never enumerate IDs in a batch event for very large worker operations.** If a job touches hundreds of entities, emit a broad signal (`invoice:invalidate-all`) instead. Enumerating 500 IDs in a socket payload is wasteful — the frontend will only be observing one or two of them.
