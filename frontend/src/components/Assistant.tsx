import { useState, type FormEvent } from "react";
import { api } from "../api";
import type { ScheduleResult, Slot, Team } from "../types";
import { clockOf, cityOf, fmtDay, fmtTime } from "../tz";

interface Props {
  team: Team;
  viewerId: number;
  viewerTz: string;
  selectedStart: string | null;
  onPick: (s: Slot) => void;
}

const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function describeFilters(r: ScheduleResult["parsed"]): string[] {
  const out = [`${r.duration_minutes} min`, `${fmtDay(r.start_date)} – ${fmtDay(r.end_date)}`];
  if (r.weekdays) {
    const missing = WD.map((_, i) => i).filter((i) => !r.weekdays!.includes(i));
    out.push(missing.length <= 2 ? `not ${missing.map((d) => WD[d]).join("/")}` : r.weekdays.map((d) => WD[d]).join("/"));
  }
  if (r.earliest !== "00:00" && r.latest !== "24:00") out.push(`${r.earliest}–${r.latest}`);
  else if (r.earliest !== "00:00") out.push(`after ${r.earliest}`);
  else if (r.latest !== "24:00") out.push(`before ${r.latest}`);
  if (r.preference) out.push(`${r.preference}s preferred`);
  if (r.buffer_minutes) out.push(`${r.buffer_minutes} min buffer`);
  return out;
}

export default function Assistant({ team, viewerId, viewerTz, selectedStart, onPick }: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ScheduleResult | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const others = team.members.filter((m) => m.id !== viewerId).map((m) => m.name.split(" ")[0]);
  const examples = [
    "30 minutes with everyone next week",
    others.length ? `45 minutes with ${others.slice(0, 2).join(" and ")} next week, mornings preferred, not Friday` : "45 minutes next week, mornings preferred",
    others.length ? `1 hour with ${others[0]} tomorrow afternoon` : "1 hour tomorrow afternoon",
  ];

  async function run(q: string) {
    if (!q.trim()) return;
    setBusy(true);
    setError("");
    setOpen(null);
    try {
      setResult(await api<ScheduleResult>(`/teams/${team.id}/schedule`, { method: "POST", body: { text: q } }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const submit = (e: FormEvent) => { e.preventDefault(); run(text); };

  return (
    <div className="panel-inner">
      <h3>Find a time</h3>
      <form onSubmit={submit}>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={1000}
          placeholder="e.g. 45 minutes with Anna and Raj next week, mornings preferred, not Friday" aria-label="Describe the meeting"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); run(text); } }} />
        <button className="btn primary block" disabled={busy || !text.trim()}>{busy ? "Searching…" : "Find a time"}</button>
      </form>
      <div className="examples">
        {examples.map((x) => (
          <button key={x} className="example" onClick={() => { setText(x); run(x); }} disabled={busy}>{x}</button>
        ))}
      </div>

      {error && <div className="error" role="alert">{error}</div>}

      {result && (
        <div className="results" data-testid="results">
          <div className="parsed">
            {describeFilters(result.parsed).map((f) => <span key={f} className="pill">{f}</span>)}
            <span className="pill ghost" title="How the request was interpreted">{result.parser === "claude" ? "Claude parsed" : "rule parser"}</span>
          </div>
          <p className="muted small-text">
            With {result.parsed.required.map((p) => p.name).join(", ")}
            {result.parsed.optional.length > 0 && <> · optional: {result.parsed.optional.map((p) => p.name).join(", ")}</>}
          </p>
          {result.unknown_names.length > 0 && <div className="warn">No one named {result.unknown_names.join(", ")} is on this team.</div>}
          {result.notes.map((n) => <div key={n} className="note">{n}</div>)}

          {result.recommendation && (
            <div className={`reco ${result.slots.length ? "" : "empty"}`}>
              <span className="tag">{result.slots.length ? (result.recommendation_source === "claude" ? "AI recommendation" : "Recommendation") : "Why nothing fits"}</span>
              <p>{result.recommendation}</p>
            </div>
          )}

          {result.slots.map((s) => {
            const a = Date.parse(s.start);
            const b = Date.parse(s.end);
            const day = s.date;
            const isSel = selectedStart === s.start;
            const expanded = open === s.start;
            return (
              <div key={s.start} className={`slot ${isSel ? "selected" : ""}`} data-testid="slot">
                <button className="slot-main" onClick={() => onPick(s)}>
                  <div>
                    <strong>{fmtDay(day, { weekday: "short", day: "numeric", month: "short" })}</strong>
                    <span className="time">{fmtTime(a, viewerTz)}–{fmtTime(b, viewerTz)}</span>
                  </div>
                  <small className="muted">{cityOf(viewerTz)} · score {Math.round(s.score)}</small>
                </button>
                {s.participants.some((p) => p.role === "optional") && (
                  <div className="opt-line small-text">
                    {s.participants.filter((p) => p.role === "optional").map((p) => (
                      <span key={p.id} className={p.attending ? "ok" : "no"}>{p.attending ? "✓" : "✗"} {p.name.split(" ")[0]}</span>
                    ))}
                  </div>
                )}
                <button className="link small-text" aria-expanded={expanded} onClick={() => setOpen(expanded ? null : s.start)}>
                  {expanded ? "Hide" : "Show"} everyone's local time
                </button>
                {expanded && (
                  <ul className="locals">
                    {s.participants.map((p) => {
                      const sh = Number(clockOf(p.local_start).slice(0, 2));
                      const eh = Number(clockOf(p.local_end).slice(0, 2)) + Number(clockOf(p.local_end).slice(3)) / 60;
                      const odd = sh < 9 || eh > 18;
                      return (
                        <li key={p.id} className={!p.attending ? "dim" : ""}>
                          <span>{p.name}{p.id === viewerId ? " (you)" : ""}{p.role === "optional" ? " · optional" : ""}</span>
                          <span>{clockOf(p.local_start)}–{clockOf(p.local_end)} <small className={odd ? "odd" : "muted"}>{cityOf(p.timezone)}{odd ? " · outside 9–18" : ""}</small></span>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            );
          })}

          {result.diagnostics && (
            <table className="diag">
              <thead><tr><th>Person</th><th>Hours</th><th>In hours</th><th>Free</th></tr></thead>
              <tbody>
                {result.diagnostics.per_person.map((p) => (
                  <tr key={p.id} className={p.free_candidates === 0 ? "zero" : ""}>
                    <td>{p.name}<small>{cityOf(p.timezone)}</small></td><td>{p.working_hours}</td><td>{p.working_hours_candidates}</td><td>{p.free_candidates}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
