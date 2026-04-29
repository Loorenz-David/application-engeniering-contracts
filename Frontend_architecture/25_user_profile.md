# 25 — User Profile Contract

## Definition

User identity and user profile are two separate concerns stored in two separate places.

| Concern | Store | Available | Fields |
|---|---|---|---|
| **Auth identity** | Zustand `auth.store.ts` | Immediately (from sign-in response) | `id`, `email`, `name`, `roles`, `permissions`, `workspaceId` |
| **Full profile** | TanStack Query (`/api/v1/me`) | After first fetch (pre-warmed at boot) | All identity fields + `avatar_file_id`, `timezone`, `preferences`, `created_at` |

The auth store answers "who is this person and what can they do?" The profile query answers "what does this person look like and how do they prefer to work?"

---

## Types (`features/profile/types.ts`)

```ts
// features/profile/types.ts
import { z } from 'zod';
import type { UserId, WorkspaceId } from '@/types/common';

// ─── 1. Response DTO ──────────────────────────────────────────────────────────

export const UserProfileSchema = z.object({
  id:             z.string().uuid().transform((v) => v as UserId),
  email:          z.string().email(),
  name:           z.string(),
  roles:          z.array(z.string().min(1)),
  permissions:    z.array(z.string()),
  workspace_id:   z.string().uuid().transform((v) => v as WorkspaceId),
  avatar_file_id: z.string().uuid().nullable(),
  timezone:       z.string(),   // IANA format e.g. "America/New_York"
  preferences: z.object({
    email_notifications: z.boolean(),
    theme:               z.enum(['light', 'dark', 'system']),
  }),
  created_at: z.string().datetime({ offset: true }),
});
export type UserProfile = z.infer<typeof UserProfileSchema>;

// ─── 2. Request DTOs ─────────────────────────────────────────────────────────

export const UpdateProfileInputSchema = z.object({
  name:     z.string().min(1, 'Name is required.').max(100),
  timezone: z.string().min(1, 'Timezone is required.'),
  preferences: z.object({
    email_notifications: z.boolean(),
    theme:               z.enum(['light', 'dark', 'system']),
  }).optional(),
});
export type UpdateProfileInput = z.infer<typeof UpdateProfileInputSchema>;

// ─── 3. View Model ───────────────────────────────────────────────────────────

export type ProfileViewModel = UserProfile & {
  initials:     string;         // "JD" from "John Doe"
  display_name: string;         // name, or email as fallback
  avatar_url:   string | null;  // resolved presigned URL — fetch on demand, never cache
};

export function toProfileViewModel(
  profile:   UserProfile,
  avatarUrl: string | null,
): ProfileViewModel {
  const parts    = profile.name.trim().split(' ');
  const initials = parts.length >= 2
    ? `${parts[0]![0]}${parts[parts.length - 1]![0]}`.toUpperCase()
    : profile.name.slice(0, 2).toUpperCase();

  return {
    ...profile,
    initials,
    display_name: profile.name || profile.email,
    avatar_url:   avatarUrl,
  };
}
```

---

## Feature folder layout

```
features/profile/
├── api/
│   ├── profile-keys.ts
│   ├── fetch-profile.ts
│   ├── update-profile.ts
│   ├── update-avatar.ts
│   ├── use-current-user-query.ts
│   └── use-avatar-url-query.ts
├── actions/
│   ├── use-update-profile.ts
│   └── use-update-avatar.ts
├── hooks/
│   └── use-current-user.ts      ← public composite hook
├── components/
│   ├── ProfileForm.tsx
│   ├── AvatarUpload.tsx
│   └── UserAvatar.tsx
├── types.ts
└── index.ts
```

---

## Query key factory

```ts
// features/profile/api/profile-keys.ts
export const profileKeys = {
  all:    ['profile'] as const,
  detail: () => [...profileKeys.all, 'detail'] as const,
};
```

---

## API functions

