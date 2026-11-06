import { useCallback, useEffect, useMemo, useState } from "react";
import { api, getToken, setToken } from "./api";
import type { BusyEvent, BusyMember, Slot, Team, User } from "./types";
import { addDays, dayBounds, weekdayOf, ymdInZone } from "./tz";
import AuthPage from "./components/AuthPage";
import Sidebar from "./components/Sidebar";
import Timeline from "./components/Timeline";
import WeekView from "./components/WeekView";
import GroupPicker from "./components/GroupPicker";
import Assistant from "./components/Assistant";
import QuickAdd from "./components/QuickAdd";
import EventDialog from "./components/EventDialog";
import { PALETTE } from "./components/WeekView";
import CalendarsModal from "./components/CalendarsModal";
import SettingsModal from "./components/SettingsModal";
import Toast, { type ToastMsg } from "./components/Toast";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [booting, setBooting] = useState(!!getToken());
  const [toast, setToast] = useState<ToastMsg | null>(null);

  // OAuth return: /?oauth=success&provider=google (or error + message)
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const status = q.get("oauth");
    if (!status) return;
    const provider = q.get("provider") === "microsoft" ? "Outlook" : "Google";
    setToast(status === "success"
      ? { kind: "success", text: `${provider} Calendar connected and synced.` }
      : { kind: "error", text: q.get("message") || `Couldn't connect ${provider}.` });
    window.history.replaceState({}, "", window.location.pathname);
  }, []);

  useEffect(() => {
    if (!getToken()) return;
    api<User>("/me").then(setUser).catch(() => setToken(null)).finally(() => setBooting(false));
  }, []);
  useEffect(() => {
    const h = () => setUser(null);
    window.addEventListener("meetsync:logout", h);
    return () => window.removeEventListener("meetsync:logout", h);
  }, []);

  const notify = useCallback((kind: ToastMsg["kind"], text: string) => setToast({ kind, text }), []);
  const closeToast = useCallback(() => setToast(null), []);

  if (booting) return <div className="boot">Loading…</div>;
  return (
    <>
      {user ? <Workspace user={user} setUser={setUser} notify={notify} /> : <AuthPage onAuthed={setUser} />}
      <Toast toast={toast} onClose={closeToast} />
    </>
  );
}

