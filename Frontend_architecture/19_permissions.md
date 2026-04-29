# 19 — Permissions Contract

## Definition

Client-side permissions are a UX mechanism. They hide inaccessible UI from users who lack the required role. They are **not** a security boundary — the backend enforces authorization on every request. A hidden button does not prevent the underlying API call.

The permission system follows the same registry pattern as surfaces and socket events: **features declare which roles can perform each of their actions. The app assembles all declarations into a single registry. Hooks and components read from that registry.**

```
features/<f>/permissions.ts     ← feature declares its permission keys and allowed roles
        ↓  assembled by
src/app/permission-registry.ts  ← single source of truth for the entire app
        ↓  read by
usePermissions()                ← returns can() function — stable, derived from role
        ↓  consumed by
Controller → context            ← feature components read can.* from context, never from hooks directly
```

---

## Role type

Roles are defined once and mirror the backend's role model. Add roles here when the backend adds them.

```ts
// src/types/roles.ts
import { z } from 'zod';

export const RoleSchema = z.enum(['admin', 'manager', 'member', 'viewer']);
export type Role = z.infer<typeof RoleSchema>;
```

The user's roles come from the auth store — set at login, never re-fetched independently.

```ts
// In the auth store — the user carries their roles
type AuthUser = {
  id:           UserId;
  email:        string;
  name:         string;
  roles:        Role[];           // always an array — supports multi-role
  permissions?: string[];         // explicit per-user overrides (optional)
};
```

Using `roles: Role[]` (plural array) supports users with multiple roles without changing the permission check logic.

---

## Per-feature permission definitions

Each feature declares its permission keys and the roles that have access. The key naming convention is `'{feature}:{action}'` — singular feature name, lowercase.

```ts
// features/invoices/permissions.ts
import type { FeaturePermissions } from '@/lib/permission-types';

export const invoicePermissions = {
  'invoice:view':    ['admin', 'manager', 'member', 'viewer'],
  'invoice:create':  ['admin', 'manager', 'member'],
  'invoice:edit':    ['admin', 'manager', 'member'],
  'invoice:delete':  ['admin'],
  'invoice:approve': ['admin', 'manager'],
  'invoice:export':  ['admin', 'manager'],
} satisfies FeaturePermissions;
```

```ts
// features/settings/permissions.ts
import type { FeaturePermissions } from '@/lib/permission-types';

export const settingsPermissions = {
  'settings:view':   ['admin', 'manager'],
  'settings:manage': ['admin'],
  'settings:billing': ['admin'],
} satisfies FeaturePermissions;
```

`satisfies FeaturePermissions` validates the shape without widening the type — the literal key names are preserved so `PermissionKey` is correctly derived at the app level.

---

## App-level registry

```ts
// src/app/permission-registry.ts
import { invoicePermissions }  from '@/features/invoices/permissions';
import { settingsPermissions } from '@/features/settings/permissions';
import { clientPermissions }   from '@/features/clients/permissions';
import type { FeaturePermissions } from '@/lib/permission-types';

export const permissionRegistry = {
  ...invoicePermissions,
  ...settingsPermissions,
  ...clientPermissions,
} as const satisfies FeaturePermissions;

// PermissionKey is derived automatically from the registry.
// Adding a new key to any feature file extends this type — no manual update needed.
export type PermissionKey = keyof typeof permissionRegistry;
```

No feature imports another feature's permissions. The registry is the only join point.

---

## Permission types

```ts
// src/lib/permission-types.ts
import type { Role } from '@/types/roles';

// Shape every feature permission file must satisfy
export type FeaturePermissions = Record<string, Role[]>;

// Context passed to can() — derives from auth store
export type PermissionContext = {
  roles:        Role[];
  permissions?: string[];  // explicit per-user overrides
};
```

---

## `usePermissions()` — the primary hook

Returns a stable `can()` function. Call once per controller or component, check as many permissions as needed.

```ts
// src/hooks/use-permissions.ts
import { useCallback }         from 'react';
import { useAuthStore }        from '@/store/auth.store';
import { permissionRegistry }  from '@/app/permission-registry';
import type { PermissionKey }  from '@/app/permission-registry';

export function usePermissions() {
  const roles       = useAuthStore((s) => s.user?.roles       ?? []);
  const overrides   = useAuthStore((s) => s.user?.permissions ?? []);

  const can = useCallback((key: PermissionKey): boolean => {
    // Explicit per-user overrides take precedence over the role matrix
    if (overrides.includes(key)) return true;

    const allowedRoles = permissionRegistry[key];
    if (!allowedRoles) return false;

    // True if any of the user's roles is in the allowed list
    return roles.some((role) => allowedRoles.includes(role));
  }, [roles, overrides]);

  return { can };
}
```

`can` is memoized with `useCallback` and only re-creates when `roles` or `overrides` changes — typically once per session. Calling it for 10 permissions in a controller is zero overhead.

---

## `usePermission(key)` — single check

For route guards and shared primitives that need to check exactly one permission. Internally delegates to `usePermissions()`.

```ts
// src/hooks/use-permission.ts
import { usePermissions }     from '@/hooks/use-permissions';
import type { PermissionKey } from '@/app/permission-registry';

export function usePermission(key: PermissionKey): boolean {
  const { can } = usePermissions();
  return can(key);
}
```

---

## Controller integration

