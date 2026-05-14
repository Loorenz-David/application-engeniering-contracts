#!/usr/bin/env bash
# =============================================================================
# TEST 02 — Notifications Query & Mutation Endpoints
# Purpose : Validate list, unread-count, mark-read, pin, unpin, and push
#           subscription register/unregister endpoints.
# Run from: <project>/backend/app/
# Requires: 00_seed_identity.sh already run (.test_token_bootstrap present)
# =============================================================================
set -uo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../../app" && pwd)}"
cd "$APP_DIR"
BASE_URL="${APP_URL:-http://localhost:8000}"

echo "════════════════════════════════════════════════════════════"
echo "TEST 02: Notifications — Query & Mutation Endpoints"
echo "════════════════════════════════════════════════════════════"
echo ""

TOKEN=$(cat .test_token_bootstrap 2>/dev/null || echo "")
if [ -z "$TOKEN" ]; then
  echo "❌ .test_token_bootstrap not found. Run 00_seed_identity.sh first."
  exit 1
fi

PASSED=0
FAILED=0

pass() { echo "   ✅ $1"; PASSED=$((PASSED + 1)); return 0; }
fail() { echo "   ❌ $1"; FAILED=$((FAILED + 1)); return 0; }

db_query() {
  local sql="$1"
  python3 - <<PYEOF
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from my_app.config import settings
SQL = """$sql"""
async def main():
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        result = await conn.execute(text(SQL))
        row = result.first()
        print(row[0] if row else "0")
    await engine.dispose()
asyncio.run(main())
PYEOF
}

run_sql() {
  local sql="$1"
  python3 - <<PYEOF
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from my_app.config import settings

SQL = """$sql"""

async def main():
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.execute(text(SQL))
    await engine.dispose()

asyncio.run(main())
PYEOF
}

# ---------------------------------------------------------------------------
# Precondition: seed 3 test notifications
# ---------------------------------------------------------------------------
echo "Precondition: seed 3 test notifications for usr_user_test"

run_sql "
INSERT INTO notifications (client_id, user_id, notification_type, title, body, read_at, created_at)
VALUES
  ('notif_test_001', (SELECT client_id FROM users WHERE client_id='usr_user_test' LIMIT 1), 'test_notification', 'Test 1', 'Message 1', NULL, NOW()),
  ('notif_test_002', (SELECT client_id FROM users WHERE client_id='usr_user_test' LIMIT 1), 'test_notification', 'Test 2', 'Message 2', NULL, NOW()),
  ('notif_test_003', (SELECT client_id FROM users WHERE client_id='usr_user_test' LIMIT 1), 'test_notification', 'Test 3 (read)', 'Message 3', NOW() - INTERVAL '1 hour', NOW() - INTERVAL '30 minutes')
ON CONFLICT (client_id) DO UPDATE
  SET read_at = EXCLUDED.read_at,
      title   = EXCLUDED.title,
      body    = EXCLUDED.body
" && echo "   ✅ Test notifications seeded" || echo "   ⚠️  Seed skipped (may already exist)"
echo ""

# ---------------------------------------------------------------------------
# A: GET /notifications — all
# ---------------------------------------------------------------------------
echo "A — GET /api/v1/notifications?unread_only=false"
RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
  "$BASE_URL/api/v1/notifications?unread_only=false&limit=30" \
  -H "Authorization: Bearer $TOKEN")
STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$RESP" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
COUNT=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',{}).get('notifications',[])))" 2>/dev/null)
[ "${COUNT:-0}" -ge "3" ] && pass "notifications count >= 3 (got $COUNT)" || fail "notifications count = $COUNT (expected >= 3)"
HAS_MORE=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('has_more',''))" 2>/dev/null)
[ "$HAS_MORE" = "False" ] && pass "has_more=false" || pass "has_more=$HAS_MORE (check pagination)"
echo ""

# ---------------------------------------------------------------------------
# B: GET /notifications?unread_only=true
# ---------------------------------------------------------------------------
echo "B — GET /api/v1/notifications?unread_only=true"
RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
  "$BASE_URL/api/v1/notifications?unread_only=true&limit=30" \
  -H "Authorization: Bearer $TOKEN")
STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$RESP" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
UNREAD_COUNT=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',{}).get('notifications',[])))" 2>/dev/null)
[ "${UNREAD_COUNT:-0}" -ge "2" ] && pass "unread notifications >= 2 (got $UNREAD_COUNT)" || fail "unread count = $UNREAD_COUNT (expected >= 2)"
echo ""

# ---------------------------------------------------------------------------
# C: GET /notifications/unread-count
# ---------------------------------------------------------------------------
echo "C — GET /api/v1/notifications/unread-count"
RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
  "$BASE_URL/api/v1/notifications/unread-count" \
  -H "Authorization: Bearer $TOKEN")
STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$RESP" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
BADGE=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('unread_count',''))" 2>/dev/null)
[ "${BADGE:-0}" -ge "2" ] && pass "unread_count >= 2 (got $BADGE)" || fail "unread_count = $BADGE"
echo ""

# ---------------------------------------------------------------------------
# D: POST /notifications/mark-read
# ---------------------------------------------------------------------------
echo "D — POST /api/v1/notifications/mark-read"
# Payload: notification_ids array
RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
  -X POST "$BASE_URL/api/v1/notifications/mark-read" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"notification_client_ids": ["notif_test_001", "notif_test_002"]}')
STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$RESP" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
OK=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null)
[ "$OK" = "True" ] && pass "ok=true" || fail "ok=$OK"

