// Drives the real UI against the running servers (backend :8000, frontend :5173) with seeded data.
// Usage: node run.mjs   (seed first: python seed_demo.py)
import { chromium } from "playwright";
import assert from "node:assert/strict";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const shot = (page, name) => page.screenshot({ path: `screenshots/${name}.png`, fullPage: false });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1480, height: 900 }, timezoneId: "Europe/London", locale: "en-GB" });
const page = await ctx.newPage();
const problems = [];
page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));

const step = async (name, fn) => { await fn(); console.log("ok  -", name); };

await step("auth page renders; bad password shows an error", async () => {
  await page.goto(BASE);
  await page.getByRole("button", { name: "Log in" }).first().waitFor();
  await shot(page, "01-auth");
  await page.getByLabel("Email").fill("leo@demo-meetsync.com");
  await page.getByLabel("Password").fill("wrong-password");
  await page.locator("form button.primary").click();
  await page.getByRole("alert").filter({ hasText: "Incorrect email or password" }).waitFor();
});

await step("after login the group picker is shown first (no timetable yet)", async () => {
  await page.getByLabel("Password").fill("demo-pass-123");
  await page.locator("form button.primary").click();
  await page.getByRole("heading", { name: /which group/i }).waitFor();
  await page.getByTestId("group-Product Squad").waitFor();
  assert.equal(await page.getByTestId("week-grid").count(), 0);
  assert.equal(await page.getByTestId("timeline-grid").count(), 0);
  await shot(page, "02a-group-picker");
});

await step("open the group -> week calendar shows who is doing what, per day", async () => {
  await page.getByTestId("group-Product Squad").click();
  await page.getByTestId("week-grid").waitFor();
  await page.waitForTimeout(600);
  assert.equal(await page.locator(".wk-head").count(), 7);
  const legend = await page.getByTestId("people-legend").innerText();
  for (const n of ["Leo", "Anna", "Ben", "Raj"]) assert.ok(legend.includes(n), `legend has ${n}`);
  const texts = await page.getByTestId("wk-event").allInnerTexts();
  console.log("     week events:", texts.length, "| sample:", texts.slice(0, 4).map((t) => t.replace(/\s+/g, " ")));
  assert.ok(texts.length > 10);
  assert.ok(texts.some((t) => /^Raj\s+Busy/.test(t)), "Raj's private events show as 'Raj Busy'");
  assert.ok(texts.some((t) => /^Anna\s+(Standup|Customer call|Sprint planning)/.test(t)), "Anna's shared titles visible");
  await shot(page, "02-week");
  // hiding a person removes their blocks
  const before = await page.getByTestId("wk-event").count();
  await page.getByTestId("people-legend").getByRole("button", { name: /^Raj/ }).click();
  assert.ok((await page.getByTestId("wk-event").count()) < before);
  await page.getByTestId("people-legend").getByRole("button", { name: /^Raj/ }).click();
});

await step("quick add asks a follow-up only when something important is missing, then adds", async () => {
  await page.getByRole("tab", { name: "Add to my calendar" }).click();
  const box = page.getByLabel("Describe what you'll be doing");
  await box.fill("Zebra checkup tomorrow");                       // no time given
  await page.getByRole("button", { name: "Add to my calendar" }).last().click();
  const q = page.getByTestId("question").first();
  await q.waitFor({ timeout: 60000 });
  console.log("     assistant asked:", (await q.innerText()).trim());
  assert.equal(await page.getByTestId("added-item").count(), 0, "nothing is added while a question is open");
  await shot(page, "02c-question");
  await box.fill("3-4pm");
  await page.getByRole("button", { name: "Send answer" }).click();
  const item = page.getByTestId("added-item").first();
  await item.waitFor({ timeout: 60000 });
  const info = (await item.innerText()).replace(/\s+/g, " ");
  console.log("     added:", info);
  assert.match(info, /zebra/i);
  assert.match(info, /15:00.16:00/);
  // a complete request does NOT trigger a question
  await box.fill("Gym on monday 6-7pm");
  await page.getByRole("button", { name: "Add to my calendar" }).last().click();
  await page.getByTestId("added-item").nth(1).waitFor({ timeout: 60000 });
  assert.equal(await page.getByTestId("question").count(), 1, "no extra question for a complete request");
  await shot(page, "02d-chat");
});

