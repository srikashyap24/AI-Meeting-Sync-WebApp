import { useEffect, useMemo, useState } from "react";
import type { BusyEvent, BusyMember } from "../types";
import {
  addDays, cityOf, dayBounds, fmtDay, fmtMinutes, fmtTime, intersectIv, localToUtcMs, shortOffset, subtractIv, workingIntervals,
  ymdInZone, type Iv,
} from "../tz";

interface Props {
  members: BusyMember[];
  viewerId: number;
  viewerTz: string;
  date: string; // viewer-zone YYYY-MM-DD
  onDate: (d: string) => void;
  full24: boolean;
  onFull24: (v: boolean) => void;
  included: Set<number>;
  onToggleIncluded: (id: number) => void;
  highlight: { start: number; end: number } | null;
  loading: boolean;
  onEvent: (m: BusyMember, e: BusyEvent) => void;
}

const DAY_START_MIN = 6 * 60;
const DAY_END_MIN = 22 * 60;

export default function Timeline({ members, viewerId, viewerTz, date, onDate, full24, onFull24, included, onToggleIncluded, highlight, loading, onEvent }: Props) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  const todayYmd = ymdInZone(now, viewerTz);
  const [dayStart, dayEnd] = useMemo(() => dayBounds(date, viewerTz), [date, viewerTz]);
  const [w0, w1] = useMemo<Iv>(
    () => (full24 ? [dayStart, dayEnd] : [localToUtcMs(date, DAY_START_MIN, viewerTz), localToUtcMs(date, DAY_END_MIN, viewerTz)]),
    [full24, dayStart, dayEnd, date, viewerTz],
  );
  const span = w1 - w0;
  const pos = (ms: number) => ((Math.min(Math.max(ms, w0), w1) - w0) / span) * 100;
  const box = (a: number, b: number) => ({ left: `${pos(a)}%`, width: `${Math.max(pos(b) - pos(a), 0)}%` });

  // Hour ticks every real hour from local midnight, so 23h/25h DST days stay honest.
  const ticks = useMemo(() => {
    const out: { ms: number; label: string }[] = [];
    for (let ms = dayStart; ms < dayEnd; ms += 3_600_000) if (ms >= w0 && ms <= w1) out.push({ ms, label: fmtTime(ms, viewerTz).slice(0, 2) });
    return out;
  }, [dayStart, dayEnd, w0, w1, viewerTz]);
  const stripStart = useMemo(() => {
    // chip strip: 7 days beginning at the Monday-agnostic "anchor" kept stable while the selected date is inside it
    return date < todayYmd || date > addDays(todayYmd, 6) ? date : todayYmd;
  }, [date, todayYmd]);
  const strip = Array.from({ length: 7 }, (_, i) => addDays(stripStart, i));

  const rows = useMemo(
    () =>
      members.map((m) => {
        const work = workingIntervals(m, w0, w1);
        const busy: Iv[] = m.events.map((e) => [Date.parse(e.start), Date.parse(e.end)] as Iv);
        return { m, work, free: subtractIv(work, busy) };
      }),
    [members, w0, w1],
  );

  const everyoneFree = useMemo(() => {
    const chosen = rows.filter((r) => included.has(r.m.id));
    if (!chosen.length) return [] as Iv[];
    return chosen.map((r) => r.free).reduce((acc, f) => intersectIv(acc, f));
  }, [rows, included]);

  const nowInView = now >= w0 && now <= w1;
  const hl = highlight && highlight.end > w0 && highlight.start < w1 ? highlight : null;

  return (
    <section className="timeline">
      <header className="tl-head">
        <div className="tl-title">
          <button className="icon-btn" aria-label="Previous day" onClick={() => onDate(addDays(date, -1))}>‹</button>
          <h2>{fmtDay(date, { weekday: "long", day: "numeric", month: "long" })}</h2>
          <button className="icon-btn" aria-label="Next day" onClick={() => onDate(addDays(date, 1))}>›</button>
          <button className="btn small ghost" onClick={() => onDate(todayYmd)} disabled={date === todayYmd}>Today</button>
          <span className="muted small-text">
            {cityOf(viewerTz)} · {shortOffset(dayStart + 12 * 3_600_000, viewerTz)}
            {dayEnd - dayStart !== 86_400_000 && <b className="dst"> · {(dayEnd - dayStart) / 3_600_000}-hour day (DST)</b>}
          </span>
        </div>
        <div className="tl-controls">
          <label className="switch">
            <input type="checkbox" checked={full24} onChange={(e) => onFull24(e.target.checked)} />
            <span>24h</span>
          </label>
        </div>
      </header>

      <div className="chips" role="tablist" aria-label="Days">
        {strip.map((d) => (
          <button key={d} role="tab" aria-selected={d === date} className={`chip-day ${d === date ? "active" : ""} ${d === todayYmd ? "today" : ""}`} onClick={() => onDate(d)}>
            <span>{fmtDay(d, { weekday: "short" })}</span>
            <strong>{fmtDay(d, { day: "numeric" })}</strong>
          </button>
        ))}
      </div>

      <div className={`grid ${loading ? "loading" : ""}`} data-testid="timeline-grid">
        <div className="row axis">
          <div className="label" />
          <div className="track axis-track">
            {ticks.map((t) => (
              <span key={t.ms} className="tick-label" style={{ left: `${pos(t.ms)}%` }}>{t.label}</span>
            ))}
          </div>
        </div>

        {rows.map(({ m, work }) => {
          const mine = m.id === viewerId;
          const localNow = fmtTime(now, m.timezone);
          return (
            <div className="row" key={m.id} data-testid={`row-${m.name}`}>
              <div className="label">
                <input
                  type="checkbox"
                  checked={included.has(m.id)}
                  onChange={() => onToggleIncluded(m.id)}
                  aria-label={`Count ${m.name} toward everyone free`}
                  title="Counts toward “Everyone free”"
                />
                <div className="who">
                  <strong>{m.name}{mine && <em> (you)</em>}</strong>
                  <span>{cityOf(m.timezone)} · {localNow} · {fmtMinutes(m.work_start)}–{fmtMinutes(m.work_end)}</span>
                </div>
              </div>
              <div className="track off-hours">
                {ticks.map((t) => <i key={t.ms} className="gridline" style={{ left: `${pos(t.ms)}%` }} />)}
                {work.map(([a, b], i) => <div key={i} className="work" style={box(a, b)} title={`${m.name}'s working hours`} />)}
                {m.events.map((e) => {
                  const a = Date.parse(e.start);
                  const b = Date.parse(e.end);
                  if (b <= w0 || a >= w1) return null;
                  const label = e.title && e.title !== "Busy" ? e.title : "Busy";
                  return (
                    <div key={e.id} className={`busy ${e.shared ? "titled" : ""}`} style={box(a, b)} role="button" tabIndex={0} data-testid="day-event"
                      onClick={() => onEvent(m, e)} onKeyDown={(k) => (k.key === "Enter" || k.key === " ") && (k.preventDefault(), onEvent(m, e))}
                      title={`${label}\n${fmtTime(a, viewerTz)}–${fmtTime(b, viewerTz)} (${cityOf(viewerTz)})`}>
                      <span>{label}</span>
                    </div>
                  );
                })}
                {hl && <div className="highlight" style={box(hl.start, hl.end)} />}
                {nowInView && <div className="now-line" style={{ left: `${pos(now)}%` }} />}
              </div>
            </div>
          );
        })}

        <div className="row everyone" data-testid="row-everyone">
          <div className="label">
            <div className="who">
              <strong>Everyone free</strong>
              <span>{included.size} of {members.length} counted · in working hours</span>
            </div>
          </div>
          <div className="track">
            {ticks.map((t) => <i key={t.ms} className="gridline" style={{ left: `${pos(t.ms)}%` }} />)}
            {everyoneFree.map(([a, b], i) => (
              <div key={i} className="free" style={box(a, b)} title={`${fmtTime(a, viewerTz)}–${fmtTime(b, viewerTz)} everyone is free`}>
                {(b - a) / span > 0.1 && <span>{fmtTime(a, viewerTz)}–{fmtTime(b, viewerTz)}</span>}
              </div>
            ))}
            {hl && <div className="highlight" style={box(hl.start, hl.end)} />}
            {nowInView && <div className="now-line" style={{ left: `${pos(now)}%` }} />}
          </div>
        </div>
      </div>

      <footer className="legend">
        <span><i className="sw sw-work" /> Working hours (each person's own zone)</span>
        <span><i className="sw sw-busy" /> Busy</span>
        <span><i className="sw sw-free" /> Everyone free</span>
        <span><i className="sw sw-now" /> Now</span>
        <span className="muted">Axis shows {cityOf(viewerTz)} time.</span>
      </footer>
    </section>
  );
}
