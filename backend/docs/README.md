# Backend Docs Workflow

This folder tracks plan lifecycle state for multi-agent backend delivery.

## Folder structure

- `architecture/under_construction/`: plans currently being drafted/reviewed.
- `architecture/implemented_summaries/`: completion summaries for implemented plans.
- `architecture/archives/`: archived plans with timestamp and summary reference.
- `handoff/to_frontend/`: backend plans or outputs for frontend team/agents.
- `handoff/from_frontend/`: incoming frontend requests/plans for backend work.
- `debugging/`: debug plans created after implementation issues are discovered.

## Lifecycle contract

1. Create plan in `architecture/under_construction/`.
2. Review and correct until approved.
3. Implement (same or different agent).
4. Write implementation summary in `architecture/implemented_summaries/`.
5. Archive original plan in `architecture/archives/` with timestamp and summary link.
6. If defects appear, create debug plan in `debugging/` and repeat the same lifecycle.

## Naming convention

- Plan: `PLAN_<slug>_<YYYYMMDD>.md`
- Summary: `SUMMARY_<slug>_<YYYYMMDD>.md`
- Archive record: `ARCHIVE_<slug>_<YYYYMMDD_HHMM>.md`
- Debug plan: `DEBUG_<parent_slug>_<ticket_or_issue>_<YYYYMMDD>.md`

## Traceability rules

- Every summary must reference the originating plan path.
- Every archive record must reference both plan and summary.
- Every debug plan must reference parent plan and parent summary.
- Handoff files must reference source plan or debug plan.

## Templates

Use the templates below to keep artifact shape consistent across agents:

- `architecture/under_construction/TEMPLATE_PLAN.md`
- `architecture/implemented_summaries/TEMPLATE_SUMMARY.md`
- `architecture/archives/TEMPLATE_ARCHIVE_RECORD.md`
- `debugging/TEMPLATE_DEBUG_PLAN.md`
- `handoff/to_frontend/TEMPLATE_HANDOFF_TO_FRONTEND.md`
- `handoff/from_frontend/TEMPLATE_HANDOFF_FROM_FRONTEND.md`
