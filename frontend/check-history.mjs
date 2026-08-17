// Dev-only: the History tab — the call record, the flow, and call-to-test replay.
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
page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text().slice(0, 160)}`));

await page.evaluateOnNewDocument(() =>
  localStorage.setItem("composer.agent", "northside-scheduling")
);
await page.goto(BASE, { waitUntil: "networkidle2" });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
await wait(2400);

await page.evaluate(() =>
  [...document.querySelectorAll("nav button")]
    .find((b) => b.textContent?.trim().startsWith("History"))
    ?.click()
);
await wait(2000);

const list = await page.evaluate(() => {
  const text = document.body.innerText;
  const rows = [...document.querySelectorAll("[data-call-row]")];
  return {
    heading: /History/.test(text),
    counted: /\d+ conversations?/.test(text),
    rows: rows.length,
    // Real phone calls are tagged distinctly from seeded ones — the whole
    // point is that you can tell which evidence came from a person.
    twilioTag: /phone/i.test(text),
    seedTag: /seeded/i.test(text),
    numbers: /\+\d{6,}/.test(text),
    flows: /greeting/.test(text),
    flagged: /flagged/i.test(text),
  };
});
console.log("  list:", JSON.stringify(list));
note(list.heading && list.counted, "History lists every conversation with a count");
note(list.rows > 0, `call rows render (${list.rows})`);
note(list.twilioTag, "phone calls are distinguishable");
note(list.seedTag, "seeded calls are distinguishable from them");
note(list.flagged, "client-flagged calls are marked");
note(list.numbers, "the phone numbers that called are shown");
note(list.flows, "the flow each call took is visible without opening it");
await page.screenshot({ path: `${OUT}/70-calls.png` });

// The agent that answers the phone is the *active* one, chosen independently of
// the switcher. History scoped to the selection showed an empty list straight
// after a real call, which read as "history is broken".
const spans = await page.evaluate(async () => {
  const sel = document.querySelector("[data-testid='agent-menu']")?.textContent?.trim() ?? "";
  const r = await fetch("/api/calls", { credentials: "same-origin" });
  const d = await r.json();
  const rows = document.querySelectorAll("[data-call-row]").length;
  return { selected: sel, api: d.calls.length, rows };
});
console.log("  scope:", JSON.stringify(spans));
note(
  spans.rows === spans.api,
  `history shows the whole workspace (${spans.rows} rows vs ${spans.api} calls)`
);

// A real phone call, which is the only kind with a recorded path. Seeded
// fixtures are transcripts only, and the flow panel correctly says so.
await page.evaluate(() => {
  const row =
    document.querySelector("[data-call-source='twilio']") ??
    document.querySelector("[data-call-row]");
  row?.click();
});
await wait(1200);

const open = await page.evaluate(() => {
  const text = document.body.innerText;
  return {
    transcript: /\bagent\b/i.test(text) && /\bcaller\b/i.test(text),
    action: [...document.querySelectorAll("button")].some((b) =>
      /Make a test from this call/.test(b.textContent ?? "")
    ),
  };
});
note(open.transcript, "the conversation renders in the same timeline as tests");
const detail = await page.evaluate(() => {
  const t = document.body.innerText;
  return {
    flow: /FLOW/i.test(t),
    conversation: /CONVERSATION/i.test(t),
    ending: /reached the end|caller hung up|handed to a person|failed/i.test(t),
    from: /FROM/i.test(t),
  };
});
console.log("  detail:", JSON.stringify(detail));
note(detail.flow, "an expanded call shows its flow through the graph");
note(detail.ending, "the flow says how the call ended, not just where it stopped");
note(detail.conversation, "and the conversation itself");
note(detail.from, "with the caller and callee numbers");
note(open.action, "'Make a test from this call' is offered");
await page.screenshot({ path: `${OUT}/71-call-open.png` });

if (open.action) {
  await page.evaluate(() =>
    [...document.querySelectorAll("button")]
      .find((b) => /Make a test from this call/.test(b.textContent ?? ""))
      ?.click()
  );
  // The replay is a model call; give it room.
  let drafted = false;
  for (let i = 0; i < 60 && !drafted; i++) {
    await wait(2000);
    drafted = await page.evaluate(() =>
      [...document.querySelectorAll("button")].some(
        (b) => b.textContent?.trim() === "Add to suite"
      )
    );
  }
  note(drafted, "a draft test case comes back for review");

  const draft = await page.evaluate(() => {
    const text = document.body.innerText;
    const transcript = [...document.querySelectorAll("[data-call-row]")]
      .length; // presence only; the assertion below reads the drafted persona
    return {
      // A caller who volunteered nothing should yield no facts. Asserting that
      // e.g. full_name appears would only pass if the replay invented one,
      // which is the exact failure this feature exists to avoid.
      described: /calling|caller|patient|wants|asked/i.test(text),
      assertions: (text.match(/·\s+The agent/g) ?? []).length,
      transcript,
    };
  });
  console.log("  draft:", JSON.stringify(draft));
  note(draft.described, "the draft reconstructs who was on the call");
  note(draft.assertions > 0, `assertions are listed (${draft.assertions})`);
  await page.screenshot({ path: `${OUT}/72-replay-draft.png` });
}

// Replay has to target the agent that took the call. History spans the
// workspace, so the selected agent is routinely not the owner — and the replay
// endpoint correctly 404s a call the agent never had.
const crossAgent = await page.evaluate(async () => {
  const r = await fetch("/api/calls", { credentials: "same-origin" });
  const calls = (await r.json()).calls;
  const selected = document.querySelector("[data-testid='agent-menu']")?.textContent?.trim();
  const foreign = calls.find((c) => c.agent_name && !selected?.includes(c.agent_name));
  if (!foreign) return { skipped: true };
  const res = await fetch(
    `/api/agents/${foreign.agent_id}/calls/${foreign.id}/replay`,
    { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ save: false }) }
  );
  return { skipped: false, status: res.status, agent: foreign.agent_name };
});
console.log("  cross-agent replay:", JSON.stringify(crossAgent));
note(
  crossAgent.skipped || crossAgent.status === 200,
  "a call belonging to another agent can still be replayed"
);

await browser.close();
if (problems.length) {
  console.log("\nPROBLEMS:");
  [...new Set(problems)].forEach((p) => console.log("  -", p));
  process.exit(1);
}
console.log("\nHistory + replay OK.");
