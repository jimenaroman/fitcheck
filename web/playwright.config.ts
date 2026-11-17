import { defineConfig } from "@playwright/test";

// Demo walkthrough only: replays docs/demo/fixtures/api.json, so no API, token, or model call is involved.
export default defineConfig({
  testDir: "demo",
  testMatch: "*.pw.ts",
  timeout: 60_000,
  reporter: "list",
  use: {
    baseURL: "http://localhost:5181",
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    colorScheme: "light",
  },
  webServer: {
    // A port apart from `npm run dev` (5180). The API address is unreachable on purpose.
    command: "npx vite --port 5181",
    url: "http://localhost:5181",
    env: { VITE_API_URL: "http://demo-api.invalid" },
    reuseExistingServer: false,
  },
});
