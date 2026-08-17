// Dev-only: the diagnosis and self-verification surfaces on a proposal.
//
// The Copilot's own loop is stubbed — reaching a "broke a passing case" outcome
// for real means waiting on several rounds of simulated calls. What's checked
// here is that the reasoning and the verdict are legible before Apply.
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

const PROPOSAL = {
  reply: "Two causes, one structural. Fixed the cause rather than the assertion.",
  ops: [{ op: "update_edge" }, { op: "add_node" }],
  diff: [
    { op: "update_edge", summary: "Edit edge 'nothing_further' on 'cancellation_done'", detail: {} },
    { op: "add_node", summary: "Add node 'kept_appointment'", detail: {} },
  ],
  affected: { cancellation_done: "changed", kept_appointment: "added" },
  config: { nodes: [], edges: [] },
  lint: [],
  tests: [],
  retire_tests: [],
  findings: [
    {
      case_id: "tc_1",
      case_name: "Existing appointment cancelled and caller declines to rebook",
      root_cause: "node_passed_through",
      evidence:
        "cancellation_confirmed arrived at cancellation_done and nothing_further left immediately, with no caller turn in between.",
      fix: "Tightened the nothing_further description so it only fires after the caller has answered.",
    },
    {
      case_id: "tc_2",
      case_name: "Existing appointment caller decides not to cancel after all",
      root_cause: "missing_path",
      evidence: 'Caller: "Is there a fee if I cancel?" — greeting had no exit for it.',
      fix: "Added a kept_appointment node.",
    },
  ],
  verification: {
    fixed: ["Existing appointment cancelled and caller declines to rebook"],
    still_failing: ["Existing appointment caller decides not to cancel after all"],
    broke: ["New patient books appointment and accepts first offered slot"],
    retired: [],
    passed: 2,
    total: 4,
  },
  error: "",
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

let polls = 0;
await page.setRequestInterception(true);
page.on("request", (req) => {
  const url = req.url();
  if (url.includes("/api/agents/") && url.endsWith("/copilot") && req.method() === "POST") {
    return req.respond({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ job_id: "stub-findings" }),
    });
  }
  if (url.includes("/api/jobs/stub-findings")) {
    // First poll reports the loop mid-flight so the live status can be seen.
    polls += 1;
    const running = polls <= 2;
    return req.respond({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        running
          ? {
              status: "running",
              status_text: "Running the affected calls against the proposal (round 2)…",
              progress: { done: 1, total: 4 },
              result: null,
              error: "",
            }
          : { status: "done", progress: { done: 4, total: 4 }, result: PROPOSAL, error: "" }
      ),
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

await page.type("textarea", "diagnose the failures");
await page.evaluate(() =>
  [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Send")?.click()
);

// Catch the in-flight status before the stub completes.
await wait(900);
const live = await page.evaluate(() => document.body.innerText);
note(
  /Running the affected calls against the proposal \(round 2\)/.test(live),
  "the loop narrates itself while it runs"
);
note(/1\/4/.test(live), "and shows how many calls it has run");
await page.screenshot({ path: `${OUT}/63-loop-running.png` });

await wait(2200);
const card = await page.evaluate(() => {
  const text = document.body.innerText;
  return {
    diagnosis: /Diagnosis/i.test(text),
    structuralCause: /Node passed straight through/i.test(text),
    missingPath: /No path for what the caller wanted/i.test(text),
    evidence: /no caller turn in between/i.test(text),
    quotedCaller: /Is there a fee if I cancel\?/i.test(text),
    ranBefore: /Ran before you saw it/i.test(text),
    counts: /2\/4 of the affected calls/i.test(text),
    fixed: /now passing:/i.test(text),
    broke: /broken by this change:/i.test(text),
    still: /still failing:/i.test(text),
  };
});
console.log("  card:", JSON.stringify(card));
note(card.diagnosis, "the proposal carries a Diagnosis section");
note(card.structuralCause, "root cause is shown in plain English, not as an enum");
note(card.missingPath, "each failing case gets its own cause");
note(card.evidence, "the cited evidence is shown");
note(card.quotedCaller, "including the caller's own words");
note(card.ranBefore, "the proposal reports that it was run before being shown");
note(card.counts, "with how many of the affected calls passed");
note(card.fixed, "what it fixed is listed");
note(card.still, "what still fails is listed");
note(card.broke, "and collateral damage is called out, not buried");

await page.screenshot({ path: `${OUT}/64-findings.png` });
await browser.close();

if (problems.length) {
  console.log("\nPROBLEMS:");
  [...new Set(problems)].forEach((p) => console.log("  -", p));
  process.exit(1);
}
console.log("\nFindings + verification UI OK.");
