import { useCallback, useEffect, useState } from "react";

import { api } from "../lib/api";
import type { SessionSummary } from "../lib/types";
import { Panel } from "./ui/primitives";

/**
 * Past practice sessions (A13).
 *
 * The backend has persisted every turn from the beginning — transcript,
 * acoustic profile, citations, timings — but nothing read them back, so
 * "conversation history" existed only in SQLite. This is where it becomes a
 * feature: open a session and its turns replay, with the dysfluency timelines
 * intact, and anything said next continues that same session.
 */
interface Props {
  activeId: string | null;
  /** Bumped when a turn completes, so the list picks up new titles. */
  refreshKey: number;
  onOpen: (id: string) => void;
  onNew: () => void;
}

export function SessionHistory({ activeId, refreshKey, onOpen, onNew }: Props) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const all = await api.listSessions();
      // Empty sessions are an artefact of opening the app, not something the
      // person did. Showing them would bury the real ones.
      setSessions(all.filter((s) => s.turn_count > 0));
    } catch {
      /* The panel is supplementary; a failure here must not break the app. */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const remove = async (id: string) => {
    setBusy(true);
    try {
      await api.deleteSession(id);
      if (id === activeId) onNew();
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Past sessions">
      {sessions.length === 0 ? (
        <p className="panel-empty">
          Sessions you have spoken in are kept here so you can come back to them.
        </p>
      ) : (
        <ul className="hist-list">
          {sessions.map((s) => (
            <li key={s.id} className={s.id === activeId ? "hist is-active" : "hist"}>
              <button
                type="button"
                className="hist-open"
                onClick={() => onOpen(s.id)}
                aria-current={s.id === activeId ? "true" : undefined}
              >
                <span className="hist-title">{s.title ?? "Practice session"}</span>
                <span className="hist-meta">
                  <time dateTime={s.started_at}>{formatWhen(s.started_at)}</time>
                  <span aria-hidden="true">·</span>
                  <span className="num">{s.turn_count} turns</span>
                </span>
              </button>
              <button
                type="button"
                className="hist-del"
                onClick={() => void remove(s.id)}
                disabled={busy}
                aria-label={`Delete session: ${s.title ?? "Practice session"}`}
              >
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                  <path
                    d="M2 2l8 8M10 2l-8 8"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}

      <button type="button" className="hist-new" onClick={onNew}>
        Start a new session
      </button>
    </Panel>
  );
}

function formatWhen(iso: string): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "—";

  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
