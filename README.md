# Application Contracts

This repository is a set of engineering contracts — precise, opinionated specifications that define how every layer of an application must be built. They are the shared language between you and any AI assistant you use to build software.

When you give an AI assistant the right contracts for a task, it builds code that is consistent, production-ready, and compatible with everything else in the system — without you having to re-explain architecture decisions every time.

---

## The three contract sets

| Set | Contracts | What it covers |
|---|---|---|
| [`Backend_architecture/`](Backend_architecture/README.md) | 37 contracts | Flask app factory, models, service layer (commands/queries), HTTP routing, auth, errors, background jobs, migrations, deployment, multi-tenancy, file storage, observability, security |
| [`AI_Architecture/`](AI_Architecture/README.md) | 30 contracts | LLM provider protocol, tool contract, agent identity, MCP server, single agent, orchestrator, clarification protocol, streaming, prompt versioning, background tasks, cost budgets, conversation sessions, router agent, planning orchestrator, multi-modal, provider resilience |
| [`Frontend_architecture/`](Frontend_architecture/README.md) | 22 contracts | React + TypeScript + Vite SPA — layer map, feature structure, API client, TanStack Query, Zustand, components, hooks, forms, routing, auth, errors, styling, testing, performance, real-time, file handling |

---

## How the three sets connect

The backend has no knowledge of the AI layer or the frontend. The AI layer calls the backend's service layer directly. The frontend communicates with the backend through HTTP endpoints only. This is the seam:

**AI ↔ Backend seam:**
```
AI Tool function
      ↓
  ServiceContext (built from AgentContext)
      ↓
  Backend command or query
      ↓
  Database / external service
```

**Frontend ↔ Backend seam:**
```
React Component / Page
        ↓
useQuery / useMutation  (TanStack Query)
        ↓
Query or mutation function  (feature/api/)
        ↓
API Client  (src/lib/api-client.ts)
        ↓
Backend HTTP endpoint
        ↓
Backend command or query
```

A backend command receives a `ServiceContext` whether it was triggered by an HTTP request, an agent tool, or an MCP client. The backend never imports from the AI layer or the frontend. These seams are non-negotiable.

Read [`Backend_architecture/04_context.md`](Backend_architecture/04_context.md) and [`AI_Architecture/02_tool_contract.md`](AI_Architecture/02_tool_contract.md) together to understand how identity flows across the AI/backend boundary.

Read [`Frontend_architecture/04_api_client.md`](Frontend_architecture/04_api_client.md) and [`Frontend_architecture/05_server_state.md`](Frontend_architecture/05_server_state.md) together to understand how data flows across the frontend/backend boundary.

---

## Build order

Do not try to implement everything before writing code. Build in phases — each phase produces working software.

### Phase 1 — Backend foundation
**Contracts:** `Backend_architecture/` 01 through 10  
Build the app factory, domain models, service layer, HTTP routing, and auth. No AI yet. This is the foundation everything else sits on.

### Phase 2 — First agent
**Contracts:** `AI_Architecture/` 01, 02, 03, 09  
Wire the `LLMProvider`, define one tool that calls an existing backend command, build one single agent with a system prompt. Verify the seam works end-to-end before adding more tools or agents.

### Phase 3 — MCP server (if needed)
**Contracts:** `AI_Architecture/` 04, 05, 08  
Expose backend tools and resources to Claude Desktop or other MCP clients. Skip this phase if you are building agents only.

### Phase 4 — Multi-agent patterns
**Contracts:** `AI_Architecture/` 10, 11, 19  
Add an orchestrator, subagents, and the clarification protocol when a single agent can no longer handle the task scope.

### Phase 5 — Production readiness
**Contracts:** `AI_Architecture/` 17, 18, 20, 22, 23, 29  
Safety guardrails, observability, streaming, background tasks, cost budgets, provider resilience. Do this before real users.

### Phase 6 — Free-text interface
**Contracts:** `AI_Architecture/` 26, 27, 28  
Add the intent router, planning orchestrator, and conversation session when you build a chat UI where users can type anything.

