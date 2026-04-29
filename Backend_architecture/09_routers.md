# 09 — Router Contract

## What routers own

A router handler does exactly these steps, in this order:

1. Extract identity from the JWT (`get_jwt()`)
2. Extract the request payload (`request.get_json()` or `request.args`)
3. Build a `ServiceContext`
4. Call `run_service(command_or_query, ctx)`
5. Check `outcome.success` and return the appropriate HTTP response

That is the entire job. No business logic. No ORM calls. No domain decisions.

---

## File structure

One Blueprint per domain resource:

```
routers/
├── http/
│   └── response.py          # build_ok(), build_err()
├── utils/
│   ├── jwt_handler.py       # JWTManager instance
│   ├── role_decorator.py    # @role_required, @app_scope_required
│   ├── compress_request.py
│   └── decompress_request.py
└── api_v1/
    ├── __init__.py           # register_v1_blueprints(app)
    ├── record.py
    ├── category.py
    └── auth.py
```

---

## Blueprint registration

```python
# routers/api_v1/__init__.py
from flask import Flask
from .record import record_bp
from .category import category_bp
from .auth import auth_bp


def register_v1_blueprints(app: Flask) -> None:
    app.register_blueprint(record_bp, url_prefix="/api/v1/records")
    app.register_blueprint(category_bp, url_prefix="/api/v1/categories")
    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
```

All blueprints are registered in one place. The app factory calls `register_v1_blueprints(app)`.

---

## Standard route handler

```python
# routers/api_v1/record.py
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt

from my_app.routers.http.response import build_ok, build_err
from my_app.routers.utils.role_decorator import role_required, ADMIN, MEMBER
from my_app.services.context import ServiceContext
from my_app.services.run_service import run_service
from my_app.services.commands.record.create_record import create_record
from my_app.services.queries.record.list_records import list_records

record_bp = Blueprint("api_v1_record", __name__)


@record_bp.route("/", methods=["GET"])
@jwt_required()
@role_required([ADMIN, MEMBER])
def list_records_route():
    ctx = ServiceContext(
        query_params=_normalize_query_params(),
        identity=get_jwt(),
    )
    outcome = run_service(list_records, ctx)

    if not outcome.success:
        return build_err(outcome.error)

    return build_ok(outcome.data, warnings=ctx.warnings)


@record_bp.route("/", methods=["PUT"])
@jwt_required()
@role_required([ADMIN, MEMBER])
def create_record_route():
    ctx = ServiceContext(
        incoming_data=request.get_json(silent=True) or {},
        identity=get_jwt(),
    )
    outcome = run_service(create_record, ctx)

    if not outcome.success:
        return build_err(outcome.error)

    return build_ok(outcome.data, warnings=ctx.warnings)
```

## Path parameter routes

URL path parameters are **merged into `ctx.incoming_data`** before calling `run_service`. This is the only exception to "incoming_data comes from the request body" — path params are logically part of the command or query input and are owned by the service layer, not the router.

```python
@record_bp.route("/<string:record_client_id>", methods=["GET"])
@jwt_required()
@role_required([ADMIN, MEMBER])
def get_record_route(record_client_id: str):
    ctx = ServiceContext(
        incoming_data={"client_id": record_client_id},
        identity=get_jwt(),
    )
    outcome = run_service(get_record, ctx)

    if not outcome.success:
        return build_err(outcome.error)

    return build_ok(outcome.data, warnings=ctx.warnings)


@record_bp.route("/<string:record_client_id>", methods=["DELETE"])
@jwt_required()
@role_required([ADMIN])
def delete_record_route(record_client_id: str):
    ctx = ServiceContext(
        incoming_data={"client_id": record_client_id},
        identity=get_jwt(),
    )
    outcome = run_service(delete_record, ctx)

    if not outcome.success:
        return build_err(outcome.error)

    return build_ok(outcome.data, warnings=ctx.warnings)
```

