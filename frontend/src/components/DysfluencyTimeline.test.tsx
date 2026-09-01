import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { AcousticProfile, DysfluencyEvent, DysfluencyKind } from "../lib/types";
import { DysfluencyTimeline } from "./DysfluencyTimeline";

function event(
  kind: DysfluencyKind,
  start_ms: number,
  end_ms: number,
  confidence = 0.9,
): DysfluencyEvent {
  return { kind, start_ms, end_ms, confidence, duration_ms: end_ms - start_ms };
}

/** Builds a profile, deriving event_counts the way the backend does. */
function profile(overrides: Partial<AcousticProfile> = {}): AcousticProfile {
  const events = overrides.events ?? [];
  const counts: Record<string, number> = {};
  for (const e of events) counts[e.kind] = (counts[e.kind] ?? 0) + 1;

  return {
    schema_version: "1.0",
    duration_ms: 4000,
    prosody: {
      speech_rate_wpm: null,
      articulation_rate_sps: null,
      mean_pause_ms: null,
      longest_pause_ms: null,
      pitch_mean_hz: null,
      pitch_variation: null,
      energy_variation: null,
    },
    analyzed: true,
    source: "heuristic",
    dysfluent_ms: 0,
    fluency_load: 0,
    dominant_event: null,
    ...overrides,
    events,
    // Derived last so it always matches `events`, whichever the caller passed.
    event_counts: overrides.event_counts ?? counts,
  };
}

describe("visibility", () => {
  it("renders nothing when the analyzer did not run", () => {
    // An absent analyzer must not be drawn as "no dysfluency detected" —
    // that would read as a measurement that was never taken.
    const { container } = render(
      <DysfluencyTimeline profile={profile({ analyzed: false })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("says so explicitly when it ran and found nothing", () => {
    render(<DysfluencyTimeline profile={profile({ events: [] })} />);
    expect(screen.getByText(/no interruptions detected/i)).toBeInTheDocument();
  });
});

describe("event geometry", () => {
  it("positions and sizes events to scale against the utterance", () => {
    // Duration must be legible from the drawing: a 1.4 s block in a 4 s
    // utterance has to look four times a 350 ms one, or the strip conveys
    // nothing a count would not.
    render(
      <DysfluencyTimeline
        profile={profile({
          duration_ms: 4000,
          events: [event("block", 1000, 2400)],
        })}
      />,
    );

    const bar = screen.getByRole("button", { name: /block/i });
    expect(bar).toHaveStyle({ left: "25%" }); // 1000 / 4000
    expect(bar).toHaveStyle({ width: "35%" }); // 1400 / 4000
  });

  it("keeps very short events visible with a minimum width", () => {
    // A 40 ms event in a 10 s utterance is 0.4% wide and would vanish.
    render(
      <DysfluencyTimeline
        profile={profile({
          duration_ms: 10_000,
          events: [event("interjection", 500, 540)],
        })}
      />,
    );
    const bar = screen.getByRole("button", { name: /filler word/i });
    const width = Number.parseFloat((bar as HTMLElement).style.width);
    expect(width).toBeGreaterThanOrEqual(0.4);
  });

  it("orders events by start time regardless of input order", () => {
    render(
      <DysfluencyTimeline
        profile={profile({
          events: [event("word_repetition", 3000, 3400), event("block", 500, 1200)],
        })}
      />,
    );
    const bars = screen.getAllByRole("button");
    expect(bars[0]).toHaveAccessibleName(/block/i);
    expect(bars[1]).toHaveAccessibleName(/repeated word/i);
  });
});

describe("labels and accessibility", () => {
  it("never shows a raw enum value to the reader", () => {
    render(
      <DysfluencyTimeline
        profile={profile({ events: [event("sound_repetition", 100, 500)] })}
      />,
    );
    expect(screen.getByText("Repeated sound")).toBeInTheDocument();
    expect(screen.queryByText("sound_repetition")).not.toBeInTheDocument();
  });

  it("describes the whole strip for screen readers", () => {
    render(
      <DysfluencyTimeline
        profile={profile({
          duration_ms: 4000,
          events: [event("block", 500, 1900), event("interjection", 2000, 2200)],
        })}
      />,
    );
    const strip = screen.getByRole("img");
    expect(strip).toHaveAccessibleName(/4\.0 s/);
    expect(strip).toHaveAccessibleName(/1 block/);
    expect(strip).toHaveAccessibleName(/1 filler word/);
  });

  it("gives each event an individually readable label with its timing", () => {
    render(
      <DysfluencyTimeline profile={profile({ events: [event("block", 1000, 2400)] })} />,
    );
    expect(
      screen.getByRole("button", { name: /block at 1\.0 s, lasting 1\.4 s/i }),
    ).toBeInTheDocument();
  });
});

describe("readout", () => {
  it("shows a legend with per-kind counts by default", () => {
    render(
      <DysfluencyTimeline
        profile={profile({
          events: [
            event("word_repetition", 100, 400),
            event("word_repetition", 500, 800),
            event("block", 1000, 2000),
          ],
        })}
      />,
    );
    expect(screen.getByText("Repeated word")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("reports the hovered event's duration and confidence", async () => {
    const user = userEvent.setup();
    render(
      <DysfluencyTimeline
        profile={profile({ events: [event("block", 1000, 2400, 0.82)] })}
      />,
    );

    await user.hover(screen.getByRole("button", { name: /block/i }));

    expect(screen.getByText(/1\.4 s/)).toBeInTheDocument();
    expect(screen.getByText(/82% confidence/)).toBeInTheDocument();
  });
});

describe("prosody row", () => {
  it("names the analyzer so heuristic output is never mistaken for the model", () => {
    // This distinction has to survive into the thesis: numbers from the
    // scaffold are not results.
    render(
      <DysfluencyTimeline profile={profile({ source: "heuristic", events: [] })} />,
    );
    expect(screen.getByText("Analyzer")).toBeInTheDocument();
    expect(screen.getByText("heuristic")).toBeInTheDocument();
  });

  it("is hidden in compact mode, which is used inline in the conversation", () => {
    render(<DysfluencyTimeline compact profile={profile({ events: [] })} />);
    expect(screen.queryByText("Analyzer")).not.toBeInTheDocument();
  });

  it("describes pitch in words rather than a coefficient", () => {
    render(
      <DysfluencyTimeline
        profile={profile({
          events: [],
          prosody: {
            speech_rate_wpm: 168,
            articulation_rate_sps: null,
            mean_pause_ms: null,
            longest_pause_ms: null,
            pitch_mean_hz: 180,
            pitch_variation: 0.6,
            energy_variation: null,
          },
        })}
      />,
    );
    expect(screen.getByText("strained")).toBeInTheDocument();
    expect(screen.getByText("168 wpm")).toBeInTheDocument();
    expect(screen.queryByText("0.6")).not.toBeInTheDocument();
  });
});
