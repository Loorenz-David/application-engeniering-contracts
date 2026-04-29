# 22 — File Handling Contract

## Definition

File handling covers upload, download, and display of user files. The frontend uploads files to the backend, which returns a `file_id`. The frontend never stores file URLs — only `file_id` values. Display and download are mediated through API endpoints that enforce workspace ownership.

---

## The seam with the backend

```
Frontend file input
        ↓
Multipart POST to /api/v1/files/upload
        ↓
Backend stores file, returns { file_id, filename, size_bytes, mime_type }
        ↓
file_id is stored on the entity (e.g., invoice.attachment_file_id)
        ↓
Display: GET /api/v1/files/{file_id}/url  (returns short-lived presigned URL)
Download: GET /api/v1/files/{file_id}/download
```

The frontend never receives or stores a direct storage URL (S3, GCS, etc.). It stores `file_id` and always fetches the current URL on demand.

---

## File upload types

```ts
// src/features/files/types.ts
import { z } from 'zod';

export const UploadedFileSchema = z.object({
  file_id: z.string().uuid(),
  filename: z.string(),
  size_bytes: z.number().int().nonnegative(),
  mime_type: z.string(),
});

export type UploadedFile = z.infer<typeof UploadedFileSchema>;

// Client-side validation before upload
export type FileUploadConstraints = {
  maxSizeBytes: number;
  acceptedMimeTypes: string[];
};
```

---

## Upload API function

```ts
// src/features/files/api/upload-file.ts
import { UploadedFileSchema, type UploadedFile } from '@/features/files/types';
import { ApiRequestError } from '@/lib/api-client';
import { env } from '@/lib/env';
import { getAccessToken } from '@/lib/auth-token';

export async function uploadFile(
  file: File,
  onProgress?: (percent: number) => void,
): Promise<UploadedFile> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append('file', file);

    const xhr = new XMLHttpRequest();

    if (onProgress) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const json: unknown = JSON.parse(xhr.responseText);
        const parsed = UploadedFileSchema.safeParse(json);
        if (parsed.success) {
          resolve(parsed.data);
        } else {
          reject(new ApiRequestError(502, 'invalid_response', 'Unexpected upload response.'));
        }
      } else {
        reject(new ApiRequestError(xhr.status, 'upload_failed', `Upload failed: ${xhr.statusText}`));
      }
    };

    xhr.onerror = () => reject(new ApiRequestError(0, 'network_error', 'Upload failed: network error.'));

    xhr.open('POST', `${env.VITE_API_URL}/api/v1/files/upload`);
    xhr.setRequestHeader('Authorization', `Bearer ${getAccessToken() ?? ''}`);
    xhr.send(formData);
  });
}
```

`XMLHttpRequest` is used instead of `fetch` because `fetch` does not expose upload progress events.

---

## Upload hook with progress

```ts
// src/features/files/hooks/use-file-upload.ts
import { useState, useCallback } from 'react';
import { uploadFile } from '@/features/files/api/upload-file';
import type { UploadedFile, FileUploadConstraints } from '@/features/files/types';
import { ApiRequestError } from '@/lib/api-client';

type UploadState =
  | { status: 'idle' }
  | { status: 'uploading'; progress: number }
  | { status: 'success'; file: UploadedFile }
  | { status: 'error'; error: ApiRequestError };

const DEFAULTS: FileUploadConstraints = {
  maxSizeBytes: 10 * 1024 * 1024,  // 10 MB
  acceptedMimeTypes: ['application/pdf', 'image/jpeg', 'image/png', 'image/webp'],
};

export function useFileUpload(constraints: FileUploadConstraints = DEFAULTS) {
  const [state, setState] = useState<UploadState>({ status: 'idle' });

  const upload = useCallback(async (file: File) => {
    // Client-side validation
    if (file.size > constraints.maxSizeBytes) {
      setState({
        status: 'error',
        error: new ApiRequestError(400, 'file_too_large', `File must be under ${Math.round(constraints.maxSizeBytes / 1024 / 1024)} MB.`),
      });
      return null;
    }

    if (!constraints.acceptedMimeTypes.includes(file.type)) {
      setState({
        status: 'error',
        error: new ApiRequestError(400, 'invalid_file_type', `File type ${file.type} is not accepted.`),
      });
      return null;
    }

    setState({ status: 'uploading', progress: 0 });

    try {
      const uploaded = await uploadFile(file, (progress) =>
        setState({ status: 'uploading', progress }),
      );
      setState({ status: 'success', file: uploaded });
      return uploaded;
    } catch (err) {
      const error = err instanceof ApiRequestError
        ? err
        : new ApiRequestError(500, 'unknown', 'Upload failed.');
      setState({ status: 'error', error });
      return null;
    }
  }, [constraints]);

  const reset = useCallback(() => setState({ status: 'idle' }), []);

  return { state, upload, reset };
}
```

---

## File input component

```tsx
// src/features/files/components/FileUploadInput.tsx
import { useRef } from 'react';
import { useFileUpload } from '@/features/files/hooks/use-file-upload';
import type { UploadedFile, FileUploadConstraints } from '@/features/files/types';

type FileUploadInputProps = {
  constraints?: FileUploadConstraints;
  onUpload: (file: UploadedFile) => void;
  label?: string;
};

export function FileUploadInput({ constraints, onUpload, label = 'Upload file' }: FileUploadInputProps) {
  const { state, upload } = useFileUpload(constraints);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const uploaded = await upload(file);
    if (uploaded) onUpload(uploaded);
    if (inputRef.current) inputRef.current.value = '';
  };

  return (
    <div>
      <label className="cursor-pointer">
        <input
          ref={inputRef}
          type="file"
          className="sr-only"
          accept={constraints?.acceptedMimeTypes?.join(',')}
          onChange={handleChange}
          disabled={state.status === 'uploading'}
        />
        <span className="btn-secondary">{label}</span>
      </label>

      {state.status === 'uploading' && (
        <div role="progressbar" aria-valuenow={state.progress} aria-valuemin={0} aria-valuemax={100}>
          Uploading {state.progress}%
        </div>
      )}

      {state.status === 'error' && (
        <p className="text-sm text-red-600">{state.error.message}</p>
      )}
    </div>
  );
}
```

---

## Integration with forms

When a form field accepts a file, the form stores the `file_id` from the upload result:

```tsx
<Controller
  name="attachment_file_id"
  control={form.control}
  render={({ field }) => (
    <FileUploadInput
      onUpload={(file) => field.onChange(file.file_id)}
    />
  )}
/>
```

The form submits `file_id` to the backend — never the file binary.

---

## What file handling must NOT do

- **Never store file URLs in state or in TanStack Query cache.** They are short-lived presigned URLs that expire. Store only `file_id`.
- **Never send files through the standard `apiClient`.** Use `XMLHttpRequest` for upload progress support.
- **Never assume the access token is valid for the full duration of a long upload.** The XHR reads the token at dispatch time; if the token expires mid-upload the response will be a 401. For very large files (>100 MB), consider requesting a short-lived upload token from the backend before initiating the XHR.
- **Never validate only on the backend.** Client-side size and type validation prevents wasted uploads.
- **Never allow unlimited file size.** Always set an explicit `maxSizeBytes` constraint.
- **Never display the raw storage URL to the user.** Display the filename; fetch the presigned URL only when the user clicks download/preview.
