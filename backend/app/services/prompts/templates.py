"""Prompt engineering (A12).

The brief asks for suitable techniques applied deliberately. Four are used here,
and each is separable so the report can show what it contributes:

  system prompting    `SYSTEM_PROMPT` — persona, coaching stance, scope boundary
  few-shot            `exemplars.EXEMPLARS` — behaviours prose cannot pin down
  structured          `<acoustic_context>` and `<retrieved_context>` blocks
  context-aware       recent turns plus the acoustic profile of *this* utterance

Every prompt is versioned. `PROMPT_VERSION` is written onto each turn, so when
M9 compares base against fine-tuned it can prove both ran on the same prompt,
and a mid-project prompt change can never silently invalidate earlier results.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import settings
from app.schemas.acoustic import AcousticProfile
from app.schemas.chat import Citation, Role
from app.services.llm import Message
from app.services.prompts import exemplars

#: v4: moved the per-turn coaching directive out of the <acoustic_context>
#:     block and into the system message. Evaluation showed the model reciting
#:     the guidance back to the user — "give them room, don't fill the pause"
#:     said TO the person who was speaking.
#: v5: moved the retrieved reference material the same way, for the same
#:     reason: with it in the user turn the model read the excerpts and their
#:     "[1]" labels aloud as though presenting a document. Dropped the numbered
#:     markers, which invited citation-style output, and made explicit that the
#:     reply is spoken and the reader cannot see the material.
#:
#: The pattern behind both: anything placed in the user turn reads as content
#: to relay. Instructions and context belong in the system role.
PROMPT_VERSION = "a12-v5"

SYSTEM_PROMPT = """\
You are a speaking-practice coach. You help people who experience speech \
differences — stuttering, cluttering, or plain speaking anxiety — get more \
comfortable and more confident with spoken communication.

WHAT YOU ARE
You are a practice partner and a coach. You are not a clinician, you are not a \
speech-language pathologist, and you do not assess, diagnose, or treat anyone. \
If someone asks you to evaluate whether they have a speech disorder, say plainly \
that this is outside what you can do, point them toward a qualified \
professional, and offer the practice help you can actually give.

HOW YOU LISTEN
Some turns include an <acoustic_context> block describing how the speech \
sounded: silent blocks, repeated sounds or words, stretched sounds, filler \
words, speaking rate, pauses. This exists so you can adapt, not so you can \
report it back.

  - Respond to what the person MEANT. The delivery is how it arrived, not the topic.
  - Never mention a block, repetition, or filler unless they explicitly asked \
for feedback on their speech.
  - When the block was long or the rate was high, keep your reply SHORT. Someone \
who is working hard to speak does not want a paragraph back.
  - Never finish their sentence, guess their word, or fill their pause.

HOW YOU COACH
  - One suggestion at a time, concrete enough to try in the next sentence. \
"Let the first sentence be short" is useful; "try to relax" is not.
  - Notice effort and specific improvements, not performance. No scores, no \
ratings, no "that was better than last time" unless you can say exactly what changed.
  - When they ask for technique feedback, give it: behavioural, specific, kind.
  - Ask one question at a time, and prefer a concrete choice over an open prompt.

STYLE
Warm, direct, unhurried. Speak in plain language — your replies are read aloud, \
so avoid lists, headings, markdown, and parentheticals. Two to four sentences \
is usually right.

VOCABULARY
Never use: patient, therapy, treatment, diagnosis, symptom, severity, disorder, \
impairment, rehabilitation.
Use instead: speaker, practice, coaching, exercise, what I heard, speech difference."""


#: Compact variant of SYSTEM_PROMPT, for measuring what prompt length costs.
#:
#: Motivation is measured, not stylistic. On CPU, prefill runs at ~56 tok/s, so
#: every prompt token costs ~18 ms before the user hears anything. `a12-v5`
#: builds to ~976 tokens (531 system + 445 few-shot), which is ~17.6 s of
#: prefill on a cold turn — and that is exactly the 15.6 s p95 measured by
#: `backend/scripts/bench_latency.py` against the shipped 3B.
#:
#: What is preserved, deliberately: every scope-boundary rule (no diagnosis,
#: refer out, banned vocabulary) survives verbatim in substance. M9 measured
#: the fine-tune declining diagnosis requests only 20% of the time, so this
#: variant must not weaken that further — the compression comes out of style
#: guidance the fine-tune has already internalised (M9: 97% did not name the
#: dysfluency unprompted, 100% speakable), not out of safety rules.
SYSTEM_PROMPT_COMPACT = """\
You are a speaking-practice coach for people with speech differences — \
stuttering, cluttering, or speaking anxiety.

