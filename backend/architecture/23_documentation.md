# 23 — Documentation Architecture Contract

## The problem this solves

Documentation in most codebases degrades into two useless extremes:
- **Too sparse** — a 4-line README that says "it's a FastAPI app"
- **Too historical** — a graveyard of implementation plans that describe what was built six months ago, not what exists now

Neither is useful to an agent or an engineer picking up the codebase cold. What they need is a **living map** — a document that tells you what the application does *right now*, what shapes it uses, what rules it enforces, and where to find things.

This contract defines that map and the discipline to keep it accurate.

---

## Core principle: History vs Truth

Every document in `docs/` belongs to exactly one category:

| Category | Purpose | Updated? | Location |
|---|---|---|---|
| **Living** | Current truth — what the app does now | Yes — with every relevant code change | `docs/domains/`, `docs/integrations/`, `docs/architecture/` |
| **Decision** | Why something was built the way it was | Append-only — never modified | `docs/decisions/` |
| **Runbook** | How to operate or develop the system | Yes — when the process changes | `docs/runbooks/` |
| **Archive** | Historical plans and handoffs | Never — frozen in time | `docs/archive/` |

The living documents are the application's contract with anyone reading the code. They must be accurate on every branch that is merged to main.

---

## `docs/` folder structure

```
docs/
├── README.md                           # Application map — the first thing anyone reads
│
├── domains/                            # One folder per business domain — LIVING
│   ├── <domain>/
│   │   ├── README.md                   # What this domain is, its entities, its rules
│   │   ├── api.md                      # Every endpoint: method, URL, auth, shapes
│   │   ├── events.md                   # Every event this domain emits
│   │   └── states.md                   # State machines (if applicable)
│   └── auth/
│       ├── README.md
│       └── api.md
│
├── integrations/                       # Third-party systems — LIVING
│   ├── README.md                       # Integration map (which systems, what purpose)
│   └── <provider>.md
│
├── ai_operator/                        # AI Operator capability catalog — LIVING
│   ├── README.md                       # Capabilities overview, routing logic
│   └── tools.md                        # Every registered tool: name, params, returns
│
├── architecture/                       # System-level design — LIVING
│   └── overview.md                     # Stack, layers, system diagram, runtime processes
│
├── decisions/                          # Architecture Decision Records — APPEND-ONLY
│   └── 001_fastapi_chosen_as_framework.md
│
├── runbooks/                           # Operational procedures — LIVING
│   ├── development-quickstart.md
│   ├── deployment.md
│   └── migrations.md
│
└── archive/                            # Historical — NEVER MODIFIED
    ├── handoffs/
    └── implementation-plans/
```

---

## `docs/README.md` — The application map

This is the **single entry point** for any agent or engineer new to the codebase. It must answer four questions in under five minutes of reading:

1. What does this application do?
2. What are its domains and what does each one own?
3. What external systems does it depend on?
4. Where do I go to understand a specific part?

### Template

```markdown
# {App Name} — Backend

{One paragraph: what this application does, who uses it, and what problem it solves.}

---

## Domain map

| Domain | Owns | Key entities |
|---|---|---|
| **{Domain A}** | {Responsibility} | `{Entity1}`, `{Entity2}` |
| **{Domain B}** | {Responsibility} | `{Entity3}`, `{Entity4}` |
| **Auth** | Authentication, JWT, membership-based role access | `User`, `WorkspaceMembership`, `WorkspaceRole`, `Role` |
| **Notifications** | Real-time and push notifications | `NotificationRead`, `PushSubscription` |

---

## Integrations

| System | Purpose | Docs |
|---|---|---|
| {Provider A} | {Purpose} | [docs/integrations/{provider_a}.md](integrations/{provider_a}.md) |

---

## System runtime

| Process | Entry point | Role |
|---|---|---|
| Web server | `application.py` | HTTP API + Socket.IO |
| Default worker | `redis_worker_default.py` | DB-bound async tasks |
| IO worker | `redis_worker_io.py` | External HTTP calls |
| Dispatcher | `redis_dispatcher.py` | Domain event dispatch from outbox |
| Scheduler | `redis_scheduler.py` | Cron recurring jobs |

---

## Quick navigation

- Architecture: [docs/architecture/overview.md](architecture/overview.md)
- Domain docs: [docs/domains/](domains/)
- AI Operator tools: [docs/ai_operator/tools.md](ai_operator/tools.md)
- How to run locally: [docs/runbooks/development-quickstart.md](runbooks/development-quickstart.md)
- Engineering contract: [standar_contract/README.md](../standar_contract/README.md)
```

---

