# Frontend Rebuild Brief — "Night Studio"

Paste this whole file as a single prompt into Claude Code, opened at the repo root
(`speach-to-speach ai/`). It is written to be executed, not admired: every section is a
constraint the finished UI is checked against.

---

## 0. Role

You are the design engineer rebuilding the entire frontend of **Speech Confidence Coach** —
a native speech-to-speech AI coach for people with speech differences (stammering, blocks,
prolongations). The current UI works and is well-architected underneath, but it *looks*
generated: default Inter/IBM Plex, flat grey cards, 3px radii, no atmosphere, nothing moves.

Rebuild the visual layer completely. Do not touch the logic layer. The result should look
like it came out of a studio that ships one product a year, not out of a prompt.

---

## 1. Read before you write

Open and understand these before making a single design decision:

- `Readme.md` — what the product is and the two paths (Live ~200 ms / Grounded ~1 s)
- `docs/ETHICS.md` — the constraints that outrank aesthetics
- `frontend/src/App.tsx` — the shell and the full component inventory
- `frontend/src/components/*.tsx` — Conversation, Composer, DysfluencyTimeline, ProgressPanel
- `frontend/src/hooks/useCoachSession.ts` — every piece of live state you can animate against
- `frontend/src/styles.css` — the current token system (its *structure* is good; its values are not)
- `design/furni-1.0.0.zip` — colour reference only. Do not copy its layout, its Bootstrap,
  or its component patterns. You are taking four hex values and nothing else.

**The state you are allowed to drive motion from** (already in the hook — use it, do not
invent decorative state): `connection`, `mode`, `listening`, `micLevel` (0..1, per frame),
`speaking`, `messages`, `notice`, `error`, `status`.

---

## 2. Skills — use them, in this order

1. **`design`** — before writing any code, produce a design canvas with artboards for:
   idle state, listening state (mic hot), coach speaking, a turn with a dysfluency timeline,
   the progress panel, and the error/degraded state. Get the composition right visually
   before it becomes CSS.
2. **`artifact-design`** — read it for type scale, spacing rhythm, colour discipline, and
   the difference between "designed" and "decorated". Apply its fundamentals to the React
   app, not just to artifacts.
3. **`dataviz`** — mandatory before you touch `PaceChart` in `ProgressPanel.tsx` or the
   `DysfluencyTimeline` bars. These are the two data surfaces in the product and they are
   currently the weakest thing in it.
4. **`artifact-diagramming`** — for the mode/handoff explainer (Live ↔ Grounded) if you add
   one, and for any inline SVG you author by hand.
5. **`humanizer`** — run every string of UI copy through it. "Speak in a session and your
   pacing trend will build here" is the exact register of AI filler this rebuild is meant to
   kill. Microcopy is 30% of why an interface reads as vibe-coded.
6. **Figma MCP** (`get_figma_skill`, then `use_figma`) — optional, but if you want the
   palette + type + component set to exist as a real design system rather than only as CSS
   variables, push the token set and the core components into a Figma library so the thesis
   has a design artefact to show alongside the code.

---

## 3. Direction: Night Studio

One sentence: **a dark recording booth lit by a single warm lamp, where the interface
breathes with the voice in the room.**

The Furni palette inverted. Its deep green (`#3b5d50`) stops being a surface colour and
becomes the *air* — pushed almost to black, it is the room. Its amber (`#f9bf29`) stops
being a button fill and becomes the *light* — the only hot colour on screen, reserved for
the live voice path and the primary action. Its pale tints (`#dce5e4`, `#eff2f1`) stop being
backgrounds and become the type.

The result reads as calm, low-stimulus, and expensive — which is also the correct emotional
register for an app someone opens because speaking is hard for them.

Emotional targets: **still, warm, attentive.** Not: energetic, playful, corporate, "AI".

---

## 4. Colour

Full token set. Dark is the default and ships first; light is the faithful-Furni fallback.

