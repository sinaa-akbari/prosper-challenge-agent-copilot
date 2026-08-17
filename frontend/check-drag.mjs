// Dev-only: verify free-layout dragging survives everything that normally
// breaks it — data re-renders, panel resizes, reloads, and mode switches.
//
// Positions are read from each node's own CSS transform, which React Flow sets
// in *flow* coordinates. Screen coordinates would move whenever the viewport
// re-fits, which happens constantly here, and would make every assertion lie.
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
  acceptInsecureCerts: true,   // self-signed dev cert
  executablePath: CHROME,
  headless: "new",
  args: ["--no-sandbox", "--ignore-certificate-errors"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1680, height: 1000 });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 200)}`));
page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text().slice(0, 200)}`));

// Runs on every navigation, reloads included — so the reset must happen once,
// or the reload step wipes the very layout it is meant to be checking.
await page.evaluateOnNewDocument(() => {
  localStorage.setItem("composer.agent", "northside-scheduling");
  if (!localStorage.getItem("__dragtest")) {
    localStorage.setItem("__dragtest", "1");
    localStorage.removeItem("composer.layout.northside-scheduling");
    localStorage.setItem("composer.freeLayout", "1");
  }
});
await page.goto(BASE, { waitUntil: "networkidle2" });
const settle = (ms = 900) => new Promise((r) => setTimeout(r, ms));
await settle(2600);

const positions = () =>
  page.evaluate(() => {
    const out = {};
    document.querySelectorAll(".react-flow__node").forEach((n) => {
      const m = /translate\(\s*([-\d.]+)px,\s*([-\d.]+)px\s*\)/.exec(n.style.transform || "");
      if (m) out[n.getAttribute("data-id")] = { x: +m[1], y: +m[2] };
    });
    return out;
  });

const dist = (a, b) => (a && b ? Math.hypot(a.x - b.x, a.y - b.y) : Infinity);
const TARGET = "greeting";

async function dragBy(id, dx, dy) {
  const box = await page.evaluate((nodeId) => {
    const el = document.querySelector(`.react-flow__node[data-id="${nodeId}"]`);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + 14 }; // grab the name plate
  }, id);
  if (!box) throw new Error(`node ${id} not found`);
  await page.mouse.move(box.x, box.y);
  await page.mouse.down();
  for (let i = 1; i <= 12; i++) {
    await page.mouse.move(box.x + (dx * i) / 12, box.y + (dy * i) / 12);
    await new Promise((r) => setTimeout(r, 16));
  }
  await page.mouse.up();
  await settle(500);
}

const clickByText = (text) =>
  page.evaluate((t) => {
    const b = [...document.querySelectorAll("button")].find(
      (x) => x.textContent?.trim().toLowerCase() === t.toLowerCase()
    );
    b?.click();
    return !!b;
  }, text);

/* 1 — free mode is the default */
const freeByDefault = await page.evaluate(() =>
  [...document.querySelectorAll("button")].some((b) => b.textContent?.trim() === "Free")
);
note(freeByDefault, "free layout is on by default");

const before = await positions();
note(Object.keys(before).length >= 5, `graph rendered ${Object.keys(before).length} nodes`);

/* 2 — dragging moves one node and only that node */
await dragBy(TARGET, 210, 90);
const afterDrag = await positions();
note(dist(before[TARGET], afterDrag[TARGET]) > 40, "dragged node moved");

const others = Object.keys(before).filter((id) => id !== TARGET);
const strays = others.filter((id) => dist(before[id], afterDrag[id]) > 0.5);
note(strays.length === 0, `other nodes stayed put${strays.length ? ` (moved: ${strays})` : ""}`);

await page.screenshot({ path: `${OUT}/50-dragged.png` });

/* 3 — a data re-render must not snap it back */
await page.evaluate(() => {
  const n = [...document.querySelectorAll(".react-flow__node")].find((x) =>
    x.textContent?.includes("offer_times")
  );
  n?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
});
await settle(1200); // inspector opens -> container resizes -> viewport refits
const afterRerender = await positions();
note(
  dist(afterDrag[TARGET], afterRerender[TARGET]) < 1,
  "position survived a re-render and panel resize"
);

/* 4 — and a reload */
await page.reload({ waitUntil: "networkidle2" });
await settle(2600);
const afterReload = await positions();
note(dist(afterDrag[TARGET], afterReload[TARGET]) < 1, "position persisted across reload");

/* 5 — auto mode restores the computed layout */
note(await clickByText("Free"), "found the layout toggle");
await settle(700);
const autoPos = await positions();
note(dist(afterDrag[TARGET], autoPos[TARGET]) > 40, "auto mode returns to the computed layout");

/* 6 — switching back restores the arrangement */
note(await clickByText("Auto"), "toggle switched to Auto");
await settle(700);
const backToFree = await positions();
note(
  dist(afterDrag[TARGET], backToFree[TARGET]) < 1,
  "switching back to free restores the arrangement"
);

/* 7 — Tidy forgets it */
note(await clickByText("Tidy"), "found Tidy");
await settle(900);
const tidied = await positions();
note(dist(autoPos[TARGET], tidied[TARGET]) < 1, "Tidy re-runs auto layout");

await page.screenshot({ path: `${OUT}/51-tidied.png` });
await browser.close();

if (problems.length) {
  console.log("\nPROBLEMS:");
  [...new Set(problems)].forEach((p) => console.log("  -", p));
  process.exit(1);
}
console.log("\nFree layout OK.");
