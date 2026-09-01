import { useEffect, useRef, useState } from "react";

import type { Citation, Message, StageTiming } from "../lib/types";
import { DysfluencyTimeline } from "./DysfluencyTimeline";
import { TurnTimings } from "./TurnTimings";

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

  // The other path's most recent timed turn, so a coach turn can be shown
  // against it. One image of ~200 ms beside ~2 s is the project's argument.
  const counterpart = (mode: Message["mode"]) => {
    const otherLive = mode !== "live";
    const match = [...messages]
      .reverse()
      .find(
        (m) =>
          m.role === "coach" &&
          (m.timings?.length ?? 0) > 0 &&
          (otherLive ? m.mode === "live" : m.mode !== "live"),
      );
    return match
      ? {
          label: otherLive ? "Live coach" : "Grounded",
          timings: match.timings ?? [],
          totalMs: match.totalMs,
        }
      : null;
  };

  return (
    // Coach turns are announced politely; a screen reader user should hear the
    // reply without being interrupted mid-sentence.
    <div className="conversation" aria-live="polite" aria-relevant="additions text">
      {messages.map((m, i) => (
        <MessageBubble
          key={m.id}
          message={m}
          index={i}
          compareWith={m.role === "coach" ? counterpart(m.mode) : null}
        />
      ))}
      {speaking && (
        <div className="speaking-hint">
          <span className="speaking-dots" aria-hidden="true">
            <i /> <i /> <i />
          </span>
          Coach is speaking — just talk to interrupt
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}

function MessageBubble({
  message,
  index,
  compareWith,
}: {
  message: Message;
  index: number;
  compareWith?: { label: string; timings: StageTiming[]; totalMs?: number } | null;
}) {
  const isUser = message.role === "user";

  return (
    <article
      className={`turn ${isUser ? "turn-user" : "turn-coach"}`}
      // Stagger only the first handful; a long thread should not cascade.
      style={{ animationDelay: `${Math.min(index, 4) * 40}ms` }}
    >
      <header className="turn-head">
        <span className="turn-who">{isUser ? "You" : "Coach"}</span>
        <ModeTag mode={message.mode} />
        {message.grounded === false && !isUser && (
          <span className="turn-flag" title="No matching reference material was found">
            ungrounded
          </span>
        )}
        {message.timings && message.timings.length > 0 ? (
          <span className="turn-ms">
            <TurnTimings
              timings={message.timings}
              totalMs={message.totalMs}
              compareWith={compareWith}
            />
          </span>
        ) : message.totalMs ? (
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
  return <span className={`tag tag-${mode}`}>{label}</span>;
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
        Press start and talk about whatever you need to rehearse — a presentation,
        an interview, a phone call you have been putting off. The coach listens to
        how you speak as well as what you say, and slows down when you do.
      </p>
      <p className="empty-note">
        A practice tool, not a clinical one. It doesn&rsquo;t diagnose or assess
        anyone.
      </p>
    </div>
  );
}
