import { useState, type FormEvent } from "react";
import { api } from "../api";
import type { BusyEvent, BusyMember, CalendarSrc } from "../types";
import { addDays, cityOf, fmtDay, fmtTime, localToUtcMs, ymdInZone } from "../tz";
import Modal from "./Modal";

interface Props {
  member: BusyMember;
  ev: BusyEvent;
  members: BusyMember[];
  viewerId: number;
  viewerTz: string;
  color: string;
  onClose: () => void;
  onChanged: () => void;
  notify: (kind: "success" | "error" | "info", text: string) => void;
}

const KIND: Record<CalendarSrc["kind"], string> = { google: "Google Calendar", microsoft: "Outlook", ics_url: "ICS link", ics_file: "ICS file", manual: "Manual calendar" };
const mins = (s: string) => { const [h, m] = s.split(":").map(Number); return h * 60 + m; };

function duration(a: number, b: number): string {
  const m = Math.round((b - a) / 60000);
  if (m >= 24 * 60 && m % (24 * 60) < 60) return `${Math.round(m / (24 * 60))} day${m >= 48 * 60 ? "s" : ""}`;
  const h = Math.floor(m / 60);
  return [h ? `${h} h` : "", m % 60 ? `${m % 60} min` : ""].filter(Boolean).join(" ");
}

