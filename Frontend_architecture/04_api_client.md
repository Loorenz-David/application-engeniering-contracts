# 04 — API Client Contract

## Definition

The API client is the single module responsible for all HTTP communication with the backend. It owns JWT token storage, attaches Bearer headers, handles 401 → token-refresh → retry, validates responses with Zod, and normalises errors into typed `ApiRequestError` objects. Nothing above it speaks raw HTTP; nothing in it speaks domain logic.

Entity identifiers passed through the API client are public client-facing IDs only. The frontend never sends, receives, stores, logs, or branches on backend database primary keys. For first-party creates, that public ID originates as `client_id` in the request DTO; after the response is parsed, feature code treats it as the entity's normal branded ID.

Two files form this layer:

```
src/lib/auth-token.ts   ← JWT token storage + refresh singleton
src/lib/api-client.ts   ← HTTP wrapper used by all query/mutation functions
```

No other file in the codebase calls `fetch` except these two.

---

## JWT token strategy

| Token | Storage | Reason |
|---|---|---|
| Access token (short-lived, ~15 min) | In-memory JS variable | Invisible to XSS — not in `localStorage` or `sessionStorage` |
| Refresh token (long-lived) | `httpOnly` cookie set by the backend | Inaccessible to JavaScript entirely — sent automatically by the browser |

The backend sets the `httpOnly` refresh cookie on sign-in and clears it on sign-out. The frontend never reads, writes, or deletes it — the browser handles it transparently.

When the access token expires the client gets a `401`. It sends the refresh cookie to the `/auth/refresh` endpoint and receives a new access token. If refresh fails (cookie expired or revoked), the session ends and the user is redirected to sign-in.

---

## Token module (`src/lib/auth-token.ts`)

This module is the single owner of the in-memory access token. No other file stores or mutates the token.

```ts
// src/lib/auth-token.ts
import { env } from '@/lib/env';

// ─── In-memory token storage ──────────────────────────────────────────────────

let _accessToken: string | null = null;

export function getAccessToken(): string | null {
  return _accessToken;
}

export function setAccessToken(token: string | null): void {
  _accessToken = token;
}

// ─── Refresh singleton ────────────────────────────────────────────────────────
//
// Multiple concurrent requests can all receive 401 at the same time.
// Without a singleton, each would fire its own refresh call — causing
// a race where only one succeeds and the rest invalidate each other.
// The singleton ensures exactly one refresh call runs at a time;
// every concurrent caller awaits the same promise.

let _refreshPromise: Promise<boolean> | null = null;

export function refreshAccessToken(): Promise<boolean> {
  if (_refreshPromise) return _refreshPromise;

  _refreshPromise = _executeRefresh().finally(() => {
    _refreshPromise = null;
  });

  return _refreshPromise;
}

async function _executeRefresh(): Promise<boolean> {
  try {
    const response = await fetch(`${env.VITE_API_URL}/api/v1/auth/refresh`, {
      method: 'POST',
      credentials: 'include',   // sends the httpOnly refresh cookie
    });

    if (!response.ok) {
      setAccessToken(null);
      return false;
    }

    const data = await response.json() as { access_token: string };
    setAccessToken(data.access_token);
    return true;
  } catch {
    setAccessToken(null);
    return false;
  }
}

// ─── Session initialisation (called once on app boot) ─────────────────────────

export async function initSession(): Promise<boolean> {
  return refreshAccessToken();
}
```

### Why the singleton matters

Without it:

```
Request A → 401 → refreshAccessToken() → POST /auth/refresh (fires)
Request B → 401 → refreshAccessToken() → POST /auth/refresh (fires again)
Request C → 401 → refreshAccessToken() → POST /auth/refresh (fires again)

Backend invalidates the first refresh token on use.
Requests B and C fail — user is logged out despite having a valid session.
```

With the singleton:

```
Request A → 401 → refreshAccessToken() → POST /auth/refresh (fires)
Request B → 401 → refreshAccessToken() → awaits the same promise ←┐
Request C → 401 → refreshAccessToken() → awaits the same promise ←┘

One refresh, one new token, all three requests retry successfully.
```

---

## API client (`src/lib/api-client.ts`)

