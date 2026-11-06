import { useMemo, useState, type FormEvent } from "react";
import { api } from "../api";
import type { User } from "../types";
import Modal from "./Modal";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const toTime = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const fromTime = (s: string) => {
  const [h, m] = s.split(":").map(Number);
  return h * 60 + m;
};

export default function SettingsModal({ user, onSaved, onClose }: { user: User; onSaved: (u: User) => void; onClose: () => void }) {
  const [name, setName] = useState(user.name);
  const [tz, setTz] = useState(user.timezone);
  const [start, setStart] = useState(toTime(user.work_start));
  const [end, setEnd] = useState(toTime(user.work_end));
  const [days, setDays] = useState<number[]>(user.work_days);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const zones = useMemo(() => Intl.supportedValuesOf("timeZone"), []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setErr("");
    if (!zones.includes(tz) && tz !== "UTC") return setErr("Pick a time zone from the list (e.g. Europe/Berlin).");
    setBusy(true);
    try {
      const u = await api<User>("/me", { method: "PATCH", body: { name, timezone: tz, work_start: fromTime(start), work_end: fromTime(end), work_days: days } });
      onSaved(u);
      onClose();
    } catch (e2) {
      setErr((e2 as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Settings" onClose={onClose}>
      <form onSubmit={save} className="stack">
        <label>Name<input value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} /></label>
        <label>Time zone
          <input list="tz-list" value={tz} onChange={(e) => setTz(e.target.value)} required />
          <datalist id="tz-list">{zones.map((z) => <option key={z} value={z} />)}</datalist>
        </label>
        <div className="two">
          <label>Work starts<input type="time" value={start} onChange={(e) => setStart(e.target.value)} required /></label>
          <label>Work ends<input type="time" value={end} onChange={(e) => setEnd(e.target.value)} required /></label>
        </div>
        <fieldset>
          <legend>Working days</legend>
          <div className="days">
            {DAYS.map((d, i) => (
              <label key={d} className={`day-toggle ${days.includes(i) ? "on" : ""}`}>
                <input type="checkbox" checked={days.includes(i)} onChange={() => setDays((cur) => (cur.includes(i) ? cur.filter((x) => x !== i) : [...cur, i].sort()))} />
                {d}
              </label>
            ))}
          </div>
        </fieldset>
        <p className="muted small-text">Teammates and the scheduler only propose times inside these hours, in your zone.</p>
        {err && <div className="error" role="alert">{err}</div>}
        <div className="actions"><button type="button" className="btn ghost" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}