You are not a clinician and do not assess, diagnose, or treat anyone. If asked \
to judge whether someone has a speech disorder, say plainly it is outside what \
you can do, point them to a speech-language pathologist, and offer the practice \
help you can give.

Some turns include an <acoustic_context> block describing how the speech \
sounded. It exists so you can adapt, not report it back:
  - Answer what the person MEANT; delivery is not the topic.
  - Never mention a block, repetition, or filler unless they asked about their \
speech.
  - After a long block or a fast rate, keep the reply SHORT.
  - Never finish their sentence or fill their pause.

Coach with one concrete suggestion at a time, specific enough to try in the \
next sentence. No scores or ratings. Ask one question at a time, preferring a \
concrete choice over an open prompt.

Your replies are read aloud: plain spoken language, no lists, headings, or \
markdown. Two to four sentences.

Never use: patient, therapy, treatment, diagnosis, symptom, severity, disorder, \
impairment, rehabilitation. Use: speaker, practice, coaching, exercise, what I \
heard, speech difference."""


GROUNDED_INSTRUCTION = """\
REFERENCE MATERIAL FOR THIS TURN

Below is background from the coaching library. It is context for you, NOT a \
document to show anyone. The person you are talking to cannot see it.

  - Your reply is READ ALOUD. Never output source titles, quotation blocks, \
bracketed numbers, or anything that looks like a citation. The interface shows \
sources separately.
  - Answer in your own words, in plain spoken sentences, grounded in what this \
material says.
  - If it does not cover the question, say you do not have material on it \
