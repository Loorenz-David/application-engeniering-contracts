# 26 — Dependency Management Contract

## The rule

Every package added to the project is a long-term maintenance commitment. A dependency that solves a 5-line problem is not worth an extra CVE surface, a breaking upgrade cycle, or an incompatible transitive dependency.

Before adding a package, answer these four questions:

1. Can this be done with what is already installed?
2. Is this package actively maintained (commit within 6 months, open CVEs addressed)?
3. Does it have a compatible license (MIT, Apache-2, BSD)?
4. What is the blast radius if it breaks or goes unmaintained?

If you cannot answer all four, do not add it.

---

## Pinning strategy

`requirements.txt` pins every direct dependency to an exact version:

```
Flask==3.1.0
Flask-SQLAlchemy==3.1.1
Flask-Migrate==4.0.7
Flask-JWT-Extended==4.7.1
Flask-Cors==5.0.1
flask-socketio==5.5.1
SQLAlchemy==2.0.40
pydantic==2.11.3
redis==5.2.1
rq==2.3.2
gunicorn==23.0.0
bcrypt==4.3.0
python-dotenv==1.1.0
```

Never use `~=`, `>=`, or unpinned entries in `requirements.txt`. Floating versions turn every `pip install` into a non-deterministic build.

Separate files for different environments:

```
requirements.txt           # Production — exact pins only
requirements-dev.txt       # Adds: pytest, freezegun, etc.
requirements-test.txt      # CI-only testing tools if different from dev
```

---

## Upgrade cadence

| Trigger | Action | Who |
|---|---|---|
| Scheduled — monthly | Check for new patch/minor releases | Any engineer |
| CVE published (CVSS ≥ 7.0) | Patch within 48 hours | On-call |
| CVE published (CVSS < 7.0) | Patch in the next sprint | Assigned engineer |
| Major version release | Evaluate within one quarter | Tech lead review |

Use `pip-audit` to scan for CVEs as part of the CI pipeline:

```bash
pip install pip-audit
pip-audit -r requirements.txt
```

A failing `pip-audit` blocks CI — do not merge with known high-severity CVEs.

---

## Approved dependency list

These packages are pre-approved. Adding them requires no additional justification:

| Category | Package |
|---|---|
| Web framework | `Flask`, `Flask-Cors`, `Flask-SocketIO` |
| ORM | `SQLAlchemy`, `Flask-SQLAlchemy`, `Flask-Migrate`, `alembic` |
| Auth | `Flask-JWT-Extended`, `bcrypt` |
| Validation | `pydantic` |
| Database driver | `psycopg2-binary` |
| Cache / queue | `redis`, `rq` |
| Config | `python-dotenv` |
| WSGI server | `gunicorn` |
| Testing | `pytest`, `freezegun` |

All others require explicit review and addition to this list before use.

---

## Adding a new dependency — checklist

Before opening a PR that introduces a new package:

- [ ] Verified it cannot be replaced by an existing approved dependency
- [ ] PyPI page reviewed: last release date, download count, open issue trend
- [ ] License confirmed as MIT / Apache-2.0 / BSD
- [ ] `pip-audit` run after adding — no new CVEs introduced
- [ ] Exact version pinned in `requirements.txt`
- [ ] PR description explains **why** this package was chosen over alternatives

---

## Removing a dependency

When a dependency is no longer needed:

1. Remove it from `requirements.txt`.
2. Search the codebase for all imports of that package and remove them.
3. Run the full test suite.
4. Run `pip-audit` to confirm no orphaned vulnerability entries.

Never leave unused packages in `requirements.txt`. They are a maintenance liability.

---

## Transitive dependency conflicts

When `pip install` produces a conflict:

1. Identify which direct dependencies pull in the conflicting transitive package.
2. Prefer upgrading the direct dependency to a version that resolves the conflict.
3. If pinning a transitive package directly is unavoidable, document it with a comment:

```
# Required by Flask-SocketIO 5.x — remove when upgrading to 6.x
python-engineio==4.9.1
```

Never silence pip resolver errors with `--no-deps` or `--ignore-requires-python`.

---

## Virtual environment discipline

Every developer and CI run must use an isolated virtual environment. The project root must contain:

```
.python-version     # Pinned Python version (e.g., 3.12.3)
```

And must never contain:

```
site-packages/      # Never committed
*.egg-info/         # Never committed
__pycache__/        # Never committed
```

These paths must appear in `.gitignore`.

---

## AI agent guidance

When an AI agent is building a feature and needs a new library:

1. Check the approved list above first.
2. If the package is not on the list, propose it with license, PyPI stats, and alternatives considered — do not add it silently.
3. Never add packages to solve problems that already have approved solutions in the codebase.
4. Never add packages with GPL or AGPL licenses without legal review.
