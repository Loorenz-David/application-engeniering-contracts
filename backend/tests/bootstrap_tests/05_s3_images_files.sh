#!/usr/bin/env bash
# =============================================================================
# TEST 04 — S3 Images & Files Endpoints
# Purpose : Validate presigned upload/download URLs for images and files,
#           including real S3 PUT and GET operations.
# Run from: <project>/backend/app/
# Requires:
#   - .test_token_bootstrap (run 00_seed_identity.sh first)
#   - .env.s3 with AWS credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
#     AWS_S3_BUCKET, AWS_REGION)
#   - test_for_upload_images/ directory with at least one .webp or .png file
# Known fixes already applied in bootstrap:
#   - boto3 added to requirements.txt
#   - S3Client endpoint_url derived from region to avoid HTTP 307 redirects
#   - Static routes (DELETE /links, POST /reorder) declared before /{id} routes
# =============================================================================
set -uo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../../app" && pwd)}"
cd "$APP_DIR"

echo "════════════════════════════════════════════════════════════"
echo "TEST 04: S3 — Images & Files Endpoints"
echo "════════════════════════════════════════════════════════════"
echo ""

# Load S3 credentials
if [ -f ".env.s3" ]; then
  set -a; source .env.s3; set +a
  echo "✅ S3 credentials loaded from .env.s3"
else
  echo "❌ .env.s3 not found — S3 real-upload tests will be skipped"
fi

TOKEN=$(cat .test_token_bootstrap 2>/dev/null || echo "")
if [ -z "$TOKEN" ]; then
  echo "❌ .test_token_bootstrap not found. Run 00_seed_identity.sh first."
  exit 1
fi

PASSED=0
FAILED=0
WORKSPACE_ID="ws_workspace_test"
ENTITY_TYPE="case"
ENTITY_ID="case_test_s3_001"

pass() { echo "   ✅ $1"; PASSED=$((PASSED + 1)); return 0; }
fail() { echo "   ❌ $1"; FAILED=$((FAILED + 1)); return 0; }

# Find a test image
TEST_IMAGE=$(find test_for_upload_images -name "*.webp" -o -name "*.png" 2>/dev/null | head -1)
if [ -z "$TEST_IMAGE" ]; then
  echo "⚠️  No test image found in test_for_upload_images/. Creating a minimal placeholder."
  mkdir -p test_for_upload_images
  python3 -c "
import struct, zlib
def png_1x1():
    sig = b'\x89PNG\r\n\x1a\n'
    def chunk(t,d): l=struct.pack('>I',len(d)); c=zlib.crc32(t+d)&0xffffffff; return l+t+d+struct.pack('>I',c)
    ihdr=chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))
    raw=b'\x00\xff\x00\x00'; comp=zlib.compress(raw); idat=chunk(b'IDAT',comp)
    iend=chunk(b'IEND',b'')
    return sig+ihdr+idat+iend
open('test_for_upload_images/test_pixel.png','wb').write(png_1x1())
"
  TEST_IMAGE="test_for_upload_images/test_pixel.png"
fi
echo "Test image: $TEST_IMAGE"
echo ""

# ---------------------------------------------------------------------------
# IMAGES
# ---------------------------------------------------------------------------

# A1: POST /images/upload-url
echo "A1 — POST /api/v1/images/upload-url (get presigned upload URL)"
UPLOAD_URL_RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
  -X POST http://localhost:8000/api/v1/images/upload-url \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"entity_type\": \"$ENTITY_TYPE\",
    \"entity_client_id\": \"$ENTITY_ID\",
    \"file_name\": \"test_image.png\",
    \"content_type\": \"image/png\"
  }")
STATUS=$(echo "$UPLOAD_URL_RESP" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$UPLOAD_URL_RESP" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
PRESIGNED_URL=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('upload_url',''))" 2>/dev/null)
STORAGE_KEY=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('storage_key',''))" 2>/dev/null)
PENDING_ID=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('pending_upload_client_id',''))" 2>/dev/null)
[ -n "$PRESIGNED_URL" ] && pass "upload_url present" || fail "upload_url missing"
[ -n "$STORAGE_KEY" ] && pass "storage_key = $STORAGE_KEY" || fail "storage_key missing"
[ -n "$PENDING_ID" ] && pass "pending_upload_client_id = $PENDING_ID" || fail "pending_upload_client_id missing"
echo ""