```ts
// src/lib/api-client.ts
import { z } from 'zod';
import { env } from '@/lib/env';
import { ApiErrorSchema } from '@/types/api';
import { getAccessToken, refreshAccessToken, setAccessToken } from '@/lib/auth-token';

// ─── Error type ───────────────────────────────────────────────────────────────

export class ApiRequestError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly fieldErrors?: Record<string, string[]>,
  ) {
    super(message);
    this.name = 'ApiRequestError';
  }
}

// ─── Request internals ────────────────────────────────────────────────────────

type RequestOptions = {
  method?:  'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?:    unknown;
  params?:  Record<string, string | number | boolean | undefined>;
};

async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  options: RequestOptions = {},
  isRetry = false,             // prevents infinite refresh loop
): Promise<T> {
  const { method = 'GET', body, params } = options;

  const url      = buildUrl(path, params);
  const token    = getAccessToken();

  const response = await fetch(url, {
    method,
    credentials: 'include',   // always include — needed for the refresh cookie
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  // ── 401: attempt exactly one token refresh, then retry ──────────────────────
  if (response.status === 401 && !isRetry) {
    const refreshed = await refreshAccessToken();

    if (refreshed) {
      return request(path, schema, options, true);   // retry once with new token
    }

    // Refresh failed — session is over. Clear token and signal the app.
    setAccessToken(null);
    // Dispatch a custom event so AuthProvider can redirect to sign-in
    window.dispatchEvent(new CustomEvent('auth:session-expired'));
    throw new ApiRequestError(401, 'unauthorized', 'Session expired. Please sign in again.');
  }

  // ── Non-2xx: parse and throw a typed error ───────────────────────────────────
  if (!response.ok) {
    return handleErrorResponse(response);
  }

  // ── 2xx: validate response with Zod ──────────────────────────────────────────
  const json: unknown = response.status === 204 ? {} : await response.json();
  const parsed = schema.safeParse(json);

  if (!parsed.success) {
    throw new ApiRequestError(
      502,
      'invalid_response',
      `API response did not match expected schema: ${parsed.error.message}`,
    );
  }

  return parsed.data;
}

async function handleErrorResponse(response: Response): Promise<never> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiRequestError(response.status, 'network_error', response.statusText);
  }

  const parsed = ApiErrorSchema.safeParse(body);
  if (parsed.success) {
    throw new ApiRequestError(
      response.status,
      parsed.data.error.code,
      parsed.data.error.message,
      parsed.data.error.field_errors,
    );
  }

  throw new ApiRequestError(response.status, 'unknown_error', 'An unexpected error occurred.');
}

function buildUrl(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
): string {
  const url = new URL(path, env.VITE_API_URL);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) url.searchParams.set(key, String(value));
    });
  }
  return url.toString();
}

// ─── Public API ───────────────────────────────────────────────────────────────

export const apiClient = {
  get: <T>(path: string, schema: z.ZodType<T>, params?: RequestOptions['params']) =>
    request(path, schema, { method: 'GET', params }),

  post: <T>(path: string, schema: z.ZodType<T>, body: unknown) =>
    request(path, schema, { method: 'POST', body }),

  put: <T>(path: string, schema: z.ZodType<T>, body: unknown) =>
    request(path, schema, { method: 'PUT', body }),

  patch: <T>(path: string, schema: z.ZodType<T>, body: unknown) =>
    request(path, schema, { method: 'PATCH', body }),

  delete: <T>(path: string, schema: z.ZodType<T>) =>
    request(path, schema, { method: 'DELETE' }),
};
```

---

## Session expiry event

When a refresh fails, the client dispatches `auth:session-expired` instead of importing the auth store directly (which would create a circular dependency). `AuthProvider` listens for it and redirects:

```ts
// features/auth/components/AuthProvider.tsx
useEffect(() => {
  const handler = () => {
    clearAuth();                     // clears Zustand auth store
    queryClient.clear();             // wipes cached data
    navigate(ROUTES.signIn, { replace: true });
  };

  window.addEventListener('auth:session-expired', handler);
  return () => window.removeEventListener('auth:session-expired', handler);
}, []);
```

This keeps `auth-token.ts` independent from feature, store, and component state. It may import low-level utilities such as `env`, but it never imports application state.

---

## Session initialisation on app boot