When a route has both a path parameter and a JSON body (e.g., PATCH with an ID), merge them:

```python
@record_bp.route("/<string:record_client_id>", methods=["PATCH"])
@jwt_required()
@role_required([ADMIN, MEMBER])
def update_record_route(record_client_id: str):
    body = request.get_json(silent=True) or {}
    ctx = ServiceContext(
        incoming_data={"client_id": record_client_id, **body},
        identity=get_jwt(),
    )
    outcome = run_service(update_record, ctx)

    if not outcome.success:
        return build_err(outcome.error)

    return build_ok(outcome.data, warnings=ctx.warnings)
```

The command reads `client_id` from `ctx.incoming_data` via its request parser, exactly as it reads any other field.

---

## Rules
- `run_service(fn, ctx)` — pass the function directly, never `lambda c: fn(c)`.
- `build_ok` and `build_err` are module-level functions from `routers/http/response.py`, not a class instantiated per request.
- No business data is extracted or modified in the router. `incoming_data` goes directly into the context untouched.
- Do not pop flags out of `incoming_data` in the router (e.g., `incoming_data.pop("prevent_event_bus")`). The command is responsible for interpreting its own input.
- URL path parameters are the only data the router merges into `incoming_data`. All other data comes from the request body (`request.get_json()`) or query string (`request.args`).

---

## Response builder

```python
# routers/http/response.py
from flask import jsonify

from my_app.errors import DomainError, NotFound, PermissionDenied, ValidationFailed, Conflict

_STATUS_MAP: dict[type[DomainError], int] = {
    NotFound: 404,
    PermissionDenied: 403,
    ValidationFailed: 400,
    Conflict: 409,
    DomainError: 500,
}


def build_ok(payload: object, warnings: list[str] | None = None, status: int = 200):
    return jsonify({
        "data": payload,
        "warnings": warnings or [],
    }), status


def build_err(error: DomainError):
    http_status = _STATUS_MAP.get(type(error), _STATUS_MAP[DomainError])
    return jsonify({
        "error": error.message,
        "code": error.code,
    }), http_status
```

Two functions, not a class. `build_ok` and `build_err` are called directly from route handlers.

---

## Query param normalization

Some clients send array params as `key[]`. Normalize them in a shared utility:

```python
def _normalize_query_params() -> dict:
    normalized = {}
    for raw_key, values in request.args.lists():
        key = raw_key.removesuffix("[]")
        if values:
            normalized[key] = values if len(values) > 1 else values[0]
    return normalized
```

This is a router-level concern. The service receives a plain dict.

---

## HTTP method conventions

| Intent | Method | URL pattern |
|---|---|---|
| List resources | GET | `/api/v1/records/` |
| Get one resource | GET | `/api/v1/records/<int:id>` |
| Create | PUT | `/api/v1/records/` |
| Update (partial) | PATCH | `/api/v1/records/` |
| Delete | DELETE | `/api/v1/records/` |
| Sub-resource action | PATCH | `/api/v1/records/<int:id>/archive` |

Use REST resource naming. Actions that do not fit CRUD go as sub-resource endpoints with a descriptive path segment (`/archive`, `/state/<int:state_id>`).

---

## Decorators on every protected route

All non-public endpoints must have both decorators in this order:

```python
@record_bp.route("/", methods=["GET"])
@jwt_required()
@role_required([ADMIN, MEMBER])
def handler():
    ...
```

`@jwt_required()` from Flask-JWT-Extended must appear before `@role_required`. This ordering is enforced because `@role_required` calls `verify_jwt_in_request` as a safety net but depends on JWT being already validated.

Public endpoints (webhooks, health check, client-facing forms) use dedicated scope guards instead.

---

## What the router must NOT do

- Call `db.session` directly
- Call domain functions directly
- Inspect the payload for business flags (`if "prevent_event_bus" in data`)
- Return different shapes based on business conditions
- Instantiate ORM models
- Perform data transformations beyond query param normalization
