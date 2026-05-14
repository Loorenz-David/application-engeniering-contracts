# Script Issue: A1 Assertion Counter False-Fail

**Test:** 05_s3_images_files.sh  
**Date:** 2026-05-13  
**Status:** OPEN (script-level fix)  
**Severity:** MEDIUM  

## Symptom

In test output section A1 (POST /api/v1/images/upload-url):
```
   ✅ HTTP 200
   ❌ HTTP 200  ← FALSE FAIL: same assertion passes AND fails
   ✅ upload_url present
```

The assertion "HTTP 200" appears twice with different results (pass and fail).

## Root Cause

Same counter increment logic issue we fixed in tests 02 and 04. The test script likely has a duplicate assertion or broken counter state.

Looking at line in test: `[ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP 200"`

This should only be one assertion, but it's being counted twice somehow.

## Expected Fix

- Audit test 05 for duplicate assertions or counter logic issues
- Ensure each check is counted exactly once
- Apply same stable return pattern used in tests 02/04

## Similar to

- Issue 2026-05-13_02 (health_auth sign-in 500) 
- Issue 2026-05-13_04 (vapid_assertion_logic)
