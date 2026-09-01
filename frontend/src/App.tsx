import { useMemo } from "react";

import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { ProgressPanel } from "./components/ProgressPanel";
import { useCoachSession } from "./hooks/useCoachSession";

export default function App() {
  const session = useCoachSession();

  // Refetch the dashboard when a turn completes, not on a timer.
  const refreshKey = useMemo(
    () => session.messages.filter((m) => m.role === "user").length,
    [session.messages],
  );

  const live = session.connection === "connected" && session.mode === "live";

  return (
    <div className="app">
      <header className="masthead">
        <div className="brand">
          <h1>Speech Confidence Coach</h1>
          <p>Practice speaking with a coach that listens to how you say it</p>
        </div>

        <div className={`mode-pill ${live ? "is-live" : "is-cascade"}`}>
          <span className="mode-dot" aria-hidden="true" />
          <span className="mode-name">{live ? "Live coach" : "Grounded mode"}</span>
          <span className="mode-lat">{live ? "~200 ms" : "~1 s"}</span>
        </div>
      </header>

      {session.notice && (
        <div className="banner banner-info" role="status">
          <p>{session.notice.detail}</p>
          <button type="button" onClick={session.dismissNotice} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}

      {session.error && (
        <div className="banner banner-error" role="alert">
          <p>{session.error}</p>
          <button type="button" onClick={session.dismissError} aria-label="Dismiss">
            ×
          </button>
        </div>
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

        <ProgressPanel status={session.status} refreshKey={refreshKey} />
      </main>

      <footer className="foot">
        <span>
          An accessibility practice tool. Not a medical device — it doesn't diagnose
          or assess anyone.
        </span>
      </footer>
    </div>
  );
}