await step("click an event: expanded details; edit title/time/location/notes; save; delete", async () => {
  await page.getByTestId("week-grid").waitFor();
  await page.getByRole("button", { name: "This week" }).click();   // the Gym request moved the calendar to next week
  const mine = page.getByTestId("wk-event").filter({ hasText: /Leo.*zebra/i });
  await mine.first().click();
  const dlg = page.getByTestId("event-details");
  await dlg.waitFor();
  const text = (await dlg.innerText()).replace(/\s+/g, " ");
  console.log("     details:", text.slice(0, 220));
  assert.match(text, /Zebra/i); assert.match(text, /15:00.16:00/); assert.match(text, /Quick add/); assert.match(text, /Only .Busy./);
  assert.match(text, /Same time/);
  await shot(page, "02e-event-details");
  await page.getByRole("button", { name: "Edit" }).click();
  const form = page.getByTestId("event-edit-form");
  await form.getByLabel("Title").fill("Zebra vet visit");
  await form.getByLabel("Starts").fill("16:30");
  await form.getByLabel("Ends").fill("17:15");
  await form.getByLabel("Location").fill("Clinic, 5 High St");
  await form.getByLabel("Notes").fill("Bring vaccination card");
  await shot(page, "02f-event-edit");
  await form.getByRole("button", { name: "Save changes" }).click();
  await page.getByTestId("event-details").waitFor();
  await page.getByTestId("event-details").getByText("Bring vaccination card").waitFor();
  const after = (await page.getByTestId("event-details").innerText()).replace(/\s+/g, " ");
  assert.match(after, /Zebra vet visit/); assert.match(after, /16:30.17:15/); assert.match(after, /Clinic, 5 High St/);
  await page.getByTestId("event-details").getByRole("button", { name: "Close" }).click();
  await page.getByTestId("wk-event").filter({ hasText: /Leo.*Zebra vet visit/ }).first().waitFor();   // the calendar updated
  await page.getByTestId("wk-event").filter({ hasText: /Leo.*Zebra vet visit/ }).first().click();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete" }).click();
  await page.getByTestId("event-details").waitFor({ state: "detached" });
  await page.getByTestId("wk-event").filter({ hasText: /Zebra/ }).first().waitFor({ state: "detached", timeout: 10000 });
  await page.getByRole("tab", { name: "Find a time" }).click();
});

await step("teammates' events open read-only: private details stay private", async () => {
  await page.getByRole("tab", { name: "Week" }).click();
  await page.getByTestId("week-grid").waitFor();
  await page.getByTestId("wk-event").filter({ hasText: /^Raj\s+Busy/ }).first().click();
  const d = page.getByTestId("event-details");
  await d.waitFor();
  const t = (await d.innerText()).replace(/\s+/g, " ");
  console.log("     Raj's event:", t.slice(0, 150));
  assert.match(t, /Busy . details are private/);
  assert.match(t, /Raj.s time/);
  assert.equal(await page.getByRole("button", { name: "Edit" }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "Delete" }).count(), 0);
  assert.ok(!/Location|Notes|Quick add|Teammates see/.test(t));
  await page.keyboard.press("Escape");
  await d.waitFor({ state: "detached" });
});

await step("week header opens that day in the working-hours Day view", async () => {
  await page.getByRole("tab", { name: /Day/ }).click();
  await page.getByTestId("timeline-grid").waitFor();
  for (const n of ["Leo Hartmann", "Anna Berg", "Ben Carter", "Raj Patel"]) await page.getByTestId(`row-${n}`).waitFor();
  await page.getByTestId("row-everyone").waitFor();
  assert.equal(await page.locator('.chips [role="tab"]').count(), 7);
  await page.waitForTimeout(600);
  await shot(page, "02b-day");
});

