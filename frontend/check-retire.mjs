// Dev-only: the retire-a-broken-test path in the proposal card.
//
// The Copilot only emits `retire_tests` when it actually judges a test wrong,
// which isn't reproducible on demand, so the job response is stubbed here. What
// this checks is the rendering and the accept payload, not the judgement.
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

const RETIRE_ONLY = {
  status: "done",
  result: {
    reply: "That test can't be satisfied — leaving the graph alone.",
    ops: [],
    diff: [],
    affected: {},
    config: null,
    lint: [],
    tests: [],
    retire_tests: [
      {
        case_id: "tc_247f9517",
        name: "Caller refuses DOB for existing appointment and asks for human",
        reason:
          "Assertion 1 requires the agent to ask for a date of birth, but the caller refuses it in their opening turn and assertion 2 requires an immediate transfer. Both cannot hold.",
      },
    ],
    error: "",
  },
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

let applyBody = null;
await page.setRequestInterception(true);
page.on("request", (req) => {
  const url = req.url();
  if (url.includes("/api/agents/") && url.endsWith("/copilot") && req.method() === "POST") {
    return req.respond({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ job_id: "stub-retire" }),
    });
  }
  if (url.includes("/api/jobs/stub-retire")) {
    return req.respond({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(RETIRE_ONLY),
    });
  }
  if (url.endsWith("/copilot/apply") && req.method() === "POST") {
    applyBody = JSON.parse(req.postData() ?? "{}");
    return req.respond({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ agent: null, tests_added: [], tests_retired: [{ id: "tc_247f9517" }] }),
    });
  }
  req.continue();
});

await page.evaluateOnNewDocument(() =>
  localStorage.setItem("composer.agent", "northside-scheduling")
);
await page.goto(BASE, { waitUntil: "networkidle2" });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
await wait(2400);

// Send anything — the response is stubbed.
await page.type("textarea", "why is that DOB test failing?");
await page.evaluate(() =>
  [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Send")?.click()
);
await wait(1800);

const card = await page.evaluate(() => {
  const text = document.body.innerText;
  return {
    header: /1 test retired/i.test(text),
    named: /Retire test/i.test(text) && /refuses DOB/i.test(text),
    reason: /Both cannot hold/i.test(text),
    noVerify: ![...document.querySelectorAll("button")].some(
      (b) => b.textContent?.trim() === "Verify first"
    ),
    noShowOnGraph: !/show on graph/i.test(text),
  };
});
console.log("  card:", JSON.stringify(card));
note(card.header, "card counts the retirement instead of reading '0 changes'");
note(card.named, "the test being retired is named");
note(card.reason, "the Copilot's reasoning is shown for review");
note(card.noVerify, "'Verify first' is hidden — there is no graph change to verify");
note(card.noShowOnGraph, "'show on graph' is hidden for a test-only proposal");

await page.screenshot({ path: `${OUT}/62-retire.png` });

await page.evaluate(() =>
  [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Apply")?.click()
);
await wait(1500);
console.log("  apply payload:", JSON.stringify(applyBody));
note(applyBody?.retire_tests?.[0]?.case_id === "tc_247f9517", "Apply sends retire_tests to the server");
note((applyBody?.ops ?? []).length === 0, "Apply sends no graph ops");
note(/retired 1 broken test/i.test(await page.evaluate(() => document.body.innerText)),
  "result is reported back to the engineer");

await browser.close();
if (problems.length) {
  console.log("\nPROBLEMS:");
  [...new Set(problems)].forEach((p) => console.log("  -", p));
  process.exit(1);
}
console.log("\nRetire flow OK.");
