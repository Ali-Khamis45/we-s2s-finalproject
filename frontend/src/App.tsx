import { useEffect, useMemo } from "react";

import { Atmosphere } from "./components/Atmosphere";
import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { ProgressPanel } from "./components/ProgressPanel";
import { SessionHistory } from "./components/SessionHistory";
import { ThemeToggle } from "./components/ThemeToggle";
import { Banner, Pill } from "./components/ui/primitives";
import { useCoachSession } from "./hooks/useCoachSession";

export default function App() {
  const session = useCoachSession();

  // Refetch the dashboard when a turn completes, not on a timer.
  const refreshKey = useMemo(
    () => session.messages.filter((m) => m.role === "user").length,
    [session.messages],
  );

  const live = session.connection === "connected" && session.mode === "live";
  const connected = session.connection === "connected";

  // ?session=<id> opens a stored session directly, so a practice thread can be
  // reopened from a bookmark or a link rather than only from the panel.
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("session");
    if (wanted) void session.loadSession(wanted);
    // Deliberately mount-only: re-running would fight the user's navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the URL pointing at whatever is open, without adding history entries.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (session.sessionId) url.searchParams.set("session", session.sessionId);
    else url.searchParams.delete("session");
    window.history.replaceState(null, "", url);
  }, [session.sessionId]);

  // Escape dismisses whatever is showing, newest first.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (session.error) session.dismissError();
      else if (session.notice) session.dismissNotice();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [session]);

  return (
    <>
      <Atmosphere
        micLevel={session.micLevel}
        listening={session.listening}
        connected={connected}
      />

      <div className="app">
        <header className="masthead">
          <div className="brand">
            <h1 className="rise rise-1">
              Speech <span className="brand-accent">Confidence</span> Coach
            </h1>
            <p className="brand-sub rise rise-2">
              Practice speaking with a coach that listens to how you say it — not
              only what you say.
            </p>
          </div>

          <div className="mast-controls rise rise-3">
            <ThemeToggle />
            <Pill
              tone={live ? "live" : connected ? "grounded" : "quiet"}
              label={live ? "Live coach" : "Grounded"}
              detail={live ? "~200 ms" : "~1 s"}
            />
          </div>
        </header>

        {/* Degraded is a real state, not an error: calm, explained, and it
            names what still works. */}
        {session.notice && (
          <Banner tone="info" onDismiss={session.dismissNotice}>
            {session.notice.detail}
          </Banner>
        )}

        {session.error && (
          <Banner tone="error" live="assertive" onDismiss={session.dismissError}>
            {session.error}
          </Banner>
        )}

        <main className="layout">
          <div className="main-col">
            <Conversation messages={session.messages} speaking={session.speaking} />
            <Composer
              connection={session.connection}
              listening={session.listening}
              micLevel={session.micLevel}
              speaking={session.speaking}
              onStart={() => void session.start()}
              onStop={() => void session.stop()}
              onSend={(text) => void session.sendText(text)}
              onInterrupt={session.interrupt}
            />
          </div>

          <div className="side">
            <ProgressPanel status={session.status} refreshKey={refreshKey} />
            <SessionHistory
              activeId={session.sessionId}
              refreshKey={refreshKey}
              onOpen={(id) => void session.loadSession(id)}
              onNew={() => void session.newSession()}
            />
          </div>
        </main>

        <footer className="foot">
          <strong>An accessibility practice tool.</strong> Not a medical device —
          it doesn&rsquo;t diagnose or assess anyone.
        </footer>
      </div>
    </>
  );
}
