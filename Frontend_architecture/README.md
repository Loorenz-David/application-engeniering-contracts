# Frontend Architecture Contract

This contract defines the engineering rules for every React application built on top of a backend that follows the [`Backend_architecture/`](../Backend_architecture/README.md) contract set.

It is opinionated by design. Every decision has a reason. The reason is stated so you can apply judgment to edge cases.

**Tech stack this contract targets:**
- React 18 + TypeScript (strict)
- Vite 5
- React Router v6
- TanStack Query v5
- Zustand v4
- React Hook Form v7 + Zod v3
- Tailwind CSS v3 + class-variance-authority (cva)
- Framer Motion v11
- Vitest + @testing-library/react + MSW v2

Engineers and AI assistants **must** read this contract before writing any frontend code.

---

## How this contract is organized

### Foundation
| File | Covers |
|---|---|
| [01_architecture.md](01_architecture.md) | Layer map, folder structure, tech stack, hard dependency rules |
| [02_types.md](02_types.md) | TypeScript strict config, Zod at boundaries, no `any`, branded IDs, global shared types |
| [03_environment.md](03_environment.md) | Vite env vars, `import.meta.env`, startup validation, tsconfig paths |
| [24_dto.md](24_dto.md) | DTO categories (Response, Request, Query Params, View Model), naming, transformation pipeline |

### Data Layer
| File | Covers |
|---|---|
| [04_api_client.md](04_api_client.md) | HTTP client wrapper, response envelope, error normalization |
| [05_server_state.md](05_server_state.md) | TanStack Query v5: query key factories, query/mutation hooks, cache invalidation |
| [06_client_state.md](06_client_state.md) | Zustand v4: slice pattern, selectors, when to use vs server state |

### Logic Layer
| File | Covers |
|---|---|
| [08_hooks.md](08_hooks.md) | Hook taxonomy: Actions (one operation), Controllers (aggregate UI API), Flows (multi-step), Utilities |
| [23_providers.md](23_providers.md) | Context/provider pattern: provider-per-section, context consumer hook, flow providers |

### UI Layer
| File | Covers |
|---|---|
| [07_components.md](07_components.md) | Two component categories: shared primitives (props) vs feature components (context only) |
| [09_forms.md](09_forms.md) | React Hook Form + Zod: schema-first forms, submit pattern, field errors |
| [10_pages.md](10_pages.md) | Page components: renders provider + Suspense + ErrorBoundary, nothing else |
| [32_loading_skeletons.md](32_loading_skeletons.md) | Skeleton reflections, centralized shimmer utility, page/surface/card loading states |

### Cross-Cutting Concerns
| File | Covers |
|---|---|
| [11_routing.md](11_routing.md) | React Router v6: route config, lazy loading, nested routes, protected routes |
| [12_auth.md](12_auth.md) | Token storage, refresh loop, `useAuth` hook, protected route guard |
| [13_errors.md](13_errors.md) | Error hierarchy, error boundary placement, user-facing error messages |
| [14_styling.md](14_styling.md) | Tailwind + cva: variant contract, design tokens, no arbitrary values rule |

### Feature Organization
| File | Covers |
|---|---|
| [15_feature_structure.md](15_feature_structure.md) | Full folder layout: api/, actions/, controllers/, flows/, providers/, components/ |
| [16_feature_workflow.md](16_feature_workflow.md) | 14-step build sequence: types → API → actions → controllers → providers → components → pages → dynamic loading → routes |

### Quality
| File | Covers |
|---|---|
| [17_testing.md](17_testing.md) | Vitest + RTL + MSW v2: test layers, query priorities, mock patterns |
| [18_performance.md](18_performance.md) | Code splitting, lazy routes, memoization rules, bundle discipline |

### Application Features
| File | Covers |
|---|---|
| [19_permissions.md](19_permissions.md) | Client-side permissions: effective backend permissions, `usePermission`, conditional rendering, security boundary |
| [20_notifications.md](20_notifications.md) | Message system: centralized store, app-level config (duration, max count), global `notify` singleton, sourcing rules, replaceable renderer |
| [21_realtime.md](21_realtime.md) | Socket.io: centralized event registry, SocketProvider, `refetchType: 'active'` pattern, debouncing, room management |
| [22_file_handling.md](22_file_handling.md) | File upload/download: validation, progress tracking, backend seam |
| [25_user_profile.md](25_user_profile.md) | Identity vs full profile: auth store, TanStack cache, `useCurrentUser`, avatar, preferences |
| [26_persistence.md](26_persistence.md) | Storage tiers: in-memory, localStorage, TanStack Query persister, IndexedDB — what goes where and why |

