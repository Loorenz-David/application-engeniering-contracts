# 12 — Auth Contract

## Definition

Auth covers session lifecycle: initialisation on boot, sign-in, sign-out, and the Zustand store that carries the current user. Token storage and JWT refresh are handled by the API client layer — see [04_api_client.md](04_api_client.md) for the full token module and refresh singleton. This contract covers everything above that layer.

---

## Responsibilities by file

| File | Owns |
|---|---|
| `src/lib/auth-token.ts` | In-memory access token, refresh singleton, `initSession()` — defined in [04_api_client.md](04_api_client.md) |
| `src/store/auth.store.ts` | Current user object, workspace ID, `isAuthenticated` flag |
| `features/auth/components/AuthProvider.tsx` | Boot-time session restoration, session-expired event handler |
| `features/auth/api/use-sign-in.ts` | Email/password sign-in mutation |
| `features/auth/api/use-sign-out.ts` | Sign-out mutation + cache clear |
| `features/auth/api/use-oauth-sign-in.ts` | Initiates OAuth redirect flow |
| `features/auth/components/OAuthCallback.tsx` | Handles provider redirect, exchanges code for JWT |
| `features/auth/hooks/use-auth.ts` | Public hook — identity + auth actions (not profile) |
| `features/auth/components/ProtectedRoute.tsx` | Redirects unauthenticated users |
| `features/auth/components/GuestRoute.tsx` | Redirects authenticated users away from public pages |
| `features/profile/` | Full user profile, avatar, preferences — see [25_user_profile.md](25_user_profile.md) |

---

## Auth store (`src/store/auth.store.ts`)

The store holds the current user's identity. It does **not** hold the access token — that lives in `auth-token.ts` as a plain in-memory variable (see [04_api_client.md](04_api_client.md)).

```ts
// src/store/auth.store.ts
import { create } from 'zustand';
import type { UserId, WorkspaceId } from '@/types/common';
import type { Role } from '@/types/roles';

type User = {
  id:          UserId;
  email:       string;
  name:        string;
  roles:       Role[];
  permissions: string[];
};

type AuthState = {
  user:            User | null;
  workspaceId:     WorkspaceId | null;
  isAuthenticated: boolean;
  setUser:         (user: User, workspaceId: WorkspaceId) => void;
  clearAuth:       () => void;
};

export const useAuthStore = create<AuthState>((set) => ({
  user:            null,
  workspaceId:     null,
  isAuthenticated: false,

  setUser: (user, workspaceId) =>
    set({ user, workspaceId, isAuthenticated: true }),

  clearAuth: () =>
    set({ user: null, workspaceId: null, isAuthenticated: false }),
}));

export const selectUser            = (s: AuthState) => s.user;
export const selectWorkspaceId     = (s: AuthState) => s.workspaceId;
export const selectIsAuthenticated = (s: AuthState) => s.isAuthenticated;
```

The store holds `user` and `workspaceId`. The access token is accessed separately via `getAccessToken()` from `auth-token.ts` and is only needed by the API client.

---

## Auth initialisation (`AuthProvider`)

On mount, `AuthProvider` calls `initSession()` (defined in `04_api_client.md`). This fires one refresh request using the `httpOnly` cookie. If it succeeds, the app fetches the current user and populates the store. If it fails, the store stays empty and the user sees sign-in.

`AuthProvider` also listens for the `auth:session-expired` custom event dispatched by the API client when a mid-session refresh fails.

`AuthProvider` calls `useNavigate()`, so it must be rendered inside the router tree. Mount it from the root route layout described in [11_routing.md](11_routing.md), not above `RouterProvider`.

```tsx
// features/auth/components/AuthProvider.tsx
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { initSession } from '@/lib/auth-token';
import { useAuthStore } from '@/store/auth.store';
import { fetchProfile }  from '@/features/profile/api/fetch-profile';
import { profileKeys }   from '@/features/profile/api/profile-keys';
import { ROUTES } from '@/lib/routes';

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const setUser      = useAuthStore((s) => s.setUser);
  const clearAuth    = useAuthStore((s) => s.clearAuth);
  const queryClient  = useQueryClient();
  const navigate     = useNavigate();

  // ── Boot: restore session from httpOnly refresh cookie ──────────────────────
  useEffect(() => {
    initSession()
      .then(async (ok) => {
        if (ok) {
          // One fetch — two beneficiaries.
          // Auth store gets the identity fields; TanStack cache gets the full profile.
          // useCurrentUser() will read from the warm cache on first render — no extra request.
          const profile = await fetchProfile();
          setUser(
            {
              id:          profile.id,
              email:       profile.email,
              name:        profile.name,
              roles:       profile.roles,
              permissions: profile.permissions,
            },
            profile.workspace_id,
          );
          queryClient.setQueryData(profileKeys.detail(), profile);
        }
      })
      .finally(() => setReady(true));
  }, []);

  // ── Mid-session expiry: API client signals when refresh fails ───────────────
  useEffect(() => {
    const handleExpired = () => {
      clearAuth();
      queryClient.clear();
      navigate(ROUTES.signIn, { replace: true });
    };

    window.addEventListener('auth:session-expired', handleExpired);
    return () => window.removeEventListener('auth:session-expired', handleExpired);
  }, [clearAuth, queryClient, navigate]);

  if (!ready) return <AppBootSkeleton />;

  return <>{children}</>;
}
```

