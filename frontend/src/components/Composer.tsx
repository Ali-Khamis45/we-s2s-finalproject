import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import type { ConnectionState } from "../hooks/useCoachSession";
import { Button } from "./ui/primitives";
import { VoiceOrb, type OrbState } from "./VoiceOrb";

interface Props {
  connection: ConnectionState;
  listening: boolean;
  micLevel: number;
  onStart: () => void;
  onStop: () => void;
  onSend: (text: string) => void;
  onInterrupt: () => void;
  speaking: boolean;
}

export function Composer({
  connection,
  listening,
  micLevel,
  onStart,
  onStop,
  onSend,
  onInterrupt,
  speaking,
}: Props) {
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const connected = connection === "connected";

  const orbState: OrbState = speaking
    ? "speaking"
    : connected
      ? "live"
      : connection === "connecting"
        ? "connecting"
        : "idle";

  // Grow with the content instead of scrolling a two-line box.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [draft]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!draft.trim()) return;
    onSend(draft);
    setDraft("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit(event);
    }
  };

  const caption = speaking
    ? "Coach is talking — say something to cut in"
    : connected
      ? "Listening. Take your time."
      : connection === "connecting"
        ? "Opening the microphone"
        : "Press to start talking";

  return (
    <div className="composer">
      <div className="composer-voice">
        <button
          type="button"
          className="orb-button"
          onClick={connected ? onStop : onStart}
          disabled={connection === "connecting"}
          aria-pressed={connected}
          aria-label={connected ? "Stop speaking" : "Start speaking"}
        >
          <VoiceOrb state={orbState} micLevel={listening ? micLevel : 0} />
        </button>

        <span className="orb-caption">
          <strong>{connected ? "Stop" : "Start speaking"}</strong>
          {/* The state is in text as well as in motion — never motion alone. */}
          <span role="status">{caption}</span>
        </span>

        {speaking && (
          <Button variant="ghost" onClick={onInterrupt}>
            Interrupt
          </Button>
        )}
      </div>

      <form className="composer-text" onSubmit={submit}>
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="…or type instead"
          rows={1}
          aria-label="Type a message to the coach"
        />
        <Button
          type="submit"
          variant="primary"
          progress={draft.trim() ? 1 : 0}
          disabled={!draft.trim()}
        >
          Send
        </Button>
      </form>
    </div>
  );
}