```css
:root {
  /* ground — the room, green-black, never pure #000 */
  --ground-0:  #0a0f0d;   /* deepest, page edges + vignette */
  --ground-1:  #0e1512;   /* body */
  --ground-2:  #141d19;   /* raised surface / cards */
  --ground-3:  #1b2621;   /* hover, input wells */
  --scrim:     #060a09;   /* modal + overlay */

  /* structure */
  --rule:      #223029;
  --rule-firm: #33463d;

  /* type */
  --ink:       #eff2f1;   /* Furni lighter — headings */
  --ink-2:     #dce5e4;   /* Furni light — body */
  --muted:     #8ca096;   /* metadata, timestamps */
  --faint:     #5d7268;   /* disabled, placeholder */

  /* the lamp — reserved, never decorative */
  --amber:     #f9bf29;   /* Furni secondary — primary action, live path */
  --amber-hot: #ffd35c;   /* hover / peak */
  --amber-dim: #b8891a;   /* pressed / low-emphasis */
  --amber-wash: rgba(249, 191, 41, 0.12);
  --amber-glow: rgba(249, 191, 41, 0.28);

  /* the second voice — grounded/cascade path */
  --sage:      #6f9c8a;   /* lifted from Furni primary #3b5d50 for dark-ground contrast */
  --sage-deep: #3b5d50;   /* Furni primary, used as fill not as text */
  --sage-wash: rgba(111, 156, 138, 0.12);

  --danger:    #e8705c;
  --danger-wash: rgba(232, 112, 92, 0.12);

  /* dysfluency event hues — categorical, NOT a severity ramp. See §9. */
  --ev-block:   #f9bf29;
  --ev-prolong: #e0a25f;
  --ev-sound:   #9d8ce0;
  --ev-word:    #e08bb0;
  --ev-filler:  #7d938a;
  --ev-unsure:  #4a5c54;
}
```

Rules that make this look designed rather than themed:

- **Amber is rationed.** At most one amber element is fully saturated at any moment. If the
  mic is live, the Send button is not amber. Scarcity is what makes it read as light.
- Elevation is **light, not shadow**. On a dark ground a drop shadow is invisible; raise a
  surface with a 1px `--rule-firm` top edge and a barely-there radial warm wash, not `box-shadow`.
- Contrast floor: 4.5:1 for body, 3:1 for large text and UI boundaries. `--muted` on
  `--ground-1` passes; `--faint` is for non-essential text only.
- Radii: `--r-sm: 8px`, `--r-md: 14px`, `--r-lg: 22px`, `--r-full: 999px`. The current 3px is
  the single most "unstyled default" value in the codebase — it goes.

**Light theme:** re-declare the same tokens under `:root[data-theme="light"]` mapping back to
Furni proper — ground `#eff2f1`, surface `#ffffff`, ink `#2f2f2f`, body `#6a6a6a`, primary
`#3b5d50`, accent `#f9bf29`. Every colour must be defined on bare `:root` first; nothing may
have its only definition inside a media query.

---

## 5. Typography

Three families, all Google Fonts, chosen to be compatible by contrast rather than by
similarity: an opinionated variable grotesque for display, a neutral-but-not-Inter workhorse
for text, a technical mono for numbers.

| Role | Family | Weights | Used for |
|---|---|---|---|
| Display | **Bricolage Grotesque** | 600, 700, 800 (variable, opsz) | h1–h3, mode pill name, big stats |
| Text/UI | **Instrument Sans** | 400, 500, 600 | body, buttons, labels, messages |
| Data | **JetBrains Mono** | 400, 500 | latency, ms, wpm, timestamps, confidence |

Optional fourth, for exactly one word in the masthead if you want a signature: **Instrument
Serif** *italic* — e.g. "Speech *Confidence* Coach". Use it once or not at all.

```html
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600..800&family=Instrument+Sans:ital,wght@0,400..600;1,400&family=JetBrains+Mono:wght@400;500&display=swap">
```

