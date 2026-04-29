# 02 — Types Contract

## Definition

This contract covers the **TypeScript and Zod configuration rules** that apply everywhere in the application: strict mode, the no-`any` rule, how Zod is used at data boundaries, branded ID types, and shared global types.

For the definition, naming, categories, and transformation pipeline of DTOs specifically, see [24_dto.md](24_dto.md). The two contracts are complementary: this one defines the tooling rules; that one defines the data shapes.

---

## TypeScript configuration

`tsconfig.json` must include `strict: true` with no exceptions. The following flags are required beyond `strict`:

```json
{
  "compilerOptions": {
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "noImplicitReturns": true,
    "noFallthroughCasesInSwitch": true
  }
}
```

`noUncheckedIndexedAccess` makes array index access return `T | undefined`, catching a class of runtime crashes TypeScript otherwise ignores silently.

---

## The no-`any` rule

`any` is forbidden in production code.

| Instead of | Use |
|---|---|
| `any` for unknown API data | `unknown`, then narrow with Zod |
| `any` for event handlers | The specific event type (`React.ChangeEvent<HTMLInputElement>`) |
| `any` for untyped library | A hand-written `declare module` stub |
| `(value as any)` | Fix the type or use `unknown` + a type guard |

When `@ts-ignore` or `@ts-expect-error` is unavoidable, the suppression must be on its own line with a comment explaining why:

```ts
// The library types incorrectly mark this as readonly at this version.
// TODO: remove when they ship the fix in v3.
// @ts-expect-error
config.headers['Authorization'] = token;
```

---

## Zod at every data boundary

Zod validates all data that crosses into the app from outside TypeScript's type system:

| Boundary | Where Zod runs |
|---|---|
| HTTP API responses | `api/fetch-<entity>.ts` — API function, before returning |
| Form inputs | `zodResolver` in the form hook, at submit |
| `localStorage` reads | Point of read, before storing in state |
| URL search params | Route/filter parser hook, before passing to query hooks |
| Environment variables | `src/lib/env.ts` at startup (see `03_environment.md`) |
| `postMessage` payloads | Event handler, before processing |

Zod parsing happens once, at the boundary. It does not repeat at each layer. A boundary may be an API function, form resolver, env parser, storage read helper, URL-param parser, or browser event handler. Components and controllers consume already-parsed values.

```ts
// Correct — parsed at the API function boundary
export async function fetchInvoice(id: InvoiceId): Promise<Invoice> {
  const { invoice } = await apiClient.get(`/api/v1/invoices/${id}`, GetInvoiceResponse);
  return invoice;  // Invoice type guaranteed — Zod threw if shape was wrong
}

// Wrong — returning unknown and parsing later in the controller
export async function fetchInvoice(id: InvoiceId): Promise<unknown> { ... }
```

The TypeScript type is **always** derived from the Zod schema with `z.infer`. Never write a TypeScript interface and then write a matching Zod schema separately — they will drift.

---

## Global shared types

Types shared across multiple features live in `src/types/`, not inside any one feature.

```ts
// src/types/api.ts
import { z } from 'zod';

// Paginated list envelope — matches the backend's pagination wrapper
export const PaginatedResponseSchema = <T extends z.ZodTypeAny>(itemSchema: T) =>
  z.object({
    items:    z.array(itemSchema),
    total:    z.number().int(),
    page:     z.number().int(),
    per_page: z.number().int(),
    has_next: z.boolean(),
  });

export type PaginatedResponse<T> = {
  items:    T[];
  total:    number;
  page:     number;
  per_page: number;
  has_next: boolean;
};

// Error envelope — matches the backend's error response shape (see 04_api_client.md)
export const ApiErrorSchema = z.object({
  error: z.object({
    code:         z.string(),
    message:      z.string(),
    field_errors: z.record(z.array(z.string())).optional(),
  }),
});
export type ApiError = z.infer<typeof ApiErrorSchema>;
```

---

## Discriminated unions over optional fields

When a value can be in multiple distinct states, model them as a discriminated union — not as optional fields on a single type:

```ts
// Wrong — optional fields allow impossible combinations (data + error both set)
type AsyncData<T> = {
  data?: T;
  error?: Error;
  isLoading: boolean;
};

// Correct — every state is unambiguous and exhaustively checkable
type AsyncData<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: T }
  | { status: 'error'; error: Error };
```

TanStack Query returns this pattern natively via `query.status`. Use `status` when a component or controller needs to distinguish more than two states. Boolean flags such as `isPending`, `isError`, and `isSuccess` are acceptable when the branch is simple and does not allow impossible state combinations in your own types.

Do not create custom async-state objects with loose optional fields. Either use TanStack Query's result object or define an explicit discriminated union.

---

## Branded ID types

Branded types prevent accidentally passing a `userId` where an `invoiceId` is required. The TypeScript compiler enforces the distinction.

```ts
// src/types/common.ts
declare const __brand: unique symbol;
type Brand<T, B> = T & { [__brand]: B };

export type UserId      = Brand<string, 'UserId'>;
export type InvoiceId   = Brand<string, 'InvoiceId'>;
export type WorkspaceId = Brand<string, 'WorkspaceId'>;
export type CustomerId  = Brand<string, 'CustomerId'>;
export type ClientId    = Brand<string, 'ClientId'>;
```

`ClientId` is only for a real domain entity named client/customer if the product has one. It is not the type for request DTO `client_id`. A create request's `client_id` becomes the created entity's branded public ID, such as `InvoiceId`.

Branding happens at the Zod schema boundary using `.transform()`:

```ts
// features/invoices/types.ts
export const InvoiceSchema = z.object({
  id:          z.string().uuid().transform((v) => v as InvoiceId),
  customer_id: z.string().uuid().transform((v) => v as CustomerId),
  // ...
});
```

After this point, the TypeScript compiler rejects any attempt to pass an `InvoiceId` where a `ClientId` is expected — even though both are strings at runtime.

---

## What types must NOT do

- **Never write a TypeScript `interface` for a shape that has a Zod schema.** Use `z.infer`. Parallel declarations drift.
- **Never cast with `as` to silence a type error.** Fix the underlying type. The exception is ID branding at the schema boundary — and only there.
- **Never import a type from a feature's internal files from outside that feature.** Import from `features/<feature>/index.ts` only.
- **Never define the same type in two places.** One schema → one `z.infer` type → one source of truth.
- **Never use `object` or `{}` as a type.** Use `Record<string, unknown>` for open objects, or define the shape explicitly.
- **Never parse external data inside a render component or business controller.** Parse at the boundary helper: API function, form resolver, env parser, storage helper, URL-param parser, or event handler.
- **Never re-parse data after it has crossed a validated boundary.** Pass the typed value through the layers.