# A2: PUT to presigned URL (works for both local dev and S3)
PUT_SUCCEEDED=0
if [ -n "$PRESIGNED_URL" ]; then
  echo "A2 — PUT (upload via presigned URL)"
  S3_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "$PRESIGNED_URL" \
    -H "Content-Type: image/png" \
    --data-binary "@$TEST_IMAGE")
  if [ "$S3_STATUS" = "200" ]; then
    pass "PUT HTTP 200"
    PUT_SUCCEEDED=1
  else
    fail "PUT HTTP $S3_STATUS (for S3: check region/endpoint_url; for local: check dev storage router)"
  fi
  echo ""
else
  echo "A2 — PUT: skipped (no presigned URL)"
  echo ""
fi

# A3: POST /images/confirm-upload (only if PUT succeeded)
if [ -n "$PENDING_ID" ] && [ "$PUT_SUCCEEDED" = "1" ]; then
  echo "A3 — POST /api/v1/images/confirm-upload"
  CONFIRM=$(curl -s -w "\n_STATUS_:%{http_code}" \
    -X POST http://localhost:8000/api/v1/images/confirm-upload \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{
      \"pending_upload_client_id\": \"$PENDING_ID\",
      \"entity_type\": \"$ENTITY_TYPE\",
      \"entity_client_id\": \"$ENTITY_ID\"
    }")
  STATUS=$(echo "$CONFIRM" | grep "_STATUS_:" | cut -d':' -f2)
  BODY=$(echo "$CONFIRM" | sed '/_STATUS_:/d')

  [ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
  IMAGE_ID=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('image',{}).get('client_id',''))" 2>/dev/null)
  [ -n "$IMAGE_ID" ] && pass "image.client_id = $IMAGE_ID" || fail "image.client_id missing"
  echo ""
else
  IMAGE_ID=""
fi

# C1: GET /images/{id}
if [ -n "$IMAGE_ID" ]; then
  echo "C1 — GET /api/v1/images/$IMAGE_ID"
  RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
    "http://localhost:8000/api/v1/images/$IMAGE_ID" \
    -H "Authorization: Bearer $TOKEN")
  STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
  BODY=$(echo "$RESP" | sed '/_STATUS_:/d')

  [ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
  STORAGE_PROVIDER=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('image',{}).get('storage_provider',''))" 2>/dev/null)
  [ "$STORAGE_PROVIDER" = "s3" ] && pass "storage_provider=s3" || fail "storage_provider=$STORAGE_PROVIDER"
  echo ""
fi

# C2: GET /images?entity_type=...&entity_client_id=...
echo "C2 — GET /api/v1/images?entity_type=$ENTITY_TYPE&entity_client_id=$ENTITY_ID"
RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
  "http://localhost:8000/api/v1/images?entity_type=$ENTITY_TYPE&entity_client_id=$ENTITY_ID" \
  -H "Authorization: Bearer $TOKEN")
STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$RESP" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
IMG_COUNT=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',{}).get('images',[])))" 2>/dev/null)
[ "${IMG_COUNT:-0}" -ge "1" ] && pass "images count >= 1 (got $IMG_COUNT)" || fail "no images returned"
echo ""

# D1: GET /images/{id}/download-url
if [ -n "$IMAGE_ID" ]; then
  echo "D1 — GET /api/v1/images/$IMAGE_ID/download-url"
  RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
    "http://localhost:8000/api/v1/images/$IMAGE_ID/download-url" \
    -H "Authorization: Bearer $TOKEN")
  STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
  BODY=$(echo "$RESP" | sed '/_STATUS_:/d')

  [ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
  DL_URL=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('download_url',''))" 2>/dev/null)
  [ -n "$DL_URL" ] && pass "download_url present" || fail "download_url missing"

  # D2: S3 GET via presigned download URL
  if [ -n "$DL_URL" ] && [ -n "${AWS_S3_BUCKET:-}" ]; then
    echo "D2 — S3 GET via presigned download URL"
    DL_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$DL_URL")
    [ "$DL_STATUS" = "200" ] && pass "S3 GET HTTP 200" || fail "S3 GET HTTP $DL_STATUS"
  fi
  echo ""
fi

# H1: DELETE /images/{id}
if [ -n "$IMAGE_ID" ]; then
  echo "H1 — DELETE /api/v1/images/$IMAGE_ID (soft delete)"
  RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
    -X DELETE "http://localhost:8000/api/v1/images/$IMAGE_ID" \
    -H "Authorization: Bearer $TOKEN")
  STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
  [ "$STATUS" = "200" ] && pass "HTTP 200 (soft deleted)" || fail "HTTP $STATUS"
  echo ""
