# S3 / Images / Files Endpoint Tests
**Date:** 2026-05-13  
**Session:** 05  
**Test File:** `backend/app/test_s3_images_files.py`  
**Bucket:** `test-bootstrap-local` (eu-north-1)

---

## Summary

| Total | ✅ Pass | ❌ Fail | ⚠️ Warn |
|-------|---------|---------|---------|
| 17    | 17      | 0       | 0       |

All 9 image endpoints + 3 file endpoints + 5 S3 real-upload verifications passed.

---

## Issues Found & Fixed During This Session

### Classification for Bootstrap Handoff
- Bootstrap fix candidate: #1 (`boto3` dependency), #2 (S3 endpoint URL handling), #4 (FastAPI route ordering in images router)
- Test-harness / environment-only: #3 (token permission string normalization for manually crafted JWT in tests)

### 1. `boto3` not installed in venv
- **Symptom:** `generate_upload_url` returned 500 — "unexpected internal error"
- **Root cause:** boto3 dependency missing from venv
- **Fix:** `pip install boto3` in `.venv`
- **Fixed in bootstrap:** ✅ 2026-05-13 — `boto3==1.35.0` added to `requirements.txt` in `phase_01_base.py`

### 2. S3 presigned PUT returned HTTP 307 (redirect)
- **Symptom:** S3 PUT via presigned URL returned 307 (redirect to regional endpoint)
- **Root cause:** `get_storage_client()` in `storage/__init__.py` was instantiating `S3Client` without `endpoint_url`, defaulting to the global S3 endpoint instead of the regional one (`s3.eu-north-1.amazonaws.com`)
- **Fix:** Modified `storage/__init__.py` to derive `endpoint_url` as `https://s3.{region}.amazonaws.com` and pass it to `S3Client()`
- **Fixed in bootstrap:** ✅ 2026-05-13 — `get_storage_client()` in `phase_09_foundation_records.py` now derives `endpoint_url=f"https://s3.{region}.amazonaws.com"` for the `s3` provider

### 3. Token permission format mismatch
- **Symptom:** `GET /{image_client_id}`, `GET /{image_client_id}/download-url`, `DELETE /{image_client_id}`, `POST /{image_client_id}/annotations` all returned 403
- **Root cause:** Test token included permissions like `GET:/api/v1/images/{image_client_id}` but the `BackendPermissionMiddleware` normalizes actual request paths by replacing `client_id` segments (pattern `/[a-z]{2,5}_[A-Z0-9]{10,}`) with `/<client_id>`. Token must use normalized form.
- **Fix:** Updated token permissions to use `/<client_id>` instead of `/{image_client_id}` placeholder
- **Bootstrap handoff note:** This is not a generated backend defect by itself; it is a test-token construction requirement when bypassing normal auth issuance. No bootstrap change needed.

### 4. Route ordering bug: `DELETE /links` shadowed by `DELETE /{image_client_id}`
- **Symptom:** `DELETE /api/v1/images/links` always returned "Image not found"
- **Root cause:** FastAPI route matching is first-match. The wildcard route `DELETE /{image_client_id}` was registered before `DELETE /links`, causing `/links` literal to be captured as `image_client_id="links"`, which then failed the DB lookup.
- **Fix:** Reordered routes in `routers/api_v1/images.py` — `DELETE /links` now appears before `DELETE /{image_client_id}`
- **Note:** This is a contract-generation bug. All static sub-path routes (`/links`, `/reorder`, `/upload-url`, etc.) must be registered before wildcard path param routes (`/{id}`) in FastAPI.
- **Fixed in bootstrap:** ✅ 2026-05-13 — Route order in `images.py` corrected in `phase_09_foundation_records.py`: all static routes (`GET ""`, `DELETE /links`, `POST /reorder`) now registered before wildcard routes (`/{image_client_id}`, etc.)

---

## Test Results

| ID     | Endpoint                                           | Status | Notes |
|--------|----------------------------------------------------|--------|-------|
| A1     | POST /api/v1/images/upload-url (item)              | ✅     | pending_upload created in DB |
| A2     | S3 PUT via presigned URL (item image .webp)        | ✅     | HTTP 200 |
| A3     | POST /api/v1/images/confirm-upload (item)          | ✅     | image + image_link created |
| B1     | Upload+confirm case image 2 (.webp)                | ✅     | HTTP 200 S3 + confirm OK |
| B2     | Upload+confirm case image 3 (.png)                 | ✅     | HTTP 200 S3 + confirm OK |
| C1     | GET /api/v1/images/{id}                            | ✅     | Full image object returned |
| C2     | GET /api/v1/images?entity_type=case&entity_client_id=case_test_001 | ✅ | 5 images returned with link metadata |
| D1     | GET /api/v1/images/{id}/download-url               | ✅     | Presigned GET URL returned with X-Amz signature |
| D2     | S3 GET via presigned download URL                  | ✅     | HTTP 200 — file downloaded |
| E1     | POST /api/v1/images/reorder                        | ✅     | `{"reordered": 2}` returned, sort_order updated |
| F1     | POST /api/v1/images/{id}/annotations               | ✅     | annotation_id=ian_... returned, DB count +1 |
| G1     | DELETE /api/v1/images/links (unlink)               | ✅     | Link removed after route fix |
| H1     | DELETE /api/v1/images/{id} (soft delete)           | ✅     | deleted_at set in DB |
| I1     | POST /api/v1/files/upload-url                      | ✅     | pending_upload created, `expires_in_seconds=300` |
| I2_s3  | S3 PUT via presigned URL (file attachment .png)    | ✅     | HTTP 200 |
| I2     | POST /api/v1/files/confirm-upload                  | ✅     | `{"status":"confirmed", size_bytes: 134607}` |
| I3     | POST /api/v1/files/download-url                    | ✅     | Presigned GET URL returned + HTTP 200 verify |

---

## DB State After Tests

```
pending_uploads: 13 (accumulated across test runs)
images: 9 (2 soft-deleted across runs)
image_links: 8 (some unlinked)
image_annotations: 2
```

---

## Observations

- Storage key pattern for images: `images/{workspace_id}/{entity_type}/{entity_client_id}/{uuid}.ext` ✅
- Storage key pattern for files: `{env}/{workspace_id}/{use_case}/{uuid}.ext` ✅
- Presign TTL: images=900s, files=300s upload / 900s download ✅
- `source_reference` returned as `"s3_image_url"` (not null) ✅
- `storage_provider` returned as `"s3"` ✅
- `last_event.event_type` = `"upload_item_image"` or `"upload_case_image"` depending on entity_type ✅
- File confirm uses `storage_key` in body (not `pending_upload_client_id`) ✅
- File download uses `pending_upload_client_id` in body (POST not GET) ✅
