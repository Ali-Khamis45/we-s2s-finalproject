import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Message, StageTiming } from "../lib/types";
import { SessionTimings, TurnTimings } from "./TurnTimings";

/** A realistic spoken turn: transcription dominates, the analyser is 3 ms. */
const SPOKEN: StageTiming[] = [
  { stage: "stt", ms: 776 },
  { stage: "acoustic", ms: 3 },
  { stage: "llm_ttft", ms: 2948 },
  { stage: "llm", ms: 8986 },
];

describe("collapsed state", () => {
  it("shows only a total until asked for more", () => {
    // A waterfall on every turn would shout over the conversation.
    render(<TurnTimings timings={SPOKEN} totalMs={9810} />);
    expect(screen.getByRole("button", { name: /9\.81 s/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("renders nothing at all when a turn has no timings", () => {
    // Live-path turns record none.
    const { container } = render(<TurnTimings timings={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("falls back to summing stages when no total is given", () => {
    render(<TurnTimings timings={[{ stage: "stt", ms: 500 }]} />);
    expect(screen.getByRole("button", { name: /500 ms/ })).toBeInTheDocument();
  });
});

describe("expanded chart", () => {
  it("keeps a 3 ms stage visible next to a 776 ms one", async () => {
    // Proportional width alone would make the analyser sub-pixel. A floor
    // keeps it on screen without distorting the rest.
    const user = userEvent.setup();
    render(<TurnTimings timings={SPOKEN} totalMs={9810} />);
    await user.click(screen.getByRole("button", { name: /9\.81 s/ }));

    const bar = screen.getByRole("img");
    const segments = bar.querySelectorAll<HTMLElement>(".tt-seg");
    expect(segments.length).toBeGreaterThan(0);
    for (const seg of segments) {
      expect(seg.style.width).toMatch(/^max\(3px,/);
    }
  });

  it("does not lay time-to-first-token end to end with generation", async () => {
    // llm_ttft is contained within llm; drawing both would double-count.
    const user = userEvent.setup();
    render(<TurnTimings timings={SPOKEN} totalMs={9810} />);
    await user.click(screen.getByRole("button", { name: /9\.81 s/ }));

    const bar = screen.getByRole("img");
    expect(bar).toHaveAccessibleName(expect.stringContaining("Transcribe"));
    expect(bar).toHaveAccessibleName(expect.stringContaining("Generate"));
    expect(bar).not.toHaveAccessibleName(expect.stringContaining("First token"));
    // It is still reported, just not as a segment.
    expect(screen.getByText(/First token after/)).toBeInTheDocument();
  });

  it("describes the whole breakdown for a screen reader", async () => {
    const user = userEvent.setup();
    render(<TurnTimings timings={SPOKEN} totalMs={9810} />);
    await user.click(screen.getByRole("button", { name: /9\.81 s/ }));
    expect(screen.getByRole("img")).toHaveAccessibleName(
      /Transcribe 776 ms.*Analyse speech 3 ms/,
    );
  });

  it("never shows a raw stage key", async () => {
    const user = userEvent.setup();
    render(<TurnTimings timings={SPOKEN} totalMs={9810} />);
    await user.click(screen.getByRole("button", { name: /9\.81 s/ }));
    expect(screen.queryByText("stt")).not.toBeInTheDocument();
    expect(screen.queryByText("llm_ttft")).not.toBeInTheDocument();
  });
});

describe("table fallback", () => {
  it("offers the same data as a real table", async () => {
    // No information may be carried by colour or geometry alone.
    const user = userEvent.setup();
    render(<TurnTimings timings={SPOKEN} totalMs={9810} />);
    await user.click(screen.getByRole("button", { name: /9\.81 s/ }));
    await user.click(screen.getByRole("button", { name: /show as table/i }));

    const table = screen.getByRole("table");
    expect(within(table).getByRole("rowheader", { name: "Transcribe" })).toBeInTheDocument();
    expect(within(table).getByText("776 ms")).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: "Total" })).toBeInTheDocument();
  });
});

describe("comparison strip", () => {
  it("puts both paths on one shared axis", async () => {
    // This is the thesis as a single image: ~200 ms against seconds.
    const user = userEvent.setup();
    render(
      <TurnTimings
        timings={SPOKEN}
        totalMs={9810}
        compareWith={{
          label: "Live coach",
          timings: [{ stage: "llm", ms: 210 }],
          totalMs: 210,
        }}
      />,
    );
    await user.click(screen.getByRole("button", { name: /9\.81 s/ }));

    expect(screen.getByText("Live coach")).toBeInTheDocument();
    expect(screen.getByText("This turn")).toBeInTheDocument();
    // Both bars scale to the slower path, so the gap is legible.
    expect(screen.getAllByRole("img")).toHaveLength(2);
  });
});

describe("session aggregate", () => {
  const message = (timings: StageTiming[]): Message => ({
    id: Math.random().toString(),
    role: "coach",
    mode: "knowledge",
    text: "x",
    timings,
  });

  it("reports p50 and p95 per stage", () => {
    render(
      <SessionTimings
        messages={[
          message([{ stage: "stt", ms: 500 }]),
          message([{ stage: "stt", ms: 700 }]),
          message([{ stage: "stt", ms: 900 }]),
        ]}
      />,
    );
    const row = screen.getByRole("row", { name: /Transcribe/ });
    expect(within(row).getByText("700 ms")).toBeInTheDocument();
    expect(within(row).getByText("3")).toBeInTheDocument();
  });

  it("renders nothing when no turn has been timed", () => {
    const { container } = render(<SessionTimings messages={[message([])]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
