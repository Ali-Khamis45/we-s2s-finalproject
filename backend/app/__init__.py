"""Speech Confidence Coach backend.

Two conversational paths share one session (see docs/PROJECT_PLAN.md):

  Live Coach       native S2S via Moshi, GPU-resident, ~200 ms
  Grounded Knowledge   Whisper -> acoustic tags -> prompt -> RAG -> LLM -> TTS, CPU, ~1 s

Nothing in this package imports a model at module scope. Heavy dependencies are
resolved lazily inside the service that needs them, so the API boots and serves
health checks on a machine where no weights have been downloaded yet.
"""

__version__ = "0.1.0"
