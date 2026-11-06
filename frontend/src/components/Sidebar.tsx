import { useState, type FormEvent } from "react";
import type { Team, User } from "../types";

interface Props {
  user: User;
  teams: Team[];
  activeId: number | null;
  onSelect: (id: number) => void;
  onCreate: (name: string) => Promise<void>;
  onJoin: (code: string) => Promise<void>;
  onLeave: () => void;
  onAllGroups: () => void;
  onCalendars: () => void;
  onSettings: () => void;
  onLogout: () => void;
}

export default function Sidebar(p: Props) {
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const active = p.teams.find((t) => t.id === p.activeId);

  async function wrap(fn: () => Promise<void>, reset: () => void) {
    setErr("");
    try {
      await fn();
      reset();
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  const create = (e: FormEvent) => { e.preventDefault(); if (name.trim()) wrap(() => p.onCreate(name.trim()), () => setName("")); };
  const join = (e: FormEvent) => { e.preventDefault(); if (code.trim()) wrap(() => p.onJoin(code.trim()), () => setCode("")); };

  return (
    <aside className="sidebar">
      <div className="brand"><span className="logo">◷</span> MeetSync</div>

      <div className="side-section">
        <button className="side-link" onClick={p.onAllGroups}>⇦ All groups</button>
        <h4>Groups</h4>
        <nav>
          {p.teams.map((t) => (
            <button key={t.id} className={`team-btn ${t.id === p.activeId ? "active" : ""}`} onClick={() => p.onSelect(t.id)}>
              <span>{t.name}</span><small>{t.members.length}</small>
            </button>
          ))}
          {!p.teams.length && <p className="muted small-text">No teams yet. Create one or join with an invite code.</p>}
        </nav>
        <form onSubmit={create} className="inline-form">
          <input placeholder="New team name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} aria-label="New team name" />
          <button className="btn small">Create</button>
        </form>
        <form onSubmit={join} className="inline-form">
          <input placeholder="Invite code" value={code} onChange={(e) => setCode(e.target.value)} aria-label="Invite code" />
          <button className="btn small">Join</button>
        </form>
        {err && <div className="error small-text" role="alert">{err}</div>}
      </div>

      {active && (
        <div className="side-section">
          <h4>{active.name}</h4>
          <div className="invite">
            <span className="muted small-text">Invite code</span>
            <code>{active.invite_code}</code>
          </div>
          <button className="btn small ghost danger" onClick={p.onLeave}>Leave team</button>
        </div>
      )}

      <div className="side-spacer" />
      <div className="side-section">
        <button className="side-link" onClick={p.onCalendars}>📅 My calendars</button>
        <button className="side-link" onClick={p.onSettings}>⚙︎ Settings</button>
        <div className="me">
          <div className="avatar">{p.user.name.slice(0, 1).toUpperCase()}</div>
          <div><strong>{p.user.name}</strong><span>{p.user.email}</span></div>
        </div>
        <button className="side-link" onClick={p.onLogout}>Log out</button>
      </div>
    </aside>
  );
}