### Phase 7 — Per-application AI features
**Contracts:** `AI_Architecture/` 21, 24, 25, 30  
Prompt versioning and evaluation, workspace-level agent configuration, multi-modal file input. Add these as each application's needs demand.

### Phase 8 — Frontend foundation
**Contracts:** `Frontend_architecture/` 01, 02, 03, 11  
Set up Vite, TypeScript strict config, path aliases, env validation, and the route skeleton. No feature code yet.

### Phase 9 — Frontend API layer
**Contracts:** `Frontend_architecture/` 04, 05, 12  
Build the API client, configure TanStack Query, implement auth token handling. Verify a single round-trip to the backend before building any feature.

### Phase 10 — First frontend feature
**Contracts:** `Frontend_architecture/` 15, 16, 07, 08, 09, 10  
Build one complete feature (types → API hooks → components → page). Establish the folder pattern all subsequent features will follow.

### Phase 11 — Frontend cross-cutting infrastructure
**Contracts:** `Frontend_architecture/` 13, 14, 19, 20  
Wire error boundaries, establish the styling system, add the notification store.

### Phase 12 — Frontend quality and application features
**Contracts:** `Frontend_architecture/` 17, 18, 21, 22  
Test harness, bundle baseline, real-time, file handling — add when the application requires them.

---

## Using these contracts with an AI assistant

### The core workflow

1. **Identify the task** — what are you building? A new backend command, a new agent tool, a new frontend feature?
2. **Find the relevant contracts** — use the navigation matrix in each set's README to identify which contracts apply.
3. **Give the AI the contracts** — paste or reference the relevant files. Do not give the AI all 89 contracts at once.
4. **Give the AI the existing code** — the contracts define the pattern; the existing code gives the AI the application context.
5. **Ask the AI to flag gaps** — before writing code, ask it to identify anything the contracts do not specify for this task. Those gaps become new contracts, not silent decisions.

### Which contracts to give for common tasks

| Task | Contracts to provide |
|---|---|
| Add a new backend command | `Backend/06_commands.md`, `Backend/04_context.md`, `Backend/05_errors.md` |
| Add a new backend query | `Backend/07_queries.md`, `Backend/04_context.md` |
| Add a new HTTP endpoint | `Backend/09_routers.md`, `Backend/06_commands.md` or `Backend/07_queries.md` |
| Add a new database model | `Backend/03_models.md`, `Backend/24_multi_tenancy.md`, `Backend/25_soft_delete.md` |
| Add a new agent tool | `AI/02_tool_contract.md`, `AI/03_agent_identity.md`, `Backend/06_commands.md` |
| Build a new single agent | `AI/09_single_agent.md`, `AI/02_tool_contract.md`, `AI/19_clarification_protocol.md` |
| Build an orchestrator | `AI/10_orchestrator.md`, `AI/11_subagent.md`, `AI/19_clarification_protocol.md` |
| Add file/image support (AI) | `AI/30_multimodal.md`, `Backend/34_file_storage.md` |
| Add streaming to an agent | `AI/20_streaming.md`, `AI/09_single_agent.md` |
| Add a background agent task | `AI/22_background_tasks.md`, `AI/20_streaming.md` |
| Build a new frontend feature | `FE/15_feature_structure.md`, `FE/16_feature_workflow.md`, `FE/02_types.md`, `FE/04_api_client.md`, `FE/05_server_state.md` |
| Build a frontend form | `FE/09_forms.md`, `FE/02_types.md`, `FE/05_server_state.md` |
| Add a new frontend page/route | `FE/10_pages.md`, `FE/11_routing.md`, `FE/13_errors.md` |
| Add frontend auth | `FE/12_auth.md`, `FE/04_api_client.md`, `FE/11_routing.md` |
| Build a reusable UI component | `FE/07_components.md`, `FE/14_styling.md` |
| Add file upload to the frontend | `FE/22_file_handling.md`, `FE/09_forms.md` |
| Add real-time updates | `FE/21_realtime.md`, `FE/05_server_state.md` |
| Set up the intent router | `AI/26_router_agent.md`, `AI/27_planning_orchestrator.md`, `AI/28_conversation_session.md` |
| Add a new MCP tool | `AI/05_mcp_tools.md`, `AI/02_tool_contract.md`, `AI/08_mcp_auth.md` |