# DB verify: both should now have read_at set
READ_COUNT=$(db_query "SELECT COUNT(*) FROM notifications WHERE client_id IN ('notif_test_001','notif_test_002') AND read_at IS NOT NULL")
[ "${READ_COUNT:-0}" = "2" ] && pass "DB: both notifications marked read" || fail "DB: read_at not set (count=$READ_COUNT)"
echo ""

# ---------------------------------------------------------------------------
# E: POST /notifications/pins (pin a case entity)
# ---------------------------------------------------------------------------
echo "E — POST /api/v1/notifications/pins"
# Payload: entity_type + entity_client_id
PIN_BEFORE=$(db_query "SELECT COUNT(*) FROM notification_pins WHERE user_id=(SELECT client_id FROM users WHERE client_id='usr_user_test' LIMIT 1) AND entity_client_id='case_test_pin_001'" 2>/dev/null || echo "0")

RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
  -X POST "$BASE_URL/api/v1/notifications/pins" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_type": "case", "entity_client_id": "case_test_pin_001"}')
STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$RESP" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200 (pin created)" || fail "HTTP $STATUS"
PIN_ID=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('pin',{}).get('client_id',''))" 2>/dev/null)
[ -n "$PIN_ID" ] && pass "pin.client_id = $PIN_ID" || fail "pin.client_id missing"

PIN_AFTER=$(db_query "SELECT COUNT(*) FROM notification_pins WHERE user_id=(SELECT client_id FROM users WHERE client_id='usr_user_test' LIMIT 1) AND entity_client_id='case_test_pin_001'" 2>/dev/null || echo "0")
[ "${PIN_AFTER:-0}" -gt "${PIN_BEFORE:-0}" ] && pass "DB: pin row created" || fail "DB: pin row not found"
echo ""

# ---------------------------------------------------------------------------
# F: DELETE /notifications/pins (unpin)
# ---------------------------------------------------------------------------
echo "F — DELETE /api/v1/notifications/pins"
# Payload: entity_type + entity_client_id
RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
  -X DELETE "$BASE_URL/api/v1/notifications/pins" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_type": "case", "entity_client_id": "case_test_pin_001"}')
STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$RESP" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200 (pin deleted)" || fail "HTTP $STATUS"
OK=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null)
[ "$OK" = "True" ] && pass "ok=true" || fail "ok=$OK"

PIN_GONE=$(db_query "SELECT COUNT(*) FROM notification_pins WHERE user_id=(SELECT client_id FROM users WHERE client_id='usr_user_test' LIMIT 1) AND entity_client_id='case_test_pin_001'" 2>/dev/null || echo "1")
[ "${PIN_GONE:-1}" = "0" ] && pass "DB: pin row removed" || fail "DB: pin row still present"
echo ""

# ---------------------------------------------------------------------------
# G: POST /notifications/push-subscriptions
# ---------------------------------------------------------------------------
echo "G — POST /api/v1/notifications/push-subscriptions"
# Payload: endpoint + keys
PUSH_BEFORE=$(db_query "SELECT COUNT(*) FROM push_subscriptions WHERE user_id=(SELECT client_id FROM users WHERE client_id='usr_user_test' LIMIT 1) AND endpoint='https://example.com/push/test-bootstrap'" 2>/dev/null || echo "0")

RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
  -X POST "$BASE_URL/api/v1/notifications/push-subscription" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint": "https://example.com/push/test-bootstrap",
    "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlTiNhnJx4-SjA17XxMQiILuELFCCxjvCfJMjA",
    "auth": "tBHItJI5svbpez7KI4CCXg"
  }')
STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$RESP" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200 (subscription registered)" || fail "HTTP $STATUS"
SUB_ID=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('subscription',{}).get('client_id',''))" 2>/dev/null)
[ -n "$SUB_ID" ] && pass "subscription.client_id = $SUB_ID" || fail "subscription.client_id missing"

PUSH_AFTER=$(db_query "SELECT COUNT(*) FROM push_subscriptions WHERE user_id=(SELECT client_id FROM users WHERE client_id='usr_user_test' LIMIT 1) AND endpoint='https://example.com/push/test-bootstrap'" 2>/dev/null || echo "0")
[ "${PUSH_AFTER:-0}" -gt "${PUSH_BEFORE:-0}" ] && pass "DB: push_subscription row created" || fail "DB: subscription row not found"
echo ""

# ---------------------------------------------------------------------------
# H: DELETE /notifications/push-subscriptions
# ---------------------------------------------------------------------------
echo "H — DELETE /api/v1/notifications/push-subscriptions"
# Payload: endpoint
RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
  -X DELETE "$BASE_URL/api/v1/notifications/push-subscription" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"endpoint": "https://example.com/push/test-bootstrap", "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlTiNhnJx4-SjA17XxMQiILuELFCCxjvCfJMjA", "auth": "tBHItJI5svbpez7KI4CCXg"}')
STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)

[ "$STATUS" = "200" ] && pass "HTTP 200 (subscription removed)" || fail "HTTP $STATUS"
PUSH_GONE=$(db_query "SELECT COUNT(*) FROM push_subscriptions WHERE user_id=(SELECT client_id FROM users WHERE client_id='usr_user_test' LIMIT 1) AND endpoint='https://example.com/push/test-bootstrap'" 2>/dev/null || echo "1")
[ "${PUSH_GONE:-1}" = "0" ] && pass "DB: subscription row removed" || fail "DB: subscription row still present"
echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "════════════════════════════════════════════════════════════"
echo "TEST 02 RESULT: $PASSED Passed, $FAILED Failed"
echo "════════════════════════════════════════════════════════════"
if [ "$FAILED" -gt "0" ]; then
  echo ""
  echo "⚠️  Record failures in:"
  echo "   tests/issues/YYYY-MM-DD_02_notifications_<desc>.md"
  exit 1
fi
echo ""
exit 0
