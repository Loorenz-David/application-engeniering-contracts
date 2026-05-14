# Fix: [Short Description]

**Date:** YYYY-MM-DD  
**Issue Ref:** `issues/EXAMPLE_issue_template.md`  
**Test File:** `tests/05_cases_crud.sh`  
**Step Fixed:** G1 — POST /api/v1/cases/{id}/links  

---

## Summary

One-line description of what was fixed and why.

> Added `workspace_id` kwarg to `build_workspace_event()` so all case services can pass `workspace_id=ctx.workspace_id` without a `TypeError`.

---

## Files Changed

### 1. Test backend (local runtime fix)

**File:** `run_test/bootstrap_test_full_build/backend/app/my_app/services/infra/events/build_event.py`

```python
# Before
def build_workspace_event(
    entity, event_name: str, *, extra: dict | None = None
) -> WorkspaceEvent:
    return WorkspaceEvent(
        event_name=event_name,
        client_id=entity.client_id,
        workspace_id=getattr(entity, "workspace_id", None),
        extra=extra or {},
    )


# After
def build_workspace_event(
    entity, event_name: str, *, workspace_id: str | None = None, extra: dict | None = None
) -> WorkspaceEvent:
    return WorkspaceEvent(
        event_name=event_name,
        client_id=entity.client_id,
        workspace_id=workspace_id or getattr(entity, "workspace_id", None),
        extra=extra or {},
    )
```

### 2. Bootstrap phase file (propagated to generator)

**File:** `backend/architecture/phase_05_realtime.py`

- Located the `build_event.py` generation block
- Applied the same signature change so all future builds include the fix

---

## Verification

Test re-run after fix:

```bash
cd run_test/bootstrap_test_full_build/backend/app
source .venv/bin/activate
export APP_ENV=development
bash ../../tests/05_cases_crud.sh
```

Result:

```
TEST 05 RESULT: 22 Passed, 0 Failed
```

---

## Notes

- This fix is safe to apply without a DB migration.
- No API contract changes — caller payloads unchanged.
- Bootstrap contract note: generated `build_workspace_event` must match all caller signatures in service layer.
