import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const fromHere = (path: string) => fileURLToPath(new URL(path, import.meta.url));
const FIXTURE = JSON.parse(readFileSync(fromHere("../../docs/demo/fixtures/api.json"), "utf-8")) as Record<
  string,
  { type: "json" | "text"; body: unknown }
>;
const POSTING = readFileSync(fromHere("../../docs/demo/fixtures/demo_posting.txt"), "utf-8");
const SHOTS = fromHere("../../docs/demo/screenshots/");
const VIDEO = fromHere("../../docs/demo/video/walkthrough.webm");
const API = "http://demo-api.invalid";

/** Answers every API request from the fixture; anything else leaving the page is blocked and fails the run. */
async function serveFixture(page: Page, unexpected: string[], delays: Record<string, number>) {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === "localhost") return route.continue();
    if (url.origin !== API) {
      unexpected.push(`blocked ${url.href}`);
      return route.abort();
    }
    const key = `${route.request().method()} ${url.pathname}`;
    const saved = FIXTURE[key];
    if (!saved) {
      unexpected.push(`no fixture for ${key}`);
      return route.fulfill({ status: 404, json: { detail: "not in the demo fixture" } });
    }
    if (delays[key]) await new Promise((done) => setTimeout(done, delays[key])); // keeps the loading screen up
    return route.fulfill(
      saved.type === "json" ? { json: saved.body } : { body: String(saved.body), contentType: "text/plain" },
    );
  });
}

const pause = (page: Page, ms = 900) => page.waitForTimeout(ms); // so the video is watchable
/** Tall shots are stitched together, so the fixed tab bar would land mid-image: hide it for those. */
async function fullShot(page: Page, file: string, selector?: string) {
  const style = await page.addStyleTag({ content: ".tabbar { display: none !important; }" });
  const path = `${SHOTS}${file}.png`;
  await (selector ? page.locator(selector).screenshot({ path }) : page.screenshot({ path, fullPage: true }));
  await style.evaluate((node) => node.remove());
}

const tab = (page: Page, name: string) => page.getByRole("navigation", { name: "Main" }).getByRole("button", { name });

test("walkthrough: log in, home, loading, check a posting, history, gaps, resume", async ({ browser }) => {
  const context = await browser.newContext({ recordVideo: { dir: fromHere("../test-results/video/"), size: { width: 390, height: 844 } } });
  const page = await context.newPage();
  const unexpected: string[] = [];
  const delays: Record<string, number> = {};
  await serveFixture(page, unexpected, delays);

  await page.goto("/");
  await expect(page.getByPlaceholder("Access token")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}01-login.png` });
  await page.getByPlaceholder("Access token").fill("demo-token"); // fake; shown only as dots
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByText("Today's matches")).toBeVisible();
  await page.waitForLoadState("networkidle");
  await pause(page);
  await page.screenshot({ path: `${SHOTS}08-home.png` }); // the tab bar stays in this one

  delays["GET /auth/check"] = 1500; // logged in: a reload shows the loading splash while the token is checked
  await page.reload();
  await expect(page.getByRole("status")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}09-loading.png` });
  delete delays["GET /auth/check"];
  await expect(tab(page, "Check")).toBeVisible();
  await pause(page);

  await tab(page, "Check").click();
  await page.getByLabel("Job posting").fill(POSTING);
  await pause(page);
  await page.screenshot({ path: `${SHOTS}02-paste-posting.png` });
  await page.getByRole("button", { name: /^Check \(/ }).click();
  await expect(page.getByText("Full card")).toBeVisible();
  await pause(page, 1500);
  await fullShot(page, "03-fit-card", "article.result");
  await page.getByText("Full card").click();
  await pause(page);
  await fullShot(page, "04-full-card");

  for (const [name, file] of [["History", "05-history"], ["Gaps", "06-gaps"], ["Resume", "07-resume"]]) {
    await tab(page, name).click();
    await page.waitForLoadState("networkidle");
    await pause(page, 1200);
    await fullShot(page, file);
  }

  await context.close();
  await page.video()!.saveAs(VIDEO);
  expect(unexpected).toEqual([]);
});