## `docs/domains/<domain>/README.md` — Domain overview

Every domain has a README that describes what it owns, its core entities, and its invariants. This is the first file an agent reads before touching that domain.

### Template

```markdown
# Domain: {Domain Name}

## Responsibility

{One sentence. What this domain owns and nothing else.}

---

## Entities

### {EntityName}

| Field | Type | Description |
|---|---|---|
| `client_id` | `string (prefixed ULID)` | Primary key and stable public identifier |
| `workspace_id` | `string (prefixed ULID)` | Owning workspace client_id |
| `state_id` | `string (prefixed ULID)` | Current state client_id (see States) |
| `created_at` | `datetime (UTC)` | Creation timestamp |

---

## Business rules

- {Rule 1 in plain language.}
- {Rule 2.}
- {Rule 3.}

---

## Relationships to other domains

| Domain | Relationship |
|---|---|
| {Domain B} | {Relationship description} |

---

## Files in this domain

| Layer | Location | Responsibility |
|---|---|---|
| Router | `routers/api_v1/<domain>.py` | HTTP endpoints |
| Commands | `services/commands/<domain>/` | Write operations |
| Queries | `services/queries/<domain>/` | Read operations |
| Domain | `domain/<domain>/` | Guards, state machines, calculations |
| Models | `models/tables/<domain>/` | ORM table definitions |
```

---

## `docs/domains/<domain>/api.md` — Endpoint and shape catalog

This is the most important file for agents building features or integrations. It documents every endpoint with its exact request and response shapes using concrete JSON examples.

### Template

```markdown
# {Domain Name} API

Base path: `/api/v1/{resource}/`
Auth: All endpoints require `Authorization: Bearer <access_token>` unless noted.

---

## Endpoints

### GET `/api/v1/records/`
List records for the authenticated workspace.

**Auth:** `ADMIN`, `MEMBER`

**Query params:**

| Param | Type | Required | Description |
|---|---|---|---|
| `limit` | `int` | No | Max records (default: 50, max: 200) |
| `after_cursor` | `string` | No | Pagination cursor |
| `state_id` | `int` | No | Filter by state |

**Response shape:**

```json
{
  "data": {
    "records": [
      {
        "client_id": "rec_01ABC...",
        "name": "Example Record",
        "state_id": 1,
        "created_at": "2025-01-15T12:00:00Z"
      }
    ],
    "records_pagination": {
      "has_more": true,
      "after_cursor": "eyJjcmVhdGVkX2F0IjoiLi4uIiwiY2xpZW50X2lkIjoiLi4uIn0=",
      "before_cursor": null
    }
  },
  "warnings": []
}
```

---

### PUT `/api/v1/records/`
Create a record.

**Auth:** `ADMIN`, `MEMBER`

**Request shape:**

```json
{
  "name": "My Record",
  "category_id": 3
}
```

**Response shape:**

```json
{
  "data": {
    "record": {
      "client_id": "rec_01ABD...",
      "name": "My Record",
      "state_id": 1,
      "created_at": "2025-01-15T12:00:00Z"
    }
  },
  "warnings": []
}
```

---

### Error responses

All endpoints return errors in this shape:

```json
{
  "error": "Human-readable message safe to display.",
  "code": "not_found | bad_request | forbidden | conflict | internal_error"
}
```

| HTTP Status | Code | Meaning |
|---|---|---|
| 400 | `bad_request` | Validation failed |
| 403 | `forbidden` | Insufficient permissions |
| 404 | `not_found` | Resource does not exist |
| 409 | `conflict` | State or uniqueness conflict |
| 500 | `internal_error` | Unexpected server error |
```

---

## `docs/domains/<domain>/events.md` — Event catalog

Every event emitted by this domain, with its full payload shape.

### Template

```markdown
# {Domain Name} Events

Events emitted by this domain are consumed by event handlers in `services/infra/events/handlers/{domain}/`.

---

## `record.created`

Emitted when a record is committed to the database.

**Payload:**

```json
{
  "event_type": "record.created",
  "schema_version": 1,
  "workspace_id": 7,
  "payload": {
    "record_id": 124,
    "client_id": "abc-124-def",
    "name": "My Record"
  },
  "meta": {
    "triggered_by_user_id": 42,
    "timestamp": "2025-01-15T12:00:00Z"
  }
}
```

**Consumed by:**
- `record_notification.py` — sends creation notification

---

## `record.state_changed`

Emitted when a record transitions to a new state.

**Payload:**

```json
{
  "event_type": "record.state_changed",
  "schema_version": 1,
  "workspace_id": 7,
  "payload": {
    "record_id": 124,
    "client_id": "abc-124-def",
    "previous_state_id": 1,
    "new_state_id": 2
  },
  "meta": { "timestamp": "2025-01-15T13:00:00Z" }
}
```
```

