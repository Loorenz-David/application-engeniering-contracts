# Cases Endpoint Tests
**Date:** 2026-05-13  
**Session:** 06  
**Test File:** `backend/app/test_cases.py`  
**Router:** `GET|POST|PATCH|DELETE /api/v1/cases`

---

## Summary

| Total | ✅ Pass | ❌ Fail | ⚠️ Warn |
|-------|---------|---------|---------|
| 22    | 22      | 0       | 0       |

All 19 case endpoints fully validated (22 assertions across the workflow).

---

## Issues Found & Fixed During This Session

### Classification for Bootstrap Handoff
- Bootstrap fix candidate: #1 (`HistoryRecord.to_value` default), #2 (`build_workspace_event` signature mismatch), #3 (FastAPI route ordering in cases router)
- Test-harness / contract-clarity: #4 (message content payload used wrong shape in test; API expects flat blocks)

### 1. `HistoryRecord.to_value` — NOT NULL with no default
- **File:** `my_app/models/base/history_record.py`
- **Symptom:** Every `create_case` returned HTTP 500 — `IntegrityError: null value in column "to_value" of relation "cases" violates not-null constraint`
- **Root cause:** `to_value: Mapped[dict] = mapped_column(JSON, nullable=False)` had no `default=`. `Case` extends `HistoryRecord` but no service ever sets `to_value` explicitly — DB rejected the NULL insert.
- **Fix:** Added `default=dict` to `to_value`:
  ```python
  to_value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
  ```
- **Contract note:** Generated models that extend `HistoryRecord` must provide a default for all NOT NULL columns without application-level assignment.
- **Fixed in bootstrap:** ✅ 2026-05-13 — `default=dict` added to `to_value` column in `HistoryRecord` in `phase_02_identity.py`

---

### 2. `build_workspace_event()` — unexpected `workspace_id` kwarg
- **File:** `my_app/services/infra/events/build_event.py`
- **Symptom:** After fixing bug #1, `create_case` returned 500 — `TypeError: build_workspace_event() got an unexpected keyword argument 'workspace_id'`
- **Root cause:** All five case services call `build_workspace_event(case, Event.X, workspace_id=ctx.workspace_id)`, but the original function signature only accepted `(entity, event_name, *, extra)`. Additionally, `Case` has no `.workspace_id` attribute, so a plain `getattr` fallback was required.
- **Fix:** Added `workspace_id: str | None = None` parameter and used `workspace_id or getattr(entity, 'workspace_id', None)`:
  ```python
  def build_workspace_event(
      entity, event_name: str, *, workspace_id: str | None = None, extra: dict | None = None
  ) -> WorkspaceEvent:
      return WorkspaceEvent(
          event_name=event_name,
          client_id=entity.client_id,
          workspace_id=workspace_id or getattr(entity, 'workspace_id', None),
          extra=extra or {},
      )
  ```
- **Contract note:** `build_workspace_event` signature must match every caller in generated services.
- **Fixed in bootstrap:** ✅ 2026-05-13 — `workspace_id: str | None = None` kwarg added to `build_workspace_event()` in `phase_05_realtime.py`; body uses `workspace_id or getattr(entity, "workspace_id", None)`

---

### 3. Route ordering — `GET /unread-counts` shadowed by `GET /{case_client_id}`
- **File:** `my_app/routers/api_v1/cases.py`
- **Symptom:** `GET /api/v1/cases/unread-counts` returned `{"error": "Case not found"}` (HTTP 200 with ok=false)
- **Root cause:** FastAPI registers routes in declaration order. `GET /{case_client_id}` was declared before `GET /unread-counts` — the static path `/unread-counts` was matched as a wildcard value `case_client_id="unread-counts"`, then forwarded to `get_case` which returned not-found.
- **Fix:** Moved `GET /unread-counts` (and `GET ""` / `POST ""`) to be declared **before** `GET /{case_client_id}` in the router file.
- **Contract note:** Same systematic bug as `images.py` (fixed in session 05). All generated routers must ensure static paths and collection-level routes precede wildcard `/{id}` routes.
- **Fixed in bootstrap:** ✅ 2026-05-13 — Route order in `cases.py` corrected in `phase_09_foundation_records.py`: `GET ""` (list) and `GET /unread-counts` now declared before `GET /{case_client_id}`

