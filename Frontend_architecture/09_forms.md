# 09 — Form Contract

## Definition

Forms are schema-first. The Zod schema is defined before the form component. It is the single source of truth for field names, types, and validation. React Hook Form v7 with `zodResolver` connects the schema to the form state.

---

## The pattern

```
Zod schema (types.ts)
       ↓
zodResolver (in the form hook/component)
       ↓
React Hook Form
       ↓
Form component (renders fields)
       ↓
onSubmit → mutation hook
```

---

## Schema definition

Input schemas live in the feature's `types.ts`, separate from response schemas:

```ts
// src/features/invoices/types.ts
import { z } from 'zod';
import type { InvoiceId } from '@/types/common';

export const CreateInvoiceInputSchema = z.object({
  client_id:   z.string().uuid(),  // frontend-generated entity ID — see 24_dto.md
  customer_id: z.string().uuid({ message: 'Select a customer.' }),
  due_date:    z.string().datetime({ offset: true, message: 'Enter a valid due date.' }),
  notes:       z.string().max(500).optional(),
  line_items:  z.array(
    z.object({
      description:      z.string().min(1, 'Description is required.'),
      quantity:         z.number().int().positive('Quantity must be at least 1.'),
      unit_price_cents: z.number().int().nonnegative('Price cannot be negative.'),
    })
  ).min(1, 'Add at least one line item.'),
});

export type CreateInvoiceInput = z.infer<typeof CreateInvoiceInputSchema>;
```

---

## Form hook

Complex forms get their own hook. Simple forms embed the hook inside the component.

`client_id` is a hidden form value — not a user-visible field. It is generated once when the form initialises and preserved through the entire lifecycle including failure retries. See [24_dto.md](24_dto.md) and [08_hooks.md](08_hooks.md) for the full failure recovery pattern.

```ts
// src/features/invoices/hooks/use-create-invoice-form.ts
import { useForm } from 'react-hook-form';
import { useLocation } from 'react-router-dom';
import { zodResolver } from '@hookform/resolvers/zod';
import { CreateInvoiceInputSchema, type CreateInvoiceInput } from '@/features/invoices/types';
import type { InvoiceId } from '@/types/common';

export function useCreateInvoiceForm() {
  const { state } = useLocation();
  // If navigated back from a failed optimistic create, prefill contains the exact
  // input that was attempted — including the original client_id for idempotent retry.
  const prefill = state?.prefill as CreateInvoiceInput | undefined;

  const form = useForm<CreateInvoiceInput>({
    resolver: zodResolver(CreateInvoiceInputSchema),
    defaultValues: prefill ?? {
      client_id:   crypto.randomUUID() as InvoiceId,  // hidden — user never sees this
      customer_id: '',
      due_date:    '',
      notes:       '',
      line_items:  [{ description: '', quantity: 1, unit_price_cents: 0 }],
    },
  });

  return { form, hasPrefill: Boolean(prefill) };
}
```

The `onSubmit` and action call live in the component or controller so they have access to navigation and mutation state. The form hook's only job is schema + `defaultValues`.

---

## Form component

The form component receives the form object from the hook and renders fields:

```tsx
// src/features/invoices/components/CreateInvoiceForm.tsx
import { useCreateInvoiceForm } from '@/features/invoices/hooks/use-create-invoice-form';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';

type CreateInvoiceFormProps = {
  onSuccess: (id: InvoiceId) => void;
};

export function CreateInvoiceForm({ onSuccess }: CreateInvoiceFormProps) {
  const { form } = useCreateInvoiceForm();
  const { createInvoice, isPending } = useCreateInvoiceContext();
  const { register, formState: { errors } } = form;

  const onSubmit = form.handleSubmit((input) => {
    createInvoice(input, {
      onSuccess: (invoice) => onSuccess(invoice.id),
    });
  });

  return (
    <form onSubmit={onSubmit} noValidate>
      <input type="hidden" {...register('client_id')} />
      <Input
        {...register('due_date')}
        type="datetime-local"
        label="Due date"
        error={errors.due_date?.message}
      />
      <Button type="submit" disabled={isPending}>
        {isPending ? 'Creating…' : 'Create Invoice'}
      </Button>
    </form>
  );
}
```

---

## Field registration

Use `register` for simple fields. Use `Controller` for controlled third-party components (select dropdowns, date pickers, rich text editors):

```tsx
// Simple field — register
<input {...register('notes')} />

// Controlled third-party — Controller
<Controller
  name="status"
  control={form.control}
  render={({ field }) => (
    <Select
      value={field.value}
      onChange={field.onChange}
      options={STATUS_OPTIONS}
    />
  )}
/>
```

---

## Field arrays

Use React Hook Form's `useFieldArray` for dynamic field lists:

```tsx
import { useFieldArray } from 'react-hook-form';

const { fields, append, remove } = useFieldArray({
  control: form.control,
  name: 'line_items',
});

return (
  <ul>
    {fields.map((field, index) => (
      <li key={field.id}>
        <input {...register(`line_items.${index}.description`)} />
        <input {...register(`line_items.${index}.quantity`, { valueAsNumber: true })} />
        <button type="button" onClick={() => remove(index)}>Remove</button>
      </li>
    ))}
    <button type="button" onClick={() => append({ description: '', quantity: 1, unit_price_cents: 0 })}>
      Add item
    </button>
  </ul>
);
```

---

## Server-side validation errors

Backend validation errors (`validation_failed` with `field_errors`) are mapped back onto form fields using `form.setError`:

```ts
onError: (err) => {
  if (err instanceof ApiRequestError && err.code === 'validation_failed' && err.fieldErrors) {
    Object.entries(err.fieldErrors).forEach(([field, messages]) => {
      form.setError(field as keyof CreateInvoiceInput, {
        type: 'server',
        message: messages[0],
      });
    });
  }
},
```

This ensures server-side errors appear on the correct field, not just as a generic toast.

---

## Disabled state during submission

Always disable the submit button and form fields during submission:

```tsx
<Button type="submit" disabled={isPending}>
  {isPending ? 'Saving…' : 'Save'}
</Button>
```

For full form lockout:

```tsx
<fieldset disabled={isPending}>
  {/* all fields inside */}
</fieldset>
```

---

## What forms must NOT do

- **Never write a form schema separately from the TypeScript type.** Use `z.infer` — one source of truth.
- **Never validate in the `onSubmit` handler with manual `if/else`.** All validation belongs in the Zod schema and runs through `zodResolver`.
- **Never store form values in Zustand or TanStack Query.** React Hook Form owns form state. Submit the final value via a mutation hook.
- **Never call the mutation directly from the component's `onSubmit` without going through `form.handleSubmit`.** `handleSubmit` runs validation before calling your handler.
- **Never show a generic "something went wrong" error for `validation_failed`.** Map server-side field errors back onto the form fields.
- **Never forget `noValidate` on the `<form>` element.** The browser's native validation UI conflicts with the custom validation display.
- **Never call `form.reset()` in `onError`.** Resetting on failure discards the user's work. Leave the form as-is; surface the error and let the user fix and retry.
- **Never sync form values from the query cache after the initial mount with `useEffect → form.reset()`.** If the cache rolls back after an optimistic failure, this pattern wipes the user's edits. Initialize once from `defaultValues`; use `action.variables` for failure recovery.
- **Never generate `client_id` inside `onSubmit`.** Generate it in `defaultValues` when the form initialises so it is stable across retries and failure recovery.
