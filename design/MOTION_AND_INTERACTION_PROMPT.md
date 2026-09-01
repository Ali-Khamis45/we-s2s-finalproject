# Build Brief — Motion, Interaction, and End-to-End Wiring

Paste into Claude Code at the repo root. Extends `design/UI_REBUILD_PROMPT.md` — that file
defines the palette, type and motion tokens; this one defines the signature interactions and
the front-to-back integration pass.

---

## 0. The reference shots, and how to use them

Three references were supplied. Two were viewed directly; the third is described from its
title and is marked as unconfirmed.

| # | Shot | Designer | What it actually shows |
|---|---|---|---|
| 1 | [Hover Morph Effect Hero Collection](https://dribbble.com/shots/26894245-Hover-Morph-Effect-Hero-Collection-for-WordPress) | Slider Revolution | A hero whose background is a **liquid / chrome-like fluid surface that warps under the cursor**, with heavy display type layered over it |
| 2 | [Personal Portfolio Website](https://dribbble.com/shots/27165049-Personal-Portfolio-Website) | Piotrek Rak | **Oversized serif display type with imagery revealed through the letterforms**, layered panels, editorial small-caps annotations, dark shell around a light content card |
| 3 | [Autoperfect Goggles](https://dribbble.com/shots/15464706-Autoperfect-Goggles) | — | *Unconfirmed.* Specified below as a **cursor-tracked tilt with a lens-shaped reveal mask** — confirm before building §5 |

**Adapt the techniques; do not reproduce the designs.** Interaction mechanics — fluid
displacement, text-masked imagery, magnetic hover — are common craft and free to use. A shot's
specific composition, type choices, and art direction belong to its designer. Reproducing one
closely and presenting it as portfolio work is the single easiest thing for a reviewer to
recognise, and it costs more credibility than a plain interface ever would.

Create `design/REFERENCES.md` crediting all three with links, stating what was taken (the
technique) and what was not (the composition). That file is an asset in a portfolio, not an
admission.

---

## 1. The zoning rule — read before writing any effect

This app is used by people who stammer or block, while they are concentrating on speaking.
A cursor-warping hero behind a live microphone is not bold, it is hostile. So the app is
divided into three zones and **an effect is only allowed in its own zone**.

| Zone | Surfaces | Motion budget |
|---|---|---|
| **Front of house** | Landing page, `/login`, `/register`, the case-study page, the 404 | **Everything.** Fluid hero, text-masked imagery, magnetic buttons, scroll reveals, smooth scroll. This is the portfolio surface — go all the way. |
| **The practice room** | The conversation view, composer, sidebar, panels | **Only motion tied to real state.** The fluid technique is *repurposed here as the voice orb*, not used as decoration. No cursor-reactive backgrounds, no scroll effects, no parallax. |
| **Data surfaces** | Dysfluency timeline, latency waterfall, pace chart | **Entrance animation only.** Nothing that moves after it has drawn. A chart that reacts to the cursor invites misreading. |

The payoff for you: the front-of-house surfaces are exactly what a portfolio reviewer opens
first, so the expressive work lands where it converts, and the practice room stays defensible
in the report's accessibility section. You lose nothing.

---

## 2. Effect A — Fluid displacement surface

*From reference 1. This is the most valuable technique in the set, because it has a second,
better use in this project than the one it came from.*

**Build it once**, as `frontend/src/components/FluidSurface.tsx` — a WebGL canvas with a
single fragment shader:

- Domain-warped simplex noise (2–3 octaves) sampled over UV, producing a slow-moving liquid
  field.
- Uniforms: `uTime`, `uPointer` (vec2, smoothed), `uPointerVelocity`, `uIntensity` (0..1),
  `uColorA` / `uColorB` from the Night Studio tokens (`--ground-2` → `--amber` at the hot end).
- The pointer displaces the field locally with a decaying radius, so the surface *settles*
  after the cursor leaves rather than snapping back.
- Plain WebGL or `ogl` (~10 KB). Do **not** pull in three.js for one full-screen quad.
- `IntersectionObserver` pauses the render loop when off-screen; `prefers-reduced-motion`
  renders exactly one static frame and stops.

**Use 1 — landing hero (front of house).** Full-bleed, cursor-driven, `uIntensity` high.
Display type sits over it. This is the reference effect in its original role.

**Use 2 — the voice orb (practice room). This is the real prize.** The same shader, clipped to
a circle, with `uPointer` replaced by nothing and `uIntensity` driven by **`micLevel`** from
`useCoachSession`, smoothed with fast attack and slow release. The surface goes still when the
room is quiet and turns liquid when the user speaks.

That is no longer a hover effect. It is a visualisation of the thing the whole project is
about — the audio signal that the transcript throws away — and it is the screenshot that will
lead your README. Build Use 2 even if you skip Use 1.

**Fallbacks:** no WebGL context, or reduced motion → a static CSS radial gradient with the SVG
grain overlay, and the orb encodes level by radius alone. The app must never depend on a shader
to be usable.

---

## 3. Effect B — Display type with imagery through the letterforms

*From reference 2. Front of house only.*

- Oversized **Bricolage Grotesque 800** (or **Instrument Serif** for one word), clamped
  `font-size: clamp(3rem, 12vw, 11rem)`, tracking `-0.035em`, line-height `0.95`.
- The fill is an image or the fluid surface, revealed through the glyphs via
  `background-clip: text` with `color: transparent` (SVG `<mask>` as the fallback for anything
  that needs it).
- On scroll, the fill translates at ~0.15× the page scroll rate, so the imagery drifts behind
  static letterforms. Parallax on the *fill*, never on the text itself — moving type is hard to
  read and is a reduced-motion violation.
- **Accessibility is non-optional here:** the real text stays in the DOM and selectable; a
  solid-colour fallback fill is applied whenever contrast against the backing image would drop
  below 4.5:1, and the whole treatment collapses to flat `--ink` under reduced motion and in
  forced-colors mode. Test it with the image failing to load.

Use it for the landing headline and one section opener. Twice on a page, maximum. The effect
works because it is rare on the page it appears on.

---

## 4. Effect C — Magnetic and morphing hover

*Front of house at full strength; a restrained version is allowed on the practice room's
primary buttons.*

- **Magnetic pull:** on pointer-move within ~80 px, translate the element up to 8 px toward the
  cursor with a spring (`stiffness 150, damping 15`), returning on leave. Front of house only —
  in the practice room a button that dodges the cursor is a usability defect.
- **Border-radius morph:** on hover, `--r-md` → `--r-lg` over `--t-base`; on press, `0.98`
  scale over `--t-instant`. This one is allowed everywhere.
- **Label roll:** the button's text rolls up and its duplicate rolls in from below
  (`overflow: hidden` on a two-line stack, `translateY(-100%)`). Cheap, and it is the single
  move that most reads as "designed".
- **Link underline morph:** a 1px `--amber` rule that wipes in from the left with
  `transform: scaleX()` and `transform-origin` swapping on leave, so it wipes *out* to the
  right. Never `text-decoration` transitions.

**Keyboard parity is a hard requirement.** Every state reachable by hover must be reachable by
`:focus-visible` with the same styling. An interface where hovering reveals something that
tabbing does not is broken, and in this project it is broken in the exact way the report claims
it is not.

---

## 5. Effect D — Cursor-tracked tilt with lens reveal

*From reference 3 — **confirm the shot before building this**; the spec below is inferred from
its title.*

- A card that rotates up to 6° on X and Y toward the cursor (`perspective: 1200px`,
  `transform-style: preserve-3d`), returning to flat with a spring on leave.
- A circular "lens" mask follows the cursor across the card, revealing a second layer beneath —
  in this project, the natural pairing is **the transcript on top and the acoustic view
  underneath**: move the lens across a sentence and see the blocks and prolongations the words
  hide.
- Depth is sold by a specular highlight (a soft radial white at 6% opacity) tracking the
  inverse of the tilt, not by a drop shadow.

If you build it, put it on the **landing page as the explainer for what the project does** —
"the transcript says one thing, the audio says another" is the thesis, and a lens that reveals
one under the other states it in one gesture with no copy at all. Do not put it inside the live
practice view.

Touch devices get a static split view. Reduced motion disables the tilt and pins the lens to
tap position.

---

## 6. Effect E — Smooth scroll and scroll-linked reveals

*Front of house only. No link was supplied for this one; specified as standard practice.*

- **Lenis** (~3 KB) for scroll smoothing, `lerp: 0.1`, `duration: 1.2`. Mount it on the landing
  route only, and **destroy it on route change** so the practice room scrolls natively.
- **Never hijack scroll.** No section snapping, no scroll-driven "chapters" that trap the
  wheel, no scroll-to-advance. Beyond the accessibility problems, these break anchor links and
  find-in-page.
- Reveals via `IntersectionObserver`, `once: true`, 24 px rise + fade over `--t-slow`,
  staggered 60 ms per child. Never re-trigger on scroll-back.
- A thin `--amber` scroll-progress rule at the top of the landing page, at 2 px.
- **Reduced motion kills all of it:** Lenis never initialises, reveals render in their final
  state on first paint, the progress rule stays but does not animate.
- Respect `scroll-behavior` and keep every anchor link working with the keyboard.

---

## 7. The landing page you don't have yet

Effects A, B, D and E need a surface, and the app currently has no public page. Build
`/` as a marketing route, with the app moving to `/app`:

1. **Hero** — fluid surface (A) + text-masked headline (B). One sentence on what the project
   is. Two buttons: "Try the demo", "Read the case study".
2. **The problem, in one gesture** — the lens card (D): a transcript reading
   `"I want water please"` with the acoustic truth underneath showing the 1.4 s block. No
   explanatory paragraph; the interaction is the argument.
3. **The two paths** — the architecture diagram, revealed on scroll, live ~200 ms vs cascade
   ~1.9 s with the real measured numbers.
4. **The measurements** — three stat tiles pulling real figures from `docs/BENCHMARKS.md`.
5. **Ethics** — short, plain, prominent: not a medical device, no diagnosis, audio never
   stored, everything runs locally.
6. **Footer** — GitHub, the case study, the demo video.

This page is what a portfolio reviewer actually sees. Write its copy through the `humanizer`
skill and keep it under 250 words total.

---

## 8. Front-to-back: make it one system, not two

*This is the "connect everything" package, and it is worth more to the project than any of the
effects above. It maps to rubric item **A14, end-to-end integration**.*

Right now the two halves agree by hand. `frontend/src/lib/types.ts` is a manual transcription
of `backend/app/schemas/chat.py`, and nothing catches it when they drift.

**Build:**

- **Generated types, one source of truth.** FastAPI already emits OpenAPI at `/openapi.json`.
  Add `make types` → `openapi-typescript` → `frontend/src/lib/api-types.gen.ts`, and have
  `types.ts` re-export from it instead of redeclaring. CI regenerates and fails on a diff, so
  a backend schema change that the frontend has not absorbed breaks the build rather than
  production.
- **A typed API client** built from the generated types — every path, method, and response
  checked at compile time. Keep the existing `ApiError` envelope handling; it is good.
- **The WebSocket protocol, written down and typed.** Both sockets currently exchange messages
  with no schema anywhere. Define a discriminated union of frame types
  (`audio`, `text_delta`, `acoustic`, `mode_switch`, `error`, `done`) in a single shared
  definition, generate the TS side from it, and validate inbound frames on both ends. Document
  it in `docs/PROTOCOL.md` with a sequence diagram (`artifact-diagramming` skill) showing a
  live turn, a knowledge turn, and the handoff between them.
- **One error contract.** The backend envelope `{"error": {code, message}}` should cover HTTP
  *and* WebSocket closes (code + reason), with a shared enum of codes. The frontend maps codes
  to user-facing copy in exactly one place, so no component invents its own wording.
- **A status-driven UI.** `/api/status` already reports what is available. Every degraded state
  in the UI should derive from it rather than from a caught exception — the app knows what is
  missing before the user hits it, so say so up front.
- **Contract tests.** A backend test asserting the OpenAPI schema matches the committed
  snapshot, and a frontend test asserting fixtures typecheck against the generated types.
  Together they make drift impossible to merge.
- **A `make dev`** that starts backend and frontend together with one command.

---

## 9. Hard constraints

1. **Reduced motion turns everything off** — shaders stop rendering, Lenis never mounts,
   reveals render final, tilt and magnetism are disabled. Not shortened: stopped.
2. **Keyboard parity** on every hover-revealed state.
3. **No custom cursor** that hides the system cursor. If you add a cursor follower, the real
   cursor stays visible and the follower is `pointer-events: none`.
4. **Performance:** 60 fps with the orb live; shaders capped at `devicePixelRatio ≤ 2`; the
   landing page under 250 KB JS gzipped including Lenis and the WebGL helper; the practice room
   must not load the landing page's effect bundle at all — route-split it.
5. **Nothing added to the practice room that moves while the user is speaking**, other than the
   orb and the level indicator.
6. **The data surfaces stay honest** — no effect may animate, distort, or draw the eye across a
   dysfluency timeline or a pace chart.
7. Everything works with WebGL unavailable, images failing, and JS-heavy effects disabled.

---

## 10. Order

1. §8 — front-to-back wiring. Do this first; it is the one package with rubric weight, and the
   effects are easier to build against generated types.
2. Effect A, Use 2 — the voice orb. The highest-value visual in the project.
3. §7 — the landing page shell, then Effect B in the hero.
4. Effect E — smooth scroll and reveals.
5. Effect C — magnetic and morph hover, front of house, then the restrained variant in-app.
6. Effect D — the lens card, once reference 3 is confirmed.
7. `design/REFERENCES.md` and a pass over the whole checklist in §9.

## 11. Acceptance

- [ ] Full keyboard walkthrough of the landing page and the app, no mouse
- [ ] `prefers-reduced-motion: reduce` — no rAF loop running (verify in DevTools), everything usable
- [ ] WebGL disabled in the browser — both the hero and the orb degrade, nothing blank
- [ ] The orb visibly tracks `micLevel`, and is still when the room is quiet
- [ ] Practice-room bundle contains no Lenis and no shader code
- [ ] `make types` produces no diff on a clean tree; changing a backend schema makes CI fail
- [ ] `docs/PROTOCOL.md` describes every WebSocket frame currently sent by either socket
- [ ] Existing tests still green: 35 backend, 30 frontend
- [ ] `design/REFERENCES.md` credits all three shots
