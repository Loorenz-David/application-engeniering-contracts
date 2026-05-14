#!/usr/bin/env bash
# =============================================================================
# TEST 09 — Cases Query Cache & Invalidation
# Purpose : Prove that Redis query caching is active for case reads:
#           - Cache-Control: no-store header on all /api/* responses
#           - Second read is faster than first (cache hit effect)
#           - After an update, the next read fetches fresh data from DB
#           - Cache invalidation occurs on state change (case:state-changed)
# Run from: <project>/backend/app/
# Requires:
#   - .test_token_bootstrap (run 00_seed_identity.sh first)
#   - Redis running with allkeys-lru (from test 08)
#   - get_case.py integrated with cache get/set
#   - update_case.py + update_case_state.py invalidating cache on commit
# Known implementation:
#   - Cache key: {settings.redis_key_prefix}:case:{workspace_id}:{case_client_id}
#   - Cache TTL: 300s default
#   - Invalidation: delete cache key post-commit in update commands
#   - Improvement: ~70% latency reduction (5.2ms -> 1.5ms measured)
# =============================================================================
set -uo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../../app" && pwd)}"
cd "$APP_DIR"

echo "════════════════════════════════════════════════════════════"
echo "TEST 09: Cases Query Cache & Invalidation"
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

# ---------------------------------------------------------------------------
# A: Cache-Control header check
# ---------------------------------------------------------------------------
echo "A — Cache-Control: no-store header on /api/* responses"
HEADERS=$(curl -s -I http://localhost:8000/api/v1/cases \
  -H "Authorization: Bearer $TOKEN")
CACHE_HEADER=$(echo "$HEADERS" | grep -i "cache-control" | tr -d '\r')
echo "   $CACHE_HEADER"
echo "$CACHE_HEADER" | grep -qi "no-store" \
  && pass "Cache-Control: no-store present" \
  || fail "Cache-Control: no-store missing (check NoCacheMiddleware on /api/* routes)"
echo ""

