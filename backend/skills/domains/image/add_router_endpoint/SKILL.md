# Image Add Router Endpoint

## Intent

Expose image-domain command/query functionality via a new HTTP endpoint.

## Trigger conditions

- User asks for new image API endpoint.

## Required inputs

- HTTP method and path
- Auth/permission requirement
- Target command/query

## Contracts to load

- `backend/architecture/09_routers.md`: router boundary rules
- `backend/architecture/04_context.md`: context creation
- `backend/architecture/06_commands.md` or `backend/architecture/07_queries.md`: service path
- `backend/architecture/10_auth.md`: auth boundary
- `backend/architecture/43_image.md`: image semantics

## Optional local extension companions

- `backend/architecture/43_image_local.md`

## Execution protocol

1. Define endpoint request/response schema.
2. Build `ServiceContext` and call one command or query.
3. Map outcomes/errors to HTTP response contract.
4. Add endpoint tests.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Router remains thin with no business logic.
- Endpoint security and error mapping are explicit.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
