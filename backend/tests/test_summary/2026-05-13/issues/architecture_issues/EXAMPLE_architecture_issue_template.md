# Issue: [Short Description]

**Date:** YYYY-MM-DD  
**Test Run:** YYYY-MM-DD (folder date)  
**Test File:** `tests/05_cases_crud.sh`  
**Step:** G1 — POST /api/v1/cases/{id}/links  

---

## Symptom

Describe what went wrong. Include the exact failing output:

```
FAIL: G1 — POST /api/v1/cases/{id}/links
HTTP 500 | {"error": "unexpected internal error", "ok": false}
```

---

## Reproduction

Exact command that failed:

```bash
curl -s -X POST http://localhost:8000/api/v1/cases/ca_XXXXX/links \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"linked_case_id": "ca_LINK_TEST_PLACEHOLDER"}'
```

Response:

```json
{
  "error": "unexpected internal error",
  "ok": false
}
```

---

## Root Cause

Describe the root cause. Example:

> `build_workspace_event()` in `services/infra/events/build_event.py` does not accept the `workspace_id` keyword argument. All case services call it with `workspace_id=ctx.workspace_id`, which raises `TypeError` at runtime.

---

## Affected Files

| File | Change Needed |
|------|---------------|
| `my_app/services/infra/events/build_event.py` | Add `workspace_id: str | None = None` parameter |

---

## Fix

See corresponding fix in `fixes/EXAMPLE_fix_template.md` or describe inline:

```python
# Before
def build_workspace_event(entity, event_name: str, *, extra: dict | None = None):
    ...

# After
def build_workspace_event(
    entity, event_name: str, *, workspace_id: str | None = None, extra: dict | None = None
):
    ...
```

---

## Status

- [ ] Reproduced
- [ ] Root cause confirmed
- [ ] Fix applied in test backend
- [ ] Fix applied in bootstrap phase file
- [ ] Re-run test passed

**Fixed in bootstrap:** `backend/architecture/phase_05_realtime.py` — `build_workspace_event()` signature updated  
**Re-run result:** ✅ 22/22 passed after fix
