import { useMemo, useState } from "react";

import { EVENT_LABELS, type AcousticProfile, type DysfluencyKind } from "../lib/types";

/**
 * The dysfluency timeline (A16).
 *
 * This is the component that makes the project's argument visible. The
 * transcript above it is what a conventional pipeline would have kept; this
 * strip is everything that pipeline would have thrown away — where the speech
 * stopped, for how long, and what kind of event it was.
 *
 * Design decisions that are deliberate, not incidental:
 *
 *  - Blocks are rendered as gaps in a continuous speech bar rather than as
 *    marks on an empty line, because a block IS an absence. Drawing silence as
 *    a positive object misrepresents what happened.
 *  - Duration is to scale. A 1.4 s block looks four times a 350 ms one, so the
 *    strip carries the magnitude a count never could.
 *  - No score, no severity, no colour that reads as "bad" (docs/ETHICS.md).
 *    Events are distinguished by hue, not ranked by it.
 */

const EVENT_CLASS: Record<DysfluencyKind, string> = {
  block: "ev-block",
  prolongation: "ev-prolong",
  sound_repetition: "ev-sound",
  word_repetition: "ev-word",
  interjection: "ev-filler",
  unsure: "ev-unsure",
};

function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

interface Props {
  profile: AcousticProfile;
  compact?: boolean;
}

export function DysfluencyTimeline({ profile, compact = false }: Props) {
  const [hovered, setHovered] = useState<number | null>(null);

  const duration = Math.max(profile.duration_ms, 1);

  const events = useMemo(
    () =>
      (profile.events ?? [])
        .map((e, i) => ({
          ...e,
          index: i,
          leftPct: (e.start_ms / duration) * 100,
          widthPct: Math.max((Math.max(e.duration_ms, 40) / duration) * 100, 0.8),
        }))
        .sort((a, b) => a.start_ms - b.start_ms),
    [profile.events, duration],
  );

  const kinds = useMemo(
    () => Object.keys(profile.event_counts ?? {}) as DysfluencyKind[],
    [profile.event_counts],
  );

  if (!profile.analyzed) return null;

  const active = hovered !== null ? events.find((e) => e.index === hovered) : null;

  return (
    <div className={`timeline ${compact ? "timeline-compact" : ""}`}>
      <div className="timeline-head">
        <span className="timeline-label">What the transcript lost</span>
        <span className="timeline-dur">{formatMs(profile.duration_ms)}</span>
      </div>

      <div className="timeline-track" role="img" aria-label={describe(profile)}>
        {/* Continuous speech, so events read as interruptions of it. */}
        <div className="timeline-speech" />
        {events.map((e) => (
          <button
            key={e.index}
            type="button"
            className={`timeline-event ${EVENT_CLASS[e.kind]} ${
              hovered === e.index ? "is-active" : ""
            }`}
            // left/width stay percentages: they are the to-scale geometry, and
            // two tests assert them. Only the reveal is staggered, and it rides
            // on transform so nothing here triggers layout.
            style={{
              left: `${e.leftPct}%`,
              width: `${e.widthPct}%`,
              animationDelay: `${200 + e.leftPct * 3.5}ms`,
            }}
            onMouseEnter={() => setHovered(e.index)}
            onMouseLeave={() => setHovered(null)}
            onFocus={() => setHovered(e.index)}
            onBlur={() => setHovered(null)}
            aria-label={`${EVENT_LABELS[e.kind]} at ${formatMs(e.start_ms)}, lasting ${formatMs(e.duration_ms)}`}
          />
        ))}
      </div>

      <div className="timeline-readout">
        {active ? (
          <span className="timeline-active">
            <strong>{EVENT_LABELS[active.kind]}</strong> at {formatMs(active.start_ms)} ·{" "}
            {formatMs(active.duration_ms)}
            {active.confidence < 1 && (
              <span className="timeline-conf">
                {" "}
                · {Math.round(active.confidence * 100)}% confidence
              </span>
            )}
          </span>
        ) : (
          <span className="timeline-legend">
            {kinds.length === 0 ? (
              <span className="timeline-clean">No interruptions detected</span>
            ) : (
              kinds.map((kind) => (
                <span key={kind} className="legend-item">
                  <i className={`legend-swatch ${EVENT_CLASS[kind]}`} />
                  {EVENT_LABELS[kind]}
                  <b>{profile.event_counts[kind]}</b>
                </span>
              ))
            )}
          </span>
        )}
      </div>

      {!compact && <ProsodyRow profile={profile} />}
    </div>
  );
}

function ProsodyRow({ profile }: { profile: AcousticProfile }) {
  const { prosody } = profile;
  const stats: Array<{ label: string; value: string }> = [];

  // `!= null` rather than `!== null`: the schema allows undefined as well as
  // null, and a missing measurement and an absent one mean the same thing here.
  // `prosody` itself is optional, so it is read through the profile each time.
  const wpm = prosody?.speech_rate_wpm;
  const pause = prosody?.longest_pause_ms;
  const pitch = prosody?.pitch_variation;

  if (wpm != null) {
    stats.push({ label: "Pace", value: `${Math.round(wpm)} wpm` });
  }
  if (pause != null && pause > 0) {
    stats.push({ label: "Longest pause", value: formatMs(pause) });
  }
  if (pitch != null) {
    stats.push({
      label: "Delivery",
      value: pitch < 0.1 ? "flat" : pitch > 0.45 ? "strained" : "steady",
    });
  }
  // Source is shown so heuristic output is never mistaken for the trained
  // classifier — that distinction matters for anything cited in the thesis.
  stats.push({ label: "Analyzer", value: profile.source });

  if (stats.length === 0) return null;

  return (
    <dl className="prosody">
      {stats.map((s) => (
        <div key={s.label} className="prosody-item">
          <dt>{s.label}</dt>
          <dd>{s.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function describe(profile: AcousticProfile): string {
  const parts = Object.entries(profile.event_counts).map(
    ([kind, n]) => `${n} ${EVENT_LABELS[kind as DysfluencyKind].toLowerCase()}`,
  );
  if (parts.length === 0) {
    return `Speech timeline, ${formatMs(profile.duration_ms)}, no interruptions detected.`;
  }
  return `Speech timeline, ${formatMs(profile.duration_ms)}, containing ${parts.join(", ")}.`;
}
