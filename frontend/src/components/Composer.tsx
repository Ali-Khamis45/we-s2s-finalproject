import { useState, type FormEvent, type KeyboardEvent } from "react";

import type { ConnectionState } from "../hooks/useCoachSession";

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
  const connected = connection === "connected";

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

  return (
    <div className="composer">
      <div className="composer-voice">
        <button
          type="button"
          className={`mic-button ${connected ? "is-live" : ""}`}
          onClick={connected ? onStop : onStart}
          disabled={connection === "connecting"}
        >
          <MicIcon active={connected} />
          <span>
            {connection === "connecting"
              ? "Connecting…"
              : connected
                ? "Stop"
                : "Start speaking"}
          </span>
        </button>

        {connected && (
          <div className="level" aria-hidden="true">
            {Array.from({ length: 14 }, (_, i) => (
              <i
                key={i}
                className={
                  listening && micLevel * 22 > i ? "level-bar is-on" : "level-bar"
                }
              />
            ))}
          </div>
        )}

        {speaking && (
          <button type="button" className="ghost-button" onClick={onInterrupt}>
            Interrupt
          </button>
        )}
      </div>

      <form className="composer-text" onSubmit={submit}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="…or type instead"
          rows={1}
          aria-label="Type a message to the coach"
        />
        <button type="submit" className="send-button" disabled={!draft.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}

function MicIcon({ active }: { active: boolean }) {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      {active ? (
        <rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" />
      ) : (
        <>
          <rect
            x="9"
            y="3"
            width="6"
            height="11"
            rx="3"
            stroke="currentColor"
            strokeWidth="1.8"
          />
          <path
            d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        </>
      )}
    </svg>
  );
}
