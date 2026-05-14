# Script Issue: Test 06 API Payload Mismatches

**Test:** 06_cases_crud.sh  
**Date:** 2026-05-13  
**Status:** OPEN (script-level fixes required)  
**Severity:** HIGH  

## Issues Summary

Multiple test endpoints send payloads that don't match the actual API contracts, causing validation errors.

---

## Issue 1: State Change Endpoint Payload

**Endpoint:** `PATCH /api/v1/cases/{case_id}/state`  

**Test sends:**
```bash
curl -X PATCH "http://localhost:8000/api/v1/cases/$CASE_ID/state" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"state": "resolving"}'
```

**API error:**
```
Missing field: case_client_id
Missing field: new_state
```

**Fix needed:**
- Add `case_client_id` to payload (same as URL param, but API expects it)
- Change `"state"` key to `"new_state"`

**Correct payload:**
```json
{
  "case_client_id": "ca_...",
  "new_state": "resolving"
}
```

---

## Issue 2: Participants Add Endpoint Payload

**Endpoint:** `POST /api/v1/cases/{case_id}/participants`  

**Test sends:**
```bash
curl -X POST "http://localhost:8000/api/v1/cases/$CASE_ID/participants" \
  -d '{"user_id": "usr_user_test"}'
```

**API error:**
```
Missing field: case_client_id
Missing field: user_ids (expects array, not single user_id)
```

**Fix needed:**
- Add `case_client_id`
- Change `user_id` (singular) to `user_ids` (array)

**Correct payload:**
```json
{
  "case_client_id": "ca_...",
  "user_ids": ["usr_user_test"]
}
```

---

## Issue 3: Conversation Creation Payload

**Endpoint:** `POST /api/v1/cases/{case_id}/conversations`  

**Test sends:**
```bash
curl -X POST "http://localhost:8000/api/v1/cases/$CASE_ID/conversations" \
  -d '{"title": "Test Conversation"}'
```

**API error:**
```
Missing field: case_client_id
```

**Fix needed:**
- Add `case_client_id` to payload

**Correct payload:**
```json
{
  "case_client_id": "ca_...",
  "title": "Test Conversation"
}
```

---

## Issue 4: Link Creation Endpoint Payload

**Endpoint:** `POST /api/v1/cases/{case_id}/links`  

**Test sends:**
```bash
curl -X POST "http://localhost:8000/api/v1/cases/$CASE_ID/links" \
  -d '{"linked_case_id": "ca_LINK_TEST_PLACEHOLDER"}'
```

**API error:**
```
Missing field: case_client_id
Missing field: entity_type
Missing field: entity_client_id
Missing field: role
```

**Problem:** Completely unclear what this endpoint expects. The payload format doesn't match any of the error fields.

**Investigation needed:**
- What does "links" endpoint do? (Links between cases? Cases linked to other entities?)
- What should be the actual payload format?
- What are valid values for entity_type and role?

**Tentative fix:**
```json
{
  "case_client_id": "ca_...",
  "entity_type": "case",  // or something else?
  "entity_client_id": "ca_linked_id",
  "role": "related"  // or something else?
}
```

But need to clarify with backend team.

---

## Issue 5: Mark-Read Endpoint Malformed JSON

**Endpoint:** `POST /api/v1/cases/messages/mark-read`  

**Test sends malformed JSON:** Position 49 error suggests truncated or incomplete payload

**Fix needed:**
- Debug the exact payload being sent
- Ensure proper JSON format
- Include required fields (message_ids? message_client_ids?)

---

## Issue 6: Assertion Counter False-Fail (A1 section)

**Similar to tests 02, 04, 05**  
- Assertion "Case created" shows as both pass and fail
- Counter logic issue

---

## Fix Strategy

1. **Compare test payloads against actual backend** (`my_app/services/commands/cases/`, etc.)
2. **Update all payload formats** to match current API contracts
3. **Test each endpoint independently** before running full test 06
4. **Document payload formats** for future maintenance
5. **Fix assertion counter** using pattern from previous tests

---

## Reference

Backend service files to check:
- `backend/app/my_app/services/commands/cases/change_state.py`
- `backend/app/my_app/services/commands/cases/add_participants.py`
- `backend/app/my_app/services/commands/cases/create_conversation.py`
- `backend/app/my_app/services/commands/cases/create_link.py` (if exists)
- `backend/app/my_app/services/commands/cases/mark_messages_read.py`