function Workspace({ user, setUser, notify }: { user: User; setUser: (u: User | null) => void; notify: (k: ToastMsg["kind"], t: string) => void }) {
  const [teams, setTeams] = useState<Team[]>([]);
  const [teamId, setTeamId] = useState<number | null>(null);
  const [date, setDate] = useState(() => ymdInZone(Date.now(), user.timezone));
  const [full24, setFull24] = useState(false);
  const [view, setView] = useState<"week" | "day">("week");
  const [panel, setPanel] = useState<"find" | "add">("find");
  const [sel, setSel] = useState<{ memberId: number; eventId: number } | null>(null);
  const [teamsLoaded, setTeamsLoaded] = useState(false);
  const [busy, setBusy] = useState<BusyMember[]>([]);
  const [loading, setLoading] = useState(false);
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [picked, setPicked] = useState<Slot | null>(null);
  const [modal, setModal] = useState<"calendars" | "settings" | null>(null);
  const [refresh, setRefresh] = useState(0);

  const loadTeams = useCallback(async (select?: number) => {
    const t = await api<Team[]>("/teams");
    setTeams(t);
    setTeamId((cur) => select ?? (cur && t.some((x) => x.id === cur) ? cur : null));
    setTeamsLoaded(true);
  }, []);
  useEffect(() => { loadTeams().catch((e) => { notify("error", e.message); setTeamsLoaded(true); }); }, [loadTeams, notify]);

  const team = teams.find((t) => t.id === teamId) ?? null;

  useEffect(() => {
    if (!team) { setBusy([]); return; }
    let cancelled = false;
    const monday = addDays(date, -weekdayOf(date));
    const [s, e] = view === "week" ? [dayBounds(monday, user.timezone)[0], dayBounds(addDays(monday, 6), user.timezone)[1]] : dayBounds(date, user.timezone);
    setLoading(true);
    api<{ members: BusyMember[] }>(`/teams/${team.id}/busy?start=${new Date(s).toISOString()}&end=${new Date(e).toISOString()}`)
      .then((r) => !cancelled && setBusy(r.members))
      .catch((err) => !cancelled && notify("error", err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [team?.id, view === "week" ? addDays(date, -weekdayOf(date)) : date, view, user.timezone, refresh, notify]); // eslint-disable-line react-hooks/exhaustive-deps

  const included = useMemo(() => new Set((team?.members ?? []).map((m) => m.id).filter((id) => !excluded.has(id))), [team, excluded]);
  const toggle = (id: number) => setExcluded((cur) => { const n = new Set(cur); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const pick = (s: Slot) => {
    setPicked(s);
    setDate(ymdInZone(Date.parse(s.start), user.timezone));
  };

  async function leave() {
    if (!team || !confirm(`Leave “${team.name}”?`)) return;
    try {
      await api(`/teams/${team.id}/leave`, { method: "POST" });
      setTeamId(null);
      await loadTeams();
    } catch (e) { notify("error", (e as Error).message); }
  }

  const modals = (
    <>
      {modal === "calendars" && <CalendarsModal user={user} onClose={() => setModal(null)} onChanged={() => setRefresh((n) => n + 1)} notify={notify} />}
      {modal === "settings" && <SettingsModal user={user} onSaved={(u) => { setUser(u); setDate(ymdInZone(Date.now(), u.timezone)); loadTeams(); }} onClose={() => setModal(null)} />}
    </>
  );
  const create = async (name: string) => { const t = await api<Team>("/teams", { method: "POST", body: { name } }); await loadTeams(t.id); };
  const join = async (code: string) => { const t = await api<Team>("/teams/join", { method: "POST", body: { invite_code: code } }); await loadTeams(t.id); };
  const logout = () => { setToken(null); setUser(null); };

  if (!team) {
    return (
      <>
        <GroupPicker user={user} teams={teams} loading={!teamsLoaded} onOpen={(id) => { setTeamId(id); setPicked(null); setExcluded(new Set()); }}
          onCreate={create} onJoin={join} onCalendars={() => setModal("calendars")} onSettings={() => setModal("settings")} onLogout={logout} />
        {modals}
      </>
    );
  }

  const members = busy.length ? busy : team.members.map((m) => ({ ...m, events: [] }));
  const selMember = sel ? members.find((m) => m.id === sel.memberId) : undefined;
  const selEvent = selMember?.events.find((e) => e.id === sel?.eventId);
  const openEvent = (m: BusyMember, e: BusyEvent) => setSel({ memberId: m.id, eventId: e.id });
  const goDay = (d: string) => { setDate(d); setView("day"); setPicked(null); };

  return (
    <div className="app">
      <Sidebar
        user={user} teams={teams} activeId={teamId}
        onSelect={(id) => { setTeamId(id); setPicked(null); setExcluded(new Set()); }}
        onAllGroups={() => { setTeamId(null); setPicked(null); }}
        onCreate={create} onJoin={join} onLeave={leave}
        onCalendars={() => setModal("calendars")} onSettings={() => setModal("settings")} onLogout={logout}
      />
      <main className="main">
        <div className="view-switch" role="tablist" aria-label="View">
          <button role="tab" aria-selected={view === "week"} className={view === "week" ? "on" : ""} onClick={() => setView("week")}>Week</button>
          <button role="tab" aria-selected={view === "day"} className={view === "day" ? "on" : ""} onClick={() => setView("day")}>Day · working hours</button>
          <span className="muted small-text">{team.name}</span>
        </div>
        {view === "week" ? (
          <WeekView members={members} viewerTz={user.timezone} weekStart={addDays(date, -weekdayOf(date))}
            onWeek={(m) => { setDate(m); setPicked(null); }} onOpenDay={goDay} full24={full24} onFull24={setFull24} included={included}
            highlight={picked ? { start: Date.parse(picked.start), end: Date.parse(picked.end) } : null} loading={loading} onEvent={openEvent} />
        ) : (
          <Timeline members={members} viewerId={user.id} viewerTz={user.timezone} date={date} onDate={(d) => { setDate(d); setPicked(null); }}
            full24={full24} onFull24={setFull24} included={included} onToggleIncluded={toggle}
            highlight={picked ? { start: Date.parse(picked.start), end: Date.parse(picked.end) } : null} loading={loading} onEvent={openEvent} />
        )}
      </main>
      <aside className="assistant">
        <div className="panel-tabs" role="tablist" aria-label="Assistant">
          <button role="tab" aria-selected={panel === "find"} className={panel === "find" ? "on" : ""} onClick={() => setPanel("find")}>Find a time</button>
          <button role="tab" aria-selected={panel === "add"} className={panel === "add" ? "on" : ""} onClick={() => setPanel("add")}>Add to my calendar</button>
        </div>
        {panel === "find" ? (
          <Assistant key={team.id} team={team} viewerId={user.id} viewerTz={user.timezone} selectedStart={picked?.start ?? null} onPick={pick} />
        ) : (
          <QuickAdd viewerTz={user.timezone} notify={notify} onChanged={(d) => { if (d) setDate(d); setRefresh((n) => n + 1); }} />
        )}
      </aside>
      {selMember && selEvent && (
        <EventDialog key={`${selMember.id}-${selEvent.id}`} member={selMember} ev={selEvent} members={members} viewerId={user.id} viewerTz={user.timezone}
          color={PALETTE[Math.max(0, members.findIndex((m) => m.id === selMember.id)) % PALETTE.length]}
          onClose={() => setSel(null)} onChanged={() => setRefresh((n) => n + 1)} notify={notify} />
      )}
      {modals}
    </div>
  );
}