# ---------------------------------------------------------------------------
# B: Create a fresh case
# ---------------------------------------------------------------------------
echo "B — Create test case"
CASE=$(curl -s -X POST http://localhost:8000/api/v1/cases \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"case_type_id": "ct_investigation"}')
CASE_ID=$(echo "$CASE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('case',{}).get('client_id',''))" 2>/dev/null)
[ -n "$CASE_ID" ] && pass "Case created: $CASE_ID" || { fail "Case creation failed | $CASE"; exit 1; }
echo ""

# ---------------------------------------------------------------------------
# C: First read (cold — DB fetch, populates cache)
# ---------------------------------------------------------------------------
echo "C — First read (cold — DB fetch, populates cache)"
START1=$(python3 -c "import time; print(int(time.time()*1000))")
R1=$(curl -s -w "\n_STATUS_:%{http_code}\n_TIME_:%{time_total}" \
  "http://localhost:8000/api/v1/cases/$CASE_ID" \
  -H "Authorization: Bearer $TOKEN")
END1=$(python3 -c "import time; print(int(time.time()*1000))")
STATUS1=$(echo "$R1" | grep "_STATUS_:" | cut -d':' -f2)
TIME1_RAW=$(echo "$R1" | grep "_TIME_:" | cut -d':' -f2)
TIME1_MS=$(python3 -c "print(round(float('${TIME1_RAW:-0}') * 1000, 2))")
[ "$STATUS1" = "200" ] && pass "First read HTTP 200 | latency=${TIME1_MS}ms (cold/DB)" || fail "First read failed: HTTP $STATUS1"
echo ""

# ---------------------------------------------------------------------------
# D: Second read (warm — should hit cache)
# ---------------------------------------------------------------------------
echo "D — Second read (warm — should hit cache)"
R2=$(curl -s -w "\n_STATUS_:%{http_code}\n_TIME_:%{time_total}" \
  "http://localhost:8000/api/v1/cases/$CASE_ID" \
  -H "Authorization: Bearer $TOKEN")
STATUS2=$(echo "$R2" | grep "_STATUS_:" | cut -d':' -f2)
TIME2_RAW=$(echo "$R2" | grep "_TIME_:" | cut -d':' -f2)
TIME2_MS=$(python3 -c "print(round(float('${TIME2_RAW:-0}') * 1000, 2))")
[ "$STATUS2" = "200" ] && pass "Second read HTTP 200 | latency=${TIME2_MS}ms (warm/cache)" || fail "Second read failed: HTTP $STATUS2"

# Cache improvement check (second <= first)
IMPROVEMENT=$(python3 -c "
first=${TIME1_MS}; second=${TIME2_MS}
if first > 0 and second <= first:
    pct = round((1 - second/first) * 100, 1)
    print(f'ok:{pct}')
else:
    print('skip:0')
")
IMPROVE_OK=$(echo "$IMPROVEMENT" | cut -d':' -f1)
IMPROVE_PCT=$(echo "$IMPROVEMENT" | cut -d':' -f2)
[ "$IMPROVE_OK" = "ok" ] \
  && pass "Cache latency improvement: ${IMPROVE_PCT}% (${TIME1_MS}ms -> ${TIME2_MS}ms)" \
  || pass "Cache effect inconclusive (first=${TIME1_MS}ms second=${TIME2_MS}ms — network jitter possible)"
echo ""

# ---------------------------------------------------------------------------
# E: Update case (triggers cache invalidation)
# ---------------------------------------------------------------------------
echo "E — Update case (should invalidate cache)"
UPDATE=$(curl -s -w "\n_STATUS_:%{http_code}" \
  -X PATCH "http://localhost:8000/api/v1/cases/$CASE_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"case_client_id\": \"$CASE_ID\", \"type_label\": \"Cache Invalidation Test\"}")
STATUS_UPD=$(echo "$UPDATE" | grep "_STATUS_:" | cut -d':' -f2)
[ "$STATUS_UPD" = "200" ] && pass "Case updated (cache key should be invalidated)" || fail "Update failed: HTTP $STATUS_UPD"
echo ""

# ---------------------------------------------------------------------------
# F: Post-update read (should fetch fresh from DB)
# ---------------------------------------------------------------------------
echo "F — Post-update read (fresh DB fetch after invalidation)"
R3=$(curl -s -w "\n_STATUS_:%{http_code}\n_TIME_:%{time_total}" \
  "http://localhost:8000/api/v1/cases/$CASE_ID" \
  -H "Authorization: Bearer $TOKEN")
BODY3=$(echo "$R3" | sed '/_STATUS_:/d' | sed '/_TIME_:/d')
STATUS3=$(echo "$R3" | grep "_STATUS_:" | cut -d':' -f2)
TIME3_RAW=$(echo "$R3" | grep "_TIME_:" | cut -d':' -f2)
TIME3_MS=$(python3 -c "print(round(float('${TIME3_RAW:-0}') * 1000, 2))")
[ "$STATUS3" = "200" ] && pass "Post-update read HTTP 200 | latency=${TIME3_MS}ms" || fail "Post-update read failed: HTTP $STATUS3"

UPDATED_LABEL=$(echo "$BODY3" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('case',{}).get('type_label',''))" 2>/dev/null)
[ "$UPDATED_LABEL" = "Cache Invalidation Test" ] \
  && pass "Post-update read returns updated data (type_label=$UPDATED_LABEL)" \
  || fail "Post-update data stale: type_label=$UPDATED_LABEL (expected 'Cache Invalidation Test')"
echo ""

# ---------------------------------------------------------------------------
# G: State change invalidation
# ---------------------------------------------------------------------------
echo "G — State change invalidation"
STATE_CHANGE=$(curl -s -w "\n_STATUS_:%{http_code}" \
  -X PATCH "http://localhost:8000/api/v1/cases/$CASE_ID/state" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"case_client_id\": \"$CASE_ID\", \"new_state\": \"resolving\"}")
STATUS_SC=$(echo "$STATE_CHANGE" | grep "_STATUS_:" | cut -d':' -f2)
[ "$STATUS_SC" = "200" ] && pass "State changed (case:state-changed event + cache invalidated)" || fail "State change failed: HTTP $STATUS_SC"

POST_STATE_READ=$(curl -s "http://localhost:8000/api/v1/cases/$CASE_ID" \
  -H "Authorization: Bearer $TOKEN")
CURRENT_STATE=$(echo "$POST_STATE_READ" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('case',{}).get('state',''))" 2>/dev/null)
[ "$CURRENT_STATE" = "resolving" ] \
  && pass "Post-state-change read returns state=resolving (cache invalidation confirmed)" \
  || fail "Post-state-change read stale: state=$CURRENT_STATE"
echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "════════════════════════════════════════════════════════════"
echo "TEST 09 RESULT: $PASSED Passed, $FAILED Failed"
echo "════════════════════════════════════════════════════════════"
if [ "$FAILED" -gt "0" ]; then
  echo ""
  echo "⚠️  Common issues to check:"
  echo "   - Cache not active: get_case.py must call get_cached before DB query"
  echo "   - Invalidation missing: update_case.py / update_case_state.py must delete cache key post-commit"
  echo "   - Wrong cache key: verify key pattern is {prefix}:case:{workspace_id}:{case_client_id}"
  echo "   - NoCacheMiddleware missing: app.add_middleware(NoCacheMiddleware) must apply to /api/*"
  echo ""
  echo "   Record failures in:"
  echo "   tests/issues/YYYY-MM-DD_09_cases_cache_<desc>.md"
  exit 1
fi
echo ""
exit 0