---

## `docs/domains/<domain>/states.md` — State machine

Document every valid state and every valid transition. This is the ground truth for both the domain layer (`domain/<domain>/<resource>_states.py`) and the frontend.

### Template

```markdown
# {Domain Name} States

## States

| ID | Name | Terminal? | Description |
|---|---|---|---|
| 1 | `draft` | No | Record created, not yet active |
| 2 | `active` | No | Record is live |
| 3 | `closed` | Yes | Record completed successfully |
| 4 | `cancelled` | Yes | Record cancelled |

## Valid transitions

| From | To | Trigger |
|---|---|---|
| `draft` | `active` | User activates record |
| `draft` | `cancelled` | User cancels before activation |
| `active` | `closed` | Record reaches completion |
| `active` | `cancelled` | Admin cancels active record |

Terminal states have no outgoing transitions. Any attempt raises `ValidationFailed`.
```

---

## Maintenance discipline — what must be updated and when

This is the enforcement mechanism. Without it, docs drift. These rules are part of the definition of done for every code change.

| Code change | Required doc update |
|---|---|
| New endpoint added | Add to `domains/<domain>/api.md` with full request/response shapes |
| Endpoint response shape changed | Update the shape in `domains/<domain>/api.md` |
| Endpoint deprecated | Mark as deprecated in `api.md`, add `warnings` note |
| New domain event added | Add to `domains/<domain>/events.md` with full payload |
| Event payload shape changed | Update in `events.md` |
| New entity (model) added | Add entity table to `domains/<domain>/README.md` |
| New business rule added | Add to `domains/<domain>/README.md` rules list |
| State machine changed | Update `domains/<domain>/states.md` |
| New external integration added | Create `integrations/<name>.md`, add row to `docs/README.md` |
| New AI tool registered | Add to `ai_operator/tools.md` |
| New domain created | Create full `domains/<domain>/` folder with all 3–4 files, add row to `docs/README.md` |
| Process or worker added | Add row to runtime table in `docs/README.md` |

**A PR that changes any of the above without updating the corresponding doc is incomplete.**

---

## What goes in `docs/archive/`

Archive is for:
- Implementation plans that have been shipped
- Handoff documents from other teams
- Migration plans that have been executed
- Refactor context documents

Archive documents are never modified after being moved there. They are the historical record. Date-stamp the filename: `FEATURE_REFACTOR_2026-03-28.md`.

When a plan is executed, move it from the working area to `docs/archive/`. Do not delete — the archive is append-only.

---

## Architecture Decision Records (ADRs)

When a significant architectural decision is made (choosing a library, adopting a pattern, rejecting an approach), record it as an ADR in `docs/decisions/`:

```
docs/decisions/001_fastapi_chosen_as_framework.md
docs/decisions/002_cursor_pagination_over_offset.md
docs/decisions/003_rq_chosen_over_celery.md
```

### ADR template

```markdown
# {NNN} — {Short title of the decision}

**Date:** YYYY-MM-DD
**Status:** Accepted | Superseded by {NNN}

## Context

{What situation or problem forced this decision?}

## Decision

{What was decided?}

## Consequences

**Positive:** {What does this make easier?}
**Negative:** {What does this make harder or rule out?}
**Neutral:** {What changes but is neither better nor worse?}
```

ADRs are never deleted. If a decision is reversed, the old ADR is marked "Superseded by NNN" and a new ADR records the reversal.

---

## Rules for writing documentation

### Write for someone who has never seen the codebase

Every domain README, API doc, and event catalog must be understandable to:
- An AI agent starting a new session with zero prior context
- A backend engineer joining the team on day one
- A frontend engineer who needs to understand the API contract

If a reader needs to read the code to understand the doc, the doc has failed.

### Use concrete shapes, not abstract descriptions

```markdown
<!-- Wrong — abstract, useless -->
The create record endpoint accepts record data and returns the created record.

<!-- Correct — concrete, actionable -->
**Request:** `PUT /api/v1/records/`
Body: `{ "name": "...", "category_id": 3 }`
Response: `{ "data": { "record": { "client_id": "rec_...", ... } }, "warnings": [] }`
```

### One source of truth per fact

If the response shape for `GET /api/v1/records/` exists in `api.md`, it must not also be described (possibly inconsistently) in the domain README or in an external document. One place. Always current.

### Docs are code, not prose

Documentation files are committed to the repository in the same PR as the code change they document. They go through the same review. Outdated documentation is a bug.
