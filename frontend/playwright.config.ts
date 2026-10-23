import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

// End-to-end tests against a real Studio server with a scripted gateway (no live models,
// STUDIO_FAKE_GATEWAY=1). The fixture repo and vault are rebuilt by e2e/prepare.mjs.
const PORT = 8799;
const TMP = process.env.STUDIO_E2E_TMP ?? mkdtempSync(join(tmpdir(), "aibridge-studio-e2e-"));
process.env.STUDIO_E2E_TMP = TMP;

export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  workers: 1,
  fullyParallel: false,
  reporter: "list",
  // The specs use Czech labels; english.spec.ts switches to en-US itself.
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    locale: "cs-CZ",
    trace: "retain-on-failure",
    channel: process.env.STUDIO_E2E_BROWSER,
  },
  webServer: {
    command: "npm run build && node e2e/prepare.mjs && uv run studio serve",
    url: `http://127.0.0.1:${PORT}/api/health`,
    timeout: 240_000, // cold start: vite build + uv sync can exceed 2 min on Windows
    reuseExistingServer: false,
    env: {
      STUDIO_PORT: String(PORT),
      STUDIO_E2E_TMP: TMP,
      STUDIO_DATA_DIR: join(TMP, "data"),
      STUDIO_VAULT: join(TMP, "vault"),
      STUDIO_FAKE_GATEWAY: "1",
      STUDIO_ROUTE_WAIT: "0",
    },
  },
});
