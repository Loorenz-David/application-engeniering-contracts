# 23 — Provider Contract

## Definition

A provider is a React Context wrapper that injects a controller's (or flow's) output into a component subtree. It is the mechanism that decouples feature components from the logic layer: components consume context, never hooks directly.

One provider per UI section. The provider runs the controller once; every component in its subtree reads from the same context object.

---

## The pattern

```
Page
 └── <InvoiceListProvider>        ← runs useInvoiceListController(), injects result
       ├── <InvoiceFilters>       ← useInvoiceListContext() → filters + setters
       ├── <InvoiceTable>         ← useInvoiceListContext() → invoices + isPending
       └── <InvoicePagination>    ← useInvoiceListContext() → total + page + setPage
```

The controller runs once, at the provider boundary. All components in the subtree share the same derived state and the same action function references. No prop drilling. No duplicated hook calls.

---

## Provider structure

Every provider file exports three things:
1. The `Provider` component
2. The context consumer hook (`useXxxContext`)
3. Nothing else — the context object itself is not exported

```tsx
// features/invoices/providers/InvoiceListProvider.tsx
import { createContext, useContext } from 'react';
import {
  useInvoiceListController,
  type InvoiceListController,
} from '@/features/invoices/controllers/use-invoice-list.controller';

// ─── Context ──────────────────────────────────────────────────────────────────

const InvoiceListContext = createContext<InvoiceListController | null>(null);

// ─── Provider ─────────────────────────────────────────────────────────────────

export function InvoiceListProvider({ children }: { children: React.ReactNode }) {
  const controller = useInvoiceListController();
  return (
    <InvoiceListContext.Provider value={controller}>
      {children}
    </InvoiceListContext.Provider>
  );
}

// ─── Consumer hook ────────────────────────────────────────────────────────────

export function useInvoiceListContext(): InvoiceListController {
  const ctx = useContext(InvoiceListContext);
  if (!ctx) throw new Error(
    'useInvoiceListContext must be used within <InvoiceListProvider>',
  );
  return ctx;
}
```

The context is initialized to `null` and the consumer hook throws if called outside the provider. This makes misuse fail loudly at development time.

---

## Provider placement

Providers are placed in page components, not inside other components:

```tsx
// pages/invoices/InvoicesPage.tsx
import { InvoiceListProvider } from '@/features/invoices';

export function InvoicesPage() {
  return (
    <RouteErrorBoundary>
      <Suspense fallback={<PageSkeleton />}>
        <InvoiceListProvider>
          <InvoiceListView />
        </InvoiceListProvider>
      </Suspense>
    </RouteErrorBoundary>
  );
}
```

`InvoiceListView` is the top-level feature component. It renders the layout and composes child components. Each child reads from the same context.

```tsx
// features/invoices/components/InvoiceListView.tsx
export function InvoiceListView() {
  return (
    <div className="flex flex-col gap-4">
      <InvoiceListHeader />
      <InvoiceFilters />
      <InvoiceTable />
      <InvoicePagination />
    </div>
  );
}
```

---

## One provider per UI section

A "section" is a coherent interactive area of the UI that shares a single data and action context. Common boundaries:

| Section | Provider |
|---|---|
| A list page with filters, table, pagination | `InvoiceListProvider` |
| A detail/edit page for one entity | `InvoiceDetailProvider` |
| A multi-step creation wizard | `CreateInvoiceFlowProvider` |
| A dashboard with multiple independent widgets | One provider per widget, or none if widgets use separate contexts |

If two parts of the page do not share state or actions, they do not share a provider.

---

## Detail provider

```tsx
// features/invoices/providers/InvoiceDetailProvider.tsx
import { createContext, useContext } from 'react';
import {
  useInvoiceDetailController,
  type InvoiceDetailController,
} from '@/features/invoices/controllers/use-invoice-detail.controller';
import type { InvoiceId } from '@/types/common';

const InvoiceDetailContext = createContext<InvoiceDetailController | null>(null);

type Props = { invoiceId: InvoiceId; children: React.ReactNode };

export function InvoiceDetailProvider({ invoiceId, children }: Props) {
  const controller = useInvoiceDetailController(invoiceId);
  return (
    <InvoiceDetailContext.Provider value={controller}>
      {children}
    </InvoiceDetailContext.Provider>
  );
}

export function useInvoiceDetailContext(): InvoiceDetailController {
  const ctx = useContext(InvoiceDetailContext);
  if (!ctx) throw new Error('useInvoiceDetailContext must be used within <InvoiceDetailProvider>');
  return ctx;
}
```

