// Checks the homepage weather card's freshness rules (NS_WEATHER.decide in
// deploy/site/assets/weather.js) against the shipped market clock.
//
//     node tools/check_weather_decide.mjs
//
// Run by hand: CI has no node, so this is not a pytest suite. Prints one line per
// case and exits non-zero on any failure.
// Design: docs/plans/2026-11-19-market-weather-card-design.md (Fog and the moon).
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const assets = path.join(root, "deploy", "site", "assets");

const window = {};
const document = { querySelector: () => null, visibilityState: "visible",
                   addEventListener: () => {} };
const ctx = vm.createContext({ window, document, Intl, Date, Math, JSON,
                               setInterval: () => 0, setTimeout: () => 0 });
for (const name of ["market-clock.js", "weather.js"]) {
  vm.runInContext(fs.readFileSync(path.join(assets, name), "utf8"), ctx, { filename: name });
}
const clock = window.NS_MARKET_CLOCK;
const decide = window.NS_WEATHER && window.NS_WEATHER.decide;
if (typeof decide !== "function") {
  console.error("FAIL: window.NS_WEATHER.decide is not defined");
  process.exit(1);
}

const E = 1795140000;   // any epoch: only differences matter
const hm = (h, m) => h * 60 + m;
const file = (date, final, ageSec, slot) => ({ v: 1, date, final, ts: E - ageSec,
                                               interval_min: 15, slot: slot || "11:15" });
const now = (date, weekday, minutes) => ({ date, weekday, minutes, epoch: E });

const cases = [
  ["live_mid_session", file("2026-11-19", false, 300), now("2026-11-19", 4, hm(12, 20)), "live"],
  ["stale_is_fog", file("2026-11-19", false, 2400), now("2026-11-19", 4, hm(12, 20)), "fog"],
  ["yesterdays_file_mid_session_is_fog", file("2026-11-18", true, 90000), now("2026-11-19", 4, hm(12, 20)), "fog"],
  ["pre_first_slot_is_moon", file("2026-11-19", true, 60000, "15:00"), now("2026-11-20", 5, hm(9, 35)), "moon", "8:45 AM CT today"],
  ["pre_open_is_moon", file("2026-11-19", true, 50000, "15:00"), now("2026-11-20", 5, hm(7, 0)), "moon", "8:45 AM CT today"],
  // The service does not write a 12:00 final today (half days are not modelled);
  // this pins decide for the day it does.
  ["final_before_regular_close_is_moon", file("2026-11-27", true, 5400, "12:00"), now("2026-11-27", 5, hm(13, 30)), "moon", "8:45 AM CT Monday"],
  ["evening_after_close_is_moon", file("2026-11-19", true, 10000, "15:00"), now("2026-11-19", 4, hm(19, 0)), "moon", "8:45 AM CT Friday"],
  ["dead_since_midday_hides_in_evening", file("2026-11-19", false, 20000, "12:00"), now("2026-11-19", 4, hm(19, 0)), "hide"],
  ["weekend_moon", file("2026-11-20", true, 100000, "15:00"), now("2026-11-21", 6, hm(10, 0)), "moon", "8:45 AM CT Monday"],
  ["holiday_skipped", file("2026-11-25", true, 150000, "15:00"), now("2026-11-27", 5, hm(8, 0)), "moon", "8:45 AM CT today"],
  ["stale_final_hides", file("2026-11-18", true, 250000, "15:00"), now("2026-11-21", 6, hm(10, 0)), "hide"],
  ["hidden_hides", { v: 1, hidden: true }, now("2026-11-19", 4, hm(12, 20)), "hide"],
  ["no_file_hides", null, now("2026-11-19", 4, hm(12, 20)), "hide"],
  ["unknown_version_hides", { v: 2, date: "2026-11-19" }, now("2026-11-19", 4, hm(12, 20)), "hide"],
];

let failed = 0;
for (const [name, f, n, mode, next] of cases) {
  let got;
  try {
    got = decide(f, n, clock);
  } catch (err) {
    got = { mode: "threw " + err };
  }
  const ok = got.mode === mode && (next === undefined || got.next === next);
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}: ${got.mode}${got.next ? " / " + got.next : ""}` +
              (ok ? "" : `  (want ${mode}${next ? " / " + next : ""})`));
}
process.exit(failed ? 1 : 0);