`AuthProvider` is the only place that listens for `auth:session-expired`. It owns the response: clear the store, clear the cache, redirect.

---

## useAuth hook

`useAuth` exposes auth identity and auth actions. It does not expose avatar, timezone, or preferences — for those, use `useCurrentUser()` from [25_user_profile.md](25_user_profile.md).

Nothing outside the auth lifecycle reads `useAuthStore` directly. `useAuth`, `AuthProvider`, and sign-in/sign-out callbacks may touch it; all other code uses the public `useAuth()` hook or `usePermissions()`.

```ts
// features/auth/hooks/use-auth.ts
import {
  useAuthStore,
  selectUser,
  selectWorkspaceId,
  selectIsAuthenticated,
} from '@/store/auth.store';
import { useSignOutMutation } from '@/features/auth/api/use-sign-out';

export function useAuth() {
  const user            = useAuthStore(selectUser);
  const workspaceId     = useAuthStore(selectWorkspaceId);
  const isAuthenticated = useAuthStore(selectIsAuthenticated);
  const { mutate: signOut, isPending: isSigningOut } = useSignOutMutation();

  return { user, workspaceId, isAuthenticated, signOut, isSigningOut };
}
```

---

## Sign-in flow

```ts
// features/auth/api/use-sign-in.ts
import { useMutation } from '@tanstack/react-query';
import { z } from 'zod';
import { apiClient } from '@/lib/api-client';
import { setAccessToken } from '@/lib/auth-token';
import { useAuthStore } from '@/store/auth.store';

const SignInResponseSchema = z.object({
  access_token: z.string(),
  user: z.object({
    id:          z.string().uuid(),
    email:       z.string().email(),
    name:        z.string(),
    roles:       z.array(z.string().min(1)),
    permissions: z.array(z.string()),
  }),
  workspace_id: z.string().uuid(),
});

async function signIn(credentials: { email: string; password: string }) {
  // apiClient.post sends credentials; backend sets httpOnly refresh cookie
  const result = await apiClient.post('/api/v1/auth/sign-in', SignInResponseSchema, credentials);

  // Store the access token in memory (auth-token.ts)
  setAccessToken(result.access_token);
  // Store the user in the Zustand store
  useAuthStore.getState().setUser(result.user, result.workspace_id as WorkspaceId);

  return result;
}

export function useSignInMutation() {
  return useMutation({ mutationFn: signIn });
}
```

The sign-in response returns both the access token and user data in one call. The backend sets the `httpOnly` refresh cookie as a `Set-Cookie` header — the frontend does not handle this.

---

## Sign-out flow

```ts
// features/auth/api/use-sign-out.ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { apiClient } from '@/lib/api-client';
import { setAccessToken } from '@/lib/auth-token';
import { useAuthStore } from '@/store/auth.store';

async function signOut() {
  await apiClient.post('/api/v1/auth/sign-out', z.object({}), {});
  // Backend clears the httpOnly refresh cookie via Set-Cookie
  setAccessToken(null);
  useAuthStore.getState().clearAuth();
}

export function useSignOutMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: signOut,
    onSettled: () => {
      queryClient.clear();                           // wipe in-memory cache
      localStorage.removeItem('app-query-cache');    // wipe persisted cache (see 26_persistence.md)
    },
  });
}
```

`queryClient.clear()` prevents the next user from seeing stale data from the previous session.

---

## OAuth / SSO sign-in

OAuth providers (Google, GitHub, etc.) and SAML SSO use a redirect flow, but the token storage and session state are identical to email/password sign-in once the backend returns the JWT response.

### Flow

```
1. User clicks "Sign in with Google"
2. Frontend calls GET /api/v1/auth/oauth/google/url → receives a redirect URL
3. Frontend redirects window.location to the provider's auth URL
4. Provider authenticates the user, redirects back to VITE_APP_URL/auth/callback?code=...&provider=google
5. OAuthCallback component calls POST /api/v1/auth/oauth/callback { provider, code }
6. Backend exchanges the code, finds or creates the user, returns the same SignInResponse shape
7. Frontend stores access token + user — identical to email/password from this point
```

