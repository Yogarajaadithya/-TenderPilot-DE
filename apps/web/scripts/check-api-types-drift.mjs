#!/usr/bin/env node
/**
 * Fails if lib/api-types.generated.ts no longer matches what the
 * backend's OpenAPI schema would generate right now - i.e. the API
 * contract changed but nobody re-ran `npm run generate-api-types`.
 *
 * The schema is exported directly from the Python app (no live
 * `uvicorn` server required), so this works the same locally and in
 * CI - it needs `uv` and the backend's dependencies installed, not a
 * running process.
 */

import { execFileSync } from "node:child_process";
import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import path from "node:path";

const BACKEND_DIR = path.join(process.cwd(), "..", "..", "backend");
const COMMITTED_PATH = join(process.cwd(), "lib", "api-types.generated.ts");
const isWindows = process.platform === "win32";

const tmpDir = mkdtempSync(join(tmpdir(), "api-types-drift-"));
const freshSchemaPath = join(tmpDir, "openapi.json");
const freshTypesPath = join(tmpDir, "api-types.generated.ts");

try {
  execFileSync(
    "uv",
    ["run", "python", "scripts/export_openapi.py", freshSchemaPath],
    { cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "inherit"], shell: isWindows },
  );

  execFileSync(
    "npx",
    ["openapi-typescript", freshSchemaPath, "-o", freshTypesPath],
    { stdio: ["ignore", "pipe", "inherit"], shell: isWindows },
  );
} catch {
  console.error(
    "\nCould not export the backend's OpenAPI schema to check for drift.\n" +
      "Make sure backend dependencies are installed: uv sync --locked (from backend/)\n",
  );
  rmSync(tmpDir, { recursive: true, force: true });
  process.exit(1);
}

const committed = readFileSync(COMMITTED_PATH, "utf-8");
const fresh = readFileSync(freshTypesPath, "utf-8");
rmSync(tmpDir, { recursive: true, force: true });

if (committed !== fresh) {
  console.error(
    "\nlib/api-types.generated.ts is out of date with the backend's OpenAPI schema.\n" +
      "Run: npm run generate-api-types\n",
  );
  process.exit(1);
}

console.log("api-types.generated.ts matches the backend's OpenAPI schema.");
