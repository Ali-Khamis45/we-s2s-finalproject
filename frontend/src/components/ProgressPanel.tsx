import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type { Message, ProgressOut, SystemStatus } from "../lib/types";
import { SessionTimings } from "./TurnTimings";
import { Panel, Stat } from "./ui/primitives";

/**
 * Practice trends across sessions.
 *
 * Framed as change over time, never as an assessment. There is no score, no
 * grade, and deliberately no "good" direction marked on the chart â€” a
 * downward-trend style here would turn the dashboard into the scoreboard this
 * product exists not to be (docs/ETHICS.md).
 */
export function ProgressPanel({
  status,
  refreshKey,
  messages = [],
}: {
  status: SystemStatus | null;
  refreshKey: number;
  /** Used only for the per-stage aggregate; never for anything about the user. */
  messages?: Message[];
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
    <>
      <Panel title="Your practice">
        {points.length === 0 ? (
          <p className="panel-empty">
            Your pacing over time will show up here once you have spoken a little.
          </p>
        ) : (
          <>
            <div className="stat-row">
              <Stat value={String(progress?.sessions ?? 0)} label="Sessions" />
              <Stat value={`${minutes} min`} label="Spoken" />
            </div>
            <PaceChart points={points} />
          </>
        )}
      </Panel>

      <Panel title="System">
        {/* Speed of the machine, not of the speaker. This is the one chart in
            the app allowed to be about latency. */}
        <SessionTimings messages={messages} />
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
            <code>{status?.analyzer ?? "â€”"}</code>
          </li>
          <li className="sys-row">
            <span>Prompt</span>
            <code>{status?.prompt_version ?? "â€”"}</code>
          </li>
          <li className="sys-row">
            <span>Checkpoint</span>
            <code>{status?.llm_variant ?? "â€”"}</code>
          </li>
        </ul>
      </Panel>
    </>
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
      {/* State is carried by the word, not only by the dot's colour. */}
      <span className={`sys-state ${ok ? "is-ok" : "is-off"}`}>
        <i className="sys-dot" aria-hidden="true" />
        {ok ? okText : offText}
      </span>
    </li>
  );
}

/**
 * Speaking pace across sessions.
 *
 * Pace rather than fluency load: it is the metric a speaker can act on, and
 * charting "amount of dysfluency over time" would be a severity score with a
 * line through it.
 *
 * One series, so no legend â€” the caption names it. Recessive gridlines, a
 * single emphasised endpoint, and no value printed on every point.
 */
function PaceChart({ points }: { points: NonNullable<ProgressOut["points"]> }) {
  const rates = points
    .map((p) => p.mean_speech_rate_wpm)
    .filter((r): r is number => r !== null);

  if (rates.length < 2) {
    return <p className="panel-empty">One more session and the trend appears.</p>;
  }

  const width = 268;
  const height = 78;
  const pad = 8;
  const min = Math.min(...rates) - 8;
  const max = Math.max(...rates) + 8;
  const span = Math.max(max - min, 1);

  const xy = rates.map((r, i) => {
    const x = pad + (i / (rates.length - 1)) * (width - pad * 2);
    const y = height - pad - ((r - min) / span) * (height - pad * 2);
    return [x, y] as const;
  });

  const path = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const last = rates[rates.length - 1];
  const [lx, ly] = xy[xy.length - 1];

  // Rough path length, so the draw-in dash animation covers the whole line.
  const length = xy.reduce(
    (sum, [x, y], i) =>
      i === 0 ? 0 : sum + Math.hypot(x - xy[i - 1][0], y - xy[i - 1][1]),
    0,
  );

  return (
    <figure className="chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Speaking pace across ${rates.length} sessions, most recently ${Math.round(last)} words per minute`}
        style={{ ["--chart-len" as string]: Math.ceil(length + 2) }}
      >
        {/* Two recessive gridlines. Enough to read level, not enough to compete. */}
        {[0.33, 0.67].map((t) => (
          <line
            key={t}
            className="chart-grid"
            x1={pad}
            x2={width - pad}
            y1={pad + t * (height - pad * 2)}
            y2={pad + t * (height - pad * 2)}
          />
        ))}
        {/* A faint fill under the line gives the series body at this size;
            two points alone are otherwise a hairline in a lot of space. */}
        <polygon
          className="chart-fill"
          points={`${pad},${height - pad} ${path} ${width - pad},${height - pad}`}
        />
        <polyline className="chart-line" points={path} />
        <circle className="chart-dot" cx={lx} cy={ly} r="3.5" />
      </svg>
      <figcaption className="chart-caption">
        Speaking pace Â· latest <b>{Math.round(last)} wpm</b>
      </figcaption>
    </figure>
  );
}


