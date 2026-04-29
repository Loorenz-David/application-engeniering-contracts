# 19 — Permissions Contract

## Definition

Client-side permissions are a UX mechanism. They hide inaccessible UI from users who lack the required capability. They are **not** a security boundary — the backend enforces authorization on every request. A hidden button does not prevent the underlying API call.

The frontend does **not** decide which roles are allowed to perform an operation. The backend owns roles, tiers, custom roles, subscription constraints, and per-user overrides. The frontend receives the user's **effective permissions** from the auth/session response and uses them only to shape the interface.

```
Backend role + permission model
        ↓
Session / current-user response
        ↓
Auth store holds roles + effective permissions
        ↓
usePermissions() reads effective permissions
        ↓
Controller maps permission keys to can.*
        ↓
Feature components render from context
```

Features define the permission keys they need. They do not define role rules.

```
features/<f>/permissions.ts     ← feature declares its permission keys only
        ↓  assembled by
src/app/permission-registry.ts  ← app-level list of all valid frontend permission keys
        ↓  typed by
PermissionKey                   ← derived union of all registered keys
        ↓  checked by
usePermissions()                ← checks the auth store's effective permissions
        ↓  consumed by
Controller → context            ← feature components read can.* from context
```

---

## Roles vs permissions

Roles answer "who is this user broadly?" Permissions answer "what can this user do?"

The frontend may display broad identity information such as the user's role or tier, but feature behavior is controlled by permissions. A feature component must never ask whether a user is an `admin`, `manager`, or `viewer`. It asks whether the user can perform a capability:

```ts
// Correct — feature checks a capability
can('invoice:approve');

// Wrong — feature hardcodes a business role
user.roles.includes('manager');
```

This keeps feature code stable when the backend changes role definitions, adds custom roles, or grants a permission to one workspace but not another.

---

## Auth identity shape

The user's roles and effective permissions come from the auth session. They are set at login/session restoration and updated only when the backend returns a new session/current-user payload.

```ts
// src/types/roles.ts
import { z } from 'zod';

export const RoleSchema = z.string().min(1);
export type Role = z.infer<typeof RoleSchema>;
```

```ts
// In the auth store — identity only, not the full profile
type AuthUser = {
  id:          UserId;
  email:       string;
  name:        string;
  roles:       Role[];    // display / broad app-shell decisions only
  permissions: string[];  // effective permission keys from the backend
};
```

`roles` is an array because users may have multiple roles or role-like grants. `permissions` is the resolved capability list. The frontend does not expand roles into permissions.

---

## Permission key naming

Permission keys use `'{feature}:{action}'`:

| Rule | Example |
|---|---|
| Feature segment is singular and lowercase | `invoice:view` |
| Action segment is operation-focused | `invoice:approve` |
| Use verbs, not UI labels | `member:invite`, not `member:show_invite_button` |
| Do not include role names | `report:export`, not `admin:export_report` |
| Do not include implementation details | `file:upload`, not `s3:presign` |

Frontend permission keys should mirror backend permission names closely enough that a `403` can be traced directly to the protected backend operation.

---

## Per-feature permission definitions

Each feature declares the permission keys it needs as named constants. The object groups keys into a stable, feature-friendly API for controllers.

```ts
// features/invoices/permissions.ts
import type { FeaturePermissionMap } from '@/lib/permission-types';

export const invoicePermissions = {
  view:    'invoice:view',
  create:  'invoice:create',
  edit:    'invoice:edit',
  delete:  'invoice:delete',
  approve: 'invoice:approve',
  export:  'invoice:export',
} as const satisfies FeaturePermissionMap;
```

```ts
// features/settings/permissions.ts
import type { FeaturePermissionMap } from '@/lib/permission-types';

export const settingsPermissions = {
  view:    'settings:view',
  manage:  'settings:manage',
  billing: 'settings:billing',
} as const satisfies FeaturePermissionMap;
```

The values are the permission keys. The property names are local aliases used by the feature. A feature may rename a local alias without changing the backend permission key.

---

## App-level registry

The registry assembles all feature permission objects so TypeScript can derive the complete frontend permission union. It does **not** contain role mappings.

```ts
// src/app/permission-registry.ts
import { invoicePermissions }  from '@/features/invoices/permissions';
import { settingsPermissions } from '@/features/settings/permissions';
import { clientPermissions }   from '@/features/clients/permissions';

export const permissionRegistry = {
  invoice:  invoicePermissions,
  settings: settingsPermissions,
  client:   clientPermissions,
} as const;

type PermissionRegistry = typeof permissionRegistry;
export type PermissionKey = {
  [Feature in keyof PermissionRegistry]:
    PermissionRegistry[Feature][keyof PermissionRegistry[Feature]];
}[keyof PermissionRegistry];
```

No feature imports another feature's permissions. The registry is the only join point.

---

## Permission types

```ts
// src/lib/permission-types.ts
// Shape every feature permission file must satisfy
export type FeaturePermissionMap = Record<string, string>;

// Context passed to can() — derived from auth store
export type PermissionContext = {
  permissions: readonly string[];
};
```