Feature components consume context only — they never import permission hooks directly. The controller computes the full permission surface for its feature and exposes it through context.

```ts
// features/invoices/controllers/use-invoice-list.controller.ts
import { usePermissions } from '@/hooks/use-permissions';

export function useInvoiceListController() {
  const { can }     = usePermissions();
  const invoicesQuery = useInvoicesQuery();

  return {
    invoices:  invoicesQuery.data?.items ?? [],
    isPending: invoicesQuery.isPending,

    // Permission surface — all checks computed once, exposed as plain booleans
    can: {
      create:  can('invoice:create'),
      delete:  can('invoice:delete'),
      approve: can('invoice:approve'),
      export:  can('invoice:export'),
    },
  };
}
```

Feature components read from context:

```tsx
// features/invoices/components/InvoiceListView.tsx
export function InvoiceListView() {
  const { invoices, can } = useInvoiceListContext();

  return (
    <div>
      {can.create && <CreateInvoiceButton />}
      <InvoiceTable invoices={invoices} canDelete={can.delete} />
    </div>
  );
}
```

`InvoiceTable` (shared primitive) receives `canDelete` as a prop — it never imports a permission hook. The feature component is the decision point; the primitive only renders what it receives.

---

## `<Guard>` — declarative conditional rendering

For cases where the permission check and the conditional render are in the same component and the controller pattern is not applicable (e.g. inside a shared primitive that conditionally renders an action).

```tsx
// src/components/ui/Guard.tsx
import { usePermission }     from '@/hooks/use-permission';
import type { PermissionKey } from '@/app/permission-registry';

type GuardProps = {
  permission: PermissionKey;
  fallback?:  React.ReactNode;
  children:   React.ReactNode;
};

export function Guard({ permission, fallback = null, children }: GuardProps) {
  const allowed = usePermission(permission);
  return allowed ? <>{children}</> : <>{fallback}</>;
}
```

```tsx
// Usage
<Guard permission="invoice:delete">
  <DeleteInvoiceButton />
</Guard>

// With fallback
<Guard permission="invoice:approve" fallback={<DisabledApproveButton />}>
  <ApproveInvoiceButton />
</Guard>
```

Prefer the controller pattern (expose `can.*` through context) over `<Guard>` in feature components. `<Guard>` is for shared primitives and layout-level conditional rendering where a controller context is not available.

---

## Route-level permission guard

```tsx
// src/components/auth/RequirePermission.tsx
import { Navigate, Outlet }   from 'react-router-dom';
import { usePermission }      from '@/hooks/use-permission';
import type { PermissionKey } from '@/app/permission-registry';

type Props = {
  permission:  PermissionKey;
  redirectTo?: string;
};

export function RequirePermission({ permission, redirectTo = '/' }: Props) {
  const allowed = usePermission(permission);
  return allowed ? <Outlet /> : <Navigate to={redirectTo} replace />;
}
```

```tsx
// src/app/router.tsx
{
  element: <RequirePermission permission="settings:manage" redirectTo={ROUTES.home} />,
  children: [
    { path: ROUTES.settingsBilling, element: withSuspense(BillingPage) },
  ],
},
```

---

## Permission-aware forms

Form fields that are conditionally shown based on role follow the same controller pattern — compute the boolean in the controller, expose it through context, consume it in the component.

```tsx
// Controller exposes whether the budget field is accessible
can: {
  setBudget: can('project:set_budget'),
}

// Component reads from context
const { can } = useProjectFormContext();

<form>
  <Input label="Name" {...register('name')} />
  {can.setBudget && (
    <CurrencyInput label="Budget" {...register('budget_cents')} />
  )}
</form>
```

If a hidden field has a default value that must still be submitted, send it from the action hook — never as a hidden `<input>` in the DOM.

---

## Adding a new permission

1. Add the key and allowed roles to `features/<f>/permissions.ts`
2. Import the feature's permissions in `src/app/permission-registry.ts` and spread them
3. `PermissionKey` updates automatically — TypeScript will error if an old key was renamed

No other files need to change. The hook, the Guard component, and all route guards pick up the new key automatically because they derive from the registry type.

---

## The security boundary rule

Every permission check on the frontend is a UX shortcut only.

- Hiding a "Delete" button does not prevent a manual DELETE request.
- If the backend returns `403`, surface it as "You don't have permission to do this" — not as a bug.
- The backend must enforce the same rules independently on every request.

---

## What permissions must NOT do

- **Never rely on frontend permission checks as the only gate for sensitive actions.** The backend must always re-check.
- **Never read `user.role` or `user.roles` directly in a component for a permission decision.** Always go through `usePermissions()` or the controller's `can.*` object.
- **Never call `usePermission` inside a loop, `Array.map`, or `Array.some`.** It is a hook — it must be called at the top level. Use `usePermissions()` and call `can()` inside the loop instead.
- **Never hardcode role names in feature components.** Features reference permission keys (`'invoice:delete'`), not roles (`'admin'`). The registry owns the role mapping.
- **Never define a permission key in `permission-registry.ts` directly.** Keys belong in `features/<f>/permissions.ts` — the registry only assembles them.
- **Never fetch permissions in a separate API call.** They come with the auth session and live in the auth store.
- **Never import another feature's permissions file.** The registry is the only join point.
- **Never block the 403 error pathway.** A 403 means the frontend permission check was bypassed — the error boundary and `notify.error` handle it.
