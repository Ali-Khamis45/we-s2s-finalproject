import { useMemo, useState } from "react";

import type { Message, StageTiming } from "../lib/types";

/**
 * Where a turn's time actually went.
 *
 * The project's central quantitative claim is that the cascade costs seconds
 * where native speech-to-speech costs milliseconds. Until now that lived only
 * as a total: `1.9 s`, with nothing behind it. This is the breakdown, and the
 * comparison strip at the top is the thesis as one picture.
 *
 * Design decisions worth stating:
 *
 *  - **Collapsed by default.** A waterfall on every turn would shout over the
 *    conversation, which is the actual product.
 *  - **Linear, never log.** A log axis would flatter the slow stages and hide
 *    the result. STT dominating is the finding, not a rendering problem.
 *  - **Sequential ramp, not categorical.** Stages are a sequence through a
 *    pipeline; a rainbow would imply they are unrelated categories.
 *  - **Never colour alone.** Every segment is labelled, carries an aria-label,
 *    and the whole thing has a real table behind a toggle.
 */

/** Human labels. The UI never shows a raw stage key. */
const STAGE_LABEL: Record<string, string> = {
  stt: "Transcribe",
  acoustic: "Analyse speech",
  retrieval: "Search library",
  llm_ttft: "First token",
  llm: "Generate",
  tts_first: "First audio",
};

/** Render order: roughly the order a turn passes through the pipeline. */
const STAGE_ORDER = ["stt", "acoustic", "retrieval", "llm_ttft", "llm", "tts_first"];

function label(stage: string): string {
  return STAGE_LABEL[stage] ?? stage;
}

function ordered(timings: StageTiming[]): StageTiming[] {
  return [...timings].sort((a, b) => {
    const ai = STAGE_ORDER.indexOf(a.stage);
    const bi = STAGE_ORDER.indexOf(b.stage);
    // Unknown stages sort last rather than disappearing.
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
  });
}

/**
 * `llm_ttft` is the time until the first token, and `llm` the whole
 * generation — the first is contained in the second. Summing them would
 * double-count, so time-to-first-token is reported but not laid end to end.
 */
function isCumulative(stage: string): boolean {
  return stage === "llm_ttft";
}

