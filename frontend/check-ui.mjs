// Dev-only smoke check: render the built UI in real Chrome, click through every
// tab, and fail loudly on console errors, page errors, or failed requests.
// `node check-ui.mjs [outputDir]`
import puppeteer from "puppeteer-core";
import { existsSync, mkdirSync } from "node:fs";

// Chrome lives somewhere different on every machine, and a hardcoded Windows
// path makes every one of these checks dead on a Mac. CHROME_PATH overrides.
const CHROME =
  process.env.CHROME_PATH ??
  [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "/usr/bin/google-chrome",
  ].find((p) => existsSync(p));
const BASE = process.env.BASE ?? "https://localhost:7860";
const OUT = process.argv[2] ?? "./shots";
mkdirSync(OUT, { recursive: true });

const problems = [];

const browser = await puppeteer.launch({
  acceptInsecureCerts: true,   // self-signed dev cert
  executablePath: CHROME,
  headless: "new",
  args: ["--no-sandbox", "--ignore-certificate-errors", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1680, height: 1000 });

page.on("console", (m) => {
  if (m.type() === "error") problems.push(`console: ${m.text().slice(0, 300)}`);
});
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 300)}`));
page.on("requestfailed", (r) => {
  const url = r.url();
  if (url.startsWith(BASE)) problems.push(`requestfailed: ${url} ${r.failure()?.errorText}`);
});

async function shot(name) {
  await new Promise((r) => setTimeout(r, 1400));
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log(`  captured ${name}`);
}

// Pin the demo agent so a scratch agent left over from manual clicking
// doesn't change what the check is looking at.
await page.evaluateOnNewDocument(() =>
  localStorage.setItem("composer.agent", "northside-scheduling")
);
await page.goto(BASE, { waitUntil: "networkidle2", timeout: 45000 });
await new Promise((r) => setTimeout(r, 2000));

// The graph must actually render nodes, not just an empty shell.
const nodeCount = await page.$$eval(".react-flow__node", (n) => n.length);
console.log(`  graph nodes rendered: ${nodeCount}`);
if (nodeCount === 0) problems.push("graph rendered 0 nodes");

await shot("01-build");

// Click a node to open the inspector.
const node = await page.$(".react-flow__node");
if (node) {
  await node.click();
  await shot("02-inspector");
  const inspector = await page.$$eval("*", (els) =>
    els.some((e) => e.textContent?.trim() === "Instructions")
  );
  if (!inspector) problems.push("node inspector did not open");
  await page.keyboard.press("Escape");
}

// Walk the rail.
for (const [label, name] of [
  ["Tests", "03-tests"],
  ["Issues", "04-issues"],
  ["History", "05-history"],
  ["Build", "06-build-again"],
]) {
  const clicked = await page.evaluate((text) => {
    const btn = [...document.querySelectorAll("nav button")].find(
      (b) => b.textContent?.trim().startsWith(text)
    );
    if (btn) btn.click();
    return !!btn;
  }, label);
  if (!clicked) problems.push(`rail button '${label}' not found`);
  else await shot(name);
}

// Issues should have loaded the mined data.
await page.evaluate(() => {
  [...document.querySelectorAll("nav button")]
    .find((b) => b.textContent?.trim().startsWith("Issues"))
    ?.click();
});
await new Promise((r) => setTimeout(r, 1500));
const issueCount = await page.evaluate(
  () => document.body.innerText.match(/critical|high|medium/gi)?.length ?? 0
);
console.log(`  issue severity chips found: ${issueCount}`);
if (issueCount === 0) problems.push("issues panel rendered no issues");

// Expand one issue to check evidence rendering.
await page.evaluate(() => {
  const b = [...document.querySelectorAll("button")].find((x) =>
    x.textContent?.includes("Agent") && x.textContent?.includes("call")
  );
  b?.click();
});
await shot("07-issue-expanded");

await browser.close();

if (problems.length) {
  console.log("\nPROBLEMS:");
  [...new Set(problems)].forEach((p) => console.log("  -", p));
  process.exit(1);
}
console.log("\nUI check passed with no console or page errors.");