### Initiating OAuth

```ts
// features/auth/api/use-oauth-sign-in.ts
import { useMutation } from '@tanstack/react-query';
import { z } from 'zod';
import { apiClient } from '@/lib/api-client';

const OAuthUrlSchema = z.object({ url: z.string().url() });

async function initiateOAuth(provider: 'google' | 'github') {
  const { url } = await apiClient.get(`/api/v1/auth/oauth/${provider}/url`, OAuthUrlSchema);
  // Full-page redirect — the browser follows the provider's auth flow
  window.location.href = url;
}

export function useOAuthSignIn() {
  return useMutation({ mutationFn: initiateOAuth });
}
```

### Handling the callback

```tsx
// features/auth/components/OAuthCallback.tsx
import { useEffect, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { z } from 'zod';
import { apiClient } from '@/lib/api-client';
import { setAccessToken } from '@/lib/auth-token';
import { useAuthStore } from '@/store/auth.store';
import { ROUTES } from '@/lib/routes';
import type { WorkspaceId } from '@/types/common';

// Reuses the same response schema as email/password sign-in
const OAuthCallbackResponseSchema = z.object({
  access_token: z.string(),
  user: z.object({
    id:          z.string().uuid(),
    email:       z.string().email(),
    name:        z.string(),
    roles:       z.array(z.string().min(1)),
    permissions: z.array(z.string()),
  }),
  workspace_id: z.string().uuid(),
});

export function OAuthCallback() {
  const [params]   = useSearchParams();
  const navigate   = useNavigate();
  const { setUser } = useAuthStore();
  const didRun     = useRef(false);  // StrictMode guard — prevent double invocation

  useEffect(() => {
    if (didRun.current) return;
    didRun.current = true;

    const code     = params.get('code');
    const provider = params.get('provider') ?? params.get('state');  // provider-dependent

    if (!code || !provider) {
      navigate(ROUTES.signIn, { replace: true });
      return;
    }

    apiClient
      .post('/api/v1/auth/oauth/callback', OAuthCallbackResponseSchema, { provider, code })
      .then((result) => {
        setAccessToken(result.access_token);
        setUser(result.user, result.workspace_id as WorkspaceId);
        navigate(ROUTES.home, { replace: true });
      })
      .catch(() => navigate(ROUTES.signIn, { replace: true }));
  }, []);

  return <AppBootSkeleton />;
}
```

Mount `OAuthCallback` on a `GuestRoute` at `/auth/callback`. The `provider` can come from a `state` param (OAuth standard) or a separate query param depending on the backend's choice.

### What does NOT change for OAuth

- Token storage: identical — `setAccessToken()` + `setUser()`
- Refresh loop: identical — the `httpOnly` refresh cookie is set by the backend on OAuth sign-in too
- Sign-out: identical — `clearAuth()` + `queryClient.clear()`
- Session expiry: identical — `auth:session-expired` event + `AuthProvider` redirect

---

## Route guards

```tsx
// features/auth/components/ProtectedRoute.tsx
import { Navigate, Outlet } from 'react-router-dom';
import { useAuthStore, selectIsAuthenticated } from '@/store/auth.store';

export function ProtectedRoute() {
  const isAuthenticated = useAuthStore(selectIsAuthenticated);
  return isAuthenticated ? <Outlet /> : <Navigate to={ROUTES.signIn} replace />;
}

// features/auth/components/GuestRoute.tsx
export function GuestRoute() {
  const isAuthenticated = useAuthStore(selectIsAuthenticated);
  return isAuthenticated ? <Navigate to={ROUTES.home} replace /> : <Outlet />;
}
```

---

## What auth must NOT do

- **Never store the access token in the Zustand store or `localStorage`.** It is a plain in-memory variable in `auth-token.ts`, invisible to Redux DevTools, browser storage, and XSS.
- **Never store the refresh token in JavaScript.** It lives exclusively in the `httpOnly` cookie managed by the browser.
- **Never call the refresh endpoint from anywhere except `auth-token.ts`.** The singleton there prevents concurrent refresh races.
- **Never listen for `auth:session-expired` in more than one place.** Only `AuthProvider` handles it.
- **Never skip `queryClient.clear()` on sign-out.** Cached server data is user-specific.
- **Never expose `useAuthStore` outside the auth lifecycle.** All non-auth code uses `useAuth()` or `usePermissions()`.
- **Never implement authorization decisions on the frontend.** The backend enforces permissions; the frontend only shows or hides UI.
- **Never handle the OAuth callback code more than once.** React StrictMode mounts twice in development — guard with a `useRef` flag to prevent double exchange requests.
- **Never store the OAuth `code` or `state` parameters after exchange.** They are single-use; clear the URL immediately via `navigate(..., { replace: true })`.
