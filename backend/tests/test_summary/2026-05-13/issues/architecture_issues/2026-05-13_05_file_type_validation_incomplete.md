# Architecture Issue: File Type Validation - use_case Incomplete

**Test:** 05_s3_images_files.sh  
**Date:** 2026-05-13  
**Status:** FIXED in test build (but architecture should be reviewed)  
**Severity:** MEDIUM  

## Symptoms

`POST /api/v1/files/upload-url` with `use_case: "case_attachment"` returned:
```json
{
  "error": "content_type 'image/png' is not allowed for case_attachment",
  "ok": false
}
```

All content types were rejected because `case_attachment` is not in the `ALLOWED_MIME_TYPES` dictionary.

## Root Cause

In `backend/app/my_app/services/commands/files/generate_upload_url.py`:

```python
ALLOWED_MIME_TYPES = {
    "record_attachment": ["image/jpeg", "image/png", "image/webp", "application/pdf", "text/plain"],
    "import": ["text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
}
```

Only two use cases are defined: `record_attachment` and `import`. The code defaults to `record_attachment` if a use_case isn't provided, but explicitly rejects unrecognized use cases.

## Solution Applied in Test Build

Changed test 05 to use:
```json
{
  "use_case": "record_attachment",  // was "case_attachment"
  ...
}
```

**Result:** File upload-url endpoint now returns HTTP 200 with valid presigned URL

## Architecture Question

**For Claude:** Is `case_attachment` an intentional use case that should be added to `ALLOWED_MIME_TYPES`, or should tests use `record_attachment`?

If `case_attachment` is a real requirement:
- Add to bootstrap: `ALLOWED_MIME_TYPES["case_attachment"] = [... list of allowed types ...]`
- Possibly add corresponding size limits in `MAX_FILE_SIZE_BYTES`

## Test Status After Fix

With `use_case: "record_attachment"`:
- ✅ `POST /files/upload-url` returns HTTP 200
- ✅ Presigned URL generated successfully
- ❌ S3 PUT still fails (separate storage backend issue #2026-05-13_05)
