export interface User {
  id: number;
  email: string;
  name: string;
  timezone: string;
  work_start: number;
  work_end: number;
  work_days: number[];
}

export interface Member {
  id: number;
  name: string;
  timezone: string;
  work_start: number;
  work_end: number;
  work_days: number[];
}

export interface Team {
  id: number;
  name: string;
  invite_code: string;
  members: Member[];
}

export interface CalendarSrc {
  id: number;
  kind: "google" | "microsoft" | "ics_url" | "ics_file" | "manual";
  name: string;
  share_titles: boolean;
  external_account: string | null;
  ics_url: string | null;
  last_synced_at: string | null;
  last_error: string | null;
  event_count: number;
}

export interface CalEvent {
  id: number;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  location?: string | null;
  description?: string | null;
}

export interface BusyEvent extends CalEvent {
  shared: boolean;
  // Present only on your own events (the server never sends these for teammates):
  own?: boolean;
  calendar_id?: number;
  calendar_name?: string;
  calendar_kind?: CalendarSrc["kind"];
  share_titles?: boolean;
  editable?: boolean;
  last_synced_at?: string | null;
}

export interface BusyMember extends Member {
  events: BusyEvent[];
}

export interface Participant {
  id: number;
  name: string;
  timezone: string;
  role: "required" | "optional";
  attending: boolean;
  local_start: string;
  local_end: string;
}

export interface Slot {
  start: string;
  end: string;
  score: number;
  date: string;
  available_optional_ids: number[];
  missing_optional_ids: number[];
  participants: Participant[];
}

export interface Diagnostics {
  total_candidates: number;
  working_hours_overlap: boolean;
  working_hours_overlap_without_filters: boolean;
  explanation: string;
  per_person: {
    id: number;
    name: string;
    timezone: string;
    working_hours: string;
    working_hours_candidates: number;
    free_candidates: number;
  }[];
}

export interface ScheduleResult {
  parser: "claude" | "rules" | "structured";
  parsed: {
    required: { id: number; name: string }[];
    optional: { id: number; name: string }[];
    duration_minutes: number;
    start_date: string;
    end_date: string;
    earliest: string;
    latest: string;
    weekdays: number[] | null;
    preference: string | null;
    buffer_minutes: number;
    timezone: string;
  };
  unknown_names: string[];
  notes: string[];
  slots: Slot[];
  recommendation: string;
  recommendation_source: "claude" | "rules";
  diagnostics: Diagnostics | null;
}
