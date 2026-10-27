import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e", workers: 1, timeout: 45000,
  outputDir: "../artifacts/e2e", reporter: "list",
  use: { baseURL: "http://localhost:3091", screenshot: "only-on-failure", trace: "off" },
  projects: [390, 768, 1440].map(width => ({name: `${width}px`, use: {viewport: {width, height: 900}}})),
});
