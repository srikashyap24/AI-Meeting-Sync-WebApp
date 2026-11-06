// Time zone maths with Intl only. Instants are epoch milliseconds (UTC); "ymd" is a YYYY-MM-DD calendar date.

const dtfCache = new Map<string, Intl.DateTimeFormat>();

function dtf(tz: string, key: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const k = `${tz}|${key}`;
  let f = dtfCache.get(k);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", { timeZone: tz, ...opts });
    dtfCache.set(k, f);
  }
  return f;
}

interface Wall {
  y: number;
  mo: number;
  d: number;
  h: number;
  mi: number;
  s: number;
}

export function wallClock(ms: number, tz: string): Wall {
  const parts = dtf(tz, "wall", {
    hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(new Date(ms));
  const g = (t: string) => Number(parts.find((p) => p.type === t)!.value);
  return { y: g("year"), mo: g("month"), d: g("day"), h: g("hour") % 24, mi: g("minute"), s: g("second") };
}

/** Offset of `tz` from UTC at instant `ms`, in ms (positive = ahead of UTC). */
export function offsetMs(ms: number, tz: string): number {
  const w = wallClock(ms, tz);
  return Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi, w.s) - Math.floor(ms / 1000) * 1000;
}

export function parseYmd(ymd: string): [number, number, number] {
  const [y, m, d] = ymd.split("-").map(Number);
  return [y, m, d];
}

export function addDays(ymd: string, n: number): string {
  const [y, m, d] = parseYmd(ymd);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

/** Monday=0 .. Sunday=6 */
export function weekdayOf(ymd: string): number {
  const [y, m, d] = parseYmd(ymd);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
}

/** Local wall-clock (date + minutes after midnight) -> instant. Times skipped by DST resolve to just after the gap. */
export function localToUtcMs(ymd: string, minutes: number, tz: string): number {
  const [y, m, d] = parseYmd(ymd);
  const wall = Date.UTC(y, m - 1, d, 0, minutes);
  const off1 = offsetMs(wall - offsetMs(wall, tz), tz);
  let t = wall - off1;
  const off2 = offsetMs(t, tz);
  if (off2 !== off1) t = wall - off2;
  return t;
}

export function ymdInZone(ms: number, tz: string): string {
  const w = wallClock(ms, tz);
  return `${w.y}-${String(w.mo).padStart(2, "0")}-${String(w.d).padStart(2, "0")}`;
}

/** [start, end) of a local calendar day; 23 or 25 hours on DST days. */
export function dayBounds(ymd: string, tz: string): [number, number] {
  return [localToUtcMs(ymd, 0, tz), localToUtcMs(addDays(ymd, 1), 0, tz)];
}

export function fmtTime(ms: number, tz: string): string {
  return dtf(tz, "time", { hourCycle: "h23", hour: "2-digit", minute: "2-digit" }).format(new Date(ms));
}

export function fmtDay(ymd: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" }): string {
  const [y, m, d] = parseYmd(ymd);
  return new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", ...opts }).format(new Date(Date.UTC(y, m - 1, d)));
}

export function fmtMinutes(min: number): string {
  return `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;
}

export function cityOf(tz: string): string {
  return (tz.split("/").pop() ?? tz).replace(/_/g, " ");
}

export function shortOffset(ms: number, tz: string): string {
  const p = dtf(tz, "off", { timeZoneName: "shortOffset" }).formatToParts(new Date(ms)).find((x) => x.type === "timeZoneName");
  return p?.value ?? tz;
}

/** "2026-11-09T14:30" (a wall-clock string from the API) -> HH:MM */
export const clockOf = (local: string) => local.slice(11, 16);

// ---------- interval helpers (all [start, end) in ms) ----------
export type Iv = [number, number];

export function mergeIv(ivs: Iv[]): Iv[] {
  const s = ivs.filter(([a, b]) => b > a).sort((x, y) => x[0] - y[0]);
  const out: Iv[] = [];
  for (const [a, b] of s) {
    if (out.length && a <= out[out.length - 1][1]) out[out.length - 1][1] = Math.max(out[out.length - 1][1], b);
    else out.push([a, b]);
  }
  return out;
}

export function subtractIv(base: Iv[], remove: Iv[]): Iv[] {
  const rm = mergeIv(remove);
  const out: Iv[] = [];
  for (const [a0, b] of base) {
    let a = a0;
    for (const [ra, rb] of rm) {
      if (rb <= a || ra >= b) continue;
      if (ra > a) out.push([a, ra]);
      a = Math.max(a, rb);
      if (a >= b) break;
    }
    if (a < b) out.push([a, b]);
  }
  return out;
}

export function intersectIv(a: Iv[], b: Iv[]): Iv[] {
  const out: Iv[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    const s = Math.max(a[i][0], b[j][0]);
    const e = Math.min(a[i][1], b[j][1]);
    if (e > s) out.push([s, e]);
    if (a[i][1] < b[j][1]) i++;
    else j++;
  }
  return out;
}

/** A member's working hours (in THEIR zone) as instants overlapping [w0, w1). */
export function workingIntervals(m: { timezone: string; work_start: number; work_end: number; work_days: number[] }, w0: number, w1: number): Iv[] {
  const out: Iv[] = [];
  let day = addDays(ymdInZone(w0, m.timezone), -1);
  const last = addDays(ymdInZone(w1, m.timezone), 1);
  for (let guard = 0; day <= last && guard < 10; guard++, day = addDays(day, 1)) {
    if (!m.work_days.includes(weekdayOf(day))) continue;
    const a = localToUtcMs(day, m.work_start, m.timezone);
    const b = localToUtcMs(day, m.work_end, m.timezone);
    if (b > w0 && a < w1) out.push([Math.max(a, w0), Math.min(b, w1)]);
  }
  return mergeIv(out);
}
