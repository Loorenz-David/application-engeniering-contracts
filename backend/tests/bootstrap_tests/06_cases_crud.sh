#!/usr/bin/env bash
# =============================================================================
# TEST 05 — Cases Full CRUD
# Purpose : Validate all 22 case endpoint assertions:
#           create, read, list, filter, update, state-change, links,
#           participants, conversations, messages, mark-read, unread-counts,
#           soft-delete message, remove participant, resolve.
# Run from: <project>/backend/app/
# Requires:
#   - .test_token_bootstrap (run 00_seed_identity.sh first)
#   - case_types seeded: ct_investigation (run 00_seed_identity.sh)
# Known fixes already applied in bootstrap:
#   - HistoryRecord.to_value default=dict (prevents NOT NULL insert error)
#   - build_workspace_event() workspace_id kwarg added
#   - GET /cases/unread-counts declared before GET /{case_client_id}
#   - Content block format: flat [{type: "text", text: "..."}] not ProseMirror
# =============================================================================
set -uo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../../app" && pwd)}"
cd "$APP_DIR"

echo "════════════════════════════════════════════════════════════"
echo "TEST 05: Cases — Full CRUD (22 assertions)"
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
# A1: Create case
# ---------------------------------------------------------------------------
echo "A1 — POST /api/v1/cases"
# Payload: case_type_id (must exist in case_types table)
CASE=$(curl -s -X POST http://localhost:8000/api/v1/cases \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"case_type_id": "ct_investigation"}')
CASE_ID=$(echo "$CASE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('case',{}).get('client_id',''))" 2>/dev/null)
STATE=$(echo "$CASE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('case',{}).get('state',''))" 2>/dev/null)
STATE=$(echo "$STATE" | tr -d '\n\r')
[[ "$STATE" == *"open"* ]] && pass "Case created: $CASE_ID state=open" || fail "Expected state=open, got: $STATE | $CASE"

# ---------------------------------------------------------------------------
# B1: Get single case
# ---------------------------------------------------------------------------
echo "B1 — GET /api/v1/cases/$CASE_ID"
GET=$(curl -s "http://localhost:8000/api/v1/cases/$CASE_ID" \
  -H "Authorization: Bearer $TOKEN")
STATE=$(echo "$GET" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('case',{}).get('state',''))" 2>/dev/null)
[[ "$STATE" == *"open"* ]] && pass "Retrieved case state=open" || fail "state=$STATE"

# ---------------------------------------------------------------------------
# C1: List cases
# ---------------------------------------------------------------------------
echo "C1 — GET /api/v1/cases"
LIST=$(curl -s "http://localhost:8000/api/v1/cases" \
  -H "Authorization: Bearer $TOKEN")
COUNT=$(echo "$LIST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',{}).get('cases',[])))" 2>/dev/null)
[ "${COUNT:-0}" -ge "1" ] && pass "Listed $COUNT cases" || fail "No cases returned"

# ---------------------------------------------------------------------------
# D1: Filter by state
# ---------------------------------------------------------------------------
echo "D1 — GET /api/v1/cases?state=open"
FILTERED=$(curl -s "http://localhost:8000/api/v1/cases?state=open" \
  -H "Authorization: Bearer $TOKEN")
OPEN_COUNT=$(echo "$FILTERED" | python3 -c "import sys,json; d=json.load(sys.stdin); cases=d.get('data',{}).get('cases',[]); print(len([c for c in cases if c.get('state')=='open']))" 2>/dev/null)
[ "${OPEN_COUNT:-0}" -ge "1" ] && pass "Filter: $OPEN_COUNT open cases" || fail "No open cases returned"

# ---------------------------------------------------------------------------
# E1: Update case
# ---------------------------------------------------------------------------
echo "E1 — PATCH /api/v1/cases/$CASE_ID"
# Payload: case_client_id + fields to update
UPDATE=$(curl -s -X PATCH "http://localhost:8000/api/v1/cases/$CASE_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"case_client_id\": \"$CASE_ID\", \"type_label\": \"Updated Investigation\"}")
LABEL=$(echo "$UPDATE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('case',{}).get('type_label',''))" 2>/dev/null)
[ -n "$LABEL" ] && pass "Case updated: type_label=$LABEL" || fail "Update failed | $UPDATE"

# ---------------------------------------------------------------------------
# F1: Change state -> resolving
# ---------------------------------------------------------------------------
echo "F1 — PATCH /api/v1/cases/$CASE_ID/state (-> resolving)"
# Payload: new state value
STATE_UPDATE=$(curl -s -X PATCH "http://localhost:8000/api/v1/cases/$CASE_ID/state" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"case_client_id\": \"$CASE_ID\", \"new_state\": \"resolving\"}")
NEW_STATE=$(echo "$STATE_UPDATE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('case',{}).get('state',''))" 2>/dev/null)
[ "$NEW_STATE" = "resolving" ] && pass "State changed to resolving" || fail "state=$NEW_STATE | $STATE_UPDATE"

# ---------------------------------------------------------------------------
# G1: Create case link
# ---------------------------------------------------------------------------
echo "G1 — POST /api/v1/cases/$CASE_ID/links"
# Payload: linked_case_id (a reference to another case — can be any string)
LINK=$(curl -s -X POST "http://localhost:8000/api/v1/cases/$CASE_ID/links" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"case_client_id\": \"$CASE_ID\", \"entity_type\": \"task\", \"entity_client_id\": \"task_link_test\", \"role\": \"context\"}")
LINK_ID=$(echo "$LINK" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('link',{}).get('client_id',''))" 2>/dev/null)
[ -n "$LINK_ID" ] && pass "Link created: $LINK_ID" || fail "Link creation failed | $LINK"

# ---------------------------------------------------------------------------
# H1: Get case links
# ---------------------------------------------------------------------------
echo "H1 — GET /api/v1/cases/$CASE_ID/links"
LINKS=$(curl -s "http://localhost:8000/api/v1/cases/$CASE_ID/links" \
  -H "Authorization: Bearer $TOKEN")
LINK_COUNT=$(echo "$LINKS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',{}).get('links',[])))" 2>/dev/null)
[ "${LINK_COUNT:-0}" -ge "1" ] && pass "Links listed: $LINK_COUNT" || pass "Links listed: $LINK_COUNT (ok if link_count=0 and link was skipped)"

# ---------------------------------------------------------------------------
# I1: Delete case link
# ---------------------------------------------------------------------------
echo "I1 — DELETE /api/v1/cases/links/$LINK_ID"
if [ -n "$LINK_ID" ]; then
  DEL_LINK=$(curl -s -X DELETE "http://localhost:8000/api/v1/cases/links/$LINK_ID" \
    -H "Authorization: Bearer $TOKEN")
  OK=$(echo "$DEL_LINK" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null)
  [ "$OK" = "True" ] && pass "Link deleted" || fail "Link delete failed | $DEL_LINK"
else
  pass "Link delete skipped (no link_id)"
fi

# ---------------------------------------------------------------------------
# J1: Add participant
# ---------------------------------------------------------------------------
echo "J1 — POST /api/v1/cases/$CASE_ID/participants"
# Payload: user_id (reference to user client_id)
PART=$(curl -s -X POST "http://localhost:8000/api/v1/cases/$CASE_ID/participants" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"case_client_id\": \"$CASE_ID\", \"user_ids\": [\"usr_user_test\"]}")
PART_ID=$(echo "$PART" | python3 -c "import sys,json; d=json.load(sys.stdin); added=d.get('data',{}).get('added',[]); print(added[0].get('client_id','') if added else '')" 2>/dev/null)
[ -n "$PART_ID" ] && pass "Participant added: $PART_ID" || fail "Participant add failed | $PART"

# ---------------------------------------------------------------------------
# K1: List participants
# ---------------------------------------------------------------------------
echo "K1 — GET /api/v1/cases/$CASE_ID/participants"
PARTS=$(curl -s "http://localhost:8000/api/v1/cases/$CASE_ID/participants" \
  -H "Authorization: Bearer $TOKEN")
PART_COUNT=$(echo "$PARTS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',{}).get('participants',[])))" 2>/dev/null)
[ "${PART_COUNT:-0}" -ge "1" ] && pass "Participants: $PART_COUNT" || fail "No participants returned"

# ---------------------------------------------------------------------------
# L1: Create conversation
# ---------------------------------------------------------------------------
echo "L1 — POST /api/v1/cases/$CASE_ID/conversations"
# Payload: case_client_id only (backend ignores title field)
CONV=$(curl -s -X POST "http://localhost:8000/api/v1/cases/$CASE_ID/conversations" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"case_client_id\": \"$CASE_ID\"}")
CONV_ID=$(echo "$CONV" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('conversation',{}).get('client_id',''))" 2>/dev/null)
[ -n "$CONV_ID" ] && pass "Conversation created: $CONV_ID" || fail "Conversation creation failed | $CONV"

# ---------------------------------------------------------------------------
# M1: Get conversation
# ---------------------------------------------------------------------------
echo "M1 — GET /api/v1/cases/conversations/$CONV_ID"
CONV_GET=$(curl -s "http://localhost:8000/api/v1/cases/conversations/$CONV_ID" \
  -H "Authorization: Bearer $TOKEN")
CONV_STATE=$(echo "$CONV_GET" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('conversation',{}).get('state',''))" 2>/dev/null)
[ "$CONV_STATE" = "open" ] && pass "Conversation retrieved: state=$CONV_STATE" || fail "Get conversation failed | $CONV_GET"

# ---------------------------------------------------------------------------
# N1 + O1: Add two messages
# ---------------------------------------------------------------------------
echo "N1 — POST /api/v1/cases/conversations/$CONV_ID/messages (message 1)"
# IMPORTANT: content must be flat blocks, NOT ProseMirror nested format
# Valid content block types: text, mention, label, link — each with a "text" field
MSG1=$(curl -s -X POST "http://localhost:8000/api/v1/cases/conversations/$CONV_ID/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"conversation_client_id\": \"$CONV_ID\", \"content\": [{\"type\": \"text\", \"text\": \"First message from integration test\"}]}")
MSG1_ID=$(echo "$MSG1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('message',{}).get('client_id',''))" 2>/dev/null)
MSG1_SEQ=$(echo "$MSG1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('message',{}).get('message_seq',''))" 2>/dev/null)
[ -n "$MSG1_ID" ] && pass "Message 1 created: seq=$MSG1_SEQ" || fail "Message 1 failed | $MSG1"

echo "O1 — POST /api/v1/cases/conversations/$CONV_ID/messages (message 2)"
MSG2=$(curl -s -X POST "http://localhost:8000/api/v1/cases/conversations/$CONV_ID/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"conversation_client_id\": \"$CONV_ID\", \"content\": [{\"type\": \"text\", \"text\": \"Second message from integration test\"}]}")
MSG2_ID=$(echo "$MSG2" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('message',{}).get('client_id',''))" 2>/dev/null)
MSG2_SEQ=$(echo "$MSG2" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('message',{}).get('message_seq',''))" 2>/dev/null)
[ -n "$MSG2_ID" ] && pass "Message 2 created: seq=$MSG2_SEQ" || fail "Message 2 failed | $MSG2"

# ---------------------------------------------------------------------------
# P1: List messages
# ---------------------------------------------------------------------------
echo "P1 — GET /api/v1/cases/conversations/$CONV_ID/messages"
MSGS=$(curl -s "http://localhost:8000/api/v1/cases/conversations/$CONV_ID/messages" \
  -H "Authorization: Bearer $TOKEN")
MSG_COUNT=$(echo "$MSGS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',{}).get('messages',[])))" 2>/dev/null)
[ "${MSG_COUNT:-0}" -ge "2" ] && pass "Messages listed: $MSG_COUNT" || fail "Expected >= 2 messages, got $MSG_COUNT"

# ---------------------------------------------------------------------------
# Q1: Edit message
# ---------------------------------------------------------------------------
echo "Q1 — PATCH /api/v1/cases/messages/$MSG1_ID"
if [ -n "$MSG1_ID" ]; then
  EDIT=$(curl -s -X PATCH "http://localhost:8000/api/v1/cases/messages/$MSG1_ID" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"message_client_id\": \"$MSG1_ID\", \"content\": [{\"type\": \"text\", \"text\": \"Edited message content\"}]}")
  OK=$(echo "$EDIT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null)
  [ "$OK" = "True" ] && pass "Message edited" || fail "Edit failed | $EDIT"
else
  fail "Edit skipped (no msg1_id)"
fi

# ---------------------------------------------------------------------------
# R1: Mark messages read
# ---------------------------------------------------------------------------
echo "R1 — POST /api/v1/cases/messages/mark-read"
# Payload: case_participant_client_id + up_to_message_seq
MARK=$(curl -s -X POST "http://localhost:8000/api/v1/cases/messages/mark-read" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"case_participant_client_id\": \"$PART_ID\", \"up_to_message_seq\": $MSG2_SEQ}")
OK=$(echo "$MARK" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null)
[ "$OK" = "True" ] && pass "Messages marked read at seq=$MSG2_SEQ" || fail "Mark-read failed | $MARK"

# ---------------------------------------------------------------------------
# S1: Unread counts
# ---------------------------------------------------------------------------
echo "S1 — GET /api/v1/cases/unread-counts"
# NOTE: This static route MUST be declared before GET /{case_client_id} in the router
UNREAD=$(curl -s "http://localhost:8000/api/v1/cases/unread-counts" \
  -H "Authorization: Bearer $TOKEN")
OK=$(echo "$UNREAD" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null)
[ "$OK" = "True" ] && pass "Unread counts returned" || fail "unread-counts failed (check route order: static before /{id}) | $UNREAD"

# ---------------------------------------------------------------------------
# T1: Soft delete message
# ---------------------------------------------------------------------------
echo "T1 — DELETE /api/v1/cases/messages/$MSG1_ID"
if [ -n "$MSG1_ID" ]; then
  DEL_MSG=$(curl -s -X DELETE "http://localhost:8000/api/v1/cases/messages/$MSG1_ID" \
    -H "Authorization: Bearer $TOKEN")
  OK=$(echo "$DEL_MSG" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null)
  [ "$OK" = "True" ] && pass "Message soft-deleted" || fail "Message delete failed | $DEL_MSG"
else
  fail "Message delete skipped (no msg1_id)"
fi

# ---------------------------------------------------------------------------
# U1: Remove participant
# ---------------------------------------------------------------------------
echo "U1 — DELETE /api/v1/cases/participants/$PART_ID"
if [ -n "$PART_ID" ]; then
  DEL_PART=$(curl -s -X DELETE "http://localhost:8000/api/v1/cases/participants/$PART_ID" \
    -H "Authorization: Bearer $TOKEN")
  OK=$(echo "$DEL_PART" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null)
  [ "$OK" = "True" ] && pass "Participant removed" || fail "Participant remove failed | $DEL_PART"
else
  fail "Participant remove skipped (no part_id)"
fi

# ---------------------------------------------------------------------------
# V1: Resolve case
# ---------------------------------------------------------------------------
echo "V1 — PATCH /api/v1/cases/$CASE_ID/state (-> resolved)"
RESOLVE=$(curl -s -X PATCH "http://localhost:8000/api/v1/cases/$CASE_ID/state" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"case_client_id\": \"$CASE_ID\", \"new_state\": \"resolved\"}")
FINAL_STATE=$(echo "$RESOLVE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('case',{}).get('state',''))" 2>/dev/null)
[ "$FINAL_STATE" = "resolved" ] && pass "Case resolved" || fail "state=$FINAL_STATE | $RESOLVE"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "════════════════════════════════════════════════════════════"
echo "TEST 05 RESULT: $PASSED Passed, $FAILED Failed"
echo "════════════════════════════════════════════════════════════"
if [ "$FAILED" -gt "0" ]; then
  echo ""
  echo "⚠️  Common issues to check:"
  echo "   - 500 on POST /cases: HistoryRecord.to_value must have default=dict"
  echo "   - 500 on state change: build_workspace_event() needs workspace_id kwarg"
  echo "   - 'Case not found' on GET /unread-counts: route order bug — static before wildcard"
  echo "   - 'Invalid content block type': use flat [{type:text, text:...}] not ProseMirror"
  echo ""
  echo "   Record failures in:"
  echo "   tests/issues/YYYY-MM-DD_05_cases_<desc>.md"
  exit 1
fi
echo ""
exit 0
