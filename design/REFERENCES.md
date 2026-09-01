# Design References

The interaction techniques in this interface were adapted from work by other
designers. What was taken and what was not is set out below, because the
distinction matters: interaction mechanics are shared craft, while a specific
composition belongs to the person who made it.

---

| Shot | Designer | Technique adapted | Composition reproduced |
|---|---|---|---|
| [Hover Morph Effect Hero Collection](https://dribbble.com/shots/26894245-Hover-Morph-Effect-Hero-Collection-for-WordPress) | Slider Revolution | Fluid displacement surface — domain-warped noise driven by a pointer, settling rather than snapping back | **No** |
| [Personal Portfolio Website](https://dribbble.com/shots/27165049-Personal-Portfolio-Website) | Piotrek Rak | Display type with imagery revealed through the letterforms; editorial small-caps annotations | **No** |
| [Autoperfect Goggles](https://dribbble.com/shots/15464706-Autoperfect-Goggles) | — | Cursor-tracked tilt with a lens-shaped reveal mask | **No** |

**Unconfirmed:** the third shot could not be loaded while writing the brief, and
its technique is inferred from the title. If the lens-reveal card ships, verify
the reference first.

---

## What was taken

Mechanics only: fluid displacement, text-masked imagery, magnetic hover,
cursor-tracked tilt, scroll-linked reveals. These are common techniques with
many independent implementations, and using them is ordinary craft.

## What was not

Layout, type choices, colour, and art direction are all original to this
project and come from [`UI_REBUILD_PROMPT.md`](UI_REBUILD_PROMPT.md) — the
"Night Studio" direction, its inverted-Furni palette, and its Bricolage
Grotesque / Instrument Sans / JetBrains Mono pairing. No reference's
composition was rebuilt.

## Why the fluid surface ended up somewhere else

In its source it is a hero background that warps under the cursor. Here it is
the **voice orb**: the same shader clipped to a circle, with the pointer
replaced by `micLevel`. It goes still when the room is quiet and turns liquid
when someone speaks.

That is not decoration reused. It is a visualisation of the audio signal this
entire project exists to preserve — the thing a transcript throws away — and
moving it there is the reason the technique earns its place at all.

The cursor-driven version stays on the landing page. It is explicitly barred
from the practice room: a background that reacts to the cursor, behind a live
microphone, used by someone concentrating on speaking through a block, is
hostile rather than bold. See the zoning rule in
[`MOTION_AND_INTERACTION_PROMPT.md`](MOTION_AND_INTERACTION_PROMPT.md).

## Other credits

- **Fonts** — Bricolage Grotesque, Instrument Sans, Instrument Serif and
  JetBrains Mono, all via Google Fonts under the SIL Open Font License.
- **Palette** — derived from the Furni template's four brand colours, inverted:
  its deep green becomes the ground, its amber becomes the only light source.
- **Dysfluency event colours** — derived from the
  [Okabe-Ito](https://jfly.uni-koeln.de/color/) colour-vision-deficiency-safe
  palette, then re-placed inside a lightness band and validated all-pairs. See
  `frontend/src/styles/tokens.css` for the measurements.
