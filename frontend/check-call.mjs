// Dev-only: place a real browser test call against the running server using a
// fake mic, and confirm the pipeline connects and the agent speaks its greeting.
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
  args: [
    "--no-sandbox",
    "--ignore-certificate-errors",
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
  ],
});
const page = await browser.newPage();
await page.setViewport({ width: 1680, height: 1000 });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 200)}`));

await page.evaluateOnNewDocument(() =>
  localStorage.setItem("composer.agent", "northside-scheduling")
);
await page.goto(BASE, { waitUntil: "networkidle2" });
await new Promise((r) => setTimeout(r, 2000));

// Open the call panel, then start the call.
await page.evaluate(() =>
  [...document.querySelectorAll("button")]
    .find((b) => b.textContent?.includes("Test agent"))
    ?.click()
);
await new Promise((r) => setTimeout(r, 800));
const started = await page.evaluate(() => {
  const b = [...document.querySelectorAll("button")].find((x) =>
    x.textContent?.trim().startsWith("Start call")
  );
  b?.click();
  return !!b;
});
if (!started) problems.push("Start call button not found");

console.log("  connecting and waiting for the agent to speak…");
let sawAgentTurn = false;
let live = false;
// Scope every read to the call panel — the graph canvas contains the same words.
for (let i = 0; i < 40; i++) {
  await new Promise((r) => setTimeout(r, 1500));
  const state = await page.evaluate(() => {
    const panel = document.querySelector("[data-testid='call-panel']");
    if (!panel) return null;
    return {
      callState: panel.getAttribute("data-call-state"),
      text: panel.textContent ?? "",
    };
  });
  if (!state) {
    problems.push("call panel disappeared");
    break;
  }
  if (state.callState === "error") {
    problems.push(`call panel errored: ${state.text.slice(0, 160)}`);
    break;
  }
  live ||= state.callState === "live";
  // A transcript turn only renders once TTS text has come back over the wire.
  if (live && /Northside|scheduling assistant|reschedule/i.test(state.text)) {
    sawAgentTurn = true;
    break;
  }
}

await page.screenshot({ path: `${OUT}/20-call.png` });
console.log(`  live=${live} agentSpoke=${sawAgentTurn}`);
if (!live) problems.push("call never reached the live state");
if (!sawAgentTurn) problems.push("agent never spoke (no transcript turn)");

// The debug timeline is the thing we reach for when a call misbehaves, so it
// gets the same coverage as the call itself.
await new Promise((r) => setTimeout(r, 2500)); // let TTS finish and report
const traced = await page.evaluate(() => {
  const b = [...document.querySelectorAll("[data-testid='call-panel'] button")].find(
    (x) => x.textContent?.trim() === "Trace"
  );
  b?.click();
  return !!b;
});
if (!traced) problems.push("no Trace toggle on the call panel");
await new Promise((r) => setTimeout(r, 900));

const trace = await page.evaluate(() => {
  const panel = document.querySelector("[data-testid='call-panel']");
  const text = panel?.textContent ?? "";
  return {
    hasWebrtc: text.includes("webrtc"),
    hasTts: text.includes("tts"),
    silent: text.includes("silent audio"),
  };
});
console.log(`  trace: ${JSON.stringify(trace)}`);
if (!trace.hasWebrtc || !trace.hasTts) problems.push("trace view is missing events");
if (trace.silent) problems.push("trace reports silent audio — TTS produced no sound");
await page.screenshot({ path: `${OUT}/21-trace.png` });

await browser.close();
if (problems.length) {
  console.log("\nPROBLEMS:");
  [...new Set(problems)].forEach((p) => console.log("  -", p));
  process.exit(1);
}
console.log("\nVoice call OK — WebRTC connected and the agent spoke.");
