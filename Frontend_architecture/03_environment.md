# 03 — Environment Contract

## Definition

Environment variables configure the app for each deployment target (local, staging, production). This contract defines how variables are named, typed, validated, and consumed.

---

## Vite env var rules

Vite only exposes variables prefixed with `VITE_` to client-side code via `import.meta.env`. Variables without the prefix are only available in Vite config files — never in the app bundle.

```
VITE_API_URL=https://api.myapp.com       ← available in browser code
DATABASE_URL=postgres://...               ← NEVER exposed to browser
SECRET_KEY=...                            ← NEVER exposed to browser
```

Never put secrets, database credentials, or API keys that grant write access into `VITE_`-prefixed variables. Those belong in backend environment variables only.

---

## Files

```
.env                  ← shared defaults (committed, no secrets)
.env.local            ← local developer overrides (gitignored)
.env.staging          ← staging values (committed, no secrets)
.env.production       ← production values (committed, no secrets)
```

`.env.local` is gitignored. All other `.env.*` files are committed. Secrets go in CI/CD secrets management — never in committed `.env` files.

---

## Validation at startup

All environment variables are validated with Zod when the app boots. An invalid or missing variable crashes loudly at startup rather than causing a silent failure at runtime.

```ts
// src/lib/env.ts
import { z } from 'zod';

const EnvSchema = z.object({
  VITE_API_URL: z.string().url(),
  VITE_WS_URL: z.string().url().optional(),
  VITE_APP_ENV: z.enum(['development', 'staging', 'production']).default('development'),
  VITE_APP_VERSION: z.string().min(1).default('dev'),
  VITE_SENTRY_DSN: z.string().url().optional(),
  VITE_ANALYTICS_ENABLED: z.enum(['true', 'false']).default('false').transform((v) => v === 'true'),
});

const parsed = EnvSchema.safeParse(import.meta.env);

if (!parsed.success) {
  console.error('Invalid environment configuration:');
  console.error(parsed.error.flatten().fieldErrors);
  throw new Error('Environment validation failed. Check your .env file.');
}

export const env = parsed.data;
```

This file is imported once in `src/app/App.tsx` or `src/main.tsx` before any other module. The rest of the app imports `env` from `@/lib/env` — never reads `import.meta.env` directly.

`import.meta.env` values are raw strings. `env` is the parsed application config. Transform boolean and numeric values in `EnvSchema` so application code consumes the correct type.

```ts
// Correct — typed, validated
import { env } from '@/lib/env';
const apiUrl = env.VITE_API_URL;

// Wrong — untyped, bypasses validation
const apiUrl = import.meta.env.VITE_API_URL;
```

---

## TypeScript augmentation

Augment `ImportMetaEnv` so TypeScript knows the env var shapes before validation runs:

```ts
// src/vite-env.d.ts
/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string;
  readonly VITE_WS_URL?: string;
  readonly VITE_APP_ENV: 'development' | 'staging' | 'production';
  readonly VITE_APP_VERSION?: string;
  readonly VITE_SENTRY_DSN?: string;
  readonly VITE_ANALYTICS_ENABLED?: 'true' | 'false';
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
```

---

## Path aliases

Configure path aliases in both `vite.config.ts` and `tsconfig.json` to avoid relative import hell:

```ts
// vite.config.ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
```

```json
// tsconfig.json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"]
    }
  }
}
```

The `@/` alias is the only permitted alias. Additional aliases (e.g., `@components/`, `@features/`) are forbidden — they duplicate the folder structure contract and create confusion when moving files.

---

## Feature detection vs environment detection

Prefer feature flags or config values over environment name checks:

```ts
// Wrong — branches on environment name
if (env.VITE_APP_ENV === 'production') {
  enableAnalytics();
}

// Correct — parsed config value, boolean after EnvSchema transform
if (env.VITE_ANALYTICS_ENABLED) {
  enableAnalytics();
}
```

Environment names leak deployment topology into application logic. Feature flags decouple them.

---

## What env config must NOT do

- **Never read `import.meta.env` outside of `src/lib/env.ts`.** All other files import `env` from `@/lib/env`.
- **Never put secrets in any `VITE_`-prefixed variable.** They will be visible in the browser bundle.
- **Never default a missing required variable to a fallback string.** A missing `VITE_API_URL` should throw, not silently use `localhost`.
- **Never consume raw string feature flags in app code.** Transform them in `EnvSchema` and read the parsed `env` value.
- **Never commit `.env.local`.** It is in `.gitignore` for a reason — it contains local developer overrides.
- **Never branch on `process.env.NODE_ENV` directly.** Use `env.VITE_APP_ENV` which is validated and typed.
