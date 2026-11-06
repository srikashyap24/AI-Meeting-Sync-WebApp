import { useEffect, useMemo, useState } from "react";
import type { BusyEvent, BusyMember } from "../types";
import { addDays, cityOf, dayBounds, fmtDay, fmtTime, intersectIv, localToUtcMs, subtractIv, workingIntervals, ymdInZone, type Iv } from "../tz";

export const PALETTE = ["#4f46e5", "#0f9d6b", "#e8710a", "#c026a3", "#0891b2", "#b45309", "#7c3aed", "#d03a3a"];

interface Props {
  members: BusyMember[];
  viewerTz: string;
  weekStart: string; // Monday, viewer-zone YYYY-MM-DD
  onWeek: (monday: string) => void;
  onOpenDay: (ymd: string) => void;
  full24: boolean;
  onFull24: (v: boolean) => void;
  included: Set<number>;
  highlight: { start: number; end: number } | null;
  loading: boolean;
  onEvent: (m: BusyMember, e: BusyEvent) => void;
}

const HOUR_PX = 44;
const H0 = 7;
const H1 = 21;

interface Placed { ev: BusyEvent; m: BusyMember; a: number; b: number; col: number; cols: number }

/** Side-by-side layout for overlapping blocks within a day column. */
function layout(items: Omit<Placed, "col" | "cols">[]): Placed[] {
  const sorted = [...items].sort((x, y) => x.a - y.a || y.b - x.b);
  const out: Placed[] = [];
  let cluster: Placed[] = [];
  let colEnds: number[] = [];
  let clusterEnd = -Infinity;
  const flush = () => { cluster.forEach((p) => (p.cols = colEnds.length)); out.push(...cluster); cluster = []; colEnds = []; };
  for (const it of sorted) {
    if (it.a >= clusterEnd && cluster.length) flush();
    let col = colEnds.findIndex((end) => end <= it.a);
    if (col === -1) { col = colEnds.length; colEnds.push(it.b); } else colEnds[col] = it.b;
    cluster.push({ ...it, col, cols: 1 });
    clusterEnd = Math.max(clusterEnd, it.b);
  }
  flush();
  return out;
}