export default function EventDialog({ member, ev, members, viewerId, viewerTz, color, onClose, onChanged, notify }: Props) {
  const own = member.id === viewerId && ev.own;
  const a = Date.parse(ev.start);
  const b = Date.parse(ev.end);
  const titled = !!ev.title && ev.title !== "Busy";
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);

  // edit form state (in the viewer's own zone)
  const [title, setTitle] = useState(ev.title);
  const [date, setDate] = useState(ymdInZone(a, viewerTz));
  const [start, setStart] = useState(fmtTime(a, viewerTz));
  const [end, setEnd] = useState(fmtTime(b, viewerTz));
  const [allDay, setAllDay] = useState(ev.all_day);
  const [location, setLocation] = useState(ev.location ?? "");
  const [notes, setNotes] = useState(ev.description ?? "");

  const overlapping = members.flatMap((m) => m.events.filter((o) => !(m.id === member.id && o.id === ev.id) && Date.parse(o.start) < b && Date.parse(o.end) > a).map((o) => ({ m, o })));
  const day = ymdInZone(a, viewerTz);

  async function save(e: FormEvent) {
    e.preventDefault();
    const s = allDay ? localToUtcMs(date, 0, viewerTz) : localToUtcMs(date, mins(start), viewerTz);
    // an end of 00:00 on a timed event means "until midnight", i.e. the start of the next day
    const endsAtMidnight = !allDay && mins(end) === 0 && mins(start) > 0;
    const en = allDay || endsAtMidnight ? localToUtcMs(addDays(date, 1), 0, viewerTz) : localToUtcMs(date, mins(end), viewerTz);
    if (en <= s) return notify("error", "The event must end after it starts.");
    setBusy(true);
    try {
      await api(`/calendars/${ev.calendar_id}/events/${ev.id}`, { method: "PUT", body: {
        title: title.trim() || "Busy", start: new Date(s).toISOString(), end: new Date(en).toISOString(), all_day: allDay, location, description: notes } });
      notify("success", "Event updated");
      setEditing(false);
      onChanged();
    } catch (err) { notify("error", (err as Error).message); } finally { setBusy(false); }
  }

  async function remove() {
    if (!confirm(`Delete “${ev.title}”?`)) return;
    setBusy(true);
    try {
      await api(`/calendars/${ev.calendar_id}/events/${ev.id}`, { method: "DELETE" });
      notify("success", "Event deleted");
      onChanged();
      onClose();
    } catch (err) { notify("error", (err as Error).message); setBusy(false); }
  }

  const heading = own || titled ? ev.title || "Busy" : "Busy";

  return (
    <Modal title="Event details" onClose={onClose}>
      {!editing ? (
        <div className="evd" data-testid="event-details">
          <div className="evd-head" style={{ borderLeftColor: color }}>
            <h2>{heading}</h2>
            <span className="muted">{member.name}{member.id === viewerId ? " (you)" : ""} · {cityOf(member.timezone)}</span>
          </div>

          <dl>
            <dt>When</dt>
            <dd>
              {fmtDay(day, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}<br />
              {ev.all_day ? "All day" : <>{fmtTime(a, viewerTz)}–{fmtTime(b, viewerTz)} <span className="muted">({cityOf(viewerTz)} · {duration(a, b)})</span></>}
              {!ev.all_day && member.timezone !== viewerTz && (
                <div className="muted small-text">{member.name.split(" ")[0]}'s time: {fmtTime(a, member.timezone)}–{fmtTime(b, member.timezone)} ({cityOf(member.timezone)})</div>
              )}
            </dd>

            {own && ev.location && (<><dt>Location</dt><dd>{ev.location}</dd></>)}
            {own && ev.description && (<><dt>Notes</dt><dd className="pre">{ev.description}</dd></>)}

            {own ? (
              <>
                <dt>Calendar</dt>
                <dd>{ev.calendar_name} <span className="muted">· {KIND[ev.calendar_kind!]}{ev.last_synced_at ? ` · synced ${new Date(ev.last_synced_at).toLocaleString()}` : ""}</span></dd>
                <dt>Teammates see</dt>
                <dd>{ev.share_titles ? <>The title (“{ev.title}”) — never the location or notes</> : <>Only “Busy” <span className="muted">(turn on “Share event titles” for this calendar to show the title)</span></>}</dd>
              </>
            ) : (
              <><dt>Shown as</dt><dd>{titled ? "Title shared by the calendar owner" : "Busy — details are private to the owner"}</dd></>
            )}

            <dt>Same time</dt>
            <dd>
              {overlapping.length === 0 ? <span className="muted">No one else is busy at this time</span> : (
                <ul className="overlaps">
                  {overlapping.map(({ m, o }) => (
                    <li key={`${m.id}-${o.id}`}><b>{m.name.split(" ")[0]}</b> · {o.title && o.title !== "Busy" ? o.title : "Busy"} <span className="muted">{fmtTime(Date.parse(o.start), viewerTz)}–{fmtTime(Date.parse(o.end), viewerTz)}</span></li>
                  ))}
                </ul>
              )}
            </dd>
          </dl>

          {own && !ev.editable && (
            <div className="note">This event is synced from {KIND[ev.calendar_kind!]}, so it can't be edited here. Change it in the original calendar and press Sync. To block time manually, use “Add to my calendar”.</div>
          )}
          <div className="actions">
            {own && ev.editable && <button className="btn danger" onClick={remove} disabled={busy}>Delete</button>}
            <span className="spacer" />
            <button className="btn" onClick={onClose}>Close</button>
            {own && ev.editable && <button className="btn primary" onClick={() => setEditing(true)}>Edit</button>}
          </div>
        </div>
      ) : (
        <form onSubmit={save} className="stack" data-testid="event-edit-form">
          <label>Title<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={500} autoFocus /></label>
          <div className="two">
            <label>Date<input type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></label>
            <label className="switch allday"><input type="checkbox" checked={allDay} onChange={(e) => setAllDay(e.target.checked)} /><span>All day</span></label>
          </div>
          {!allDay && (
            <div className="two">
              <label>Starts<input type="time" value={start} onChange={(e) => setStart(e.target.value)} required /></label>
              <label>Ends<input type="time" value={end} onChange={(e) => setEnd(e.target.value)} required /></label>
            </div>
          )}
          <p className="muted small-text">Times are in your zone ({viewerTz}).</p>
          <label>Location<input value={location} onChange={(e) => setLocation(e.target.value)} maxLength={500} placeholder="Optional" /></label>
          <label>Notes<textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={4} maxLength={5000} placeholder="Optional. Only you can see location and notes." /></label>
          <div className="actions">
            <button type="button" className="btn ghost" onClick={() => setEditing(false)}>Cancel</button>
            <button className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save changes"}</button>
          </div>
        </form>
      )}
    </Modal>
  );
}