function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`;
}

interface Props {
  timings: StageTiming[];
  totalMs?: number;
  /** The other path's turn, when the session has one — draws the comparison. */
  compareWith?: { label: string; timings: StageTiming[]; totalMs?: number } | null;
}

export function TurnTimings({ timings, totalMs, compareWith }: Props) {
  const [open, setOpen] = useState(false);
  const [asTable, setAsTable] = useState(false);

  const segments = useMemo(
    () => ordered(timings).filter((t) => !isCumulative(t.stage)),
    [timings],
  );
  const ttft = timings.find((t) => t.stage === "llm_ttft");

  if (timings.length === 0) return null;

  const total = totalMs ?? segments.reduce((sum, t) => sum + t.ms, 0);

  return (
    <div className="tt">
      <button
        type="button"
        className="tt-chip num"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {formatMs(total)}
        <span className={`chev ${open ? "chev-open" : ""}`} aria-hidden="true" />
      </button>

      {open && (
        <div className="tt-body">
          {compareWith && (
            <div className="tt-compare">
              <span className="eyebrow">Same question, both paths</span>
              <Bar
                rowLabel={compareWith.label}
                segments={ordered(compareWith.timings).filter(
                  (t) => !isCumulative(t.stage),
                )}
                scaleMax={Math.max(total, compareWith.totalMs ?? 0)}
                total={compareWith.totalMs}
              />
              <Bar
                rowLabel="This turn"
                segments={segments}
                scaleMax={Math.max(total, compareWith.totalMs ?? 0)}
                total={total}
              />
            </div>
          )}

          {!compareWith &&
            (asTable ? (
              <TimingTable segments={segments} ttft={ttft} total={total} />
            ) : (
              <Bar rowLabel="" segments={segments} scaleMax={total} total={total} />
            ))}

          {ttft && !asTable && (
            <p className="tt-note">
              First token after <b className="num">{formatMs(ttft.ms)}</b> — the rest
              streamed.
            </p>
          )}

          <button
            type="button"
            className="tt-toggle"
            onClick={() => setAsTable((v) => !v)}
          >
            {asTable ? "Show as chart" : "Show as table"}
          </button>
        </div>
      )}
    </div>
  );
}

function Bar({
  rowLabel,
  segments,
  scaleMax,
  total,
}: {
  rowLabel: string;
  segments: StageTiming[];
  scaleMax: number;
  total?: number;
}) {
  const max = Math.max(scaleMax, 1);
  // The dominant stage takes the one amber slot. It is almost always
  // transcription, and that being visible at a glance is the point.
  const dominant = segments.reduce(
    (best, t) => (t.ms > (best?.ms ?? 0) ? t : best),
    segments[0],
  );

  return (
    <div className="tt-row">
      {rowLabel && <span className="tt-row-label">{rowLabel}</span>}
      <div
        className="tt-bar"
        role="img"
        aria-label={
          `Time breakdown: ` +
          segments.map((t) => `${label(t.stage)} ${formatMs(t.ms)}`).join(", ")
        }
      >
        {segments.map((t) => (
          <span
            key={t.stage}
            className={`tt-seg ${t.stage === dominant?.stage ? "is-dominant" : ""}`}
            // A 3 ms analyser stage beside a 600 ms transcription would be
            // sub-pixel; a floor keeps it visible without distorting the rest.
            style={{ width: `max(3px, ${(t.ms / max) * 100}%)` }}
            title={`${label(t.stage)} · ${formatMs(t.ms)}`}
          >
            <span className="tt-seg-label">{label(t.stage)}</span>
          </span>
        ))}
      </div>
      {total !== undefined && <span className="tt-total num">{formatMs(total)}</span>}
    </div>
  );
}

function TimingTable({
  segments,
  ttft,
  total,
}: {
  segments: StageTiming[];
  ttft?: StageTiming;
  total: number;
}) {
  return (
    <div className="scroll-x">
      <table className="tt-table">
        <caption className="visually-hidden">
          Time spent in each stage of this turn
        </caption>
        <thead>
          <tr>
            <th scope="col">Stage</th>
            <th scope="col">Time</th>
            <th scope="col">Share</th>
          </tr>
        </thead>
        <tbody>
          {segments.map((t) => (
            <tr key={t.stage}>
              <th scope="row">{label(t.stage)}</th>
              <td className="num">{formatMs(t.ms)}</td>
              <td className="num">{Math.round((t.ms / Math.max(total, 1)) * 100)}%</td>
            </tr>
          ))}
          {ttft && (
            <tr>
              <th scope="row">{label(ttft.stage)}</th>
              <td className="num">{formatMs(ttft.ms)}</td>
              <td className="tt-within">within Generate</td>
            </tr>
          )}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Total</th>
            <td className="num">{formatMs(total)}</td>
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

/**
 * Session-level p50/p95 per stage, computed from the loaded turns.
 *
 * This is the one chart in the app permitted to be about speed, and it is
 * about the *system* — never about the person using it (docs/ETHICS.md).
 */
export function SessionTimings({ messages }: { messages: Message[] }) {
  const rows = useMemo(() => {
    const buckets = new Map<string, number[]>();
    for (const m of messages) {
      for (const t of m.timings ?? []) {
        if (isCumulative(t.stage)) continue;
        const list = buckets.get(t.stage) ?? [];
        list.push(t.ms);
        buckets.set(t.stage, list);
      }
    }
    return STAGE_ORDER.filter((s) => buckets.has(s)).map((stage) => {
      const values = [...(buckets.get(stage) ?? [])].sort((a, b) => a - b);
      const at = (q: number) =>
        values[Math.min(values.length - 1, Math.floor(values.length * q))];
      return { stage, n: values.length, p50: at(0.5), p95: at(0.95) };
    });
  }, [messages]);

  if (rows.length === 0) return null;

  return (
    <div className="scroll-x">
      <table className="tt-table">
        <caption className="eyebrow tt-caption">This session, per stage</caption>
        <thead>
          <tr>
            <th scope="col">Stage</th>
            <th scope="col">p50</th>
            <th scope="col">p95</th>
            <th scope="col">n</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.stage}>
              <th scope="row">{label(r.stage)}</th>
              <td className="num">{formatMs(r.p50)}</td>
              <td className="num">{formatMs(r.p95)}</td>
              <td className="num">{r.n}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