```tsx
// pages/invoices/InvoiceDetailPage.tsx
export function InvoiceDetailPage() {
  const { invoiceId } = useParams<{ invoiceId: string }>();
  if (!invoiceId) return null;

  return (
    <RouteErrorBoundary>
      <Suspense fallback={<PageSkeleton />}>
        <InvoiceDetailProvider invoiceId={invoiceId as InvoiceId}>
          <InvoiceDetailView />
        </InvoiceDetailProvider>
      </Suspense>
    </RouteErrorBoundary>
  );
}
```

---

## Flow provider

When a multi-step flow spans multiple components (e.g., a step indicator, a step body, and a navigation bar), wrap them in a flow provider:

```tsx
// features/invoices/providers/CreateInvoiceFlowProvider.tsx
import { createContext, useContext } from 'react';
import {
  useCreateInvoiceFlow,
  type CreateInvoiceFlowController,
} from '@/features/invoices/flows/use-create-invoice-flow';
import type { InvoiceId } from '@/types/common';

const CreateInvoiceFlowContext = createContext<CreateInvoiceFlowController | null>(null);

type Props = { onComplete: (id: InvoiceId) => void; children: React.ReactNode };

export function CreateInvoiceFlowProvider({ onComplete, children }: Props) {
  const flow = useCreateInvoiceFlow(onComplete);
  return (
    <CreateInvoiceFlowContext.Provider value={flow}>
      {children}
    </CreateInvoiceFlowContext.Provider>
  );
}

export function useCreateInvoiceFlowContext(): CreateInvoiceFlowController {
  const ctx = useContext(CreateInvoiceFlowContext);
  if (!ctx) throw new Error('useCreateInvoiceFlowContext must be used within <CreateInvoiceFlowProvider>');
  return ctx;
}
```

```tsx
// pages/invoices/CreateInvoicePage.tsx
export function CreateInvoicePage() {
  const navigate = useNavigate();
  return (
    <RouteErrorBoundary>
      <Suspense fallback={<CreateInvoicePageSkeleton />}>
        <CreateInvoiceFlowProvider onComplete={(id) => navigate(ROUTES.invoiceDetail(id))}>
          <CreateInvoiceWizard />
        </CreateInvoiceFlowProvider>
      </Suspense>
    </RouteErrorBoundary>
  );
}

// features/invoices/components/CreateInvoiceWizard.tsx
export function CreateInvoiceWizard() {
  const { currentStep, totalSteps, stepIndex } = useCreateInvoiceFlowContext();
  return (
    <div>
      <WizardStepIndicator current={stepIndex} total={totalSteps} />
      {currentStep === 'client'     && <ClientStep />}
      {currentStep === 'line_items' && <LineItemsStep />}
      {currentStep === 'details'    && <DetailsStep />}
      {currentStep === 'review'     && <ReviewStep />}
      <WizardNavigation />
    </div>
  );
}

// features/invoices/components/WizardNavigation.tsx
export function WizardNavigation() {
  const { isFirst, isLast, isPending, goBack, submit } = useCreateInvoiceFlowContext();
  return (
    <div className="flex justify-between">
      {!isFirst && <Button variant="secondary" onClick={goBack}>Back</Button>}
      {isLast
        ? <Button onClick={submit} disabled={isPending}>{isPending ? 'Creating…' : 'Create Invoice'}</Button>
        : <Button type="submit" form="step-form">Next</Button>
      }
    </div>
  );
}
```

---

## Context vs props: the decision rule

| Use context when | Use props when |
|---|---|
| A feature component needs data or actions from the section's controller | A shared UI primitive (`Button`, `Input`) needs configuration |
| Multiple sibling components share the same data source | A leaf component renders one item from a list (the item itself) |
| Eliminating prop drilling across 2+ levels | The component is used outside a provider tree |

A good signal that you need a provider: when you find yourself passing the same prop down through two or more layers of components that don't use it themselves.

---

## What providers must NOT do

- **Never put business logic in the provider component itself.** The provider only calls a controller and wraps children. All logic lives in the controller.
- **Never nest two providers of the same type.** If a detail page is inside a list page, they each have their own provider — the detail provider does not extend the list provider.
- **Never export the context object.** Only export the Provider component and the consumer hook.
- **Never initialize context with a default value other than `null`.** Default values hide missing-provider bugs. Throw in the consumer hook instead.
- **Never create a provider for state that is only used by one component.** If only one component needs it, `useState` inside that component is the right tool.
- **Never use a provider only to duplicate TanStack Query's cache.** If two independent controllers need the same query, TanStack Query deduplicates the request. Feature components still do not call query hooks directly; they read data through their section provider.
