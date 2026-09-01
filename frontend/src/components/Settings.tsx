import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, auth as authApi } from "../lib/api";
import { clearAuth } from "../lib/auth";
import { useAuth } from "../lib/AuthContext";
import { Button, Panel } from "./ui/primitives";

/** Account and privacy. Everything the account holds, and how to take it back. */
export function Settings() {
  const { user, update, signOut } = useAuth();
  const navigate = useNavigate();

  const [name, setName] = useState(user?.display_name ?? "");
  const [saved, setSaved] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [pwMessage, setPwMessage] = useState<string | null>(null);
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  if (!user) return null;

  const saveName = async () => {
    await update({ display_name: name.trim() });
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2000);
  };

  const changePassword = async () => {
    setPwMessage(null);
    try {
      await authApi.changePassword(current, next);
      setCurrent("");
      setNext("");
      setPwMessage("Password changed. Other devices have been signed out.");
    } catch (err) {
      setPwMessage(err instanceof ApiError ? err.message : "That didn't work.");
    }
  };

  const deleteAccount = async () => {
    setDeleteError(null);
    try {
      await authApi.deleteAccount(deletePassword);
      clearAuth();
      navigate("/login", { replace: true });
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : "That didn't work.");
    }
  };

  return (
    <div className="app settings-page">
      <header className="masthead">
        <div className="brand">
          <h1>Your account</h1>
          <p className="brand-sub">{user.email}</p>
        </div>
        <Link className="theme-toggle" to="/">
          Back to practice
        </Link>
      </header>

      <div className="settings-grid">
        <Panel title="Name">
          <label className="field">
            <span className="field-label">What the coach calls you</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={80}
              autoComplete="nickname"
            />
          </label>
          <Button onClick={saveName}>{saved ? "Saved" : "Save"}</Button>
        </Panel>

        <Panel title="Password">
          <label className="field">
            <span className="field-label">Current password</span>
            <input
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              autoComplete="current-password"
            />
          </label>
          <label className="field">
            <span className="field-label">
              New password
              <span className="field-hint">At least 12 characters</span>
            </span>
            <input
              type="password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              autoComplete="new-password"
            />
          </label>
          {pwMessage && (
            <p className="field-note" role="status">
              {pwMessage}
            </p>
          )}
          <Button onClick={changePassword} disabled={!current || next.length < 12}>
            Change password
          </Button>
        </Panel>

        <Panel title="Your data">
          <p className="panel-empty">
            Transcripts and the pacing measures taken from them are saved so you
            can look back. <strong>Audio is never stored.</strong>
          </p>
          <div className="settings-actions">
            {/* A plain link, so the browser handles the download. */}
            <a className="btn btn-secondary" href={authApi.exportUrl} download>
              <span className="btn-label">Download everything</span>
            </a>
            <Button
              variant="ghost"
              onClick={() => {
                void signOut();
                navigate("/login", { replace: true });
              }}
            >
              Sign out
            </Button>
          </div>
        </Panel>

        <Panel title="Delete account">
          <p className="panel-empty">
            This removes your account, every session, and every transcript.
            It happens immediately and cannot be undone.
          </p>
          {!confirming ? (
            <Button variant="danger" onClick={() => setConfirming(true)}>
              Delete my account
            </Button>
          ) : (
            <>
              <label className="field">
                <span className="field-label">Confirm with your password</span>
                <input
                  type="password"
                  value={deletePassword}
                  onChange={(e) => setDeletePassword(e.target.value)}
                  autoComplete="current-password"
                />
              </label>
              {deleteError && (
                <p className="field-error" role="alert">
                  {deleteError}
                </p>
              )}
              <div className="settings-actions">
                <Button
                  variant="danger"
                  onClick={deleteAccount}
                  disabled={!deletePassword}
                >
                  Delete permanently
                </Button>
                <Button variant="ghost" onClick={() => setConfirming(false)}>
                  Keep my account
                </Button>
              </div>
            </>
          )}
        </Panel>
      </div>
    </div>
  );
}
