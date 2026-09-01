import { useEffect, useRef, useState } from "react";

import type { Citation, Message } from "../lib/types";
import { DysfluencyTimeline } from "./DysfluencyTimeline";

interface Props {
  messages: Message[];
  speaking: boolean;
}

export function Conversation({ messages, speaking }: Props) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="conversation">
        <EmptyState />
      </div>
    );
  }

  return (
    <div className="conversation">
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}
      {speaking && (
        <div className="speaking-hint" aria-live="polite">
          <span className="speaking-dots">
            <i /> <i /> <i />
          </span>
          Coach is speaking — just talk to interrupt
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";

  return (
    <article className={`turn ${isUser ? "turn-user" : "turn-coach"}`}>
      <header className="turn-head">
        <span className="turn-who">{isUser ? "You" : "Coach"}</span>
        <ModeTag mode={message.mode} />
        {message.grounded === false && !isUser && (
          <span className="turn-flag" title="No matching reference material was found">
            ungrounded
          </span>
        )}
        {message.totalMs ? (
          <span className="turn-ms" title="Time from your speech to this reply">
            {Math.round(message.totalMs)} ms
          </span>
        ) : null}
      </header>

      <p className="turn-text">
        {message.text}
        {message.pending && <span className="caret" aria-hidden="true" />}
      </p>

      {message.acoustic?.analyzed && (
        <DysfluencyTimeline profile={message.acoustic} compact />
      )}

      {message.citations && message.citations.length > 0 && (
        <Citations citations={message.citations} />
      )}
    </article>
  );
}

function ModeTag({ mode }: { mode: Message["mode"] }) {
  const label = mode === "live" ? "live" : mode === "knowledge" ? "grounded" : "typed";
  return <span className={`mode-tag mode-${mode}`}>{label}</span>;
}

function Citations({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="citations">
      <button
        type="button"
        className="citations-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {citations.length} source{citations.length === 1 ? "" : "s"}
        <span className={`chev ${open ? "chev-open" : ""}`} aria-hidden="true" />
      </button>

      {open && (
        <ol className="citation-list">
          {citations.map((c, i) => (
            <li key={`${c.source}-${c.chunk_index ?? i}`}>
              <div className="citation-head">
                <span className="citation-title">{c.title ?? c.source}</span>
                {c.score !== null && (
                  <span className="citation-score">{c.score.toFixed(2)}</span>
                )}
              </div>
              <p className="citation-excerpt">{c.excerpt}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="empty">
      <h2>Ready when you are</h2>
      <p>
        Press <strong>Start speaking</strong> and just talk — about a presentation
        you're preparing, an interview coming up, or anything you want to rehearse.
        The coach listens to how you speak, not only what you say, and slows down
        when you do.
      </p>
      <p className="empty-note">
        This is a practice tool, not a clinical one. It doesn't diagnose or assess
        anyone.
      </p>
    </div>
  );
}
