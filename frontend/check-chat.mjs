// Dev-only: exercise the mic-free Chat tester end to end in the browser.
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

// Open the tester, switch to Chat.
await page.evaluate(() =>
  [...document.querySelectorAll("button")].find((b) => b.textContent?.includes("Test agent"))?.click()
);
await new Promise((r) => setTimeout(r, 600));
const switched = await page.evaluate(() => {
  const b = [...document.querySelectorAll("[data-testid='call-panel'] button")].find((x) =>
    x.textContent?.trim().startsWith("Chat")
  );
  b?.click();
  return !!b;
});
if (!switched) problems.push("Chat tab not found");

// The agent opens the conversation by itself.
let greeted = false;
for (let i = 0; i < 40; i++) {
  await new Promise((r) => setTimeout(r, 1500));
  greeted = await page.evaluate(() =>
    /Northside|scheduling assistant|reschedule/i.test(
      document.querySelector("[data-testid='call-panel']")?.textContent ?? ""
    )
  );
  if (greeted) break;
}
if (!greeted) problems.push("agent never opened the chat");

// Reply as the caller and check the graph advances. Assert that the node
// *changed*, not that it reached a particular name — the graph is edited by the
// Copilot during normal use, and a hardcoded node name rots the moment it is.
const startNode = await page.evaluate(
  () => document.querySelector("[data-testid='call-panel']")?.getAttribute("data-node") ?? ""
);
await page.evaluate(() => {
  const input = document.querySelector("[data-testid='call-panel'] input");
  if (!input) return;
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  setter.call(input, "I need to cancel my appointment on Tuesday.");
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
});

let moved = false;
let endNode = startNode;
for (let i = 0; i < 40; i++) {
  await new Promise((r) => setTimeout(r, 1500));
  endNode = await page.evaluate(
    () => document.querySelector("[data-testid='call-panel']")?.getAttribute("data-node") ?? ""
  );
  if (endNode && endNode !== startNode) {
    moved = true;
    break;
  }
}
if (!moved) problems.push("the conversation never advanced to the next node");

await page.screenshot({ path: `${OUT}/40-chat.png` });
console.log(`  greeted=${greeted} advanced=${moved} (${startNode} -> ${endNode})`);

await browser.close();
if (problems.length) {
  console.log("\nPROBLEMS:");
  [...new Set(problems)].forEach((p) => console.log("  -", p));
  process.exit(1);
}
console.log("\nChat tester OK — no microphone required.");
