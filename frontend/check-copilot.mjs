// Dev-only: drive the Issues -> "Fix with Copilot" -> diff-preview flow in real
// Chrome and capture it. Stops short of Apply so the demo state stays at v1.
import puppeteer from "puppeteer-core";
import { existsSync, mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "https://localhost:7860";
// Chrome lives somewhere different on every machine, and a hardcoded Windows
// path makes every one of these checks dead on a Mac. CHROME_PATH overrides.
const CHROME =
  process.env.CHROME_PATH ??
  [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "/usr/bin/google-chrome",
  ].find((p) => existsSync(p));
const OUT = process.argv[2] ?? "./shots";
mkdirSync(OUT, { recursive: true });

const problems = [];
const browser = await puppeteer.launch({
  acceptInsecureCerts: true,   // self-signed dev cert
  executablePath: CHROME,
  headless: "new",
  args: ["--no-sandbox", "--ignore-certificate-errors"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1680, height: 1000 });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 200)}`));
page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text().slice(0, 200)}`));

await page.evaluateOnNewDocument(() =>
  localStorage.setItem("composer.agent", "northside-scheduling")
);
await page.goto(BASE, { waitUntil: "networkidle2" });
await new Promise((r) => setTimeout(r, 2000));

// Issues tab
await page.evaluate(() =>
  [...document.querySelectorAll("nav button")]
    .find((b) => b.textContent?.trim().startsWith("Issues"))
    ?.click()
);
await new Promise((r) => setTimeout(r, 1500));

// Expand the emergency issue and hit "Fix with Copilot"
const expanded = await page.evaluate(() => {
  const b = [...document.querySelectorAll("button")].find((x) =>
    x.textContent?.includes("emergency symptoms")
  );
  b?.click();
  return !!b;
});
if (!expanded) problems.push("could not find the emergency issue row");
await new Promise((r) => setTimeout(r, 700));

const fixed = await page.evaluate(() => {
  const b = [...document.querySelectorAll("button")].find((x) =>
    x.textContent?.trim().startsWith("Fix with Copilot")
  );
  b?.click();
  return !!b;
});
if (!fixed) problems.push("could not find the Fix with Copilot button");

console.log("  waiting for the Copilot (up to 4 min)…");
let ready = false;
for (let i = 0; i < 120; i++) {
  await new Promise((r) => setTimeout(r, 2000));
  ready = await page.evaluate(() =>
    [...document.querySelectorAll("button")].some((b) => b.textContent?.trim() === "Apply")
  );
  if (ready) break;
}
if (!ready) problems.push("no proposal appeared within the timeout");

await new Promise((r) => setTimeout(r, 1500));
await page.screenshot({ path: `${OUT}/10-proposal.png` });
console.log("  captured 10-proposal");

const preview = await page.evaluate(() => ({
  banner: document.body.innerText.toLowerCase().includes("proposed"),
  nodes: document.querySelectorAll(".react-flow__node").length,
  added: document.body.innerText.match(/\badded\b/g)?.length ?? 0,
  ops: [...document.querySelectorAll("button")]
    .find((b) => /change(s)?$/.test(b.textContent?.trim() ?? ""))
    ?.textContent?.trim(),
}));
console.log("  preview:", JSON.stringify(preview));
if (!preview.banner) problems.push("preview banner missing");
if (preview.nodes <= 4) problems.push(`expected new nodes in the preview, saw ${preview.nodes}`);

await browser.close();
if (problems.length) {
  console.log("\nPROBLEMS:");
  [...new Set(problems)].forEach((p) => console.log("  -", p));
  process.exit(1);
}
console.log("\nCopilot preview flow OK.");
