import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "./Conversation";
import type { Message } from "../lib/types";
import * as replay from "../audio/replay";

/**
 * The speaker control on a coach turn.
 *
 * The rules worth pinning are the ones about *not* playing: a control that can
 * never work, and a replay talking over the live coach, are both worse than no
 * button at all.
 */

const coach = (over: Partial<Message> = {}): Message => ({
  id: "c1",
  role: "coach",
  mode: "knowledge",
  text: "Let the first sentence be short.",
  turnId: 12,
  sessionId: "s1",
  ...over,
});

const user = (over: Partial<Message> = {}): Message => ({
  id: "u1",
  role: "user",
  mode: "knowledge",
  text: "How do I use pauses?",
  turnId: 11,
  sessionId: "s1",
  ...over,
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("where the button appears", () => {
  it("offers to play a coach reply", () => {
    render(<Conversation messages={[coach()]} speaking={false} />);
    expect(screen.getByRole("button", { name: /play this reply/i })).toBeInTheDocument();
  });

  it("never reads the user's own words back to them", () => {
    render(<Conversation messages={[user()]} speaking={false} />);
    expect(screen.queryByRole("button", { name: /play this reply/i })).not.toBeInTheDocument();
  });

  it("is absent, not disabled, when the turn was never persisted", () => {
    // A live turn can close without a done frame. A control that can never
    // work is noise, so it is not rendered at all.
    render(<Conversation messages={[coach({ turnId: undefined })]} speaking={false} />);
    expect(screen.queryByRole("button", { name: /play this reply/i })).not.toBeInTheDocument();
  });

  it("is absent when the reply is still streaming", () => {
    render(<Conversation messages={[coach({ pending: true })]} speaking={false} />);
    expect(screen.queryByRole("button", { name: /play this reply/i })).not.toBeInTheDocument();
  });
});

describe("never two voices at once", () => {
  it("is disabled while the coach is speaking live", () => {
    render(<Conversation messages={[coach()]} speaking={true} />);
    expect(screen.getByRole("button", { name: /play this reply/i })).toBeDisabled();
  });
});

describe("playing", () => {
  it("asks for the audio of that turn", async () => {
    const play = vi.spyOn(replay, "play").mockResolvedValue(undefined);
    render(<Conversation messages={[coach()]} speaking={false} />);

    await userEvent.click(screen.getByRole("button", { name: /play this reply/i }));

    expect(play.mock.calls[0]?.slice(0, 2)).toEqual(["s1", 12]);
  });

  it("renames itself while playing, so the state is announced", async () => {
    // Conversation is already aria-live; a second announcer would talk over it,
    // so the button's own name carries the change.
    let release: () => void = () => {};
    vi.spyOn(replay, "play").mockImplementation((_s, _t, opts) => {
      opts?.onStart?.();
      return new Promise<void>((resolve) => {
        release = resolve;
      });
    });
    render(<Conversation messages={[coach()]} speaking={false} />);

    await userEvent.click(screen.getByRole("button", { name: /play this reply/i }));

    const stop = await screen.findByRole("button", { name: /stop playback/i });
    expect(stop).toHaveAttribute("aria-pressed", "true");
    release();
  });

  it("stops instead of fetching twice when pressed again", async () => {
    vi.spyOn(replay, "play").mockImplementation((_s, _t, opts) => {
      opts?.onStart?.();
      return new Promise<void>(() => {});
    });
    const stop = vi.spyOn(replay, "stop").mockImplementation(() => {});
    render(<Conversation messages={[coach()]} speaking={false} />);

    await userEvent.click(screen.getByRole("button", { name: /play this reply/i }));
    await userEvent.click(await screen.findByRole("button", { name: /stop playback/i }));

    expect(stop).toHaveBeenCalled();
  });

  it("says the voice is unavailable when synthesis fails", async () => {
    vi.spyOn(replay, "play").mockRejectedValue(new Error("503"));
    render(<Conversation messages={[coach()]} speaking={false} />);

    await userEvent.click(screen.getByRole("button", { name: /play this reply/i }));

    expect(await screen.findByRole("button", { name: /voice unavailable/i })).toBeInTheDocument();
  });
});
