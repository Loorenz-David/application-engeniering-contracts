# Test 06 Results Summary — Cases CRUD

**Date:** 2026-05-13  
**Test File:** `tests/06_cases_crud.sh`  
**Result:** 8 Passed, 15 Failed  
**Status:** REQUIRES FIXES (multiple API contract mismatches)  

## Overview

Test 06 validates all cases endpoints: create, read, list, filter, update, state-change, links, participants, conversations, messages, mark-read, unread-counts, soft-delete, remove participant, resolve.

The API layer is mostly working (cases creation/read/list all pass), but several endpoints have payload contract mismatches that cause validation errors.

## Issues Found

### Issue 1: Assertion Counter False-Fail (A1 section)
**Status:** OPEN (script-level)  
**Severity:** MEDIUM  
**Symptom:** Same assertion "Case created..." appears to pass, then duplicate "Expected state=open" fails even though response shows state=open

This is the same pattern as tests 02/04/05. Counter logic bug.

---

### Issue 2: State Change Endpoint Payload Mismatch
**Status:** OPEN (likely script-level, possibly architecture)  
**Severity:** HIGH  
**Endpoint:** `PATCH /api/v1/cases/{case_id}/state`  

**Test sends:**
```json
{"state": "resolving"}
```

**API expects:**
```
Missing: case_client_id
Missing: new_state
```

**Problem:** Test doesn't send case_client_id (redundant, already in URL) or new_state (should use "new_state" not "state" key).

---

### Issue 3: Link Creation Payload Mismatch
**Status:** OPEN (script-level, possibly architecture)  
**Severity:** HIGH  
**Endpoint:** `POST /api/v1/cases/{case_id}/links`  

**Test sends:**
```json
{"linked_case_id": "ca_LINK_TEST_PLACEHOLDER"}
```

**API expects:**
```
Missing: case_client_id
Missing: entity_type
Missing: entity_client_id
Missing: role
```

**Problem:** Payload format doesn't match API contract. Need to understand what "links" really means (linked cases? or case linked to other entities?).

---

### Issue 4: Participants Endpoint Payload Mismatch
**Status:** OPEN (script-level, possibly architecture)  
**Severity:** HIGH  
**Endpoint:** `POST /api/v1/cases/{case_id}/participants`  

**Test sends:**
```json
{"user_id": "usr_user_test"}
```

**API expects:**
```
Missing: case_client_id
Missing: user_ids (plural, array)
```

**Problem:** Endpoint expects array of user_ids, test sends single user_id.

---

### Issue 5: Conversation Creation Payload Mismatch
**Status:** OPEN (script-level)  
**Severity:** HIGH  
**Endpoint:** `POST /api/v1/cases/{case_id}/conversations`  

**Test sends:**
```json
{"title": "Test Conversation"}
```

**API expects:**
```
Missing: case_client_id
```

**Problem:** case_client_id not included in payload.

---

### Issue 6: Mark-Read Endpoint JSON Malformed
**Status:** OPEN (script-level)  
**Severity:** MEDIUM  
**Endpoint:** `POST /api/v1/cases/messages/mark-read`  

**Error:** 
```
JSON decode error at position 49
```

**Problem:** Payload is malformed JSON. Likely missing field or array.

---

## Passed Assertions (8/23)

| Section | Check | Status |
|---------|-------|--------|
| A1 | Case created with state=open | ✅ |
| B1 | Retrieved case state=open | ✅ |
| C1 | Listed cases (count >= 1) | ✅ |
| D1 | Filtered by state=open | ✅ |
| E1 | Updated case type_label | ✅ |
| H1 | Links listed (empty ok) | ✅ |
| I1 | Link delete skipped (no link) | ✅ |
| S1 | Unread counts returned | ✅ |

## Failed Assertions (15/23)

| Section | Endpoint | Issue | Type |
|---------|----------|-------|------|
| A1 | POST /cases | Counter false-fail | Script |
| F1 | PATCH /cases/{id}/state | Payload mismatch (new_state, case_client_id) | Script or Architecture |
| G1 | POST /cases/{id}/links | Payload format unclear | Script or Architecture |
| J1 | POST /cases/{id}/participants | Array vs single value | Script |
| K1 | GET /cases/{id}/participants | Returned empty (due to J1 fail) | Cascading |
| L1 | POST /cases/{id}/conversations | Missing case_client_id | Script |
| M1 | GET /cases/conversations/{id} | No conversation created | Cascading |
| N1 | POST /conversations/{id}/messages | Conversation doesn't exist | Cascading |
| O1 | POST /conversations/{id}/messages | Conversation doesn't exist | Cascading |
| P1 | GET /conversations/{id}/messages | No conversation/messages | Cascading |
| Q1 | PATCH /messages/{id} | Message not created | Cascading |
| R1 | POST /messages/mark-read | Malformed JSON | Script |
| T1 | DELETE /messages/{id} | No message created | Cascading |
| U1 | DELETE /participants/{id} | No participant added | Cascading |
| V1 | PATCH /cases/{id}/state (resolve) | Payload mismatch | Script or Architecture |

## Dependency Chain

```
POST /cases ✅
    ↓
PATCH /cases/state ❌ (payload mismatch)
    ↓
State transitions can't be tested
    ↓
Cascading failures in conversations, messages, etc.

POST /cases/links ❌ (unclear API contract)
    ↓
Links functionality can't be tested

POST /cases/participants ❌ (array vs single value)
    ↓
GET /cases/participants returns empty
    ↓
Participant tests fail
```

## Root Causes (Likely)

1. **Test script payloads don't match actual API** — Many endpoints have been updated but test script wasn't
2. **API contract documentation outdated** — Tests may be following old contract specs
3. **Assertion counter logic** — Same bug across multiple tests (02/04/05/06)

## What's Working ✅

| Component | Status |
|-----------|--------|
| Case creation | ✅ |
| Case retrieval (single/list) | ✅ |
| Case filtering by state | ✅ |
| Case updates (basic fields) | ✅ |
| Unread counts endpoint | ✅ |
| Database persistence | ✅ |

## What Needs Fixing

| Priority | Issue | Component | Impact |
|----------|-------|-----------|--------|
| HIGH | Assertion counter false-fail | Test script | Accurate result reporting |
| HIGH | State change payload mismatch | Test + API contracts | State transitions |
| HIGH | Link creation format unclear | Test + API | Link functionality |
| HIGH | Participants endpoint contract | Test + API | Adding participants |
| MEDIUM | Mark-read JSON malformed | Test script | Message read status |
| MEDIUM | Conversation creation payload | Test script | Conversation flow |

## Recommendation

Test 06 requires significant payload fixes. Before proceeding:

1. **Review API contracts** for each endpoint against actual backend implementation
2. **Fix test 06 payloads** to match current API contracts
3. **Fix assertion counter** bug (same pattern as other tests)
4. **Re-run test 06** to get accurate baseline

Could skip to tests 07-10 to test other functionality while fixing test 06 in parallel, or fix test 06 now and continue.

## Test Statistics

- **Assertions:** 23 total (8 pass, 15 fail)
- **Pass rate:** 35%
- **Cascading failures:** ~9 (due to 6 root cause failures)
- **Script-level issues:** ~5
- **Architecture-level issues:** ~3 (unclear contracts)
