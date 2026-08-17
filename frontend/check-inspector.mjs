// Dev-only: open the node inspector, expand an exit, and capture the field editor.
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
await new Promise((r) => setTimeout(r, 2500));

// Select the node that collects several fields.
const picked = await page.evaluate(() => {
  const n = [...document.querySelectorAll(".react-flow__node")].find((x) =>
    x.textContent?.includes("collect_details")
  );
  n?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  return !!n;
});
if (!picked) problems.push("collect_details node not found");
await new Promise((r) => setTimeout(r, 900));

// Expand the first exit.
const expanded = await page.evaluate(() => {
  const btn = [...document.querySelectorAll("aside button")].find((x) =>
    x.textContent?.includes("→")
  );
  btn?.click();
  return !!btn;
});
if (!expanded) problems.push("no exit row to expand");
await new Promise((r) => setTimeout(r, 900));

await page.screenshot({ path: `${OUT}/30-edge-editor.png` });

const info = await page.evaluate(() => {
  const aside = document.querySelector("aside");
  return {
    collects: !!aside?.textContent?.includes("Collects"),
    controls: aside?.querySelectorAll("input,select,textarea").length ?? 0,
  };
});
console.log("  edge editor:", JSON.stringify(info));
if (!info.collects) problems.push("field editor did not render");
if (info.controls < 6) problems.push(`expected editable controls, saw ${info.controls}`);

await browser.close();
if (problems.length) {
  console.log("\nPROBLEMS:");
  [...new Set(problems)].forEach((p) => console.log("  -", p));
  process.exit(1);
}
console.log("\nInspector OK.");
