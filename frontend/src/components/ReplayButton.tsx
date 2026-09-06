import { useEffect, useRef, useState } from "react";

import * as replay from "../audio/replay";

type State = "idle" | "loading" | "playing" | "error";

interface Props {
  sessionId: string;
  turnId: number;
  /** The live coach is streaming audio right now. */
  speaking: boolean;
}

/**
 * "Hear it again" on a coach reply.
 *
 * A quiet affordance in the turn header, not a primary action — the reply is
 * already there to read. It turns amber only while sounding, which is the one
 * moment a replay genuinely is the active voice.
 *
 * State is announced through this button's own accessible name. `Conversation`
 * is already an aria-live region, and a second announcer would talk over it.
 */
export function ReplayButton({ sessionId, turnId, speaking }: Props) {
  const [state, setState] = useState<State>("idle");
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      // Switching sessions must not leave a voice playing over the new one.
      replay.stop();
    };
  }, []);

  // The live coach wins. Two voices at once is the worst outcome for a tool
  // whose entire subject is how speech sounds.
  useEffect(() => {
    if (!speaking) return;
    replay.stop();
    setState((s) => (s === "idle" || s === "error" ? s : "idle"));
  }, [speaking]);

  const press = async () => {
    if (state === "loading" || state === "playing") {
      replay.stop();
      setState("idle");
      return;
    }

    setState("loading");
    try {
      await replay.play(sessionId, turnId, {
        onStart: () => mounted.current && setState("playing"),
      });
      if (mounted.current) setState("idle");
    } catch {
      // Per press, never sticky: Kokoro can be absent at boot and loaded later,
      // and a session-long dead button would hide that it came back.
      if (mounted.current) setState("error");
    }
  };

  // Loading is shown from the first press rather than after a delay, so a slow
  // synthesis never looks like a dead control.
  const label =
    state === "playing"
      ? "Stop playback"
      : state === "loading"
        ? "Loading audio"
        : state === "error"
          ? "Voice unavailable"
          : "Play this reply";

  return (
    <button
      type="button"
      className={`replay replay-${state}`}
      onClick={press}
      disabled={speaking}
      aria-pressed={state === "playing"}
      aria-label={label}
      title={speaking ? "The coach is speaking" : label}
    >
      <SpeakerIcon stopped={state === "playing"} muted={state === "error"} />
    </button>
  );
}

/** Sized in em so it tracks the header's type, and inherits currentColor. */
function SpeakerIcon({ stopped, muted }: { stopped: boolean; muted: boolean }) {
  return (
    <svg
      className="replay-icon"
      viewBox="0 0 16 16"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M8.5 2.5 4.5 5.5H2v5h2.5l4 3z" />
      {muted ? (
        <path d="M11.5 6.5l3 3m0-3l-3 3" />
      ) : stopped ? (
        <path d="M11.5 5.5v5" />
      ) : (
        <>
          <path d="M11.2 6.1a2.6 2.6 0 0 1 0 3.8" />
          <path d="M13 4.3a5 5 0 0 1 0 7.4" />
        </>
      )}
    </svg>
  );
}