export default function WeekView({ members, viewerTz, weekStart, onWeek, onOpenDay, full24, onFull24, included, highlight, loading, onEvent }: Props) {
  const [now, setNow] = useState(Date.now());
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 30_000); return () => clearInterval(t); }, []);

  const today = ymdInZone(now, viewerTz);
  const days = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i));
  const h0 = full24 ? 0 : H0;
  const h1 = full24 ? 24 : H1;
  const colorOf = (id: number) => PALETTE[Math.max(0, members.findIndex((m) => m.id === id)) % PALETTE.length];

  const cols = useMemo(() => days.map((ymd) => {
    const [ds, de] = dayBounds(ymd, viewerTz);
    const w0 = localToUtcMs(ymd, h0 * 60, viewerTz);
    const w1 = h1 === 24 ? de : localToUtcMs(ymd, h1 * 60, viewerTz);
    const items = members.filter((m) => !hidden.has(m.id)).flatMap((m) =>
      m.events.map((ev) => ({ ev, m, a: Math.max(Date.parse(ev.start), w0), b: Math.min(Date.parse(ev.end), w1) })).filter((x) => x.b > x.a));
    const chosen = members.filter((m) => included.has(m.id));
    const free: Iv[] = chosen.length
      ? chosen.map((m) => subtractIv(workingIntervals(m, w0, w1), m.events.map((e) => [Date.parse(e.start), Date.parse(e.end)] as Iv))).reduce((acc, f) => intersectIv(acc, f))
      : [];
    return { ymd, ds, de, w0, w1, placed: layout(items), free };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [members, weekStart, viewerTz, h0, h1, hidden, included]);

  const pct = (ms: number, w0: number, w1: number) => ((Math.min(Math.max(ms, w0), w1) - w0) / (w1 - w0)) * 100;
  const hours = Array.from({ length: h1 - h0 }, (_, i) => h0 + i);
  const last = addDays(weekStart, 6);
  const title = `${fmtDay(weekStart, { day: "numeric", month: "short" })} – ${fmtDay(last, { day: "numeric", month: "short", year: "numeric" })}`;
  const todayMonday = addDays(today, -((new Date(today + "T00:00:00Z").getUTCDay() + 6) % 7));

  return (
    <section className="timeline weekview">
      <header className="tl-head">
        <div className="tl-title">
          <button className="icon-btn" aria-label="Previous week" onClick={() => onWeek(addDays(weekStart, -7))}>‹</button>
          <h2>{title}</h2>
          <button className="icon-btn" aria-label="Next week" onClick={() => onWeek(addDays(weekStart, 7))}>›</button>
          <button className="btn small ghost" onClick={() => onWeek(todayMonday)} disabled={weekStart === todayMonday}>This week</button>
          <span className="muted small-text">Times in {cityOf(viewerTz)}</span>
        </div>
        <label className="switch"><input type="checkbox" checked={full24} onChange={(e) => onFull24(e.target.checked)} /><span>24h</span></label>
      </header>

      <div className="people-legend" data-testid="people-legend">
        {members.map((m) => {
          const off = hidden.has(m.id);
          return (
            <button key={m.id} className={`person-chip ${off ? "off" : ""}`} aria-pressed={!off} onClick={() => setHidden((cur) => { const n = new Set(cur); if (n.has(m.id)) n.delete(m.id); else n.add(m.id); return n; })}>
              <i style={{ background: colorOf(m.id) }} />{m.name.split(" ")[0]}<small>{cityOf(m.timezone)}</small>
            </button>
          );
        })}
        <span className="legend-free"><i className="sw sw-free" /> Everyone free (in working hours)</span>
      </div>

      <div className={`wk-scroll ${loading ? "loading" : ""}`}>
        <div className="wk" data-testid="week-grid" style={{ gridTemplateColumns: "48px repeat(7, minmax(0, 1fr))" }}>
          <div className="wk-corner" />
          {cols.map((c) => (
            <button key={c.ymd} className={`wk-head ${c.ymd === today ? "today" : ""}`} onClick={() => onOpenDay(c.ymd)} title="Open this day with working hours">
              <span>{fmtDay(c.ymd, { weekday: "short" })}</span><strong>{fmtDay(c.ymd, { day: "numeric" })}</strong>
            </button>
          ))}
          <div className="wk-gutter" style={{ height: hours.length * HOUR_PX }}>
            {hours.map((h) => <span key={h} style={{ top: (h - h0) * HOUR_PX }}>{String(h).padStart(2, "0")}:00</span>)}
          </div>
          {cols.map((c) => {
            const hl = highlight && highlight.end > c.w0 && highlight.start < c.w1 ? highlight : null;
            return (
              <div key={c.ymd} className={`wk-col ${c.ymd === today ? "today" : ""}`} data-testid={`day-${c.ymd}`} style={{ height: hours.length * HOUR_PX }}>
                {hours.map((h) => <i key={h} className="hline" style={{ top: (h - h0) * HOUR_PX }} />)}
                {c.free.map(([a, b], i) => (
                  <div key={i} className="wk-free" style={{ top: `${pct(a, c.w0, c.w1)}%`, height: `${pct(b, c.w0, c.w1) - pct(a, c.w0, c.w1)}%` }} title={`Everyone free ${fmtTime(a, viewerTz)}–${fmtTime(b, viewerTz)}`} />
                ))}
                {c.placed.map((p) => {
                  const top = pct(p.a, c.w0, c.w1);
                  const height = pct(p.b, c.w0, c.w1) - top;
                  const titled = p.ev.title && p.ev.title !== "Busy";
                  const label = titled ? p.ev.title : "Busy";
                  const color = colorOf(p.m.id);
                  const short = (height / 100) * hours.length * HOUR_PX < 34;
                  return (
                    <div key={`${p.m.id}-${p.ev.id}`} className={`wk-ev ${short ? "short" : ""}`} data-testid="wk-event" role="button" tabIndex={0}
                      onClick={() => onEvent(p.m, p.ev)} onKeyDown={(k) => (k.key === "Enter" || k.key === " ") && (k.preventDefault(), onEvent(p.m, p.ev))}
                      style={{ top: `${top}%`, height: `${height}%`, left: `calc(${(p.col / p.cols) * 100}% + 1px)`, width: `calc(${100 / p.cols}% - 2px)`, background: color + (titled ? "" : "cc") }}
                      title={`${p.m.name}: ${label} (click for details)\n${fmtTime(Date.parse(p.ev.start), viewerTz)}–${fmtTime(Date.parse(p.ev.end), viewerTz)}`}>
                      <b>{p.m.name.split(" ")[0]}</b> {label}
                      <small>{fmtTime(Date.parse(p.ev.start), viewerTz)}–{fmtTime(Date.parse(p.ev.end), viewerTz)}</small>
                    </div>
                  );
                })}
                {hl && <div className="highlight" style={{ top: `${pct(hl.start, c.w0, c.w1)}%`, height: `${pct(hl.end, c.w0, c.w1) - pct(hl.start, c.w0, c.w1)}%`, left: 0, right: 0 }} />}
                {now >= c.w0 && now <= c.w1 && <div className="now-line-h" style={{ top: `${pct(now, c.w0, c.w1)}%` }} />}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
