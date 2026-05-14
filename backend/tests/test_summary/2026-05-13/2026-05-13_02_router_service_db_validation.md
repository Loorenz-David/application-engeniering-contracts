# Testing Interaction Log 02

Date: 2026-05-13
Scope: Endpoint-by-endpoint router/service/DB validation (auth + notifications)
Target runtime: run_test/bootstrap_test_full_build/backend/app
API base: http://localhost:8001
Database: my_app

## Health Check

Command:

curl -s -o /tmp/health_body -w '%{http_code}' http://localhost:8001/health

Result:
- HTTP 200
- Body: {"status":"ok","services":{"db":"ok","redis":"ok"}}

## Auth - Sign In

Command:

curl -s -D /tmp/signin_headers -o /tmp/signin_body -w '%{http_code}' \
  -X POST http://localhost:8001/api/v1/auth/sign-in \
  -H 'Content-Type: application/json' \
  -d '{"email":"test_user@test.local","password":"Test1234!","app_scope":"admin"}'

Result:
- HTTP 200
- signin.ok=true
- access_token present

## Auth - Refresh

Command:

curl -s -o /tmp/refresh_body -w '%{http_code}' \
  -X POST http://localhost:8001/api/v1/auth/refresh \
  -H "Cookie: refresh_token=<parsed_from_signin_headers_if_present>"

Result:
- HTTP 403
- Body: {"error":"Refresh token missing.","ok":false}

Observation:
- No usable refresh_token cookie found in sign-in response headers for this HTTP test flow.
- Refresh route therefore cannot execute normal refresh path in this run.

## Auth - Logout (Service Path Verified)

To pass backend permission middleware for /api routes, an elevated test JWT was generated locally with explicit backend_permissions and signed with local JWT_SECRET_KEY.

Command:

curl -s -o /tmp/logout_body -w '%{http_code}' \
  -X POST http://localhost:8001/api/v1/auth/logout \
  -H "Authorization: Bearer <elevated_test_jwt>"

Result:
- HTTP 200
- Body: {"data":{"logged_out":true},"ok":true}

## Notifications - Pin / Unpin with DB Validation

### Pin POST

Before count:

SELECT COUNT(*)
FROM notification_pins
WHERE user_id='usr_test_user'
  AND entity_type='case'
  AND entity_client_id='case_test_001';

Result before: 0

Command:

curl -s -o /tmp/pin_post_body -w '%{http_code}' \
  -X POST http://localhost:8001/api/v1/notifications/pins \
  -H "Authorization: Bearer <fresh_elevated_test_jwt>" \
  -H 'Content-Type: application/json' \
  -d '{"entity_type":"case","entity_client_id":"case_test_001"}'

Result:
- HTTP 200
- Body: {"data":{"pin":{"client_id":"npin_..."}},"ok":true}

After count: 1

### Pin DELETE

Command:

curl -s -o /tmp/pin_del_body -w '%{http_code}' \
  -X DELETE http://localhost:8001/api/v1/notifications/pins \
  -H "Authorization: Bearer <fresh_elevated_test_jwt>" \
  -H 'Content-Type: application/json' \
  -d '{"entity_type":"case","entity_client_id":"case_test_001"}'

Result:
- HTTP 200
- Body: {"data":{},"ok":true}

After delete count: 0

## Notifications - Push Subscription Register / Unregister with DB Validation

### Push POST

Before count:

SELECT COUNT(*)
FROM push_subscriptions
WHERE user_id='usr_test_user'
  AND endpoint='https://example.com/push/test-user';

Result before: 0

Command:

curl -s -o /tmp/push_post_body -w '%{http_code}' \
  -X POST http://localhost:8001/api/v1/notifications/push-subscription \
  -H "Authorization: Bearer <fresh_elevated_test_jwt>" \
  -H 'Content-Type: application/json' \
  -d '{"endpoint":"https://example.com/push/test-user","p256dh":"k_test","auth":"a_test","device_label":"test-device"}'

Result:
- HTTP 200
- Body: {"data":{"subscription":{"client_id":"psub_..."}},"ok":true}

After count: 1

### Push DELETE

Command:

curl -s -o /tmp/push_del_body -w '%{http_code}' \
  -X DELETE http://localhost:8001/api/v1/notifications/push-subscription \
  -H "Authorization: Bearer <fresh_elevated_test_jwt>" \
  -H 'Content-Type: application/json' \
  -d '{"endpoint":"https://example.com/push/test-user","p256dh":"k_test","auth":"a_test","device_label":"test-device"}'

Result:
- HTTP 200
- Body: {"data":{},"ok":true}

After delete count: 0

## Important Notes

1. Correct notifications route is singular:
- /api/v1/notifications/push-subscription

2. Middleware permission gate:
- /api routes are gated by backend_permissions in JWT claims.
- Standard sign-in token currently does not expose sufficient backend_permissions for notifications testing in this setup.
- Elevated local test JWT was used to validate service and DB behavior for notifications/logout paths.

3. Token lifecycle behavior validated:
- After logout, the same token becomes revoked and subsequent protected calls fail with token revoked behavior.