Typographic rules:

- Display gets **negative tracking** (`-0.02em` to `-0.035em`, tighter as size grows) and
  `line-height: 1.02–1.1`. Bricolage at default tracking looks unfinished.
- Text sits at `15px / 1.6`, tracking `0`. Never letterspace lowercase body text.
- Mono is `font-variant-numeric: tabular-nums` **everywhere** — latency readouts and wpm must
  not reflow as digits change. This is the detail that separates instrument from webpage.
- Eyebrows/section labels: Instrument Sans 500, `11px`, `0.14em` tracking, uppercase,
  `--muted`. Exactly one styling for all of them.
- Type scale (rem, 1rem = 16px): `0.6875 / 0.8125 / 0.9375 / 1.0625 / 1.375 / 1.875 / 2.75 / 4`.
  Nothing between steps.
- Load fonts with `font-display: swap` and set a real fallback stack on every family.

---

## 6. Atmosphere

The single image. Rules:

- It is **generated, abstract, and dark** — a soft warm light-fall in a green-black space,
  visible grain, no recognisable objects. Think a photograph of light in a room with the room
  removed. **No stock photos of people**, no headsets, no glowing brains, no circuit boards,
  no hands on microphones. Every one of those is the visual equivalent of "vibe-coded".
- Ship it as **one optimised `.webp` under 180 KB**, placed at `frontend/public/atmosphere.webp`,
  applied to the app shell at low opacity (`0.35–0.5`) with `mix-blend-mode: screen` over
  `--ground-1`, and covered by a vignette so the edges fall to `--ground-0`.
- Over it, a **procedural layer you author, not a library**: an animated radial amber
  light-fall that drifts on a 40–60 s loop, plus an SVG `feTurbulence` grain overlay at
  ~4% opacity. The grain is what stops a dark UI from looking like flat CSS.
- The wallpaper responds, subtly, to state: the light-fall warms and expands while the mic is
  live, cools and contracts while idle. Amplitude tied to `micLevel`, heavily smoothed —
  it should be felt, not watched.
- Prefer generating the image with the `design` skill / an image step and committing it.
  If you cannot generate one, build the entire atmosphere procedurally in CSS+SVG rather than
  shipping a placeholder.

---

## 7. Motion

Everything moves, and every movement means something. Decoration that isn't tied to state is
the thing we are removing, not adding.

**Tokens**

```css
--t-instant: 90ms;   --t-fast: 160ms;   --t-base: 260ms;
--t-slow: 420ms;     --t-scene: 700ms;
--e-out:   cubic-bezier(0.22, 1, 0.36, 1);      /* entrances */
--e-in-out:cubic-bezier(0.65, 0, 0.35, 1);      /* state changes */
--e-spring: spring(1, 90, 12, 0);               /* framer-motion equivalent */
```

**Inventory — build every one of these**

| Surface | Motion |
|---|---|
| App shell | Ground gradient drifts on a 60 s loop; grain static; vignette breathes with session state |
| Masthead | Staggered entrance on mount (60 ms apart, 12 px rise + fade); serif accent word fades last |
| Mode pill | Morphs between Live and Grounded — width animates, dot pulses at ~200 ms cadence for Live and ~1 s for Grounded, so the pill *is* the latency readout |
| Mic button | The centrepiece. An audio-reactive orb: canvas or SVG, amber, radius and glow driven by `micLevel` at 60 fps with attack-fast / release-slow smoothing. Idle = slow 4 s breath. Connecting = indeterminate arc sweep. Live = reactive. Never a spinner. |
| Level meter | Replace the 14 flat bars with a continuous waveform or a radial ring around the orb; bars decay with spring release, never snap to zero |
| Composer | Textarea well lifts and its border warms on focus; Send button fills from left as text becomes valid; disabled → enabled is a transition, not a repaint |
| Messages | New turn enters with 8 px rise + fade + 1.5% scale, `--t-base`, staggered per line; coach messages animate in as the audio starts, not before |
| Speaking indicator | Three-dot or waveform tied to `speaking`, amber, cross-fades out over `--t-slow` |
| Dysfluency timeline | Track draws left→right on first paint (`--t-scene`); event blocks scale up from their own left edge, staggered by time position; hover lifts the block and reveals the readout |
| Pace chart | Line path draws with `stroke-dasharray` over `--t-scene`; points fade in after; y-axis gridlines fade first. Read the `dataviz` skill before designing it. |
| Panel rows | System status rows: the availability dot cross-fades between states, no layout shift |
| Banners | Enter from the top with a spring settle, auto-height, exit by collapsing height and fading — never a jump |
| Buttons (all) | Hover: 1px lift + border warm, `--t-fast`. Press: 0.98 scale, `--t-instant`. Focus-visible: 2px amber ring, offset 3px — visible on every interactive element without exception |
| Page/section transitions | Sections reveal on scroll via IntersectionObserver, once, 24 px rise, never re-trigger |

