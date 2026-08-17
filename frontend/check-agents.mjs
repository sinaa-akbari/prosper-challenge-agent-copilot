// Dev-only: the agent menu — activate (exclusive), rename, delete.
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
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 160)}`));
page.on("dialog", (d) => d.accept());

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
// The trigger is a toggle, so clicking it blindly closes an already-open menu —
// which is how this check first "found" zero activated agents.
const openMenu = async () => {
  await page.evaluate(() => {
    const btn = document.querySelector("[data-testid='agent-menu']");
    const isOpen = !!btn?.parentElement?.querySelector("[data-testid='agent-menu'] ~ div");
    if (!isOpen) btn?.click();
  });
  await wait(700);
};
const menuText = () =>
  page.evaluate(() => {
    const btn = document.querySelector("[data-testid='agent-menu']");
    return btn?.parentElement?.innerText ?? "";
  });
const clickIn = (label, nth = 0) =>
  page.evaluate(
    (label, nth) => {
      const root = document.querySelector("[data-testid='agent-menu']")?.parentElement;
      const hits = [...(root?.querySelectorAll("button") ?? [])].filter(
        (b) => b.textContent?.trim().toLowerCase() === label.toLowerCase()
      );
      hits[nth]?.click();
      return hits.length;
    },
    label,
    nth
  );

await page.goto(BASE, { waitUntil: "networkidle2" });
await wait(2600);

// Snapshot first. This check activates and renames real agents, and leaving a
// scratch agent answering the phone is a worse outcome than not running it.
const initial = await page.evaluate(async () => {
  const r = await fetch("/api/agents", { credentials: "same-origin" });
  const d = await r.json();
  return { active: d.active_agent_id, names: d.agents.map((a) => [a.id, a.name]) };
});
console.log("  starting state:", JSON.stringify(initial));

// Two agents are needed to prove exclusivity.
await openMenu();
const before = (await menuText()).split("\n").filter((l) => /^v\d+ ·/.test(l)).length;
if (before < 2) {
  await clickIn("New agent");
  await wait(3500);
  await openMenu();
}
let text = await menuText();
note(/Activate/i.test(text), "inactive agents offer Activate");
note(/Rename/i.test(text), "agents can be renamed");
note(/Delete/i.test(text), "agents can be deleted");
await page.screenshot({ path: `${OUT}/95-agent-menu.png` });

// Activate the first one that isn't live.
const n = await clickIn("Activate");
console.log(`  activate buttons: ${n}`);
await wait(3500);
await openMenu();
text = await menuText();
let activated = (text.match(/activated/gi) ?? []).length;
note(activated === 1, `exactly one agent reads 'activated' (found ${activated})`);

// Activating another must move it, not add a second.
const remaining = await clickIn("Activate");
if (remaining > 0) {
  await wait(3500);
  await openMenu();
  text = await menuText();
  activated = (text.match(/activated/gi) ?? []).length;
  note(activated === 1, `still exactly one after activating another (found ${activated})`);
} else {
  note(false, "expected another agent to still offer Activate");
}
await page.screenshot({ path: `${OUT}/96-agent-active.png` });

// The server is the authority, not the badge.
const api = await page.evaluate(async () => {
  const r = await fetch("/api/agents", { credentials: "same-origin" });
  const d = await r.json();
  return { active: d.active_agent_id, flagged: d.agents.filter((a) => a.active).length };
});
console.log("  api:", JSON.stringify(api));
note(!!api.active, "the server records which agent is live");
note(api.flagged === 1, `the server flags exactly one agent active (${api.flagged})`);

// Rename round-trips.
await openMenu();
await clickIn("Rename");
await wait(500);
const renamed = `Renamed ${Date.now().toString().slice(-5)}`;
await page.evaluate((v) => {
  const root = document.querySelector("[data-testid='agent-menu']")?.parentElement;
  const el = root?.querySelector("input");
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  setter.call(el, v);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}, renamed);
await clickIn("Save");
await wait(3000);
note(
  (await page.evaluate(() => document.body.innerText)).includes(renamed),
  "renaming an agent takes effect"
);

// Delete asks first.
await openMenu();
await clickIn("Delete");
await wait(500);
note(/Really delete\?/i.test(await menuText()), "delete asks for confirmation before acting");
await page.screenshot({ path: `${OUT}/97-agent-delete.png` });

// Put everything back.
const restored = await page.evaluate(async (initial) => {
  const opts = { method: "POST", credentials: "same-origin",
                 headers: { "Content-Type": "application/json" } };
  for (const [id, name] of initial.names) {
    await fetch(`/api/agents/${id}/rename`, { ...opts, body: JSON.stringify({ name }) });
  }
  if (initial.active) {
    await fetch(`/api/agents/${initial.active}/activate`, { ...opts, body: "{}" });
  }
  // Renaming cuts a version, so give the writes a moment to land before
  // reading back — otherwise this reports a restore failure that isn't one.
  await new Promise((r) => setTimeout(r, 1200));
  const r = await fetch("/api/agents", { credentials: "same-origin" });
  const d = await r.json();
  return { active: d.active_agent_id, names: d.agents.map((a) => [a.id, a.name]) };
}, initial);
note(
  restored.active === initial.active &&
    JSON.stringify(restored.names.sort()) === JSON.stringify(initial.names.sort()),
  "state restored to how the check found it"
);

await browser.close();
if (problems.length) {
  console.log("\nPROBLEMS:");
  [...new Set(problems)].forEach((p) => console.log("  -", p));
  process.exit(1);
}
console.log("\nAgent menu OK.");
