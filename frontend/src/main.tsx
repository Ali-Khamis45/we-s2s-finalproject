import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import App from "./App";
import { AuthScreen } from "./components/AuthScreen";
import { Settings } from "./components/Settings";
import { AuthProvider, useAuth } from "./lib/AuthContext";
import "./styles.css";

/**
 * Waits for the boot refresh before deciding what to render.
 *
 * Rendering the login screen first and swapping it out once the refresh
 * succeeds produces a flash of "signed out" on every reload, which reads as
 * being logged out — quiet nothing is better than a wrong answer.
 */
function Guarded({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <BootScreen />;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function PublicOnly({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <BootScreen />;
  if (user) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function BootScreen() {
  return (
    <div className="boot" role="status" aria-label="Loading">
      <span className="boot-dot" />
    </div>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route
            path="/login"
            element={
              <PublicOnly>
                <AuthScreen mode="login" />
              </PublicOnly>
            }
          />
          <Route
            path="/register"
            element={
              <PublicOnly>
                <AuthScreen mode="register" />
              </PublicOnly>
            }
          />
          <Route
            path="/settings"
            element={
              <Guarded>
                <Settings />
              </Guarded>
            }
          />
          <Route
            path="/"
            element={
              <Guarded>
                <App />
              </Guarded>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
