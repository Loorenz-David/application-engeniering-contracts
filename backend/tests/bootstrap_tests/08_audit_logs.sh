#!/usr/bin/env bash
# =============================================================================
# TEST 07 — Audit Log Bootstrap Validation
# Purpose : Verify that audit_logs table exists, audited events produce rows,
#           and audit records are queryable.
# Run from: <project>/backend/app/
# Requires: .test_token_bootstrap (run 00_seed_identity.sh first)
# Key design:
#   - Use case:state-changed as the audited event trigger (not create)
#   - Must trigger via the API, not raw DB insert, to exercise event bus
#   - Audit handler must have the event in its allowlist
# =============================================================================
set -uo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../../app" && pwd)}"
cd "$APP_DIR"

echo "════════════════════════════════════════════════════════════"
echo "TEST 07: Audit Log Validation"
echo "════════════════════════════════════════════════════════════"
echo ""

TOKEN=$(cat .test_token_bootstrap 2>/dev/null || echo "")
if [ -z "$TOKEN" ]; then
  echo "❌ .test_token_bootstrap not found. Run 00_seed_identity.sh first."
  exit 1
fi

PASSED=0
FAILED=0

pass() { echo "   ✅ $1"; ((PASSED+=1)); return 0; }
fail() { echo "   ❌ $1"; ((FAILED+=1)); return 0; }

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

# ---------------------------------------------------------------------------
# 1: audit_logs table exists
# ---------------------------------------------------------------------------
echo "1 — Check audit_logs table exists"
TABLE_EXISTS=$(db_query "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='audit_logs')")
[ "$TABLE_EXISTS" = "t" ] || [ "$TABLE_EXISTS" = "True" ] || [ "$TABLE_EXISTS" = "true" ] \
  && pass "audit_logs table exists" \
  || fail "audit_logs table not found (check migrations)"
echo ""

# ---------------------------------------------------------------------------
# 2: Baseline audit count
# ---------------------------------------------------------------------------
echo "2 — Record baseline audit count"
INITIAL_COUNT=$(db_query "SELECT COUNT(*) FROM audit_logs")
pass "Baseline audit_logs count: $INITIAL_COUNT"
echo ""

# ---------------------------------------------------------------------------
# 3: Trigger audited event — create case then change state
# ---------------------------------------------------------------------------
echo "3 — Trigger audited event (case:state-changed)"

# 3a: Create a case
CASE=$(curl -s -X POST http://localhost:8000/api/v1/cases \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"case_type_id": "ct_investigation"}')
CASE_ID=$(echo "$CASE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('case',{}).get('client_id',''))" 2>/dev/null)

if [ -z "$CASE_ID" ]; then
  fail "Could not create case for audit trigger"
  echo "   Response: $CASE"
else
  pass "Case created: $CASE_ID"

  # 3b: Change state (this is the audited event: case:state-changed)
  STATE_CHANGE=$(curl -s -X PATCH "http://localhost:8000/api/v1/cases/$CASE_ID/state" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"case_client_id\": \"$CASE_ID\", \"new_state\": \"resolving\"}")
  NEW_STATE=$(echo "$STATE_CHANGE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('case',{}).get('state',''))" 2>/dev/null)

  if [ "$NEW_STATE" = "resolving" ]; then
    pass "State changed to resolving (case:state-changed event fired)"
  else
    fail "State change failed — audit event may not have fired | $STATE_CHANGE"
  fi
fi
echo ""

# ---------------------------------------------------------------------------
# 4: Verify audit record written
# ---------------------------------------------------------------------------
echo "4 — Verify audit_logs row was written"
# Give event bus a moment to process (async handler)
sleep 0.5
FINAL_COUNT=$(db_query "SELECT COUNT(*) FROM audit_logs")
INCREASE=$(( FINAL_COUNT - INITIAL_COUNT ))

if [ "$INCREASE" -gt "0" ]; then
  pass "Audit records created: $INCREASE new row(s) (total=$FINAL_COUNT)"
else
  fail "No audit records written after state change (total=$FINAL_COUNT)"
  echo "   Check: audit_handler.py allowlist must include 'case:state-changed'"
  echo "   Check: handler is registered in event bus"
fi
echo ""

# ---------------------------------------------------------------------------
# 5: Verify audit_logs table structure
# ---------------------------------------------------------------------------
echo "5 — Verify audit_logs column structure"
COLUMNS=$(db_query "SELECT string_agg(column_name, ', ' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_name='audit_logs'")
echo "   Columns: $COLUMNS"
pass "audit_logs schema readable: $COLUMNS"
echo ""

# ---------------------------------------------------------------------------
# 6: Query audit logs
# ---------------------------------------------------------------------------
echo "6 — Query audit_logs (read path)"
QUERYABLE=$(db_query "SELECT COUNT(*) FROM audit_logs")
if [ -n "$QUERYABLE" ] && [ "$QUERYABLE" -ge "0" ]; then
  pass "audit_logs queryable: $QUERYABLE total rows"
else
  fail "Failed to query audit_logs"
fi
echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "════════════════════════════════════════════════════════════"
echo "TEST 07 RESULT: $PASSED Passed, $FAILED Failed"
echo "════════════════════════════════════════════════════════════"
if [ "$FAILED" -gt "0" ]; then
  echo ""
  echo "⚠️  Common issues to check:"
  echo "   - Audit handler allowlist is empty: add 'case:state-changed' to audited_events registry"
  echo "   - Handler not registered in event bus on startup"
  echo "   - Event dispatch path doesn't reach audit_handler (check build_workspace_event is called)"
  echo "   - Initial false negatives: ensure you trigger via API, not raw DB insert"
  echo ""
  echo "   Record failures in:"
  echo "   tests/issues/YYYY-MM-DD_07_audit_<desc>.md"
  exit 1
fi
echo ""
exit 0
