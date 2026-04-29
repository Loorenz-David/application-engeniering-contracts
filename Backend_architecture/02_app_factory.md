# 02 — App Factory & Configuration Contract

## App factory

The application is created by a single `create_app(config_name)` function in `my_app/__init__.py`. There is no module-level Flask instance.

```python
# my_app/__init__.py

def create_app(config_name: str = "development") -> Flask:
    env_path = resolve_env_path(config_name)
    if env_path:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=True)

    app = Flask(__name__)
    app.config.from_object(config_map[config_name])

    _init_extensions(app)
    _register_blueprints(app)
    _register_middleware(app)

    return app
```

**Rules:**
- `create_app` is the only entry point. No global `app = Flask(__name__)` anywhere.
- Config is loaded from environment variables via a `Config` class — never hardcoded.
- Extensions (`db`, `jwt`, `migrate`, `socketio`) are initialized inside `create_app`, not at import time.
- Blueprint registration happens inside `create_app` via a dedicated `_register_blueprints(app)` function. Not inline.
- Middleware (`before_request`, `after_request`) is registered inside `create_app` via a dedicated `_register_middleware(app)` function.

---

## Configuration contract

Config lives in `config/`. One class per environment:

```
config/
├── default.py       # Base Config class with all env var reads
├── development.py   # DevelopmentConfig(Config)
├── testing.py       # TestingConfig(Config)
└── production.py    # ProductionConfig(Config)
```

### Base `Config` class rules

```python
# config/default.py
import os

class Config:
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "devkey")
    JWT_SECRET_KEY: str = os.environ.get("JWT_SECRET_KEY") or SECRET_KEY
    SQLALCHEMY_DATABASE_URI: str = os.environ.get("DATABASE_URL", "")
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    SQLALCHEMY_ENGINE_OPTIONS: dict = {
        "connect_args": {"options": "-c timezone=UTC"}
    }
    REDIS_URI: str | None = os.environ.get("REDIS_URI")
    # ... all other env vars
```

- Every config key reads from `os.environ`. Never from a hardcoded value except safe defaults.
- `production.py` raises `RuntimeError` if required keys are missing. Development tolerates missing optional keys.
- Boolean env vars: parse with `.lower() == "true"` — never `bool(os.environ.get(...))` (that always returns `True`).
- Integer env vars: wrap with `int(os.environ.get("KEY", "default_int_as_string"))`.

### `resolve_env_path` function

```python
def resolve_env_path(config_name: str) -> str | None:
    if config_name == "production":
        env_path = "/home/ubuntu/config/my_app/.env"
    else:
        env_path = ".env"

    if os.path.exists(env_path):
        return env_path

    if config_name == "production":
        raise RuntimeError(f"Missing required env file at {env_path}")

    return None
```

Production must fail hard on missing env file. Development silently skips.

---

## Extension initialization

```python
def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    jwt.init_app(app)
    Migrate(app, db)
    _initialize_socketio(app, _get_frontend_origins())
    configure_mappers()

    with app.app_context():
        from my_app.services.infra.events import get_event_bus
        get_event_bus()
```

---

## Middleware contract

```python
def _register_middleware(app: Flask) -> None:

    @app.before_request
    def decompress_payload():
        return decompress_request()

    @app.after_request
    def compress_and_set_headers(response):
        if request.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return compress_payload(response)
```

Middleware registered here applies to all routes. Route-specific header overrides belong in the route handler, not in the middleware.

---

## Health check endpoint

Every application exposes `GET /` that checks all critical dependencies:

```python
@app.route("/", methods=["GET"])
def health():
    status = {"status": "ok", "services": {}}
    ok = True

    try:
        db.session.execute(text("SELECT 1"))
        status["services"]["db"] = "ok"
    except Exception as e:
        status["services"]["db"] = f"error: {e}"
        ok = False

    try:
        assert_redis_available(app.config["REDIS_URI"])
        status["services"]["redis"] = "ok"
    except Exception as e:
        status["services"]["redis"] = f"error: {e}"
        ok = False

    status["status"] = "ok" if ok else "degraded"
    return status, (200 if ok else 503)
```

Health checks use standard HTTP 200 / 503. They are the only place where `except Exception` is acceptable without re-raising, because health checks must never crash.

---

## CORS

```python
CORS(
    app,
    supports_credentials=True,
    resources={r"/*": {"origins": frontend_origins}},
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
)
```

`frontend_origins` is always read from `os.environ.get("FRONTEND_ORIGINS", "http://localhost:5173").split(",")`. Never hardcoded in config.

---

## Startup validation

`create_app` must fail loudly if required configuration is missing. Silent failures in production (e.g., `REDIS_URI=None` with no error) are harder to diagnose than an explicit startup crash.

Add a `_validate_config(app)` call at the end of `create_app`, before returning:

```python
def create_app(config_name: str = "development") -> Flask:
    ...
    _init_extensions(app)
    _register_blueprints(app)
    _register_middleware(app)
    _validate_config(app)   # must be last
    return app


_REQUIRED_IN_PRODUCTION = [
    "SECRET_KEY",
    "JWT_SECRET_KEY",
    "SQLALCHEMY_DATABASE_URI",
    "REDIS_URI",
    "FRONTEND_ORIGINS",
]


def _validate_config(app: Flask) -> None:
    if app.config.get("TESTING"):
        return  # skip in test environment

    if app.config.get("ENV") == "production":
        missing = [k for k in _REQUIRED_IN_PRODUCTION if not app.config.get(k)]
        if missing:
            raise RuntimeError(
                f"Missing required production config keys: {', '.join(missing)}"
            )
```

**Rules:**
- Validation is skipped in the `testing` environment — tests provide minimal config.
- In production, any missing required key raises `RuntimeError` before the first request is served.
- In development, missing optional keys are tolerated — no validation fires.
- The required key list lives in the factory file, not scattered across modules.

---

## Gunicorn configuration

Production deployments run Flask under gunicorn. The gunicorn configuration lives in `gunicorn.conf.py` at the project root, not passed as CLI flags:

```python
# gunicorn.conf.py
import multiprocessing

# Worker count: 2x CPU cores + 1 is the standard starting point
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "eventlet"          # required for Flask-SocketIO
threads = 1                        # eventlet workers are single-threaded
bind = "0.0.0.0:5000"
timeout = 120                      # seconds before a worker is killed
keepalive = 5
max_requests = 1000                # recycle workers to prevent memory leaks
max_requests_jitter = 100          # prevents all workers recycling simultaneously
accesslog = "-"                    # stdout
errorlog = "-"                     # stderr
loglevel = "info"
```

Start command:

```bash
gunicorn --config gunicorn.conf.py "my_app:create_app('production')"
```

**Rules:**
- `worker_class = "eventlet"` is required when using Flask-SocketIO. Without it, WebSocket connections fail.
- `max_requests` + `max_requests_jitter` prevents memory leaks from accumulating across thousands of requests.
- Never set `workers` by hardcoded integer — always derive from CPU count.
- `timeout` must be longer than your slowest expected request (external HTTP calls, heavy queries). Default 30s is too short for workloads with slow external API calls.
- `accesslog = "-"` routes access logs to stdout for collection by the container/supervisor layer.
