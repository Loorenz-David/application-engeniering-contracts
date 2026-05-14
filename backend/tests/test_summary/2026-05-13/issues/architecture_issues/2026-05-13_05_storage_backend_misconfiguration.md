# Architecture Issue: Storage Backend Misconfiguration

**Test:** 05_s3_images_files.sh  
**Date:** 2026-05-13  
**Status:** OPEN (Architecture-level fix required in main bootstrap)  
**Severity:** HIGH (S3/file upload flow completely broken)  

## Symptoms

- S3 presigned upload URLs point to `http://localhost:5000/dev/storage/put/...` instead of real AWS S3
- S3 PUT operations return **HTTP 403 Forbidden**
- Image/file confirmation endpoints return **HTTP 422** because files were never uploaded
- Test result: 10 Passed, 8 Failed (vs 6/7 before file type fix)

## Root Cause

The app is configured to use `LocalStorageClient` (development filesystem mock) but:

1. **No storage service running** — There's no handler for `/dev/storage/put` endpoints
2. **Presigned URLs are fake** — `LocalStorageClient.generate_presigned_put_url()` returns custom URLs, not AWS S3 signatures
3. **No endpoint handler** — The FastAPI app doesn't implement `/dev/storage/put` or `/dev/storage/get` routes

### Code Location
- Storage provider selection: `backend/app/my_app/services/infra/storage/__init__.py`
- LocalStorageClient: `backend/task_system/bootstrap/phases/phase_09_foundation_records.py` (lines 90-190)
- Settings: `backend/app/my_app/config.py` → `STORAGE_PROVIDER` environment variable

### Current Config Flow
```
STORAGE_PROVIDER env var
    ↓
get_storage_client() in __init__.py
    ↓
If "local" → LocalStorageClient(base_path=/tmp/...) 
    ↓
generate_presigned_put_url() returns http://localhost:5000/dev/storage/put/{key}
    ↓
Test sends curl PUT to localhost:5000
    ↓
No handler exists → 403 Forbidden
```

## Solution Options

### Option 1: Use Localstack (Recommended for test environment)
- Run `docker run -p 4566:4566 localstack/localstack`
- Set `STORAGE_PROVIDER=localstack`
- Set `STORAGE_ENDPOINT_URL=http://localhost:4566`
- App will use S3Client with localstack endpoint (real S3 signatures)

### Option 2: Implement /dev/storage/* routes in FastAPI
- Add FastAPI routes for `/dev/storage/put/{key}` and `/dev/storage/get/{key}`
- Store files to `LOCAL_STORAGE_PATH`
- Allow test to PUT/GET files locally
- **Simpler but less realistic than Option 1**

### Option 3: Use real AWS S3 (production-like)
- Provide valid AWS credentials via `.env.s3`
- Set `STORAGE_PROVIDER=s3`
- Set AWS region, bucket, access keys in config
- Most realistic but slower for tests

## Affected Tests
- **05_s3_images_files.sh** — All S3 operations fail
- Tests 06+ that depend on file uploads

## Also Found: File Type Validation Issue

While debugging, discovered: `POST /files/upload-url` rejects all content types because only `record_attachment` and `import` use cases are defined. Test was using `case_attachment` which isn't configured.

**Fix Applied in Test Build:** Updated test to use `record_attachment` instead  
**Architecture Follow-up:** Need to add `case_attachment` to `ALLOWED_MIME_TYPES` in main bootstrap if that's intentional

---

## Test Impact Summary

| Endpoint | Status | Reason |
|----------|--------|--------|
| POST /images/upload-url | ✅ Works | API layer works fine |
| PUT presigned image URL | ❌ 403 | No storage service running |
| POST /images/confirm-upload | ❌ 422 | File never uploaded due to PUT 403 |
| POST /files/upload-url | ✅ Works (after use_case fix) | API layer works fine |
| PUT presigned file URL | ❌ 403 | No storage service running |
| POST /files/confirm-upload | ❌ 422 | File never uploaded due to PUT 403 |

---

## Recommended Next Steps

1. **For Claude (main bootstrap):**
   - Choose storage backend option (recommend Localstack)
   - Update bootstrap to configure storage provider correctly
   - Update docker-compose or scripts to start required services

2. **For test build:** Already fixed content-type validation issue; awaiting storage backend implementation
