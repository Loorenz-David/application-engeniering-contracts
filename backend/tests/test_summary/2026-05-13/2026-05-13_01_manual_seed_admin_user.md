# Testing Interaction Log 01

Date: 2026-05-13
Scope: Manual DB seed for first test identity (no public registration route)
Target runtime: run_test/bootstrap_test_full_build/backend/app
Database: my_app (PostgreSQL)

## Objective
Create:
- workspace: test_workspace
- role: admin
- user: test_user
- membership: test_user linked to admin role in test_workspace

## Preconditions
- Docker services running from backend/app (postgres and redis)
- Alembic migrations applied in my_app

## DB Creation Commands Executed

1) Generate bcrypt hash for test user password

source .venv/bin/activate
python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt'], deprecated='auto').hash('Test1234!'))"

2) Seed role, workspace, workspace-role, user, and workspace-membership

BEGIN;

INSERT INTO roles (name, client_id)
SELECT 'ADMIN'::role_name_enum, 'role_test_admin'
WHERE NOT EXISTS (SELECT 1 FROM roles WHERE client_id='role_test_admin');

INSERT INTO workspaces (name, time_zone, created_by_id, created_at, client_id)
SELECT 'test_workspace', 'UTC', NULL, NOW(), 'ws_test_workspace'
WHERE NOT EXISTS (SELECT 1 FROM workspaces WHERE client_id='ws_test_workspace');

INSERT INTO workspace_roles (workspace_id, role_id, name, description, is_system, client_id)
SELECT 'ws_test_workspace', 'role_test_admin', 'admin', 'Admin role for test workspace', TRUE, 'wsr_test_admin'
WHERE NOT EXISTS (SELECT 1 FROM workspace_roles WHERE client_id='wsr_test_admin');

INSERT INTO users (
  created_at, created_by_id, username, phone_number, email, password,
  languages, language_preference, profile_picture, online, last_online,
  last_app_view_record_id, last_history_record_id, client_id
)
SELECT NOW(), NULL, 'test_user', NULL, 'test_user@test.local', '<bcrypt hash generated at runtime>',
       NULL, NULL, NULL, TRUE, NULL, NULL, NULL, 'usr_test_user'
WHERE NOT EXISTS (SELECT 1 FROM users WHERE client_id='usr_test_user');

INSERT INTO workspace_memberships (user_id, workspace_id, workspace_role_id, is_active, joined_at, client_id)
SELECT 'usr_test_user', 'ws_test_workspace', 'wsr_test_admin', TRUE, NOW(), 'wsm_test_user'
WHERE NOT EXISTS (SELECT 1 FROM workspace_memberships WHERE client_id='wsm_test_user');

COMMIT;

## Verification Query Executed

SELECT u.username,
       u.email,
       ws.name AS workspace,
       r.name AS role,
       wr.name AS workspace_role,
       wsm.is_active
FROM users u
JOIN workspace_memberships wsm ON wsm.user_id = u.client_id
JOIN workspaces ws ON ws.client_id = wsm.workspace_id
JOIN workspace_roles wr ON wr.client_id = wsm.workspace_role_id
JOIN roles r ON r.client_id = wr.role_id
WHERE u.client_id = 'usr_test_user';

## Verification Result
- username: test_user
- email: test_user@test.local
- workspace: test_workspace
- role: ADMIN
- workspace_role: admin
- is_active: true

## Notes
- A passlib bcrypt backend warning was printed during hash generation, but hash generation and insertions succeeded.
- API server remains running on port 8001 for next router/service interaction tests.

## Curl Command Executed

curl -i -X POST http://localhost:8001/api/v1/auth/sign-in \
  -H 'Content-Type: application/json' \
  -d '{"email":"test_user@test.local","password":"Test1234!","app_scope":"admin"}'

## Curl Result
- HTTP status: 200 OK
- Response envelope: ok=true
- Response data includes:
  - access_token
  - user object
  - workspace_id