On startup, call `initSession()` before rendering protected routes. It fires one refresh call to restore the session from the existing `httpOnly` cookie:

```ts
// features/auth/components/AuthProvider.tsx
const [ready, setReady] = useState(false);

useEffect(() => {
  initSession()
    .then((ok) => {
      if (ok) {
        // fetch current user and store in auth store
      }
    })
    .finally(() => setReady(true));
}, []);

if (!ready) return <AppBootSkeleton />;
```

The app does not render until `initSession()` resolves. This prevents a flash of the sign-in page for users with a valid refresh cookie.

---

## Using the client in query/mutation functions

Query functions call `apiClient` — they never call `fetch` directly:

```ts
// features/invoices/api/fetch-invoices.ts
import { apiClient } from '@/lib/api-client';
import { InvoiceSchema } from '@/features/invoices/types';
import { PaginatedResponseSchema } from '@/types/api';
import type { ListInvoicesParams } from '@/features/invoices/types';

const InvoicePageSchema = PaginatedResponseSchema(InvoiceSchema);

export async function fetchInvoices(params: ListInvoicesParams) {
  return apiClient.get('/api/v1/invoices', InvoicePageSchema, params);
}
```

```ts
// features/invoices/api/create-invoice.ts
import { z } from 'zod';
import { apiClient } from '@/lib/api-client';
import { InvoiceSchema, type CreateInvoiceInput } from '@/features/invoices/types';

const CreateInvoiceResponseSchema = z.object({ invoice: InvoiceSchema });

export async function createInvoice(input: CreateInvoiceInput) {
  const { invoice } = await apiClient.post('/api/v1/invoices', CreateInvoiceResponseSchema, input);
  return invoice;
}
```

---

## Response envelope

The backend wraps all responses in a typed envelope. The query or mutation function's schema includes the envelope at the HTTP boundary:

```ts
z.object({ invoice: InvoiceSchema })         // single entity
PaginatedResponseSchema(InvoiceSchema)        // list
z.object({})                                  // empty success (DELETE)
```

The low-level `apiClient` returns the parsed envelope exactly as the schema describes. Feature API functions may unwrap the useful payload before returning it to TanStack Query:

```ts
const { invoice } = await apiClient.get(`/api/v1/invoices/${id}`, GetInvoiceResponse);
return invoice;
```

This keeps envelope validation centralized while letting query hooks cache the domain DTO directly.

---

## Error codes

| HTTP status | `code` | Meaning |
|---|---|---|
| 400 | `validation_failed` | Field-level errors — map back onto the form via `fieldErrors` |
| 401 | `unauthorized` | Token expired — handled internally by the refresh cycle |
| 403 | `forbidden` | Authenticated but not authorised |
| 404 | `not_found` | Entity does not exist |
| 409 | `conflict` | Optimistic concurrency or uniqueness violation |
| 422 | `unprocessable` | Backend domain error (business rule) |
| 500 | `server_error` | Unhandled backend exception |
| 502/503 | `network_error` | Gateway unavailable |

Callers branch on `error.code`, not `error.status`.

---

## What the API client must NOT do

- **Never call `fetch` from a component, hook, controller, or query function.** Only `api-client.ts` and `auth-token.ts` call `fetch`.
- **Never store the access token in `localStorage` or `sessionStorage`.** In-memory only.
- **Never store the refresh token in JavaScript.** It lives in the `httpOnly` cookie — the browser manages it.
- **Never fire more than one concurrent refresh request.** The singleton in `auth-token.ts` enforces this.
- **Never retry a failed request more than once** per 401. The `isRetry` flag enforces this.
- **Never refresh on 403.** A 403 means the session is valid but the user lacks permission. Surface the typed `forbidden` error to the caller.
- **Never import from `features/`, `store/`, or `components/` inside `auth-token.ts`.** Token storage must be independent from app state. Signal session expiry through a DOM event.
- **Never return raw JSON.** Every response is parsed with a Zod schema before returning.
- **Never throw plain `Error` objects.** Always throw `ApiRequestError` with `status` and `code`.
- **Never cache responses.** Caching is TanStack Query's responsibility.
- **Never expose backend database IDs.** API paths, params, DTOs, logs, query keys, and events use the public client-facing ID only.
