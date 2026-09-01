import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cx } from "./cx";

/**
 * The primitive set. Built before the feature components, because doing it the
 * other way round is what produces the inconsistency that reads as generated.
 *
 * Amber is rationed here rather than by convention: only `variant="primary"`
 * is allowed to be fully saturated, and the app never renders two at once.
 * Scarcity is what makes it read as light rather than as a theme colour.
 */

/* ---------------------------------------------------------------- Button */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** Fills from the left as the action becomes available. */
  progress?: number;
  children: ReactNode;
}

export function Button({
  variant = "secondary",
  progress,
  className,
  children,
  style,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      className={cx("btn", `btn-${variant}`, className)}
      style={
        progress === undefined
          ? style
          : ({ ...style, "--btn-fill": `${Math.round(progress * 100)}%` } as React.CSSProperties)
      }
      {...rest}
    >
      <span className="btn-label">{children}</span>
    </button>
  );
}

/* ------------------------------------------------------------------ Pill */

interface PillProps {
  /** Drives colour and pulse cadence — the pill *is* the latency readout. */
  tone: "live" | "grounded" | "quiet";
  label: string;
  detail?: string;
  className?: string;
}

export function Pill({ tone, label, detail, className }: PillProps) {
  return (
    <span className={cx("pill", `pill-${tone}`, className)}>
      <span className="pill-dot" aria-hidden="true" />
      <span className="pill-label">{label}</span>
      {detail && <span className="pill-detail num">{detail}</span>}
    </span>
  );
}

/* ---------------------------------------------------------------- Banner */

interface BannerProps {
  tone: "info" | "error";
  children: ReactNode;
  onDismiss?: () => void;
  /** Screen readers should hear an error immediately, a notice politely. */
  live?: "polite" | "assertive";
}

export function Banner({ tone, children, onDismiss, live = "polite" }: BannerProps) {
  return (
    <div
      className={cx("banner", `banner-${tone}`)}
      role={tone === "error" ? "alert" : "status"}
      aria-live={live}
    >
      <span className="banner-rail" aria-hidden="true" />
      <p className="banner-text">{children}</p>
      {onDismiss && (
        <button
          type="button"
          className="banner-close"
          onClick={onDismiss}
          aria-label="Dismiss"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <path
              d="M2 2l8 8M10 2l-8 8"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </svg>
        </button>
      )}
    </div>
  );
}

/* ----------------------------------------------------------------- Panel */

interface PanelProps {
  title: string;
  children: ReactNode;
  className?: string;
}

export function Panel({ title, children, className }: PanelProps) {
  return (
    <section className={cx("panel", className)}>
      {/* Elevation is light, not shadow: a warm top edge, no box-shadow —
          which does nothing on a dark ground anyway. */}
      <span className="panel-edge" aria-hidden="true" />
      <h3 className="panel-title eyebrow">{title}</h3>
      {children}
    </section>
  );
}

/* ------------------------------------------------------------------ Stat */

export function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat">
      <span className="stat-value num">{value}</span>
      <span className="stat-label eyebrow">{label}</span>
    </div>
  );
}
