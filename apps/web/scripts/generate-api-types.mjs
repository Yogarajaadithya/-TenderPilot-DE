#!/usr/bin/env node
/**
 * Regenerates lib/api-types.generated.ts from the backend's OpenAPI
 * schema. The schema is exported directly from the Python app (no live
 * `uvicorn` server required) via backend/scripts/export_openapi.py,
 * then converted to TypeScript types.
 */

import { execFileSync } from "node:child_process";
import path from "node:path";

const BACKEND_DIR = path.join(process.cwd(), "..", "..", "backend");
const OPENAPI_JSON = path.join(BACKEND_DIR, "openapi.json");
const OUTPUT_PATH = path.join("lib", "api-types.generated.ts");
const isWindows = process.platform === "win32";

console.log("Exporting OpenAPI schema from the backend app...");
execFileSync("uv", ["run", "python", "scripts/export_openapi.py"], {
  cwd: BACKEND_DIR,
  stdio: "inherit",
  shell: isWindows,
});

console.log("Generating TypeScript types...");
execFileSync("npx", ["openapi-typescript", OPENAPI_JSON, "-o", OUTPUT_PATH], {
  stdio: "inherit",
  shell: isWindows,
});
