# Case Add Router Endpoint

## Intent

Expose case-domain command/query functionality via a new HTTP endpoint.

## Trigger conditions

- User asks for new case API endpoint.

## Required inputs

- HTTP method and path
- Auth/permission requirement
- Target command/query

## Contracts to load

- `backend/architecture/09_routers.md`: router boundary rules
- `backend/architecture/04_context.md`: context creation
- `backend/architecture/06_commands.md` or `backend/architecture/07_queries.md`: service call path
- `backend/architecture/10_auth.md`: auth boundary
- `backend/architecture/44_case.md`: case semantics

## Optional local extension companions

- `backend/architecture/44_case_local.md`

## Execution protocol

1. Define endpoint request/response schemas.
2. Build `ServiceContext` and call one command or query.
3. Map outcomes/errors to HTTP responses.
4. Add endpoint tests.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Router remains thin and orchestration-free.
- Endpoint auth and errors are explicit.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
