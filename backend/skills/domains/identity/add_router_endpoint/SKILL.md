# Identity Add Router Endpoint

## Intent

Expose identity command/query functionality via a new HTTP endpoint.

## Trigger conditions

- User asks for new API endpoint in identity scope.

## Required inputs

- HTTP method + route path
- Auth/permission requirement
- Target command/query

## Contracts to load

- `backend/architecture/09_routers.md`: router boundary contract
- `backend/architecture/04_context.md`: context creation
- `backend/architecture/06_commands.md` or `backend/architecture/07_queries.md`: target call path
- `backend/architecture/10_auth.md`: auth and RBAC boundary

## Optional local extension companions

- `backend/architecture/40_identity_local.md`
- `backend/architecture/41_user_local.md`

## Execution protocol

1. Define request/response schema at router edge.
2. Build `ServiceContext` and call one command or query.
3. Map domain outcomes/errors to HTTP response contract.
4. Add endpoint tests.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Router contains no business logic.
- Endpoint auth and error mapping are explicit.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