rather than inventing an answer. Being wrong about technique is worse than \
being unhelpful.
  - Never instruct the person to read, look at, or try the passages themselves."""


#: Prefixes that mark a turn as wanting retrieved, factual content. Used by the
#: Live-to-Knowledge handoff (A4) and to decide whether to retrieve at all.
_KNOWLEDGE_CUES: tuple[str, ...] = (
    "what is", "what's", "what are", "what should", "what do i",
    "how do i", "how can i", "how should", "how does", "how long",
    "why do", "why does", "why is", "why am i",
    "can you explain", "explain", "tell me about", "teach me",
    "is it true", "does it help", "what helps", "any tips", "advice on",
    "technique", "exercise for", "strategy",
)


def wants_knowledge(text: str) -> bool:
    """Whether a turn is asking for grounded reference content.

    Deliberately conservative. A false positive costs the user ~800 ms of extra
    latency for a retrieval they did not need; a false negative just means the
    live coach answers conversationally, which is usually fine. So this only
    fires on reasonably explicit asks.
    """
    t = text.strip().lower()
    if not t:
        return False
    if any(t.startswith(cue) for cue in _KNOWLEDGE_CUES):
        return True
    return t.endswith("?") and any(cue in t for cue in _KNOWLEDGE_CUES)


@dataclass(slots=True)
class HistoryTurn:
    role: Role
    text: str


@dataclass(slots=True)
class PromptBundle:
    """A built prompt plus what went into it, for logging and evaluation."""

    messages: list[Message]
    version: str = PROMPT_VERSION
    used_acoustic: bool = False
    used_retrieval: bool = False
    used_few_shot: bool = False
    citations: list[Citation] = field(default_factory=list)

    def describe(self) -> dict[str, object]:
        return {
            "prompt_version": self.version,
            "acoustic": self.used_acoustic,
            "retrieval": self.used_retrieval,
            "few_shot": self.used_few_shot,
            "messages": len(self.messages),
        }


def trim_excerpt(text: str, limit: int) -> str:
    """Shorten a retrieved chunk for the prompt, cutting on a sentence boundary.

    Prompt processing dominates CPU inference, and retrieved chunks dominate the
    prompt, so this is the single biggest lever on cascade latency. Cutting at a
    sentence end rather than mid-word keeps the passage readable to the model;
    falling back to a word boundary handles chunks with no punctuation.
    """
    text = text.strip()
    if len(text) <= limit:
        return text

    window = text[:limit]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut > limit // 3:
        return window[: cut + 1]

    space = window.rfind(" ")
    return (window[:space] if space > 0 else window).rstrip() + "…"


def render_retrieved(citations: list[Citation]) -> str:
    """Structured retrieval block.

    Excerpts are numbered so the model can refer to them internally, and each
    carries its source so an ungrounded claim is visible in the logged prompt.
    Only a capped portion of each chunk reaches the model; the citation objects
    the UI renders keep the full text.
    """
    if not citations:
        return ""
    lines = ["<reference_material>"]
    for c in citations:
        # No "[1]" markers and no source titles. Numbered labels invite the
        # model to reproduce them as citations, and the reply is spoken aloud —
        # reading out "bracket one, The Art of Public Speaking" is nonsense to a
        # listener. The UI renders the real citations from the same objects.
        lines.append(trim_excerpt(c.excerpt, settings.max_excerpt_chars))
        lines.append("---")
    lines.append("</reference_material>")
    return "\n".join(lines).strip()


def build(
    *,
    user_text: str,
    acoustic: AcousticProfile | None = None,
    citations: list[Citation] | None = None,
    history: list[HistoryTurn] | None = None,
    few_shot: bool = True,
    compact: bool | None = None,
) -> PromptBundle:
    """Assemble the full prompt for one cascade turn.

    `compact` swaps in the shortened system prompt and the reduced exemplar set
    (`a12-v5-compact`). It exists to measure what prompt length costs: prefill
    dominates CPU latency, so this is the experiment, not a second product
    persona. Both variants keep every scope-boundary rule — see
    SYSTEM_PROMPT_COMPACT and EXEMPLARS_COMPACT for what was cut and why.
    """
    citations = citations or []
    history = history or []
    # None means "follow the setting", so every caller picks it up without
    # threading the flag through. An explicit True/False still wins, which is
    # what lets an evaluation run pin the variant regardless of environment.
    if compact is None:
        compact = settings.prompt_compact

    system = SYSTEM_PROMPT_COMPACT if compact else SYSTEM_PROMPT

    # Reference material goes in the SYSTEM message, not the user turn. Placed
    # beside the question it reads as a document to present, and the model
    # recited the excerpts and their "[1]" labels straight into a reply that
    # then gets spoken aloud. Same lesson as the coaching directive in v4.
    if citations and (reference := render_retrieved(citations)):
        system = f"{system}\n\n{GROUNDED_INSTRUCTION}\n\n{reference}"

    # The per-turn acoustic directive goes in the SYSTEM message, not alongside
    # the observations in the user turn. Phrased as a rule and placed here, the
    # model applies it; placed in the user turn it gets recited back.
    if acoustic is not None and (directive := acoustic.coaching_directive()):
        system = (
            f"{system}\n\nFOR THIS TURN ONLY — how to respond, never something "
            f"to say out loud:\n{directive}"
        )

    messages: list[Message] = [Message(role="system", content=system)]

    used_few_shot = False
    if few_shot:
        chosen = exemplars.EXEMPLARS_COMPACT if compact else exemplars.EXEMPLARS
        for m in exemplars.render(chosen):
            messages.append(Message(role=m["role"], content=m["content"]))
        used_few_shot = True

    # Context-aware: the recent thread, oldest first, trimmed to a budget.
    for turn in history[-settings.history_turns :]:
        messages.append(
            Message(
                role="assistant" if turn.role is Role.COACH else "user",
                content=turn.text,
            )
        )

    # The current turn carries its own structured blocks. Retrieval first, then
    # the acoustic reading, then the words — so the model sees the reference
    # material before the thing it must answer.
    parts: list[str] = []

    used_acoustic = False
    if acoustic is not None and (block := acoustic.to_prompt_block()):
        parts.append(block)
        used_acoustic = True

    parts.append(user_text.strip())
    messages.append(Message(role="user", content="\n\n".join(parts)))

    return PromptBundle(
        messages=messages,
        # The variant must be visible in the version, or the guarantee this
        # module exists to provide — that a comparison ran both arms on the
        # same prompt — silently stops holding.
        version=f"{PROMPT_VERSION}-compact" if compact else PROMPT_VERSION,
        used_acoustic=used_acoustic,
        used_retrieval=bool(citations),
        used_few_shot=used_few_shot,
        citations=citations,
    )


def build_session_title(first_message: str, limit: int = 60) -> str:
    """A short label for the history list. No model call — this runs per session."""
    text = " ".join(first_message.split())
    if len(text) <= limit:
        return text or "Practice session"
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"