**Reduced-motion contract — non-negotiable**

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

That CSS block is the floor, not the whole job. Also:

- Read the preference in JS (`useReducedMotion` from framer-motion, or a `matchMedia` hook)
  and **stop the rAF loops entirely** — the orb, the ground drift, the waveform. A paused
  canvas is the point; a fast-forwarded animation still burns battery and still flickers.
- Reduced-motion mode must remain fully legible and fully functional: the orb becomes a
  static amber ring whose *size* still encodes mic level, updated at most 4×/second.
- Never encode information *only* in motion. If the pill's pulse says "live", the text must
  say it too.
- No parallax, no auto-playing carousels, no motion in the periphery while the user is
  mid-utterance. This app is used by people who are concentrating hard on speaking; a
  distracting interface is a functional defect here, not a taste question.

---

## 8. Stack and file plan

New dependencies are approved. Suggested set — justify anything beyond it:

```
framer-motion     orchestration, layout animations, useReducedMotion
lenis             smooth scroll (optional; drop it if it fights reduced-motion)
clsx              conditional classNames
```

Keep **CSS variables + hand-written CSS modules** as the styling layer. Do not introduce
Tailwind: the existing `styles.css` token architecture is genuinely good and a utility
rewrite would lose the semantic naming that makes the ethics constraints legible in the code.

```
frontend/src/
  styles/
    tokens.css         colour, type, space, radius, motion tokens (both themes)
    base.css           reset, typography, focus rings, reduced-motion floor
    atmosphere.css     ground gradient, grain, vignette, light-fall
  components/
    ui/                Button, Pill, Banner, Panel, Stat, Eyebrow — the primitives
    VoiceOrb.tsx       canvas audio-reactive orb (the centrepiece)
    Atmosphere.tsx     procedural background layer
    ...existing components, restyled
  hooks/
    useReducedMotionSafe.ts
    useSmoothed.ts     attack/release smoothing for micLevel
```

Build the primitives in `ui/` **first**, then restyle the feature components against them.
Doing it the other way round produces the inconsistency that reads as vibe-coded.

---

## 9. Hard constraints — these outrank the design

1. **Do not change any component's props, state shape, or the API layer.** `lib/api.ts`,
   `lib/types.ts`, `audio/*`, and `hooks/useCoachSession.ts` are off limits except for
   additive, motion-only helpers.
2. **All 30 frontend tests must still pass.** They query by role, accessible name, and text
   content — not by class — so restyling is safe, but every `aria-label`, button name, and
   visible string they assert on (`"168 wpm"`, `"heuristic"`, `"Repeated sound"`,
   `/block at 1.0 s, lasting 1.4 s/`, `/no interruptions detected/i`) must survive verbatim.
   If you change one, update the test in the same commit and say so.
