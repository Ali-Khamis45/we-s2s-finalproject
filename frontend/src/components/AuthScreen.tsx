import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError } from "../lib/api";
import { Atmosphere } from "./Atmosphere";
import { useAuth } from "../lib/AuthContext";
import { Button } from "./ui/primitives";

/**
 * Sign in and create account.
 *
 * One column over the Night Studio ground, the atmosphere doing the work. No
 * split-screen hero, no floating glass card.
 *
 * Deliberately absent: a shake animation on error. This app is used by people
 * who are already anxious about performing, and a stress cue for a mistyped
 * password is the wrong instinct. A calm colour change and a clear sentence do
 * the same job without the jolt.
 */
export function AuthScreen({ mode }: { mode: "login" | "register" }) {
  const { signIn, signUp } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isRegister = mode === "register";
  // Mirrors the server policy: length, not composition rules.
  const tooShort = isRegister && password.length > 0 && password.length < 12;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (isRegister) await signUp(email, password, displayName.trim() || undefined);
      else await signIn(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Couldn't reach the coach service. Check it's running and try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {/* The same room as the app itself — signing in should not feel like a
          different product. Idle: no mic, so the lamp sits at its resting
          warmth. */}
      <Atmosphere micLevel={0} listening={false} connected={false} />
      <div className="auth-screen">
        <div className="auth-card">
        <h1 className="auth-title">
          {isRegister ? (
            <>
              Make an <span className="brand-accent">account</span>
            </>
          ) : (
            <>
              Welcome <span className="brand-accent">back</span>
            </>
          )}
        </h1>
        <p className="auth-sub">
          {isRegister
            ? "Your practice sessions stay yours. Nobody else can read them."
            : "Sign in to pick up where you left off."}
        </p>

        <form className="auth-form" onSubmit={submit} noValidate>
          {isRegister && (
            <Field label="What should the coach call you?" hint="Optional">
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                autoComplete="nickname"
                maxLength={80}
              />
            </Field>
          )}

          <Field label="Email">
            <input
              type="email"
              inputMode="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              required
            />
          </Field>

          <Field
            label="Password"
            hint={isRegister ? "At least 12 characters. Length beats symbols." : undefined}
            error={tooShort ? `${12 - password.length} more to go.` : undefined}
          >
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={isRegister ? "new-password" : "current-password"}
              required
            />
          </Field>

          {error && (
            <p className="auth-error" role="alert">
              {error}
            </p>
          )}

          <Button
            type="submit"
            variant="primary"
            progress={email && password && !tooShort ? 1 : 0}
            disabled={busy || !email || !password || tooShort}
            className="auth-submit"
          >
            {busy
              ? isRegister
                ? "Creating your account…"
                : "Signing in…"
              : isRegister
                ? "Create account"
                : "Sign in"}
          </Button>
        </form>

        {isRegister && (
          // On the screen, not behind a link. Someone deciding whether to hand
          // over recordings of their speech should not have to go looking.
          <p className="auth-ethics">
            Practice transcripts are saved so you can look back at them.{" "}
            <strong>Audio is never stored.</strong> You can export or delete
            everything at any time. This is a practice tool, not a medical
            device — it doesn&rsquo;t diagnose or assess anyone.
          </p>
        )}

          <p className="auth-switch">
            {isRegister ? (
              <>
                Already have an account? <Link to="/login">Sign in</Link>
              </>
            ) : (
              <>
                New here? <Link to="/register">Create an account</Link>
              </>
            )}
          </p>
        </div>
      </div>
    </>
  );
}

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    // Label above the field, never a placeholder standing in for one — a
    // placeholder disappears the moment someone starts typing.
    <label className="field">
      <span className="field-label">
        {label}
        {hint && <span className="field-hint">{hint}</span>}
      </span>
      {children}
      {error && <span className="field-error">{error}</span>}
    </label>
  );
}

