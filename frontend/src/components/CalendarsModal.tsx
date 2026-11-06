import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import type { CalendarSrc, CalEvent, User } from "../types";
import { addDays, fmtDay, fmtTime, localToUtcMs, ymdInZone } from "../tz";
import Modal from "./Modal";

const KIND_LABEL: Record<CalendarSrc["kind"], string> = {
  google: "Google", microsoft: "Outlook", ics_url: "ICS link", ics_file: "ICS file", manual: "Manual",
};

interface Props {
  user: User;
  onClose: () => void;
  onChanged: () => void;
  notify: (kind: "success" | "error" | "info", text: string) => void;
}

export default function CalendarsModal({ user, onClose, onChanged, notify }: Props) {
  const [cals, setCals] = useState<CalendarSrc[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [icsUrl, setIcsUrl] = useState("");
  const [icsName, setIcsName] = useState("");
  const [manualName, setManualName] = useState("");

  const load = useCallback(async () => {
    try {
      setCals(await api<CalendarSrc[]>("/calendars"));
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [notify]);
  useEffect(() => { load(); }, [load]);

  const changed = async () => { await load(); onChanged(); };

  async function run<T>(id: number | null, fn: () => Promise<T>, ok?: string) {
    setBusyId(id);
    try {
      const r = await fn();
      if (ok) notify("success", ok);
      await changed();
      return r;
    } catch (e) {
      notify("error", (e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  // Optimistic: flip immediately, then confirm with the server (reload restores the truth on failure).
  async function toggleShare(id: number, value: boolean) {
    setCals((cur) => cur.map((c) => (c.id === id ? { ...c, share_titles: value } : c)));
    try {
      await api(`/calendars/${id}`, { method: "PATCH", body: { share_titles: value } });
      onChanged();
    } catch (e) {
      notify("error", (e as Error).message);
      load();
    }
  }

  async function connect(provider: "google" | "microsoft") {
    try {
      const { url } = await api<{ url: string }>(`/calendars/connect/${provider}`);
      window.location.href = url;
    } catch (e) {
      notify("error", (e as Error).message);
    }
  }

  const addUrl = (e: FormEvent) => {
    e.preventDefault();
    run(-1, async () => {
      const c = await api<CalendarSrc>("/calendars/ics-url", { method: "POST", body: { url: icsUrl, name: icsName || undefined } });
      if (c.last_error) notify("error", c.last_error);
      else notify("success", `Added “${c.name}” (${c.event_count} events)`);
      setIcsUrl(""); setIcsName("");
    });
  };
  const addManual = (e: FormEvent) => {
    e.preventDefault();
    run(-1, async () => { await api("/calendars/manual", { method: "POST", body: { name: manualName } }); setManualName(""); }, "Calendar created");
  };
  const upload = (file: File | undefined) => {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    run(-1, () => api("/calendars/ics-upload", { method: "POST", form }), `Imported ${file.name}`);
  };

  return (
    <Modal title="My calendars" onClose={onClose} wide>
      <section>
        <p className="note">Calendars belong to you, so everything you connect here appears automatically in <b>every group</b> you're in.</p>
        <h4>Connect a calendar</h4>
        <div className="connect-row">
          <button className="btn" onClick={() => connect("google")}>Connect Google Calendar</button>
          <button className="btn" onClick={() => connect("microsoft")}>Connect Outlook / Microsoft 365</button>
        </div>
        <div className="add-grid">
          <form onSubmit={addUrl} className="stack-sm">
            <strong>ICS link</strong>
            <input placeholder="https:// or webcal:// link" value={icsUrl} onChange={(e) => setIcsUrl(e.target.value)} required aria-label="ICS URL" />
            <div className="inline-form"><input placeholder="Name (optional)" value={icsName} onChange={(e) => setIcsName(e.target.value)} aria-label="ICS name" /><button className="btn small" disabled={busyId === -1}>Add</button></div>
          </form>
          <div className="stack-sm">
            <strong>ICS file</strong>
            <label className="btn file-btn">Choose .ics file<input type="file" accept=".ics,text/calendar" hidden onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} /></label>
            <span className="muted small-text">Recurring events are expanded.</span>
          </div>
          <form onSubmit={addManual} className="stack-sm">
            <strong>Manual calendar</strong>
            <div className="inline-form"><input placeholder="e.g. Personal" value={manualName} onChange={(e) => setManualName(e.target.value)} required aria-label="Manual calendar name" /><button className="btn small" disabled={busyId === -1}>Create</button></div>
            <span className="muted small-text">Add busy blocks by hand.</span>
          </form>
        </div>
      </section>

      <section>
        <h4>Connected</h4>
        {loading && <p className="muted">Loading…</p>}
        {!loading && !cals.length && <p className="muted">Nothing connected yet. Teammates only see busy blocks from calendars you add.</p>}
        <ul className="cal-list">
          {cals.map((c) => (
            <li key={c.id} className="cal-item" data-testid="calendar-item">
              <div className="cal-top">
                <span className={`kind ${c.kind}`}>{KIND_LABEL[c.kind]}</span>
                <strong>{c.name}</strong>
                <span className="muted small-text">
                  {c.external_account ? `${c.external_account} · ` : ""}{c.event_count} {c.event_count === 1 ? "event" : "events"}
                  {c.last_synced_at ? ` · synced ${new Date(c.last_synced_at).toLocaleString()}` : c.kind === "manual" ? "" : " · never synced"}
                </span>
              </div>
              {c.last_error && <div className="error small-text">{c.last_error}</div>}
              <div className="cal-actions">
                <label className="switch" title="When on, teammates see event titles instead of “Busy”">
                  <input type="checkbox" checked={c.share_titles}
                    onChange={(e) => toggleShare(c.id, e.target.checked)} />
                  <span>Share event titles</span>
                </label>
                <span className="spacer" />
                {c.kind === "manual" && <button className="btn small" onClick={() => setEditing(editing === c.id ? null : c.id)}>{editing === c.id ? "Close events" : "Edit events"}</button>}
                {c.kind !== "manual" && <button className="btn small" disabled={busyId === c.id} onClick={() => run(c.id, async () => {
                  const r = await api<CalendarSrc>(`/calendars/${c.id}/sync`, { method: "POST" });
                  if (r.last_error) notify("error", r.last_error); else notify("success", `Synced ${r.event_count} events`);
                })}>{busyId === c.id ? "Syncing…" : "Sync"}</button>}
                <button className="btn small ghost danger" disabled={busyId === c.id} onClick={() => { if (confirm(`Remove “${c.name}” and its events?`)) run(c.id, () => api(`/calendars/${c.id}`, { method: "DELETE" }), "Calendar removed"); }}>Remove</button>
              </div>
              {editing === c.id && <EventEditor calId={c.id} tz={user.timezone} onChanged={changed} notify={notify} />}
            </li>
          ))}
        </ul>
      </section>
    </Modal>
  );
}

function EventEditor({ calId, tz, onChanged, notify }: { calId: number; tz: string; onChanged: () => void; notify: Props["notify"] }) {
  const today = ymdInZone(Date.now(), tz);
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [editId, setEditId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [date, setDate] = useState(today);
  const [start, setStart] = useState("09:00");
  const [end, setEnd] = useState("10:00");
  const [allDay, setAllDay] = useState(false);

  const load = useCallback(async () => {
    try { setEvents(await api<CalEvent[]>(`/calendars/${calId}/events`)); } catch (e) { notify("error", (e as Error).message); }
  }, [calId, notify]);
  useEffect(() => { load(); }, [load]);

  const mins = (s: string) => { const [h, m] = s.split(":").map(Number); return h * 60 + m; };
  const reset = () => { setEditId(null); setTitle(""); setAllDay(false); };

  async function save(e: FormEvent) {
    e.preventDefault();
    const s = allDay ? localToUtcMs(date, 0, tz) : localToUtcMs(date, mins(start), tz);
    const en = allDay ? localToUtcMs(addDays(date, 1), 0, tz) : localToUtcMs(date, mins(end), tz);
    if (en <= s) return notify("error", "The event must end after it starts.");
    const body = { title: title || "Busy", start: new Date(s).toISOString(), end: new Date(en).toISOString(), all_day: allDay };
    try {
      if (editId) await api(`/calendars/${calId}/events/${editId}`, { method: "PUT", body });
      else await api(`/calendars/${calId}/events`, { method: "POST", body });
      reset();
      await load();
      onChanged();
    } catch (err) { notify("error", (err as Error).message); }
  }
  async function remove(id: number) {
    try { await api(`/calendars/${calId}/events/${id}`, { method: "DELETE" }); await load(); onChanged(); } catch (err) { notify("error", (err as Error).message); }
  }
  function edit(ev: CalEvent) {
    const s = Date.parse(ev.start);
    setEditId(ev.id); setTitle(ev.title); setAllDay(ev.all_day);
    setDate(ymdInZone(s, tz)); setStart(fmtTime(s, tz)); setEnd(fmtTime(Date.parse(ev.end), tz));
  }

  return (
    <div className="editor">
      <form onSubmit={save} className="event-form">
        <input placeholder="Title (shown only if you share titles)" value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Event title" />
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required aria-label="Event date" />
        <input type="time" value={start} onChange={(e) => setStart(e.target.value)} disabled={allDay} aria-label="Start time" />
        <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} disabled={allDay} aria-label="End time" />
        <label className="switch"><input type="checkbox" checked={allDay} onChange={(e) => setAllDay(e.target.checked)} /><span>All day</span></label>
        <button className="btn small primary">{editId ? "Save" : "Add"}</button>
        {editId && <button type="button" className="btn small ghost" onClick={reset}>Cancel</button>}
      </form>
      <p className="muted small-text">Times are in your zone ({tz}).</p>
      <ul className="event-list">
        {events.length === 0 && <li className="muted">No events yet.</li>}
        {events.map((ev) => (
          <li key={ev.id}>
            <span>{fmtDay(ymdInZone(Date.parse(ev.start), tz))} · {ev.all_day ? "All day" : `${fmtTime(Date.parse(ev.start), tz)}–${fmtTime(Date.parse(ev.end), tz)}`} · {ev.title}</span>
            <span><button className="link" onClick={() => edit(ev)}>Edit</button> <button className="link danger" onClick={() => remove(ev.id)}>Delete</button></span>
          </li>
        ))}
      </ul>
    </div>
  );
}
