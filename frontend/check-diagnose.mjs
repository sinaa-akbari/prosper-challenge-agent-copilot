// Dev-only: the Tests tab — structured transcript rendering and the
// "Diagnose failures" hand-off to the Copilot.
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
const note = (ok, msg) => {
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${msg}`);
  if (!ok) problems.push(msg);
};

const browser = await puppeteer.launch({
  acceptInsecureCerts: true,
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
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
await wait(2400);

// Tests tab
await page.evaluate(() =>
  [...document.querySelectorAll("nav button")]
    .find((b) => b.textContent?.trim().startsWith("Tests"))
    ?.click()
);
await wait(1600);

const diagnoseBtn = await page.evaluate(() =>
  [...document.querySelectorAll("button")].some((b) =>
    /^Diagnose \d+ failure/.test(b.textContent?.trim() ?? "")
  )
);
note(diagnoseBtn, "Tests tab shows a 'Diagnose N failures' button");

// Expand the first failing case and show its transcript.
await page.evaluate(() => {
  const rows = [...document.querySelectorAll("button")].filter((b) =>
    b.textContent?.includes("checks")
  );
  rows[0]?.click();
});
await wait(900);
await page.evaluate(() => {
  const s = [...document.querySelectorAll("summary")].find((x) =>
    x.textContent?.includes("Show transcript")
  );
  s?.click();
});
await wait(900);

const transcript = await page.evaluate(() => {
  const text = document.body.innerText;
  return {
    speakers: /\bagent\b/i.test(text) && /\bcaller\b/i.test(text),
    // transition rows render the function and its collected fields as chips
    transitions: /→/.test(text),
    endReason: /ended:\s*(terminal|hangup|max_turns)/i.test(text),
    pres: document.querySelectorAll("pre").length,
  };
});
console.log("  transcript:", JSON.stringify(transcript));
note(transcript.speakers, "transcript labels agent and caller turns");
note(transcript.transitions, "transcript renders state transitions");
note(transcript.endReason, "transcript explains how the call ended");
note(transcript.pres === 0, "transcript is structured markup, not a <pre> dump");

await page.screenshot({ path: `${OUT}/60-tests.png` });

// Hand off to the Copilot.
await page.evaluate(() =>
  [...document.querySelectorAll("button")]
    .find((b) => /^Diagnose \d+ failure/.test(b.textContent?.trim() ?? ""))
    ?.click()
);
await wait(1600);

const handoff = await page.evaluate(() => {
  const text = document.body.innerText;
  return {
    onBuild: !!document.querySelector(".react-flow__node"),
    started: /Reading the failing transcripts/i.test(text) || /Diagnose the failing tests/i.test(text),
  };
});
note(handoff.onBuild, "switches to the Build tab so the diff can be previewed");
note(handoff.started, "Copilot receives the diagnose request");
await page.screenshot({ path: `${OUT}/61-diagnosing.png` });

await browser.close();
if (problems.length) {
  console.log("\nPROBLEMS:");
  [...new Set(problems)].forEach((p) => console.log("  -", p));
  process.exit(1);
}
console.log("\nDiagnose flow OK.");
