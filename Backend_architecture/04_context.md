# 04 — ServiceContext Contract

## What ServiceContext is

`ServiceContext` is the typed request envelope that travels from the router into every command and query. It carries **two things only**:

1. **Identity** — who is making this request (JWT claims: `user_id`, `workspace_id`, `base_role_id`, `app_scope`, etc.)
2. **Incoming data** — the raw payload or query params from the HTTP request

That is its full scope. It is not a configuration object.

For complex write operations that need to track touched entities, pending events, or cascading response data, use a command-local `WorkContext` instead. See [39_work_context.md](39_work_context.md).

---

## Canonical definition

```python
# services/context.py
from __future__ import annotations


class ServiceContext:

    def __init__(
        self,
        incoming_data: dict | None = None,
        incoming_file: object | None = None,
        query_params: dict | None = None,
        identity: dict | None = None,
    ) -> None:
        self.incoming_data: dict = incoming_data or {}
        self.incoming_file = incoming_file
        self.query_params: dict = query_params or {}
        self._identity: dict = identity or {}
        self.warnings: list[str] = []

    # --- Identity convenience properties ---

    @property
    def workspace_id(self) -> int:
        """The active workspace for this session. All domain queries must filter by this."""
        return self._identity["workspace_id"]

    @property
    def user_id(self) -> int:
        return self._identity["user_id"]

    @property
    def workspace_role_id(self) -> int:
        return self._identity["workspace_role_id"]

    @property
    def base_role_id(self) -> int:
        """Permission tier: 1=ADMIN, 2=MEMBER, 3=DRIVER. For fast role checks in commands."""
        return self._identity["base_role_id"]

    @property
    def app_scope(self) -> str:
        """Surface the session is scoped to: 'admin' | 'driver' | 'client'."""
        return self._identity["app_scope"]

    @property
    def time_zone(self) -> str:
        return self._identity.get("time_zone", "UTC")

    # --- Permission checks (read from JWT — zero DB lookups) ---

    def has_permission(self, permission: "Permission") -> bool:
        """Non-raising check. Use for conditional logic, not access control."""
        return permission.value in self._identity.get("permissions", [])

    def require_permission(self, permission: "Permission") -> None:
        """Raises PermissionDenied if the caller lacks the given permission.
        ADMIN tier (base_role_id=1) bypasses this check — ADMINs are never locked out.
        Call this at the top of every write command before any other work.
        """
        from my_app.errors import PermissionDenied
        if self._identity.get("base_role_id") == 1:
            return
        if not self.has_permission(permission):
            raise PermissionDenied(
                f"Your role does not have the '{permission.value}' permission."
            )

    # --- Warning accumulator (non-fatal issues the router surfaces to the client) ---

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)
```

**Property notes:**
- `workspace_id` does **not** use `.get()` with a fallback — it raises `KeyError` if missing, because a session without a workspace is a misconfigured token and should fail immediately.
- All identity fields are read-only properties. Commands never mutate `ctx._identity`.
- `require_permission` and `has_permission` read only from `self._identity["permissions"]` — the list embedded in the JWT at login time. No database access.
- Legacy code may use `ctx.team_id` as an alias for `ctx.workspace_id`. In new applications, use `ctx.workspace_id` exclusively.

See [24_multi_tenancy.md](24_multi_tenancy.md) for the workspace architecture and [28_roles_permissions.md](28_roles_permissions.md) for the full permission system.

---

## What ServiceContext must NOT contain

| Anti-pattern | Why it is wrong | Correct approach |
|---|---|---|
| `prevent_event_bus: bool` | Business configuration leaking into transport | Command decides its own event behavior based on its inputs |
| `on_create_return: str` | Serialization strategy as a flag | Commands return explicit typed dicts; serializers are not configurable at the router |
| `check_workspace_id: bool` | Authorization policy as a flag | Commands and queries always enforce workspace scope — never conditionally |
| `inject_workspace_id: bool` | ORM mutation policy as a flag | Commands assign `workspace_id=ctx.workspace_id` explicitly — no injection helpers |
| `allow_is_system_modification: bool` | Privilege escalation as a flag | System-level modification is a separate command with its own authorization path |
| `relationship_map: dict` | ORM linkage as runtime config | Relationship resolution is explicit inside each command |
| `touched_entities: dict` | Operation-local mutation tracking | Use `WorkContext` inside complex commands |
| `pending_events: list` | Command side-effect tracking | Use `WorkContext.events`, then emit after commit |
| `ai_operation: str` | AI-specific scope leaking into the general context | AI tool invocations carry their own typed input, not a flag on ServiceContext |
| `current_workspace: str` | Workspace-switching state as context | The JWT *is* the workspace session — switch by issuing a new token |

**The rule:** If a command needs to behave differently based on a flag, it is two commands. If the flag is set at the router, it is business logic at the wrong layer.

---

## How to use ServiceContext in commands

Commands receive `ctx: ServiceContext` and extract what they need. They do not inspect flags; they read data:

```python
# services/commands/<domain>/create_record.py

def create_record(ctx: ServiceContext) -> dict:
    request = parse_create_record_request(ctx.incoming_data)

    with db.session.begin():
        record = Record(
            workspace_id=ctx.workspace_id,   # always from ctx, never from request body
            client_id=generate_client_id(),
            ...
        )
        db.session.add(record)
    ...
```

---

## How to use ServiceContext in queries

```python
# services/queries/<domain>/list_records.py

def list_records(ctx: ServiceContext) -> dict:
    query = (
        db.session.query(Record)
        .filter(Record.workspace_id == ctx.workspace_id)   # mandatory first filter
        ...
    )
    # ... paginate, serialize
```

---

## Warnings

Warnings are non-fatal notices surfaced to the API client in the response body. Use them for degraded-but-successful outcomes:

```python
ctx.add_warning("Geocoding failed for address — order saved without coordinates.")
```

Do not use warnings for errors. If the operation cannot succeed, raise a `DomainError` subclass.
