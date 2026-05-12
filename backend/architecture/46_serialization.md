# 46 — Serialization Standard

## Overview

Foundation services produce typed dataclass instances. Serialization — the decision of which fields to include and in what shape — is a presentation concern owned by the **router layer**, not the service layer.

A domain-level serializer module contains named functions that convert result dataclass instances into dicts. Routers import these functions explicitly and call them based on context.

---

## Layer responsibilities

```
Foundation service   →  queries / mutates DB, returns typed dataclass instance(s)
Serializer module    →  converts dataclass instance → dict for a named view
Router               →  calls service, picks serializer view, builds HTTP response
Bootstrap handler    →  calls multiple services, assembles composite / sideloaded response
```

Services never call serializer functions. Serializers never touch the DB.

---

## Serializer module — `domain/<domain>/serializers.py`

One module per domain. Functions are plain callables — no class hierarchy, no registration.

Naming convention: `serialize_<resource>_<view>(r: ResultType) -> dict`

Standard views:

| View | When to use | Relationships |
|---|---|---|
| `compact` | Lists, search results, sideloaded references | Omitted |
| `full` | Detail endpoints, command responses | Embedded |
| `flat` | Bootstrap / sideloaded responses | Reference IDs only |

`asdict(r)` from `dataclasses` provides the base `full` serialization at zero cost.

### Cross-domain user result types — `domain/users/results.py`

Three user result types cover the three distinct serialization contexts.

**`UserCreatedByResult`** — minimal `created_by` for tables that only need identity (who created the record, nothing else):

```python
@dataclass
class UserCreatedByResult:
    client_id: str
    username:  str
```

Use this when the service doesn't need presence state or role context. Build it with a simple `User` row — no extra joins.

**`UserCompactResult`** — `created_by` with presence state and role context (messages, real-time features):

```python
@dataclass
class UserCompactResult:
    client_id:           str
    username:            str
    workspace_role_name: str | None  # WorkspaceMembership → WorkspaceRole.name; None when not loaded
    online:              bool
    last_online:         str | None
    app_viewing:         str | None
```

**`UserLoginResult`** — returned as the `user` payload on login / workspace switch:

```python
@dataclass
class UserLoginResult:
    client_id:           str
    email:               str
    username:            str
    role:                str   # workspace_role.name — display name for the active workspace
    backend_permissions: list
    ui:                  dict
```

**`UserProfileResult`** — base user profile view (apps add columns as needed):

```python
@dataclass
class UserProfileResult:
    client_id:  str
    email:      str
    username:   str
    created_at: str
```

`domain/users/serializers.py` exports:
- `serialize_user_created_by(r: UserCreatedByResult) -> dict` — identity only
- `serialize_user_compact(r: UserCompactResult) -> dict` — identity + presence + role
- `serialize_user_login(r: UserLoginResult) -> dict`
- `serialize_user_profile_full(r: UserProfileResult) -> dict`

---

### Example — `domain/cases/serializers.py`

```python
from dataclasses import asdict
from my_app.domain.cases.results import (
    CaseResult, CaseLinkResult, CaseParticipantResult,
    CaseConversationResult, CaseConversationMessageResult,
)
from my_app.domain.users.serializers import serialize_user_compact


def serialize_case_compact(r: CaseResult) -> dict:
    return {
        "client_id":           r.client_id,
        "state":               r.state,
        "type_label":          r.type_label,
        "conversations_count": r.conversations_count,
        "messages_count":      r.messages_count,
        "created_at":          r.created_at,
    }

def serialize_case_full(r: CaseResult) -> dict:
    return asdict(r)


def serialize_conversation_compact(r: CaseConversationResult) -> dict:
    return {
        "client_id":      r.client_id,
        "state":          r.state,
        "messages_count": r.messages_count,
        "created_at":     r.created_at,
    }

def serialize_conversation_full(r: CaseConversationResult) -> dict:
    return {
        "client_id":        r.client_id,
        "state":            r.state,
        "messages_count":   r.messages_count,
        "last_message_seq": r.last_message_seq,
        "created_at":       r.created_at,
        "last_messages":    [serialize_message_full(m) for m in r.last_messages],
    }


def serialize_message_compact(r: CaseConversationMessageResult) -> dict:
    return {
        "client_id":        r.client_id,
        "message_seq":      r.message_seq,
        "plain_text":       r.plain_text if not r.has_been_deleted else "",
        "has_been_deleted": r.has_been_deleted,
        "created_at":       r.created_at,
        "created_by":       serialize_user_compact(r.created_by) if r.created_by else None,
    }

def serialize_message_full(r: CaseConversationMessageResult) -> dict:
    return {
        "client_id":        r.client_id,
        "message_seq":      r.message_seq,
        "content":          None if r.has_been_deleted else r.content,
        "plain_text":       r.plain_text if not r.has_been_deleted else "",
        "has_been_edited":  r.has_been_edited,
        "has_been_deleted": r.has_been_deleted,
        "created_at":       r.created_at,
        "created_by":       serialize_user_compact(r.created_by) if r.created_by else None,
    }
```

---

## Service return types

Services return **dataclass instances**, never serialized dicts.

```python
# correct
def get_case(ctx) -> CaseResult:
    ...
    return CaseResult(
        client_id            = case.client_id,
        state                = case.state.value,
        type_label           = case.type_label,
        participants_count   = case.participants_count,
        conversations_count  = case.conversations_count,
        messages_count       = case.messages_count,
        created_at           = case.created_at.isoformat(),
        created_by_id        = case.created_by_id,
    )

# wrong — serialization belongs in the router
def get_case(ctx) -> dict:
    ...
    return {"client_id": case.client_id, ...}
```

List-returning services return a bare list of instances:

```python
def list_cases(ctx) -> list[CaseResult]:
    ...
    return [CaseResult(...) for c in cases]
```

### Exempt cases — return plain dict

Commands that produce no resource output return an empty dict:

```python
def soft_delete_message(ctx) -> dict:
    ...
    return {}
```

Computed results that have no natural resource shape also return a plain dict:

```python
def get_unread_counts(ctx) -> dict:
    ...
    return {"unread_counts": {row.client_id: max(0, row.unread_count) for row in rows}}
```

---

## Router pattern

```python
from my_app.domain.cases.serializers import (
    serialize_case_compact,
    serialize_case_full,
    serialize_message_full,
    serialize_conversation_full,
)

# Detail endpoint → full
@case_bp.route("/<case_client_id>", methods=["GET"])
@jwt_required()
def get_case_route(case_client_id: str):
    outcome = run_service(get_case, _ctx({"case_client_id": case_client_id}))
    return build_ok(serialize_case_full(outcome.data)) if outcome.success else build_err(outcome.error)

# List endpoint → compact
@case_bp.route("", methods=["GET"])
@jwt_required()
def list_cases_route():
    outcome = run_service(list_cases, _ctx(request.args.to_dict()))
    return build_ok({"cases": [serialize_case_compact(r) for r in outcome.data]}) if outcome.success else build_err(outcome.error)

# Conversation detail — adjacent image pattern
@case_bp.route("/conversations/<conversation_client_id>", methods=["GET"])
@jwt_required()
def get_conversation_route(conversation_client_id: str):
    outcome = run_service(get_conversation, _ctx({"conversation_client_id": conversation_client_id}))
    if outcome.success:
        return build_ok({"conversation": serialize_conversation_full(outcome.data), "images": []})
    return build_err(outcome.error)

# Message list — adjacent image pattern; list_messages returns {"messages": [...], "images": [...]}
@case_bp.route("/conversations/<conversation_client_id>/messages", methods=["GET"])
@jwt_required()
def list_messages_route(conversation_client_id: str):
    data = request.args.to_dict()
    data["conversation_client_id"] = conversation_client_id
    outcome = run_service(list_messages, _ctx(data))
    if outcome.success:
        return build_ok({
            "messages": [serialize_message_full(r) for r in outcome.data["messages"]],
            "images":   outcome.data.get("images", []),
        })
    return build_err(outcome.error)
```

### Adjacent image pattern

Images are **never embedded** in message or case objects. They are returned as a sideloaded `images` array at the top level of the response, each image entry carrying `entity_client_id` to link back to its owner:

```json
{
  "messages": [...],
  "images": [
    {
      "entity_client_id": "ccm_...",
      "link_client_id":   "ilnk_...",
      "display_order":    0,
      "image":            { ... }
    }
  ]
}
```

The frontend holds a `Map<entity_client_id, images[]>` and resolves images per-message on render — zero additional round-trips. Upload and delete remain decoupled from the entity that owns the image.

---

## Bootstrap / sideloaded responses

Bootstrap handlers call multiple services and assemble the composite response. Each section independently chooses its serializer view.

```python
# handlers/bootstrap/case_page.py
def case_page(ctx):
    case         = run_service(get_case,          ctx).data
    participants = run_service(list_participants,  ctx).data
    messages     = run_service(list_messages,      ctx).data

    return build_ok({
        "case":         serialize_case_full(case),
        "participants": [serialize_participant_compact(p) for p in participants],
        "messages":     [serialize_message_full(m)       for m in messages],
    })
```

### Sideloaded / flat response

When related resources are returned as parallel top-level arrays, parent records use the `flat` view (reference IDs only) and children use `compact`:

```python
return build_ok({
    "tasks":      [serialize_task_flat(t)      for t in tasks],
    "task_steps": [serialize_step_compact(s)   for s in steps],
})
```

The `flat` serializer returns only reference IDs for relationships — no nesting:

```python
def serialize_task_flat(r: TaskResult) -> dict:
    return {
        "client_id": r.client_id,
        "title":     r.title,
        "step_ids":  [s.client_id for s in r.steps],
    }
```

---

## File structure

```
my_app/
└── domain/
    └── <domain>/
        ├── results.py      # dataclass result types — data shape
        └── serializers.py  # named serializer functions — presentation views
```

---

## Rules

- **Services return dataclass instances, never dicts for resource types.** The router decides what to include and in what format.
- **Serializers are plain functions, not classes.** No inheritance, no registry, no metaclass magic.
- **`asdict()` provides `full` serialization at no cost.** Only define a `full` function when you need to exclude fields or transform nested types.
- **Routers import serializers explicitly.** No dynamic dispatch on view name — the import is the documentation.
- **Empty acks and computed dicts are exempt.** If a service produces no natural resource, returning `{}` or a plain computed dict is correct.
- **`compact` omits relationships entirely.** Use it wherever the client does not need to traverse to a related resource in the same response.
- **`flat` returns reference IDs only for relationships.** Used in sideloaded responses so the client can reconstruct the graph locally without nesting.
- **Datetime fields are serialized to ISO 8601 strings in the service** (`created_at.isoformat()`). Serializer functions treat them as strings.
- **User compact is always `serialize_user_compact(r.created_by) if r.created_by else None`.** Services that return messages or images JOIN User and populate `created_by` as a `UserCompactResult`. `workspace_role_name` is `None` unless the service also loads the membership for the active workspace.
- **Deleted message content is nulled by the serializer, not the service.** Services set `content` to `None` only when `has_been_deleted` is True before returning. Serializers also guard: `content = None if r.has_been_deleted else r.content`. Same for `plain_text`.
- **Images are never embedded in message objects.** Use the adjacent image pattern — services return `{"messages": [...], "images": [...]}` and routers pass through both arrays unchanged.
