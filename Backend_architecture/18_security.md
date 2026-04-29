# 18 — Security Contract

## Threat model

Every endpoint is public until proven otherwise. Every caller is untrusted until their JWT is verified. Every input is malicious until validated. These are not paranoid assumptions — they are the baseline.

---

## Input validation

### Rule 1 — Validate at the boundary, trust inside

Validation happens once, at the command's request parser. Once `parse_*_request()` returns a typed `Pydantic` model, downstream code trusts those fields without re-validating.

```python
# Correct — validate at entry, use typed fields everywhere after
def create_record(ctx: ServiceContext) -> dict:
    request = parse_create_record_request(ctx.incoming_data)
    # request.name is now a validated non-empty string
    # request.category_id is now a validated int | None
```

### Rule 2 — Never interpolate user input into strings used as code

SQL injection, shell injection, and template injection all come from the same mistake:

```python
# FATAL — SQL injection
query = f"SELECT * FROM records WHERE workspace_id = {ctx.workspace_id}"
db.session.execute(text(query))

# Correct — parameterized
db.session.execute(text("SELECT * FROM records WHERE workspace_id = :wid"), {"wid": ctx.workspace_id})

# Best — ORM query (no raw SQL needed)
db.session.query(Record).filter(Record.workspace_id == ctx.workspace_id)
```

Raw SQL strings (`text(...)`) are **forbidden** unless there is no ORM equivalent. If you must use `text()`, all parameters must be bound via the `params` argument — never f-strings or `.format()`.

### Rule 3 — Validate integer IDs from URLs

Path parameters from Flask routes are typed by Flask (`<int:record_id>`) but must still be validated against the caller's workspace scope:

```python
@record_bp.route("/<int:record_id>", methods=["GET"])
@jwt_required()
@role_required([ADMIN, MEMBER])
def get_record(record_id: int):
    # record_id is an int — but does it belong to this workspace?
    # The query/command enforces this — never skip it
    ...
```

Never trust that a URL ID belongs to the caller's workspace without an explicit DB check in the command or query.

---

## Output sanitization

### Never return sensitive fields in API responses

The following fields must never appear in any API response:

| Field | Why |
|---|---|
| `password` / `password_hash` | Credential exposure |
| JWT tokens in response bodies (except auth endpoints) | Session exposure |
| Raw SQLAlchemy error messages | Schema exposure |
| Internal integer auto-increment IDs without `client_id` | IDOR enumeration |
| Stack traces | Implementation exposure |
| Encryption keys, VAPID keys, API keys | Credential exposure |

Serializers explicitly list the fields they return. They are not auto-serializing ORM instances:

```python
# Correct — explicit whitelist
def serialize_user(user: User) -> dict:
    return {
        "client_id": user.client_id,
        "email": user.email,
        "role": user.base_role_id,
    }

# Wrong — exposes everything including password_hash
return user.__dict__
```

---

## CORS hardening

```python
# Correct
CORS(
    app,
    supports_credentials=True,
    resources={r"/*": {"origins": frontend_origins}},
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["Content-Encoding"],   # explicit — not "*"
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
)
```

`expose_headers="*"` is **forbidden** in production. Enumerate only the headers the client needs.

`frontend_origins` must never contain `"*"` in production. The wildcard permits requests from any origin — including attacker-controlled pages.

---

## Secrets management

### What goes where

| Location | Allowed | Forbidden |
|---|---|---|
| `.env` (local dev) | Dev credentials, local DB URI | Production secrets |
| Environment variables (production) | All secrets | Nothing hardcoded |
| Source code | Config keys (not values), default fallbacks for non-sensitive keys | Any credential, any key, any password |
| Git repository | `.env.example` with placeholder values | Any real `.env` file |

### Secret rotation readiness

All secrets are read from environment variables at startup. Rotating a secret requires only redeploying with the new value — no code change.

### Never in code

```python
# FATAL — hardcoded credentials
PROVIDER_API_KEY = "sk-live-xxxxxxxxxxxx"

# Correct
PROVIDER_API_KEY = os.environ.get("PROVIDER_API_KEY")
```

---

## Webhook security

Every incoming webhook must verify the request signature before processing:

```python
# routers/webhooks/<provider>.py
import hmac
import hashlib
import base64

def _verify_webhook_signature(request_body: bytes, signature_header: str, secret: str) -> bool:
    digest = hmac.new(secret.encode(), request_body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode()
    return hmac.compare_digest(computed, signature_header)


@webhook_bp.route("/provider/record-created", methods=["POST"])
def provider_record_created():
    raw_body = request.get_data()
    signature = request.headers.get("X-Provider-Signature", "")
    secret = current_app.config["WEBHOOK_SECRET_PROVIDER"]

    if not _verify_webhook_signature(raw_body, signature, secret):
        logger.warning("Webhook signature verification failed | source=provider endpoint=record-created")
        return jsonify({"error": "Unauthorized"}), 401

    # safe to process
    ...
```

Use `hmac.compare_digest` for signature comparison — never `==`. Timing-safe comparison prevents timing attacks.

---

## Rate limiting

Rate limiting is applied at the infrastructure level (Nginx, ALB, CloudFront WAF). The application layer adds per-user rate limiting for high-risk endpoints:

```python
from functools import wraps
from flask import current_app
from my_app.services.infra.redis import get_redis_client
from my_app.errors import PermissionDenied


def rate_limit(max_requests: int, window_seconds: int, key_prefix: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from flask_jwt_extended import get_jwt
            user_id = get_jwt().get("user_id", "anonymous")
            redis = get_redis_client(current_app.config["REDIS_URI"])
            key = f"{current_app.config['REDIS_KEY_PREFIX']}:ratelimit:{key_prefix}:{user_id}"
            count = redis.incr(key)
            if count == 1:
                redis.expire(key, window_seconds)
            if count > max_requests:
                raise PermissionDenied("Rate limit exceeded. Please wait before retrying.")
            return fn(*args, **kwargs)
        return wrapper
    return decorator
```

Apply to auth endpoints, AI operator, and bulk import endpoints:

```python
@auth_bp.route("/login", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60, key_prefix="login")
def login():
    ...
```

---

## IDOR (Insecure Direct Object Reference) prevention

Every resource lookup must assert ownership:

```python
# Wrong — fetches any record by ID regardless of workspace
record = db.session.get(Record, record_id)

# Correct — scopes to caller's workspace
record = (
    db.session.query(Record)
    .filter(Record.id == record_id, Record.workspace_id == ctx.workspace_id)
    .first()
)
if record is None:
    raise NotFound(f"Record {record_id} not found.")
```

Return `NotFound` (not `PermissionDenied`) when a resource doesn't belong to the caller's workspace. Returning `PermissionDenied` confirms the resource exists — that is an information leak.

---

## Security checklist for every new endpoint

Before any endpoint ships:

- [ ] JWT verification in place (`@jwt_required()`)
- [ ] Role check in place (`@role_required(...)`)
- [ ] Input validated via Pydantic request model
- [ ] No raw SQL strings with user input
- [ ] All resource fetches scoped to `ctx.workspace_id`
- [ ] Response serializer uses explicit field whitelist
- [ ] No sensitive fields (passwords, tokens, keys) in response
- [ ] Webhook endpoints verify signatures
- [ ] Rate limiting applied if endpoint is high-risk