`permissions` is typed as `string[]` at the auth boundary because the backend may return a key the current frontend does not know yet during rolling deployments. `can()` accepts only known `PermissionKey` values inside application code.

---

## `usePermissions()` — the primary hook

Returns a stable `can()` function. Call once per controller or component, check as many permissions as needed.

```ts
// src/hooks/use-permissions.ts
import { useCallback, useMemo } from 'react';
import { useAuthStore }         from '@/store/auth.store';
import type { PermissionKey }   from '@/app/permission-registry';

export function usePermissions() {
  const permissions = useAuthStore((s) => s.user?.permissions ?? []);

  const permissionSet = useMemo(
    () => new Set<string>(permissions),
    [permissions],
  );

  const can = useCallback((key: PermissionKey): boolean => {
    return permissionSet.has(key);
  }, [permissionSet]);

  return { can };
}
```

The hook performs membership checks against the backend-provided effective permission set. It does not read `roles`, and it does not apply fallback role logic.

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
import { invoicePermissions } from '../permissions';

export function useInvoiceListController() {
  const { can } = usePermissions();
  const invoicesQuery = useInvoicesQuery();

  return {
    invoices:  invoicesQuery.data?.items ?? [],
    isPending: invoicesQuery.isPending,

    // Permission surface — all checks computed once, exposed as plain booleans
    can: {
      create:  can(invoicePermissions.create),
      delete:  can(invoicePermissions.delete),
      approve: can(invoicePermissions.approve),
      export:  can(invoicePermissions.export),
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

`InvoiceTable` receives `canDelete` as a prop — it never imports a permission hook. The feature component is the decision point; the primitive only renders what it receives.

---

## App-shell role usage

Roles may be used for broad app-shell choices that are not operation authorization:

- choosing the default landing route
- selecting a navigation preset
- displaying the user's workspace role label
- separating major app surfaces such as admin console vs client portal

Even then, prefer backend-provided fields such as `available_surfaces` or `default_route` when the app has complex custom roles. Feature operations still use permissions.

---

## `<Guard>` — declarative conditional rendering

For cases where the permission check and the conditional render are in the same component and the controller pattern is not applicable.

```tsx
// src/components/ui/Guard.tsx
import { usePermission }      from '@/hooks/use-permission';
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
<Guard permission="invoice:delete">
  <DeleteInvoiceButton />
</Guard>

<Guard permission="invoice:approve" fallback={<DisabledApproveButton />}>
  <ApproveInvoiceButton />
</Guard>
```

Prefer the controller pattern over `<Guard>` in feature components. `<Guard>` is for route/layout-level conditional rendering or shared composition where a controller context is not available.

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
    {
      path: ROUTES.settingsBilling,
      element: lazyRoute(() =>
        import('@/pages/settings/BillingPage').then((m) => ({ default: m.BillingPage })),
      ),
    },
  ],
},
```

Route guards are still UX gates. The backend must return `403` for unauthorized requests even if the route was visible.

---

## Permission-aware forms

Form fields that are conditionally shown based on capability follow the same controller pattern — compute the boolean in the controller, expose it through context, consume it in the component.

```tsx
// Controller exposes whether the budget field is accessible
can: {
  setBudget: can(projectPermissions.setBudget),
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

1. Add the backend permission and enforce it on the protected command/query.
2. Ensure the auth/session or `/me` response includes the permission when the user is allowed to perform it.
3. Add the key to `features/<f>/permissions.ts`.
4. Import the feature's permission object in `src/app/permission-registry.ts`.
5. Use the permission from the controller and expose a `can.*` boolean through context.

No frontend permission mapping needs to change. If a role gains or loses a permission, the backend session output changes and the frontend follows automatically.

---

## The security boundary rule

Every permission check on the frontend is a UX shortcut only.

- Hiding a "Delete" button does not prevent a manual DELETE request.
- If the backend returns `403`, surface it as "You don't have permission to do this" — not as a bug.
- The backend must enforce the same rules independently on every request.
- The auth store can be tampered with in the browser; never trust it for authorization.

---

## What permissions must NOT do

- **Never rely on frontend permission checks as the only gate for sensitive actions.** The backend must always re-check.
- **Never map roles to permissions in frontend code.** The backend sends effective permissions.
- **Never read `user.role` or `user.roles` directly in a component for a feature authorization decision.** Always go through `usePermissions()` or the controller's `can.*` object.
- **Never call `usePermission` inside a loop, `Array.map`, or `Array.some`.** It is a hook — it must be called at the top level. Use `usePermissions()` and call `can()` inside the loop instead.
- **Never hardcode role names in feature components.** Features reference permission keys (`'invoice:delete'`), not roles (`'admin'`).
- **Never define permission keys only in `permission-registry.ts`.** Keys belong in `features/<f>/permissions.ts` — the registry only assembles them.
- **Never fetch permissions in a separate API call.** They come with the auth session/current-user payload and live in the auth store.
- **Never import another feature's permissions file.** The registry is the only join point.
- **Never block the 403 error pathway.** A 403 means the frontend permission check was bypassed or stale — the error boundary and `notify.error` handle it.
