import { useState, type FormEvent } from "react";
import type { Team, User } from "../types";
import { cityOf } from "../tz";

interface Props {
  user: User;
  teams: Team[];
  loading: boolean;
  onOpen: (id: number) => void;
  onCreate: (name: string) => Promise<void>;
  onJoin: (code: string) => Promise<void>;
  onCalendars: () => void;
  onSettings: () => void;
  onLogout: () => void;
}

export default function GroupPicker(p: Props) {
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");

  async function submit(e: FormEvent, fn: () => Promise<void>, reset: () => void) {
    e.preventDefault();
    setErr("");
    try { await fn(); reset(); } catch (x) { setErr((x as Error).message); }
  }

  return (
    <div className="picker">
      <header className="picker-top">
        <div className="brand big"><span className="logo">◷</span> MeetSync</div>
        <div className="picker-actions">
          <button className="btn small" onClick={p.onCalendars}>📅 My calendars</button>
          <button className="btn small" onClick={p.onSettings}>⚙︎ Settings</button>
          <button className="btn small ghost" onClick={p.onLogout}>Log out</button>
        </div>
      </header>

      <div className="picker-body">
        <h1>Hi {p.user.name.split(" ")[0]}, which group do you want to open?</h1>
        <p className="muted">Your calendars (Google, Outlook, ICS, manual) are connected once to <b>you</b> and show up automatically in every group below.</p>

        {p.loading && <p className="muted">Loading your groups…</p>}
        <div className="group-grid" data-testid="group-grid">
          {p.teams.map((t) => (
            <button key={t.id} className="group-card" onClick={() => p.onOpen(t.id)} data-testid={`group-${t.name}`}>
              <strong>{t.name}</strong>
              <span className="muted small-text">{t.members.length} {t.members.length === 1 ? "person" : "people"}</span>
              <div className="avatars">
                {t.members.slice(0, 6).map((m) => <span key={m.id} className="avatar sm" title={`${m.name} · ${cityOf(m.timezone)}`}>{m.name.slice(0, 1)}</span>)}
              </div>
              <span className="small-text muted">{t.members.slice(0, 4).map((m) => m.name.split(" ")[0]).join(", ")}{t.members.length > 4 ? "…" : ""}</span>
              <span className="open">Open →</span>
            </button>
          ))}
          {!p.loading && !p.teams.length && <p className="muted">You're not in any group yet. Create one or join with an invite code.</p>}
        </div>

        <div className="picker-forms">
          <form onSubmit={(e) => submit(e, () => p.onCreate(name.trim()), () => setName(""))} className="inline-form">
            <input placeholder="New group name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} aria-label="New group name" required />
            <button className="btn primary">Create group</button>
          </form>
          <form onSubmit={(e) => submit(e, () => p.onJoin(code.trim()), () => setCode(""))} className="inline-form">
            <input placeholder="Invite code" value={code} onChange={(e) => setCode(e.target.value)} aria-label="Invite code" required />
            <button className="btn">Join group</button>
          </form>
        </div>
        {err && <div className="error" role="alert">{err}</div>}
      </div>
    </div>
  );
}
