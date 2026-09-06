import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Stylesheet guards for things jsdom cannot catch.
 *
 * These exist because the unit suite renders components without a layout
 * engine: every component test can pass while the page is visibly broken. The
 * rules below encode layout invariants only a real browser would expose.
 *
 * Read from disk rather than imported: vitest stubs CSS imports to an empty
 * string, so `?raw` yields nothing here.
 */
const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf-8");

/** Return the declaration block for a top-level selector. */
function rule(selector: string): string {
  // Matches the selector at the start of a line through its closing brace.
  // Good enough for this flat stylesheet, which has no nesting.
  const re = new RegExp(
    `^\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`,
    "m",
  );
  const found = css.match(re);
  if (!found) throw new Error(`no rule found for ${selector}`);
  return found[1];
}

describe("absolutely positioned canvases", () => {
  /**
   * A <canvas> is a *replaced element*. When an absolutely positioned replaced
   * element has `width: auto`, CSS resolves its width from the INTRINSIC size
   * — 300x150 for a canvas whose width/height attributes were never set — and
   * then treats the insets as over-constrained and drops one.
   *
   * So `inset: 18%` alone does not size a canvas. It silently renders at
   * 300x150 and spills out of its container. This shipped: the orb's shader
   * core covered the composer the moment the mic went live, and no component
   * test could see it, because jsdom lays nothing out.
   */
  it("gives .orb-fluid an explicit size, not just insets", () => {
    const decl = rule(".orb-fluid");

    expect(decl).toMatch(/position:\s*absolute/);
    // The actual guard: without these the canvas falls back to 300x150.
    expect(decl, "a canvas cannot be sized by insets alone").toMatch(
      /(^|[\s;])width:/,
    );
    expect(decl, "a canvas cannot be sized by insets alone").toMatch(
      /(^|[\s;])height:/,
    );
  });
});
