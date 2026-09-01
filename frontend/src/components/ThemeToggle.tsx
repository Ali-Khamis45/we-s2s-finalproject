import { useEffect, useState } from "react";

type Theme = "system" | "dark" | "light";
const KEY = "scc-theme";

/**
 * Theme control.
 *
 * The token layer supports three states — an explicit `data-theme` stamp in
 * either direction, plus an unstamped default that follows the OS. Without
 * this, the light palette existed in CSS and no one could ever reach it.
 *
 * Cycles system → dark → light so the OS-following default stays reachable
 * rather than being a one-way door.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const stored = localStorage.getItem(KEY);
      if (stored === "dark" || stored === "light") return stored;
    } catch {
      /* private mode, or site data blocked */
    }
    return "system";
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);

    try {
      if (theme === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, theme);
    } catch {
      /* not fatal: the stamp above already applied */
    }
  }, [theme]);

  const next: Record<Theme, Theme> = { system: "dark", dark: "light", light: "system" };
  const label: Record<Theme, string> = {
    system: "Match system",
    dark: "Dark",
    light: "Light",
  };

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => setTheme(next[theme])}
      aria-label={`Theme: ${label[theme]}. Switch to ${label[next[theme]]}.`}
      title={`Theme: ${label[theme]}`}
    >
      <Glyph theme={theme} />
      <span className="theme-label">{label[theme]}</span>
    </button>
  );
}

function Glyph({ theme }: { theme: Theme }) {
  if (theme === "light") {
    return (
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="4.5" fill="currentColor" />
        <path
          d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
        />
      </svg>
    );
  }
  if (theme === "dark") {
    return (
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.8 6.8 0 0 0 10.5 10.5z"
          fill="currentColor"
        />
      </svg>
    );
  }
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect
        x="2.5"
        y="4.5"
        width="19"
        height="13"
        rx="2"
        stroke="currentColor"
        strokeWidth="1.8"
      />
      <path d="M8 20.5h8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