### Layout System
| File | Covers |
|---|---|
| [27_responsive.md](27_responsive.md) | `BreakpointProvider` (single listener), CSS-first rule, `useBreakpoint()`, shared primitives (Dialog, Drawer) |
| [28_surfaces.md](28_surfaces.md) | Surface Manager: registry, `useSurface()`, DrawerSurface, ModalSurface, surface stacking, feature decoupling |
| [29_scrollbars.md](29_scrollbars.md) | CSS-only custom scrollbars: global tokens, webkit + Firefox APIs, utility classes, performance rules |
| [30_dynamic_loading.md](30_dynamic_loading.md) | Route, surface, feature, and heavy-library lazy loading; preloading rules; fallback taxonomy |
| [31_animations.md](31_animations.md) | Framer Motion default, CSS vs Motion rules, reduced motion, animation tokens, surface/list transitions |

---

## Navigation matrix — what to read for each task

| Task | Start here | Then read |
|---|---|---|
| **Bootstrap a new frontend app** | [01_architecture.md](01_architecture.md) | 03, 11, 12 |
| **Add a new feature end-to-end** | [16_feature_workflow.md](16_feature_workflow.md) | 15, 02, 04, 05 |
| **Build an action hook (create/delete/update)** | [08_hooks.md](08_hooks.md) | 05 |
| **Build a controller hook** | [08_hooks.md](08_hooks.md) | 05, 06, 19 |
| **Build a flow hook (multi-step wizard)** | [08_hooks.md](08_hooks.md) | 09 |
| **Wire a provider for a UI section** | [23_providers.md](23_providers.md) | 08, 10 |
| **Build a feature component** | [07_components.md](07_components.md) | 23, 14 |
| **Build a shared UI primitive** | [07_components.md](07_components.md) | 14 |
| **Build a skeleton/loading state** | [32_loading_skeletons.md](32_loading_skeletons.md) | 07, 10, 14 |
| **Fetch and display server data** | [05_server_state.md](05_server_state.md) | 04, 08 |
| **Build a form with validation** | [09_forms.md](09_forms.md) | 02, 08 |
| **Add a new page/route** | [10_pages.md](10_pages.md) | 11, 13, 23 |
| **Lazy-load a route, surface, or heavy library** | [30_dynamic_loading.md](30_dynamic_loading.md) | 11, 18, 28 |
| **Protect a route** | [11_routing.md](11_routing.md) | 12, 19 |
| **Add client-side auth** | [12_auth.md](12_auth.md) | 04, 05, 11 |
| **Display or update the current user's profile** | [25_user_profile.md](25_user_profile.md) | 12, 05, 22 |
| **Choose where to persist data** | [26_persistence.md](26_persistence.md) | 05, 06 |
| **Add OAuth / SSO sign-in** | [12_auth.md](12_auth.md) | 11 |
| **Add real-time updates** | [21_realtime.md](21_realtime.md) | 05, 08 |
| **Handle errors gracefully** | [13_errors.md](13_errors.md) | 10, 04, 20 |
| **Add global client state** | [06_client_state.md](06_client_state.md) | 05 |
| **Add a new API call** | [04_api_client.md](04_api_client.md) | 02, 05 |
| **Define DTOs for a new entity** | [24_dto.md](24_dto.md) | 02, 15 |
| **Add TypeScript types for an entity** | [02_types.md](02_types.md) | 24, 15 |
| **Write tests for a controller** | [17_testing.md](17_testing.md) | 08 |
| **Write tests for a feature component** | [17_testing.md](17_testing.md) | 07, 23 |
| **Optimize rendering performance** | [18_performance.md](18_performance.md) | 07, 08 |
| **Show toast notifications** | [20_notifications.md](20_notifications.md) | 13 |
| **Restrict UI by permission** | [19_permissions.md](19_permissions.md) | 12, 08 |
| **Add file upload to a form** | [22_file_handling.md](22_file_handling.md) | 09, 08 |
| **Set up env vars correctly** | [03_environment.md](03_environment.md) | 01 |
| **Apply consistent styling** | [14_styling.md](14_styling.md) | 07 |
| **Style scrollbars globally** | [29_scrollbars.md](29_scrollbars.md) | 14 |
| **Animate a surface, route, list, or transition** | [31_animations.md](31_animations.md) | 14, 18, 28 |
| **Handle phone vs desktop layout** | [27_responsive.md](27_responsive.md) | 07 |
| **Open a feature in a drawer or modal** | [28_surfaces.md](28_surfaces.md) | 11, 27 |
| **Register a feature with the surface manager** | [28_surfaces.md](28_surfaces.md) | 15 |
| **Decouple two features from each other** | [28_surfaces.md](28_surfaces.md) | 15, 07 |