---

### 4. Content block format mismatch in test
- **Test file:** `backend/app/test_cases.py`
- **Symptom:** `POST /cases/conversations/{id}/messages` returned `"Invalid content block type: 'paragraph'"`
- **Root cause:** Test was sending Tiptap/ProseMirror rich-text format (`{type: "paragraph", content: [{type: "text", text: "..."}]}`). The `validate_content()` function in `services/infra/content.py` expects a flat list of `InputContentTypeEnum` blocks (`text`, `mention`, `label`, `link`) each with a `text` field.
- **Fix:** Updated test content to flat format:
  ```python
  "content": [{"type": "text", "text": "First message from integration test"}]
  ```
- **Contract note:** API docs / contracts must clearly specify the flat block schema rather than implying rich-text nesting.
- **Bootstrap handoff note:** Treat this primarily as contract/documentation precision work unless rich-text payloads are intended feature scope.

---

## Endpoint Results

| ID  | Method | Path                                          | Result | Details                         |
|-----|--------|-----------------------------------------------|--------|---------------------------------|
| A1  | POST   | /cases                                        | ✅     | case created, state=open        |
| B1  | GET    | /cases/{id}                                   | ✅     | state=open                      |
| C1  | GET    | /cases                                        | ✅     | 4 cases returned                |
| D1  | GET    | /cases?state=open                             | ✅     | 3 open cases                    |
| E1  | PATCH  | /cases/{id}                                   | ✅     | type_label updated              |
| F1  | PATCH  | /cases/{id}/state                             | ✅     | state=resolving                 |
| G1  | POST   | /cases/{id}/links                             | ✅     | link created                    |
| H1  | GET    | /cases/{id}/links                             | ✅     | 1 link returned                 |
| I1  | DELETE | /cases/links/{link_id}                        | ✅     | link removed                    |
| J1  | POST   | /cases/{id}/participants                      | ✅     | 1 participant added             |
| K1  | GET    | /cases/{id}/participants                      | ✅     | 1 participant returned          |
| L1  | POST   | /cases/{id}/conversations                     | ✅     | conversation created            |
| M1  | GET    | /cases/conversations/{conv_id}                | ✅     | conversation returned           |
| N1  | POST   | /cases/conversations/{conv_id}/messages       | ✅     | msg seq=1                       |
| O1  | POST   | /cases/conversations/{conv_id}/messages       | ✅     | msg seq=2                       |
| P1  | GET    | /cases/conversations/{conv_id}/messages       | ✅     | 2 messages returned             |
| Q1  | PATCH  | /cases/messages/{msg_id}                      | ✅     | message edited                  |
| R1  | POST   | /cases/messages/mark-read                     | ✅     | last_read_message_seq=2         |
| S1  | GET    | /cases/unread-counts                          | ✅     | unread_counts map returned      |
| T1  | DELETE | /cases/messages/{msg_id}                      | ✅     | message soft-deleted            |
| U1  | DELETE | /cases/participants/{participant_id}           | ✅     | participant removed             |
| V1  | PATCH  | /cases/{id}/state                             | ✅     | state=resolved                  |

---

## Files Modified

| File | Change |
|------|--------|
| `my_app/models/base/history_record.py` | Added `default=dict` to `to_value` column |
| `my_app/services/infra/events/build_event.py` | Added `workspace_id` kwarg to `build_workspace_event()` |
| `my_app/routers/api_v1/cases.py` | Moved `GET /unread-counts` before `GET /{case_client_id}` |
| `backend/app/test_cases.py` | Fixed content block format (`paragraph` → flat `text` blocks) |
