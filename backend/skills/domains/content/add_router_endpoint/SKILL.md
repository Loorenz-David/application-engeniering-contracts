# Content Add Router Endpoint

## Intent

Expose content-domain command/query functionality via a new HTTP endpoint.

## Trigger conditions

- User asks for new content API endpoint.

## Required inputs

- HTTP method and route
- Auth/permission requirement
- Target command/query

## Contracts to load

- `backend/architecture/09_routers.md`: router boundary rules
- `backend/architecture/04_context.md`: context creation
- `backend/architecture/06_commands.md` or `backend/architecture/07_queries.md`: service path
- `backend/architecture/10_auth.md`: auth boundary
- `backend/architecture/45_content.md`: content semantics

## Optional local extension companions

- `backend/architecture/45_content_local.md`

## Execution protocol

1. Define endpoint I/O schema.
2. Build `ServiceContext` and call one command or query.
3. Map outcomes and errors to HTTP responses.
4. Add endpoint tests.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Router contains no business logic.
- Endpoint contract and auth are explicit.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