### How to write an effective prompt

**Include the seam.** For any AI task, always include `AI/02_tool_contract.md` and `Backend/04_context.md`. The seam is where most integration bugs originate.

**State the non-negotiables.** Start your prompt with: *"Follow the contracts provided. Do not deviate from the tool contract, service layer seam, or error handling patterns. Flag anything the contracts do not specify before writing code."*

**Give one task at a time.** One agent, one tool, one command per conversation. The contracts are precise enough that the AI will produce complete, correct code for a bounded task. Broad prompts produce code that partially follows multiple contracts and fully follows none.

**Review against the contracts.** After the AI writes code, verify it against the relevant contracts yourself. The non-negotiable rules (below) are your checklist.

**Example prompt structure:**
```
I am building [application name] following the contract architecture in these files:
[paste relevant contracts]

Here is the existing code context:
[paste relevant existing files]

Task: Build [specific thing].

Before writing code:
- Identify any gaps in the contracts for this specific task
- Confirm which existing backend commands/queries this tool will call

Then implement following the contracts exactly.
```

---

## The most important non-negotiable rules

These rules appear in the individual contract READMEs. They are repeated here because they are the ones most commonly violated when building with AI assistants.

**Backend rules:**
1. All business logic lives in commands and queries — never in routers or models.
2. Every command and query receives a `ServiceContext` — no anonymous operations.
3. Every database operation is within a workspace scope — multi-tenancy is enforced at the query level, not the application level.
4. Errors are typed `DomainError` subclasses — never raise raw exceptions from the service layer.

**AI layer rules:**
1. Tools own zero business logic — validate input, build `ServiceContext`, call one command or query, return the result.
2. The backend has no imports from the AI layer — the dependency arrow always points downward.
3. `AgentRunner.run()` always returns `AgentResult` — callers branch on `result.status`.
4. Research before escalating — agents must attempt to resolve uncertainty with their own tools before calling `ask_clarification`.
5. Clarification travels upward, never sideways — no peer-to-peer agent communication.
6. The LLM never sees a file URL or storage path — only `file_id`, resolved through tools.
7. All tool results are wrapped in structural delimiters before entering the message history — primary defense against prompt injection.
8. Provider calls always go through `ResilientProvider` in production — retry, circuit breaking, and fallback are infrastructure concerns, not agent concerns.

---

## When you find a gap

If a task requires a decision that no contract covers:

1. Make the decision.
2. Write it as a new contract in the appropriate set.
3. Add it to the relevant `README.md` index and navigation matrix.
4. Only then write the code.

Undocumented decisions become invisible architecture. Contracts that exist but are not followed are worse than no contracts — they create false confidence. If a contract needs to change, change the contract first, then the code.

---

## Extending the contracts

Each contract follows this structure:

```markdown
# [Number] — [Title]

## [Section name]
[Purpose / problem statement]

---

## [Section name]
[Code or specification]

---

## What [thing] must NOT do
[Explicit prohibitions — as important as the positive rules]
```

New contracts are numbered sequentially. Do not renumber existing contracts — cross-references throughout the set depend on stable numbers.

When adding a contract:
- Add it to the relevant set's `README.md` table and navigation matrix.
- Add non-negotiable rules that belong in the set's README.
- Cross-reference it from any existing contracts it extends or depends on.

---

## Quick reference

| I want to... | Read first |
|---|---|
| Understand the overall backend architecture | [`Backend_architecture/README.md`](Backend_architecture/README.md) |
| Understand the overall AI architecture | [`AI_Architecture/README.md`](AI_Architecture/README.md) |
| Find where the backend and AI layer connect | [`Backend_architecture/04_context.md`](Backend_architecture/04_context.md) + [`AI_Architecture/02_tool_contract.md`](AI_Architecture/02_tool_contract.md) |
| Know what to build first | Build order section above |
| Know which contracts to give an AI for a task | Contract-to-task table above |
| Add a new contract | Extending the contracts section above |