3. **The dysfluency event colours are categorical hues, never a severity ramp.** Nothing in
   this interface may imply that one kind of speech is worse than another — no red-to-green,
   no "score", no warning iconography, no downward-trend styling on the pace chart. This is
   an ethics requirement from `docs/ETHICS.md`, not a preference.
4. **The disclaimer stays visible**, not buried in a collapsed footer: this is an
   accessibility practice tool, not a medical device.
5. **Degraded states are first-class.** The app is designed to run with models missing. The
   "Live coach unavailable, falling back to Grounded" state must look deliberate and calm —
   design it as a real screen, not as an error.
6. Keyboard: full tab order, visible focus on everything, Escape dismisses banners,
   Space/Enter toggles the mic. Screen readers: `aria-live="polite"` on new coach turns,
   `role="status"` on connection changes.
7. Performance budget: 60 fps on the orb with the mic live, main-thread work under 4 ms/frame,
   no layout thrash (animate `transform`/`opacity` only), total added JS under 60 KB gzipped.

---

## 10. Phases

Work in order and show me the result at each gate.

1. **Design canvas** (`design` skill) — six artboards, palette + type applied. Stop. Get sign-off.
2. **Tokens + base** — `tokens.css`, `base.css`, fonts loaded, both themes, reduced-motion floor.
3. **Atmosphere** — image generated/committed + procedural light-fall + grain. Static first, drifting second.
4. **UI primitives** — `ui/` components with full interaction states.
5. **VoiceOrb** — the centrepiece, including smoothing, connecting state, and the reduced-motion fallback.
6. **Feature components** — Conversation, Composer, DysfluencyTimeline, ProgressPanel, banners, masthead, footer.
7. **Data surfaces** — timeline + pace chart, after reading `dataviz`.
8. **Copy pass** — every string through `humanizer`.
9. **Verification** — see below.

---

## 11. Acceptance checklist

Run this yourself before telling me you are done. Report it as a list with pass/fail.

- [ ] `npm test` — 30/30 passing
- [ ] `npm run build` — clean, no TS errors
- [ ] Both themes render correctly; no token defined only inside a media query
- [ ] Every interactive element has a visible focus ring
- [ ] `prefers-reduced-motion: reduce` — all rAF loops stopped (verify in DevTools performance),
      UI fully usable, no information lost
- [ ] Contrast audit: every text/background pair ≥ 4.5:1 (≥ 3:1 for large + UI edges)
- [ ] All four session states designed and screenshotted: idle, connecting, live, degraded
- [ ] Orb holds 60 fps with the mic live
- [ ] No stock photography, no gradient-mesh cliché, no glassmorphism, no purple-blue AI gradient
- [ ] Nothing in the UI scores, ranks, or pathologises the user's speech
- [ ] Screenshots of every state committed to `design/screens/`

---

## 12. What "vibe-coded" looks like — do not ship any of this

An explicit anti-pattern list. If the finished UI contains one of these, it failed.

- Default Inter / system-ui at default tracking; a single font doing every job
- The purple→blue diagonal gradient, on anything, ever
- Glassmorphism: `backdrop-filter: blur()` on floating white cards
- Generic 8px-everything radii with no size hierarchy; or 3px radii, which read as unstyled
- Emoji as iconography (🎤 ✨ 🚀), or an icon set mixed from three sources
- `box-shadow: 0 4px 6px rgba(0,0,0,0.1)` on a dark ground, where it does nothing
- Equal visual weight on every element, so nothing is primary
- Colour used decoratively: five accent hues doing no semantic work
- Copy that hedges and inflates: "Seamlessly practice your speech with the power of AI",
  "Your journey to confidence starts here", "Elevate your communication"
- Centred hero + three feature cards + a CTA — the landing-page skeleton applied to a tool
- Animations that are all the same 300 ms ease-in-out fade
- A loading spinner where a state-specific animation belongs
- Perfectly even spacing with no rhythm, no dense/loose contrast between regions

The test to apply to every screen: **could this have been the second-best result from a
one-line prompt?** If yes, it isn't finished.
