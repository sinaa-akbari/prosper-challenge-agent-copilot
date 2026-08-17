// Dev-only: the sign-in screen and the gate behind it.
// Run against a server started with auth on:
//   AUTH_DISABLED=0 AUTH_PASSWORD=localtest123 python server.py
//
// Signing up is open, so requesting a code sends a real SMS. That's skipped by
// default — a check that costs money and burns rate limit every run is a check
// people stop running. Set CHECK_SEND=1 to exercise the live send.
import puppeteer from "puppeteer-core";
import { existsSync, mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "https://localhost:7860";
const PASSWORD = process.env.CHECK_PASSWORD ?? "localtest123";
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

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
await page.goto(BASE, { waitUntil: "networkidle2" });
await wait(2200);

const gate = await page.evaluate(() => {
  const text = document.body.innerText;
  return {
    signIn: /Sign in/i.test(text),
    // The builder must not be behind it, even briefly.
    noGraph: !document.querySelector(".react-flow__node"),
    noTabs: ![...document.querySelectorAll("nav button")].some((b) => /Build/.test(b.textContent)),
    phoneField: /Mobile number/i.test(text),
    // Signing in for the first time is how an account gets made; the screen
    // should say so rather than looking like a locked door.
    explains: /creates your workspace/i.test(text),
    invites: /Sign in or create an account/i.test(text),
  };
});
console.log("  gate:", JSON.stringify(gate));
note(gate.signIn, "an unauthenticated visitor gets the sign-in screen");
note(gate.noGraph && gate.noTabs, "the builder is not rendered behind it");
note(gate.phoneField, "phone is the primary method");
note(gate.invites, "it invites new accounts rather than only existing ones");
note(gate.explains, "it explains that signing in creates a workspace");
await page.screenshot({ path: `${OUT}/90-signin.png` });

// A malformed number is rejected client-to-server without sending anything.
await page.type('input[type="tel"]', "600123456");
await page.evaluate(() =>
  [...document.querySelectorAll("button")].find((b) => /Send me a code/.test(b.textContent))?.click()
);
await wait(2500);
note(
  await page.evaluate(() => /country code/i.test(document.body.innerText)),
  "a number without a country code is refused before any SMS is sent"
);
await page.screenshot({ path: `${OUT}/91-signin-validation.png` });

if (process.env.CHECK_SEND === "1") {
  await page.evaluate(() => {
    const el = document.querySelector('input[type="tel"]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(el, "+34600123456");
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.evaluate(() =>
    [...document.querySelectorAll("button")].find((b) => /Send me a code/.test(b.textContent))?.click()
  );
  await wait(4000);
  const codeStage = await page.evaluate(() => ({
    prompt: /Six-digit code/i.test(document.body.innerText),
    sent: /Code sent/i.test(document.body.innerText),
    canChange: /change/i.test(document.body.innerText),
  }));
  console.log("  code stage:", JSON.stringify(codeStage));
  note(codeStage.prompt, "asks for the six-digit code");
  note(codeStage.sent, "confirms the code was sent");
  note(codeStage.canChange, "the number can be corrected");
}

// The administrator path, and that it actually unlocks the app.
await page.evaluate(() =>
  [...document.querySelectorAll("button")].find((b) => /Administrator sign-in/.test(b.textContent))?.click()
);
await wait(900);
await page.type('input[type="password"]', PASSWORD);
await page.evaluate(() =>
  [...document.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Sign in")?.click()
);

let loaded = false;
for (let i = 0; i < 25 && !loaded; i++) {
  await wait(1000);
  loaded = await page.evaluate(() => !!document.querySelector(".react-flow__node"));
}
note(loaded, "signing in loads the builder");

const cookie = (await page.cookies()).find((c) => c.name === "composer_session");
note(!!cookie, "a session cookie is set");
note(!!cookie?.httpOnly, "the session cookie is HttpOnly, so script can't read it");
await page.screenshot({ path: `${OUT}/92-signed-in.png` });

// And that it survives a reload rather than bouncing back to the gate.
await page.reload({ waitUntil: "networkidle2" });
await wait(3000);
note(
  await page.evaluate(() => !!document.querySelector(".react-flow__node")),
  "the session survives a reload"
);

await browser.close();
if (problems.length) {
  console.log("\nPROBLEMS:");
  [...new Set(problems)].forEach((p) => console.log("  -", p));
  process.exit(1);
}
console.log("\nSign-in OK.");