await step("teammates' events are masked as Busy; Anna's shared titles show; Leo sees his own", async () => {
  // Go to a weekday that has seeded events (the seed starts 3 days back, so start from today).
  const today = page.getByRole("button", { name: "Today", exact: true });
  if (await today.isEnabled()) await today.click();
  for (let i = 0; i < 7; i++) {
    const day = await page.locator("h2").first().innerText();
    if (!/Sat|Sun/.test(day)) break;
    await page.getByRole("button", { name: "Next day" }).click();
  }
  await page.waitForTimeout(500);
  const raj = await page.getByTestId("row-Raj Patel").locator(".busy span").allInnerTexts();
  const ben = await page.getByTestId("row-Ben Carter").locator(".busy span").allInnerTexts();
  const anna = await page.getByTestId("row-Anna Berg").locator(".busy span").allInnerTexts();
  const leo = await page.getByTestId("row-Leo Hartmann").locator(".busy span").allInnerTexts();
  console.log("     Raj:", raj, "| Ben:", ben, "| Anna:", anna, "| Leo:", leo);
  assert.ok(raj.length > 0 && raj.every((t) => t === "Busy"), "Raj's private titles must be masked");
  assert.ok(anna.some((t) => t !== "Busy"), "Anna shares titles");
  assert.ok(leo.some((t) => t !== "Busy"), "Leo sees own titles");
});

await step("24h toggle widens the axis", async () => {
  const before = await page.locator(".axis-track .tick-label").count();
  await page.getByLabel("24h").check();
  const after = await page.locator(".axis-track .tick-label").count();
  assert.ok(after > before, `ticks ${before} -> ${after}`);
  await shot(page, "03-24h");
  await page.getByLabel("24h").uncheck();
});

await step("unchecking a person changes who counts toward Everyone free", async () => {
  const count = () => page.locator('[data-testid="row-everyone"] .free').count();
  const before = await count();
  for (const n of ["Ben Carter", "Raj Patel", "Anna Berg"]) await page.getByLabel(`Count ${n} toward everyone free`).uncheck();
  const solo = await count();
  console.log(`     free segments: all=${before}, Leo only=${solo}`);
  assert.ok(solo >= before);
  for (const n of ["Ben Carter", "Raj Patel", "Anna Berg"]) await page.getByLabel(`Count ${n} toward everyone free`).check();
});

await step("ask the assistant (brief's example) -> suggested slots appear", async () => {
  await page.getByLabel("Describe the meeting").fill("45 minutes with Anna and Raj next week, mornings preferred, not Friday");
  await page.getByRole("button", { name: "Find a time" }).click();
  await page.getByTestId("slot").first().waitFor({ timeout: 15000 });
  const n = await page.getByTestId("slot").count();
  const pills = await page.locator(".parsed .pill").allInnerTexts();
  console.log("     slots:", n, "| parsed:", pills.join(" / "));
  assert.ok(n >= 2);
  assert.ok(pills.some((p) => p.includes("45 min")));
  assert.ok(pills.some((p) => p === "not Fri"), "Friday excluded");
  await shot(page, "04-suggestions");
});

await step("click a slot -> jumps to its day and highlights it on the timeline", async () => {
  await page.getByTestId("slot").nth(1).locator(".slot-main").click();
  await page.waitForTimeout(700);
  assert.ok((await page.locator(".highlight").count()) >= 5, "highlight on each member row + everyone row");
  assert.ok(await page.getByTestId("slot").nth(1).evaluate((el) => el.classList.contains("selected")));
  await shot(page, "05-slot-highlight");
});

await step("same slot is highlighted in the week view too", async () => {
  await page.getByRole("tab", { name: "Week" }).click();
  await page.getByTestId("week-grid").waitFor();
  assert.equal(await page.locator(".wk-col .highlight").count(), 1);
  await shot(page, "05b-week-highlight");
  await page.getByRole("tab", { name: /Day/ }).click();
});