```ts
// features/profile/api/fetch-profile.ts
import { apiClient } from '@/lib/api-client';
import { UserProfileSchema, type UserProfile } from '../types';

export async function fetchProfile(): Promise<UserProfile> {
  return apiClient.get('/api/v1/me', UserProfileSchema);
}
```

```ts
// features/profile/api/update-profile.ts
import { apiClient } from '@/lib/api-client';
import { UserProfileSchema, type UserProfile, type UpdateProfileInput } from '../types';

export async function updateProfile(input: UpdateProfileInput): Promise<UserProfile> {
  return apiClient.patch('/api/v1/me', UserProfileSchema, input);
}
```

```ts
// features/profile/api/update-avatar.ts
import { z } from 'zod';
import { apiClient } from '@/lib/api-client';

const UpdateAvatarResponseSchema = z.object({ avatar_file_id: z.string().uuid().nullable() });

export async function updateAvatar(fileId: string) {
  return apiClient.patch('/api/v1/me/avatar', UpdateAvatarResponseSchema, { file_id: fileId });
}
```

---

## Query hook

```ts
// features/profile/api/use-current-user-query.ts
import { useQuery } from '@tanstack/react-query';
import { fetchProfile } from './fetch-profile';
import { profileKeys } from './profile-keys';

export function useCurrentUserQuery(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: profileKeys.detail(),
    queryFn:  fetchProfile,
    staleTime: 1000 * 60 * 5,   // profile changes rarely — 5 min fresh window
    ...options,
  });
}
```

---

## Action hooks

```ts
// features/profile/actions/use-update-profile.ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { updateProfile } from '../api/update-profile';
import { profileKeys } from '../api/profile-keys';
import { useAuthStore } from '@/store/auth.store';

export function useUpdateProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: updateProfile,
    onSuccess: (updated) => {
      // Update the cache directly — avoids a round-trip refetch
      queryClient.setQueryData(profileKeys.detail(), updated);

      // Sync the auth store's identity copy for the fields that can change
      const current = useAuthStore.getState();
      if (current.user) {
        useAuthStore.getState().setUser(
          { ...current.user, name: updated.name, email: updated.email },
          current.workspaceId!,
        );
      }
    },
  });
}
```

```ts
// features/profile/actions/use-update-avatar.ts
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { uploadFile } from '@/features/files/api/upload-file';
import { updateAvatar } from '../api/update-avatar';
import { profileKeys } from '../api/profile-keys';

export function useUpdateAvatar() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (file: File) => {
      const uploaded = await uploadFile(file);
      return updateAvatar(uploaded.file_id);
    },
    onSuccess: () => {
      // Invalidate so the next read refetches the updated avatar_file_id
      queryClient.invalidateQueries({ queryKey: profileKeys.detail() });
    },
  });
}
```

---

## Public hook — `useCurrentUser`

All components and hooks that need profile data consume `useCurrentUser`. Nothing reads `useCurrentUserQuery` or `useAuthStore` directly for profile data.

```ts
// features/profile/hooks/use-current-user.ts
import { useAuth } from '@/features/auth/hooks/use-auth';
import { useCurrentUserQuery } from '../api/use-current-user-query';

export function useCurrentUser() {
  const { user, workspaceId, isAuthenticated, signOut, isSigningOut } = useAuth();

  const { data: profile, isPending: isLoadingProfile } = useCurrentUserQuery({
    enabled: isAuthenticated,
  });

  return {
    // Identity — always available when isAuthenticated is true
    user,
    workspaceId,
    isAuthenticated,
    signOut,
    isSigningOut,

    // Full profile — available after first fetch; undefined during first load
    profile,
    isLoadingProfile,
  };
}
```

**Decision guide:**
- Need to check authentication or display broad role identity → `useAuth()`
- Need avatar, timezone, or preferences → `useCurrentUser()`
- Need to update profile → `useUpdateProfile()` or `useUpdateAvatar()` from the profile feature

---

## Boot integration

`AuthProvider` uses `fetchProfile` instead of a separate `fetchCurrentUser`. It populates the auth store with identity fields AND pre-warms the TanStack cache with the full profile in a single network call:

```tsx
// features/auth/components/AuthProvider.tsx (boot effect only)
import { fetchProfile } from '@/features/profile/api/fetch-profile';
import { profileKeys }  from '@/features/profile/api/profile-keys';

useEffect(() => {
  initSession()
    .then(async (ok) => {
      if (ok) {
        const profile = await fetchProfile();

        // Populate auth store — identity fields only
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

        // Pre-warm TanStack cache — useCurrentUser() has data instantly
        queryClient.setQueryData(profileKeys.detail(), profile);
      }
    })
    .finally(() => setReady(true));
}, []);
```

One fetch, two beneficiaries. `useCurrentUserQuery` will read from the warm cache on first render — no extra network request.

---

## Avatar display pattern

Avatar URLs are short-lived presigned URLs (see [22_file_handling.md](22_file_handling.md)). Never store them — fetch on demand:

```ts
// features/profile/api/use-avatar-url-query.ts
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { apiClient } from '@/lib/api-client';
import type { FileId } from '@/types/common';

const AvatarUrlSchema = z.object({ url: z.string().url() });

export function useAvatarUrlQuery(fileId: string | null | undefined) {
  return useQuery({
    queryKey: ['avatar-url', fileId],
    queryFn:  () => apiClient.get(`/api/v1/files/${fileId}/url`, AvatarUrlSchema),
    enabled:  Boolean(fileId),
    staleTime: 1000 * 60 * 10,   // presigned URL valid for 10 min on the backend
    gcTime:    1000 * 60 * 11,   // keep in cache slightly longer than staleTime
    meta:      { persist: false }, // never write presigned URLs to persisted cache
  });
}
```

```tsx
// features/profile/components/UserAvatar.tsx
import { useCurrentUser } from '../hooks/use-current-user';
import { useAvatarUrlQuery } from '../api/use-avatar-url-query';

export function UserAvatar({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }) {
  const { user, profile } = useCurrentUser();
  const { data: avatarData } = useAvatarUrlQuery(profile?.avatar_file_id);

  if (avatarData?.url) {
    return <img src={avatarData.url} alt={user?.name ?? ''} className={avatarClass(size)} />;
  }

  // Fallback: initials badge while loading or when no avatar is set
  const initials = user?.name?.slice(0, 2).toUpperCase() ?? '??';
  return (
    <div className={cn(avatarClass(size), 'flex items-center justify-center bg-muted')}>
      <span className="text-xs font-medium">{initials}</span>
    </div>
  );
}
```

`UserAvatar` is a shared component — export it from `features/profile/index.ts` so any feature can use it.

---

## Public API (`features/profile/index.ts`)

```ts
export { useCurrentUser }         from './hooks/use-current-user';
export { useUpdateProfile }       from './actions/use-update-profile';
export { useUpdateAvatar }        from './actions/use-update-avatar';
export { UserAvatar }             from './components/UserAvatar';
export type { UserProfile, UpdateProfileInput, ProfileViewModel } from './types';
```

---

## What user profile must NOT do

- **Never store the full profile in the Zustand auth store.** Auth store holds identity only. Profile is server state.
- **Never call `useCurrentUserQuery` directly from a component.** Use `useCurrentUser()`.
- **Never store or persist `avatar_url` as profile data.** Cache `avatar_file_id`; fetch the presigned URL on demand via `useAvatarUrlQuery` and mark that URL query as non-persisted.
- **Never sync all profile fields to the auth store on update.** Only sync `name` and `email` — the only identity fields that can change.
- **Never skip pre-warming the TanStack cache in `AuthProvider`.** Doing so causes `useCurrentUser()` to show a loading state on every boot even though the data was just fetched.
- **Never let a feature component import `useCurrentUserQuery` directly.** The public hook `useCurrentUser()` is the only export components should use.
- **Never use `UserProfile` as the auth store's `User` type.** They are intentionally separate shapes. `User` is identity; `UserProfile` is the full backend DTO.
