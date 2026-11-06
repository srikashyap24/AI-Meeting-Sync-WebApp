import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { fmtDay, fmtTime, ymdInZone } from "../tz";

interface Added {
  id: number;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  date: string;
  location?: string | null;
}
interface Resp {
  status: "question" | "added" | "none";
  question?: string;
  parser: "llm" | "rules";
  calendar_id: number | null;
  events: Added[];
  notes: string[];
}
type Turn = { role: "user" | "assistant"; content: string };
type Line =
  | { kind: "user"; text: string }
  | { kind: "question"; text: string }
  | { kind: "note"; text: string }
  | { kind: "added"; calId: number; events: Added[]; parser: string };

interface Props {
  viewerTz: string;
  onChanged: (focusDate?: string) => void;
  notify: (kind: "success" | "error" | "info", text: string) => void;
}

const EXAMPLES = [
  "Dentist tomorrow 3-4pm",
  "Gym on Monday at 6pm for an hour",
  "I'm on leave next Wednesday and Thursday",
  "Lunch with Sam on 12 Nov at 1pm; call mum today at 8pm",
];

export default function QuickAdd({ viewerTz, onChanged, notify }: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [history, setHistory] = useState<Turn[]>([]); // only the open clarification; cleared once events are added
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest" }); }, [lines, busy]);

  const asking = history.length > 0;

  async function run(q: string) {
    q = q.trim();
    if (!q) return;
    setBusy(true);
    setText("");
    setLines((l) => [...l, { kind: "user", text: q }]);
    try {
      const r = await api<Resp>("/me/quick-add", { method: "POST", body: { text: q, history } });
      if (r.status === "question") {
        setHistory((h) => [...h, { role: "user", content: q }, { role: "assistant", content: r.question! }]);
        setLines((l) => [...l, { kind: "question", text: r.question! }]);
      } else if (r.status === "added") {
        setHistory([]);
        setLines((l) => [...l, { kind: "added", calId: r.calendar_id!, events: r.events, parser: r.parser }, ...r.notes.map((n) => ({ kind: "note" as const, text: n }))]);
        notify("success", r.events.length === 1 ? `Added “${r.events[0].title}”` : `Added ${r.events.length} events`);
        onChanged(r.events[0].date);
      } else {
        setHistory([]);
        setLines((l) => [...l, ...r.notes.map((n) => ({ kind: "note" as const, text: n }))]);
      }
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const submit = (e: FormEvent) => { e.preventDefault(); run(text); };

  async function undo(calId: number, ev: Added) {
    try {
      await api(`/calendars/${calId}/events/${ev.id}`, { method: "DELETE" });
      setLines((l) => l.map((x) => (x.kind === "added" ? { ...x, events: x.events.filter((e) => e.id !== ev.id) } : x)).filter((x) => x.kind !== "added" || x.events.length));
      onChanged();
    } catch (e) {
      notify("error", (e as Error).message);
    }
  }

  return (
    <div className="panel-inner">
      <h3>Add to my calendar</h3>
      <p className="muted small-text">Say what you'll be doing and when. If something important is missing I'll ask. Saved to your “Quick add” calendar, shown in every group.</p>

      {lines.length > 0 && (
        <div className="chat" data-testid="chat">
          {lines.map((l, i) => {
            if (l.kind === "user") return <div key={i} className="bubble me">{l.text}</div>;
            if (l.kind === "question") return <div key={i} className="bubble bot" data-testid="question">{l.text}</div>;
            if (l.kind === "note") return <div key={i} className="warn">{l.text}</div>;
            return (
              <div key={i} className="bubble bot added-card" data-testid="added-list">
                <div className="muted small-text">Added <span className="pill ghost">{l.parser === "llm" ? "AI parsed" : "rule parser"}</span></div>
                {l.events.map((it) => (
                  <div key={it.id} className="added-row" data-testid="added-item">
                    <div>
                      <strong>{it.title}</strong>
                      <div className="muted small-text">
                        {fmtDay(ymdInZone(Date.parse(it.start), viewerTz))} · {it.all_day ? "All day" : `${fmtTime(Date.parse(it.start), viewerTz)}–${fmtTime(Date.parse(it.end), viewerTz)}`}{it.location ? ` · ${it.location}` : ""}
                      </div>
                    </div>
                    <button className="btn small ghost danger" onClick={() => undo(l.calId, it)} aria-label={`Undo ${it.title}`}>Undo</button>
                  </div>
                ))}
                <div className="muted small-text">Click the event on the calendar to see details or edit it.</div>
              </div>
            );
          })}
          {busy && <div className="bubble bot typing">Thinking…</div>}
          <div ref={bottom} />
        </div>
      )}

      <form onSubmit={submit}>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={asking ? 2 : 3} maxLength={1000} aria-label="Describe what you'll be doing"
          placeholder={asking ? "Type your answer…" : "e.g. Dentist tomorrow 3-4pm"} disabled={busy}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); run(text); } }} />
        <button className="btn primary block" disabled={busy || !text.trim()}>{busy ? "Working…" : asking ? "Send answer" : "Add to my calendar"}</button>
        {asking && <button type="button" className="link small-text" onClick={() => { setHistory([]); setLines((l) => [...l, { kind: "note", text: "Cancelled. Nothing was added." }]); }}>Cancel this request</button>}
      </form>
      {!asking && lines.length === 0 && (
        <div className="examples">
          {EXAMPLES.map((x) => <button key={x} className="example" onClick={() => setText(x)} disabled={busy}>{x}</button>)}
        </div>
      )}
    </div>
  );
}