---

## The seam: frontend ↔ backend

The frontend communicates with the backend only through HTTP. Components never touch the network. This is the full chain:

```
Feature Component
        ↓  reads from
Provider Context  (useXxxContext)
        ↓  injected by
Provider Component  (runs controller once)
        ↓  calls
Controller hook  (aggregates queries + actions + permissions)
        ↓  composes
Action hook  (wraps one mutation)
Query hook   (wraps one TanStack Query)
        ↓  calls
API function  (typed fetch, Zod-validated)
        ↓  via
API Client  (src/lib/api-client.ts — the only file that calls fetch)
        ↓  HTTP
Backend endpoint → command or query
```

Read [04_api_client.md](04_api_client.md), [05_server_state.md](05_server_state.md), [08_hooks.md](08_hooks.md), and [23_providers.md](23_providers.md) together to understand how data flows from the backend to a rendered component.

---

## Build order

### Phase 1 — Project foundation
**Contracts:** 01, 02, 03, 11  
Vite setup, TypeScript strict config, path aliases, env validation, route skeleton.

### Phase 2 — API layer
**Contracts:** 04, 05, 12  
API client, TanStack Query configuration, auth token handling. Verify one round-trip works before building any feature.

### Phase 3 — First feature
**Contracts:** 16, 15, 08, 23, 07, 09, 10  
Build one complete feature following the 14-step workflow: types → API → actions → controller → provider → components → page → dynamic loading → route. This establishes the pattern.

### Phase 4 — Cross-cutting infrastructure
**Contracts:** 13, 14, 19, 20, 31  
Error boundaries, styling system, notification store, animation baseline — needed before building additional features.

### Phase 5 — Quality baseline
**Contracts:** 17, 18, 30  
Test harness, first feature coverage, bundle baseline.

### Phase 6 — Application-specific features
**Contracts:** 21, 22  
Real-time and file handling — add when the application requires them.

---

## Non-negotiable rules (memorize these)

1. **Feature components consume context only — never the logic layer directly.** A feature component (`features/<f>/components/`) may not import from `api/`, `actions/`, `controllers/`, `flows/`, or `store/`. It reads from the context hook its provider injects.
2. **Shared UI primitives consume props only — never context.** A component in `src/components/ui/` has no knowledge of any feature, domain, or provider.
3. **One controller per UI section, running in its provider.** The controller runs once. Every component in the provider's subtree shares the same derived state and action references.
4. **Actions wrap one mutation each.** An action that calls two mutations is two actions. Composition happens in the controller or flow.
5. **Server state lives in TanStack Query — never in `useState` or Zustand.** If the data comes from the backend, it is server state.
6. **Every API response is validated with Zod before entering the app.** Schema mismatch throws immediately. Silent `any` leakage from API responses is forbidden.
7. **Feature boundaries are enforced through `index.ts`.** No deep imports from outside a feature. If it is not in `index.ts`, it is private.
8. **Forms are schema-first.** Zod schema defined first → `z.infer` type → `zodResolver` wires it to RHF. The schema is the single source of truth for validation.
9. **Error boundaries exist at every route boundary.** A broken feature must never crash the app shell or a sibling feature.
10. **Every route is lazy-loaded.** No page component is synchronously imported. Bundle splitting is a hard requirement.
11. **Client-side permissions are UX only, never a security boundary.** The backend enforces authorization. The frontend hides inaccessible UI as a courtesy.
12. **Never store access tokens in `localStorage`.** Use `httpOnly` cookies for the refresh token and in-memory storage for the access token.
13. **No `any` in production code.** Use `unknown` and narrow with Zod. Every `@ts-ignore` requires a comment explaining why.
14. **Dynamic loading happens at boundaries.** Routes load pages, surfaces load overlay content, and dynamic adapters load heavy one-off libraries. Do not scatter dynamic imports through leaf components.
15. **Animation is a UI concern.** Framer Motion is the default for UI transitions, CSS handles simple state transitions, and reduced motion must be respected.
16. **Loading UI reflects the final UI.** Cards, pages, surfaces, and feature sections use skeleton reflections with the centralized shimmer utility instead of ad hoc spinners or mismatched placeholders.