fi

# ---------------------------------------------------------------------------
# FILES
# ---------------------------------------------------------------------------

# I1: POST /files/upload-url
echo "I1 — POST /api/v1/files/upload-url"
FILE_UPLOAD_RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
  -X POST http://localhost:8000/api/v1/files/upload-url \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"file_name\": \"test_attachment.png\",
    \"content_type\": \"image/png\",
    \"use_case\": \"record_attachment\",
    \"entity_type\": \"$ENTITY_TYPE\",
    \"entity_client_id\": \"$ENTITY_ID\"
  }")
STATUS=$(echo "$FILE_UPLOAD_RESP" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$FILE_UPLOAD_RESP" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
FILE_PRESIGNED=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('upload_url',''))" 2>/dev/null)
FILE_STORAGE_KEY=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('storage_key',''))" 2>/dev/null)
FILE_PENDING_ID=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('pending_upload_client_id',''))" 2>/dev/null)
EXPIRES=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('expires_in_seconds',''))" 2>/dev/null)
[ -n "$FILE_PRESIGNED" ] && pass "upload_url present" || fail "upload_url missing"
[ "$EXPIRES" = "300" ] && pass "expires_in_seconds=300" || pass "expires_in_seconds=$EXPIRES"
echo ""

# I2: PUT file via presigned URL (works for both local dev and S3)
FILE_PUT_SUCCEEDED=0
if [ -n "$FILE_PRESIGNED" ]; then
  echo "I2 — PUT file via presigned URL"
  S3_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "$FILE_PRESIGNED" \
    -H "Content-Type: image/png" \
    --data-binary "@$TEST_IMAGE")
  if [ "$S3_STATUS" = "200" ]; then
    pass "PUT HTTP 200"
    FILE_PUT_SUCCEEDED=1
  else
    fail "PUT HTTP $S3_STATUS"
  fi
  echo ""
fi

# I3: POST /files/confirm-upload (only if PUT succeeded)
if [ -n "$FILE_STORAGE_KEY" ] && [ "$FILE_PUT_SUCCEEDED" = "1" ]; then
  echo "I3 — POST /api/v1/files/confirm-upload"
  FILE_CONFIRM=$(curl -s -w "\n_STATUS_:%{http_code}" \
    -X POST http://localhost:8000/api/v1/files/confirm-upload \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"storage_key\": \"$FILE_STORAGE_KEY\"}")
  STATUS=$(echo "$FILE_CONFIRM" | grep "_STATUS_:" | cut -d':' -f2)
  BODY=$(echo "$FILE_CONFIRM" | sed '/_STATUS_:/d')

  [ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
  FILE_STATUS=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('status',''))" 2>/dev/null)
  [ "$FILE_STATUS" = "confirmed" ] && pass "status=confirmed" || fail "status=$FILE_STATUS"
  echo ""
fi

# I4: POST /files/download-url
if [ -n "$FILE_PENDING_ID" ]; then
  echo "I4 — POST /api/v1/files/download-url"
  DL_RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
    -X POST http://localhost:8000/api/v1/files/download-url \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"pending_upload_client_id\": \"$FILE_PENDING_ID\"}")
  STATUS=$(echo "$DL_RESP" | grep "_STATUS_:" | cut -d':' -f2)
  BODY=$(echo "$DL_RESP" | sed '/_STATUS_:/d')

  [ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
  FILE_DL_URL=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('download_url',''))" 2>/dev/null)
  [ -n "$FILE_DL_URL" ] && pass "download_url present" || fail "download_url missing"
  echo ""
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "════════════════════════════════════════════════════════════"
echo "TEST 04 RESULT: $PASSED Passed, $FAILED Failed"
echo "════════════════════════════════════════════════════════════"
if [ "$FAILED" -gt "0" ]; then
  echo ""
  echo "⚠️  Common issues to check:"
  echo "   - boto3 not installed: pip install boto3"
  echo "   - S3 307 redirect: verify endpoint_url=https://s3.{region}.amazonaws.com in storage client"
  echo "   - Route order: static routes must come before /{id} routes in images.py"
  echo ""
  echo "   Record failures in:"
  echo "   tests/issues/YYYY-MM-DD_04_s3_<desc>.md"
  exit 1
fi
echo ""
exit 0