await step("expand everyone's local time for a slot", async () => {
  await page.getByTestId("slot").nth(1).getByRole("button", { name: /everyone's local time/ }).click();
  const rows = await page.getByTestId("slot").nth(1).locator(".locals li").allInnerTexts();
  console.log("     local times:", rows.map((r) => r.replace(/\s+/g, " ")).join(" | "));
  assert.equal(rows.length, 3);
  await shot(page, "06-local-times");
});

await step("impossible request shows plain-language diagnostics", async () => {
  await page.getByLabel("Describe the meeting").fill("1 hour with everyone next week, before 11am");
  await page.getByRole("button", { name: "Find a time" }).click();
  await page.locator(".reco.empty").waitFor({ timeout: 15000 });
  console.log("     diagnostic:", (await page.locator(".reco.empty p").innerText()).slice(0, 160) + "…");
  assert.equal(await page.getByTestId("slot").count(), 0);
  await page.locator("table.diag").waitFor();
  await shot(page, "07-diagnostics");
});

await step("calendars modal: lists calendars, share toggle, manual event editor", async () => {
  await page.getByRole("button", { name: /My calendars/ }).click();
  await page.getByRole("dialog", { name: "My calendars" }).waitFor();
  const items = page.getByTestId("calendar-item");
  await items.first().waitFor();
  assert.ok((await items.count()) >= 2);
  await shot(page, "08-calendars");
  // create a manual calendar and add an event
  const calName = `E2E ${Date.now()}`;
  await page.getByLabel("Manual calendar name").fill(calName);
  await page.getByRole("dialog", { name: "My calendars" }).getByRole("button", { name: "Create" }).click();
  const mine = items.filter({ hasText: calName });
  await mine.waitFor();
  await mine.getByRole("button", { name: "Edit events" }).click();
  await mine.getByLabel("Event title", { exact: true }).fill("Playwright block");
  await mine.getByRole("button", { name: "Add", exact: true }).click();
  await mine.locator(".event-list li", { hasText: "Playwright block" }).waitFor();
  const toggle = mine.getByLabel("Share event titles");
  await toggle.check();
  await page.waitForTimeout(400);
  assert.ok(await toggle.isChecked());
  await shot(page, "09-event-editor");
  // clean up
  page.once("dialog", (d) => d.accept());
  await mine.getByRole("button", { name: "Remove" }).click();
  await mine.waitFor({ state: "detached" });
  // unconfigured / mocked OAuth: server answers with a message, not a crash
  await page.getByRole("button", { name: "Connect Google Calendar" }).click();
  await page.locator(".toast.error", { hasText: "isn't configured" }).waitFor();
  await page.locator(".toast .icon-btn").click();
  await page.keyboard.press("Escape");
});

await step("ICS link to localhost is refused (SSRF guard) with a clear message", async () => {
  await page.getByRole("button", { name: /My calendars/ }).click();
  await page.getByLabel("ICS URL").fill("http://127.0.0.1:8000/api/health");
  await page.getByLabel("ICS URL").press("Enter");
  await page.locator(".toast.error", { hasText: "private or internal network" }).waitFor({ timeout: 5000 });
  console.log("     toast:", (await page.locator(".toast").innerText()).replace(/\s+/g, " "));
  await page.keyboard.press("Escape");
});

await step("settings modal saves a new time zone and working hours", async () => {
  await page.getByRole("button", { name: /Settings/ }).click();
  await page.getByLabel("Work ends").fill("18:00");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached" });
  await page.getByText("09:00–18:00").first().waitFor();
  await page.getByRole("button", { name: /Settings/ }).click();
  await page.getByLabel("Work ends").fill("17:00");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached" });
});

await step("OAuth return toast (success and error) is shown and the URL is cleaned", async () => {
  await page.goto(`${BASE}/?oauth=success&provider=google`);
  await page.locator(".toast.success").waitFor();
  assert.ok(!page.url().includes("oauth="));
  await shot(page, "10-oauth-toast");
  await page.goto(`${BASE}/?oauth=error&provider=microsoft&message=${encodeURIComponent("Microsoft sign-in was cancelled or denied.")}`);
  await page.locator(".toast.error", { hasText: "cancelled or denied" }).waitFor();
});

await step("reloading the app asks for the group again; 'All groups' returns to the picker", async () => {
  await page.getByTestId("group-Product Squad").click();   // page was reloaded by the OAuth step -> picker is shown
  await page.getByTestId("week-grid").waitFor();
  await page.getByRole("button", { name: /All groups/ }).click();
  await page.getByTestId("group-Product Squad").waitFor();
  await page.getByTestId("group-Product Squad").click();
  await page.getByTestId("week-grid").waitFor();
});

await step("log out returns to the auth page", async () => {
  await page.getByRole("button", { name: "Log out" }).click();
  await page.getByRole("button", { name: "Log in" }).first().waitFor();
});

await browser.close();
const real = problems.filter((p) => !/401|Failed to load resource/.test(p));
if (real.length) { console.error("Browser problems:\n" + real.join("\n")); process.exit(1); }
console.log("\nAll UI steps passed. Screenshots in e2e/screenshots/");
