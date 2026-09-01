import { useCallback, useEffect, useRef, useState } from "react";

import { MicrophoneCapture } from "../audio/capture";
import { StreamPlayer } from "../audio/player";
import { api, wsUrl } from "../lib/api";
import type {
  AcousticProfile,
  Citation,
  Message,
  Mode,
  StageTiming,
  SystemStatus,
} from "../lib/types";

const LIVE_SAMPLE_RATE = 24_000;
const KNOWLEDGE_SAMPLE_RATE = 16_000;

export type ConnectionState = "idle" | "connecting" | "connected" | "error";

interface ModeNotice {
  mode: Mode;
  liveAvailable: boolean;
  detail?: string;
}

let messageSeq = 0;
const nextId = () => `m${++messageSeq}`;

/**
 * Owns the conversation: sockets, microphone, playback, and the transition
 * between the two modes.
 *
 * The mode switch is the part worth understanding. Live Coach is preferred
 * because it answers in ~200 ms, but it cannot retrieve — so when the server
 * flags a turn as needing reference material, this reconnects on the knowledge
 * socket, replays the question there, and reports the switch to the user. The
 * conversation is one thread throughout; the server keeps both modes writing
 * into the same session.
 */
export function useCoachSession() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("knowledge");
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const [messages, setMessages] = useState<Message[]>([]);
  const [notice, setNotice] = useState<ModeNotice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [micLevel, setMicLevel] = useState(0);

  const socketRef = useRef<WebSocket | null>(null);
  const captureRef = useRef<MicrophoneCapture | null>(null);
  const playerRef = useRef<StreamPlayer | null>(null);
  const pendingCoachRef = useRef<string | null>(null);
  const sessionRef = useRef<string | null>(null);
  const speakingTimer = useRef<number | null>(null);

  useEffect(() => {
    sessionRef.current = sessionId;
  }, [sessionId]);

  // ---- message helpers ------------------------------------------------

  const appendMessage = useCallback((message: Message) => {
    setMessages((prev) => [...prev, message]);
  }, []);

  const patchMessage = useCallback((id: string, patch: Partial<Message>) => {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, ...patch } : m)),
    );
  }, []);

  /** Append streamed coach text, opening a pending bubble on the first delta. */
  const appendCoachDelta = useCallback(
    (delta: string, turnMode: Mode) => {
      const id = pendingCoachRef.current;
      if (id) {
        setMessages((prev) =>
          prev.map((m) => (m.id === id ? { ...m, text: m.text + delta } : m)),
        );
        return;
      }
      const newId = nextId();
      pendingCoachRef.current = newId;
      setMessages((prev) => [
        ...prev,
        { id: newId, role: "coach", mode: turnMode, text: delta, pending: true },
      ]);
    },
    [],
  );

  const finalizeCoach = useCallback(
    (patch: Partial<Message> = {}) => {
      const id = pendingCoachRef.current;
      if (!id) return;
      pendingCoachRef.current = null;
      patchMessage(id, { pending: false, ...patch });
    },
    [patchMessage],
  );

  // ---- teardown -------------------------------------------------------

  const teardown = useCallback(async () => {
    if (speakingTimer.current !== null) {
      clearInterval(speakingTimer.current);
      speakingTimer.current = null;
    }
    socketRef.current?.close();
    socketRef.current = null;
    await captureRef.current?.stop();
    captureRef.current = null;
    await playerRef.current?.close();
    playerRef.current = null;
    setListening(false);
    setSpeaking(false);
    setMicLevel(0);
  }, []);

  useEffect(() => () => void teardown(), [teardown]);

  // ---- bootstrap ------------------------------------------------------

  const refreshStatus = useCallback(async () => {
    try {
      const s = await api.status();
      setStatus(s);
      return s;
    } catch {
      setError("Can't reach the coach service. Is the backend running?");
      return null;
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  const ensureSession = useCallback(async (): Promise<string> => {
    if (sessionRef.current) return sessionRef.current;
    const created = await api.createSession();
    sessionRef.current = created.id;
    setSessionId(created.id);
    return created.id;
  }, []);

  // ---- inbound frames -------------------------------------------------

  const handleFrame = useCallback(
    (raw: string, activeMode: Mode) => {
      let frame: { type: string; data: Record<string, unknown> };
      try {
        frame = JSON.parse(raw);
      } catch {
        return;
      }
      const data = frame.data ?? {};

      switch (frame.type) {
        case "ready":
        case "mode": {
          const liveAvailable = Boolean(data.live_available ?? activeMode === "live");
          if (data.session_id) {
            sessionRef.current = String(data.session_id);
            setSessionId(String(data.session_id));
          }
          if (!liveAvailable && data.detail) {
            setNotice({
              mode: "knowledge",
              liveAvailable: false,
              detail: String(data.detail),
            });
          }
          break;
        }

        case "transcript": {
          const role = String(data.role);
          const text = String(data.text ?? "");
          const final = Boolean(data.final);
          if (!text) break;

          if (role === "user") {
            // The user's own words arrive only after transcription, so they
            // land here rather than being echoed optimistically on send.
            if (final) {
              appendMessage({
                id: nextId(),
                role: "user",
                mode: activeMode,
                text,
              });
            }
          } else if (final) {
            // Live mode delivers whole utterances; knowledge mode streams
            // deltas and closes on `done`.
            if (activeMode === "live") {
              finalizeCoach();
              appendMessage({ id: nextId(), role: "coach", mode: "live", text });
            }
          } else {
            appendCoachDelta(text, activeMode);
          }
          break;
        }

        case "acoustic": {
          const profile = data as unknown as AcousticProfile;
          // Attach to the most recent user message — the one it describes.
          setMessages((prev) => {
            for (let i = prev.length - 1; i >= 0; i--) {
              if (prev[i].role === "user" && !prev[i].acoustic) {
                const copy = [...prev];
                copy[i] = { ...copy[i], acoustic: profile };
                return copy;
              }
            }
            return prev;
          });
          break;
        }

        case "citations": {
          const citations = (data.citations ?? []) as Citation[];
          const grounded = Boolean(data.grounded);
          if (pendingCoachRef.current) {
            patchMessage(pendingCoachRef.current, { citations, grounded });
          } else {
            // Citations can arrive before the first token; stash them on a
            // bubble opened now so nothing is lost.
            const id = nextId();
            pendingCoachRef.current = id;
            appendMessage({
              id,
              role: "coach",
              mode: activeMode,
              text: "",
              pending: true,
              citations,
              grounded,
            });
          }
          break;
        }

        case "done": {
          finalizeCoach({
            timings: (data.timings ?? []) as StageTiming[],
            totalMs: Number(data.total_ms ?? 0),
            grounded: Boolean(data.grounded ?? true),
          });
          break;
        }

        case "handoff": {
          setNotice({
            mode: "knowledge",
            liveAvailable: true,
            detail: String(data.detail ?? "Looking that up in the reference library."),
          });
          break;
        }

        case "error": {
          setError(String(data.message ?? "Something went wrong."));
          finalizeCoach();
          break;
        }
      }
    },
    [appendCoachDelta, appendMessage, finalizeCoach, patchMessage],
  );

  // ---- connect --------------------------------------------------------

  const connect = useCallback(
    async (target: Mode) => {
      await teardown();
      setError(null);
      setConnection("connecting");

      const wantLive = target === "live";
      const rate = wantLive ? LIVE_SAMPLE_RATE : KNOWLEDGE_SAMPLE_RATE;
      const path = wantLive ? "/ws/live" : "/ws/knowledge";

      let id: string;
      try {
        id = await ensureSession();
      } catch {
        setError("Couldn't start a practice session.");
        setConnection("error");
        return;
      }

      const player = new StreamPlayer(wantLive ? LIVE_SAMPLE_RATE : 24_000);
      await player.resume();
      playerRef.current = player;

      const socket = new WebSocket(wsUrl(path, { session_id: id }));
      socket.binaryType = "arraybuffer";
      socketRef.current = socket;

      socket.onopen = async () => {
        setConnection("connected");
        setMode(target);
        try {
          const capture = new MicrophoneCapture({
            sampleRate: rate,
            onLevel: setMicLevel,
            onFrame: (pcm) => {
              if (socket.readyState === WebSocket.OPEN) socket.send(pcm);
            },
          });
          await capture.start();
          captureRef.current = capture;
          setListening(true);
        } catch {
          setError(
            "Microphone access was blocked. You can still type — allow the mic to speak.",
          );
        }
      };

      socket.onmessage = (event) => {
        if (typeof event.data === "string") {
          handleFrame(event.data, target);
        } else {
          playerRef.current?.enqueue(event.data as ArrayBuffer);
        }
      };

      socket.onerror = () => setError("The connection dropped.");

      socket.onclose = (event) => {
        setConnection("idle");
        setListening(false);
        // 1013 "try again later" is the live socket declining because Moshi is
        // down; it has already told us the fallback, so take it automatically
        // rather than leaving the user on a dead screen.
        if (event.code === 1013 && target === "live") {
          void connect("knowledge");
        }
      };

      speakingTimer.current = window.setInterval(() => {
        setSpeaking(playerRef.current?.isPlaying ?? false);
      }, 120);
    },
    [ensureSession, handleFrame, teardown],
  );

  /** Start in the best mode the server currently offers. */
  const start = useCallback(async () => {
    const current = status ?? (await refreshStatus());
    await connect(current?.live_available ? "live" : "knowledge");
  }, [connect, refreshStatus, status]);

  const stop = useCallback(async () => {
    socketRef.current?.send(JSON.stringify({ type: "stop" }));
    await teardown();
    setConnection("idle");
  }, [teardown]);

  const sendText = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      appendMessage({ id: nextId(), role: "user", mode: "text", text: trimmed });
      setError(null);

      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN && mode === "knowledge") {
        socket.send(JSON.stringify({ type: "text", data: { message: trimmed } }));
        return;
      }

      // No knowledge socket open (idle, or mid live session): use the HTTP
      // path, which runs the identical cascade server-side.
      try {
        const id = await ensureSession();
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: trimmed, session_id: id }),
        });
        const body = await response.json();
        if (!response.ok) {
          setError(body?.error?.message ?? "The coach couldn't answer that.");
          return;
        }
        appendMessage({
          id: nextId(),
          role: "coach",
          mode: body.mode,
          text: body.reply,
          citations: body.citations ?? [],
          timings: body.timings ?? [],
          totalMs: body.total_ms,
          grounded: body.grounded,
        });
      } catch {
        setError("Couldn't reach the coach service.");
      }
    },
    [appendMessage, ensureSession, mode],
  );

  /** Push-to-talk release: answer now instead of waiting for the silence timer. */
  const flush = useCallback(() => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "flush" }));
    }
  }, []);

  /** Barge-in: stop the coach's audio the moment the user starts talking. */
  const interrupt = useCallback(() => {
    playerRef.current?.flush();
    setSpeaking(false);
  }, []);

  /**
   * Replay a stored session.
   *
   * The backend has persisted every turn since the first commit, including the
   * acoustic profile and citations, but nothing in the UI ever read them back —
   * so conversation history existed in the database and nowhere a user could
   * see it. This loads one and continues it: further turns append to the same
   * session rather than starting a new one.
   */
  const loadSession = useCallback(
    async (id: string) => {
      await teardown();
      setError(null);
      try {
        const detail = await api.getSession(id);
        sessionRef.current = detail.id;
        setSessionId(detail.id);
        setMessages(
          detail.turns
            .filter((t) => t.text.trim())
            .map((t) => ({
              id: nextId(),
              role: t.role,
              mode: t.mode,
              text: t.text,
              acoustic: t.acoustic,
              citations: t.citations ?? [],
              totalMs: t.total_ms ?? undefined,
            })),
        );
        pendingCoachRef.current = null;
      } catch {
        setError("Couldn't open that session.");
      }
    },
    [teardown],
  );

  /** Begin a fresh session, leaving the previous one stored. */
  const newSession = useCallback(async () => {
    await teardown();
    setError(null);
    setMessages([]);
    pendingCoachRef.current = null;
    sessionRef.current = null;
    setSessionId(null);
  }, [teardown]);

  return {
    status,
    sessionId,
    mode,
    connection,
    messages,
    notice,
    error,
    listening,
    speaking,
    micLevel,
    start,
    stop,
    connect,
    sendText,
    flush,
    interrupt,
    loadSession,
    newSession,
    refreshStatus,
    dismissNotice: () => setNotice(null),
    dismissError: () => setError(null),
  };
}
