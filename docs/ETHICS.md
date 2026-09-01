# Ethics & Scope Statement

*Task S4. This is the authoritative scope boundary for the project and must be reflected
verbatim in the final report and in any user-facing copy.*

## What this project is

An **accessibility tool**. It helps people who experience speech differences — including
stuttering, cluttering, and speech anxiety — practise spoken communication in a private,
low-pressure setting, and gives them feedback on pacing and delivery.

The assignment brief permits accessibility as a project domain and explicitly excludes
medical ones. That exclusion is respected here not as a technicality but because the
distinction is real: speech-language pathology is a licensed clinical profession, and this
system is not qualified to practise it.

## What this project is not

- **Not a diagnostic tool.** It does not identify, label, or grade any speech disorder.
  The dysfluency analyzer detects *acoustic events* (blocks, repetitions, prolongations)
  in order to adapt the coach's conversational pacing. It does not infer a condition from
  them, and its outputs are never presented to the user as a clinical finding.
- **Not therapy.** It does not deliver a treatment programme, and it does not replace a
  speech-language pathologist.
- **Not a substitute for professional care.** The interface should direct users toward a
  qualified professional for anything beyond practice and confidence-building.

## Language rules

These apply across code, UI copy, prompts, the knowledge base, and the report:

| Avoid | Use instead |
|---|---|
| patient | user, speaker |
| therapy, treatment, session plan | practice, coaching, exercise |
| diagnosis, symptom, severity | detected event, acoustic marker |
| disorder, impairment | speech difference |
| rehabilitation | confidence building, skill practice |

## Data and consent

- **SEP-28k** is used for training the acoustic event classifier only. Its audio comes
  from publicly published podcasts and is **fetched, never redistributed** — the repo
  gitignores all audio.
- **User speech is not retained by default.** Conversation history stores the transcript
  and derived metrics; raw audio is discarded once processed unless the user explicitly
  opts in to keep a recording.
- **No user data leaves the machine.** Both the flagship and cascade paths run locally.

## Model limitations to disclose

Stating these in the product and the report is part of the ethical position, not a
weakness to hide:

- Moshi is **English-only**, so the coach currently serves English speakers only.
- The dysfluency classifier is trained on podcast speech, which **under-represents severe
  dysfluency** and skews toward adult speakers. Its accuracy on other populations is
  unmeasured.
- The coach can be wrong. Feedback is a practice aid, not an assessment.
