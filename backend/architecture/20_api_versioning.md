# 20 — API Versioning Contract

## Versioning strategy

APIs are versioned at the URL path level: `/api/v1/`, `/api/v2/`. This is the simplest, most debuggable approach — the version is visible in every log line, every client request, and every network trace.

---

## When to create a new version

A new version is required when a change is **not backwards compatible** for existing clients. Backwards incompatible means:

| Change type | Requires new version? |
|---|---|
| Removing a response field | Yes |
| Renaming a response field | Yes |
| Changing a field's type (`string` → `int`) | Yes |
| Making a previously optional request field required | Yes |
| Changing the meaning of an existing field | Yes |
| Adding a new optional response field | No |
| Adding a new optional request field | No |
| Adding a new endpoint | No |
| Changing HTTP status codes (2xx only) | Evaluate case by case |

If you are unsure, ask: "Would an existing client break without code changes?" If yes, it is a breaking change.

---

## How to introduce a new version

1. Create the new version's blueprint folder: `routers/api_v2/`
2. Add a registration function: `routers/api_v2/__init__.py → register_v2_blueprints(app)`
3. Register both versions in `create_app()`:

```python
from .routers.api_v1 import register_v1_blueprints
from .routers.api_v2 import register_v2_blueprints

register_v1_blueprints(app)
register_v2_blueprints(app)
```

4. The new version shares all underlying services. It has its own router and its own serializers. It does not duplicate commands or queries.

```
routers/
├── api_v1/
│   └── record.py          # v1 response shape
└── api_v2/
    └── record.py          # v2 response shape — new serializer, same command
```

The command `create_record` does not know which version called it. Only the serializer changes between versions.

---

## Backwards compatibility rules

Within a version, these rules are enforced forever:

**Never remove a field.** If a field is no longer useful, deprecate it (keep returning it, document it as deprecated).

**Never change a field's type.** If a field must change type, introduce a new field with a new name.

**Never change a field's semantics.** If `status: "active"` meant one thing and now means another, that is a breaking change regardless of the field name remaining the same.

**Add new fields freely.** Well-behaved clients ignore unknown fields. Adding a field is always safe.

---

## Deprecation process

Before removing anything in the current version:

1. Add a deprecation notice in the response body `warnings` array:

```python
ctx.add_warning("Field 'legacy_plan_type' is deprecated and will be removed in v3. Use 'plan_objective' instead.")
```

2. Document the deprecation in the API changelog.
3. Maintain the deprecated field for at least one full version (i.e., if deprecating in v2, remove in v3, not in v2.1).
4. Notify clients via the standard communication channel before removing.

---

## Internal vs external versioning

Routes that are only called by own frontends (admin app, driver app) have more flexibility because breaking changes can be coordinated. Routes that are called by third-party clients (webhooks, public API consumers) require the full deprecation process.

Tag endpoints at creation time:

```python
# routers/api_v2/order.py
# audience: internal — admin and driver apps only

# routers/api_v2/public/order_tracking.py
# audience: external — public tracking links, third-party integrations
```

---

## Blueprint URL prefix conventions

```python
app.register_blueprint(order_bp,         url_prefix="/api/v1/orders")
app.register_blueprint(plan_bp,          url_prefix="/api/v1/plans")
app.register_blueprint(auth_bp,          url_prefix="/api/v1/auth")
app.register_blueprint(webhooks_bp,      url_prefix="/webhooks")      # unversioned
app.register_blueprint(health_bp,        url_prefix="/")              # unversioned
```

Webhooks and the health check endpoint are unversioned. They follow their own evolution contract with the external provider.

---

## Sunset policy

A version is officially sunset when:
- All known clients have migrated to a newer version, and
- At least 90 days have passed since the migration was complete

On sunset:
- The blueprint is removed from `register_*_blueprints(app)`
- All routes return `410 Gone` for an additional 30-day grace period
- The blueprint folder is deleted from the repository

Never delete a version's code without the 30-day `410 Gone` grace period. Clients may have cached the old URL and not yet received a new deployment.
