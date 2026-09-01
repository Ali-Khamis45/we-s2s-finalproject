import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type { ProgressOut, SystemStatus } from "../lib/types";

/**
 * Practice trends across sessions (A17).
 *
 * Framed as change over time, never as an assessment. There is no score and no
 * "good" direction marked on the chart — the point is to let someone see that
 * they have been practising and that their pacing is settling, which is the
 * measurable value the brief asks a product to deliver.
 */
export function ProgressPanel({
  status,
  refreshKey,
}: {
  status: SystemStatus | null;
  refreshKey: number;
}) {
  const [progress, setProgress] = useState<ProgressOut | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .progress()
      .then((p) => !cancelled && setProgress(p))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const points = progress?.points ?? [];
  const minutes = Math.round((progress?.total_practice_ms ?? 0) / 60000);

  return (
    <aside className="panel">
      <section className="panel-block">
        <h3>Your practice</h3>
        {points.length === 0 ? (
          <p className="panel-empty">
            Speak in a session and your pacing trend will build here.
          </p>
        ) : (
          <>
            <div className="stat-row">
              <Stat label="Sessions" value={String(progress?.sessions ?? 0)} />
              <Stat label="Spoken" value={`${minutes} min`} />
            </div>
            <PaceChart points={points} />
          </>
        )}
      </section>

      <section className="panel-block">
        <h3>System</h3>
        <ul className="sys-list">
          <SysRow
            label="Live coach"
            ok={status?.live_available ?? false}
            okText="ready"
            offText="not running"
          />
          <SysRow
            label="Coaching model"
            ok={status?.llm_reachable ?? false}
            okText="ready"
            offText="not running"
          />
          <SysRow
            label="Knowledge base"
            ok={(status?.corpus_chunks ?? 0) > 0}
            okText={`${status?.corpus_chunks ?? 0} chunks`}
            offText="empty"
          />
          <li className="sys-row">
            <span>Analyzer</span>
            <code>{status?.analyzer ?? "—"}</code>
          </li>
          <li className="sys-row">
            <span>Prompt</span>
            <code>{status?.prompt_version ?? "—"}</code>
          </li>
          <li className="sys-row">
            <span>Checkpoint</span>
            <code>{status?.llm_variant ?? "—"}</code>
          </li>
        </ul>
      </section>
    </aside>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

function SysRow({
  label,
  ok,
  okText,
  offText,
}: {
  label: string;
  ok: boolean;
  okText: string;
  offText: string;
}) {
  return (
    <li className="sys-row">
      <span>{label}</span>
      <span className={`sys-state ${ok ? "is-ok" : "is-off"}`}>
        {ok ? okText : offText}
      </span>
    </li>
  );
}

/**
 * Speaking pace across sessions.
 *
 * Pace is charted rather than fluency load because it is the metric a speaker
 * can act on directly, and because charting "amount of dysfluency" over time
 * would turn the dashboard into the scoreboard this project deliberately isn't.
 */
function PaceChart({ points }: { points: ProgressOut["points"] }) {
  const rates = points
    .map((p) => p.mean_speech_rate_wpm)
    .filter((r): r is number => r !== null);

  if (rates.length < 2) {
    return (
      <p className="panel-empty">
        One more session and the pace trend will appear.
      </p>
    );
  }

  const width = 260;
  const height = 72;
  const pad = 6;
  const min = Math.min(...rates) - 8;
  const max = Math.max(...rates) + 8;
  const span = Math.max(max - min, 1);

  const coords = rates.map((r, i) => {
    const x = pad + (i / (rates.length - 1)) * (width - pad * 2);
    const y = height - pad - ((r - min) / span) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const last = rates[rates.length - 1];

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img"
        aria-label={`Speaking pace across ${rates.length} sessions, most recently ${Math.round(last)} words per minute`}>
        <polyline
          className="chart-line"
          points={coords.join(" ")}
          fill="none"
          strokeWidth="1.75"
        />
        <circle
          className="chart-dot"
          cx={coords[coords.length - 1].split(",")[0]}
          cy={coords[coords.length - 1].split(",")[1]}
          r="3"
        />
      </svg>
      <figcaption>
        Speaking pace · latest <b>{Math.round(last)} wpm</b>
      </figcaption>
    </figure>
  );
}
