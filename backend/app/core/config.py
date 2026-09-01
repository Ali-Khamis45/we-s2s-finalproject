"""Application settings, loaded from the environment or a local .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SCC_",
        extra="ignore",
    )

    # ---- app ----
    app_name: str = "Speech Confidence Coach"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = False

    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # ---- storage ----
    data_dir: Path = REPO_ROOT / "data"
    database_url: str = ""
    chroma_dir: Path = REPO_ROOT / "data" / "chroma"
    corpus_dir: Path = REPO_ROOT / "data" / "corpus"

    # ---- Live Coach (A3, M2) ----
    # The Moshi service is owned by Track M and runs as a separate process.
    # When it is unreachable the orchestrator degrades to cascade-only (A14).
    moshi_url: str = "ws://127.0.0.1:8998/api/chat"
    moshi_enabled: bool = True
    moshi_connect_timeout_s: float = 3.0
    moshi_sample_rate: int = 24_000

    # ---- STT (A7) ----
    # CHOSEN BY MEASUREMENT, not by defaulting to the middle option. What
    # matters here is not transcript quality but WORD-TIMESTAMP accuracy: the
    # acoustic branch derives block duration and pause length from it.
    # Benchmarked on a 3.6 s utterance with a 1400 ms block spliced in
    # (backend/scripts/bench_whisper.py, CPU int8):
    #
    #   tiny    285 ms   MISSED the block entirely
    #   base    598 ms   block measured 1440 ms  (40 ms error)
    #   small  2025 ms   block measured 1460 ms  (60 ms error)
    #
    # `base` is 3.4x faster than `small` and marginally MORE accurate on the
    # measurement that matters, so it is the default.
    #
    # Do not drop to `tiny`. It transcribed "I-I-I want ... water" as
    # "I want water please" — normalising away both the repetitions and the
    # 1.4 s pause. A model that fluent-izes disfluent speech destroys the exact
    # signal this project exists to preserve.
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str | None = "en"
    stt_sample_rate: int = 16_000

    # ---- TTS (A8) ----
    tts_voice: str = "af_heart"
    tts_sample_rate: int = 24_000
    # Pacing bounds for the coach's own voice. A detected block slows delivery
    # toward tts_speed_min; fluent, confident speech lets it return to normal.
    tts_speed_default: float = 1.0
    tts_speed_min: float = 0.75
    tts_speed_max: float = 1.15

    # ---- Dysfluency analyzer (Track M / M4) ----
    dysfluency_model_path: Path | None = None
    dysfluency_threshold: float = 0.5

    # ---- RAG (A9, A11) ----
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    collection_name: str = "coaching_corpus"
    chunk_size: int = 512
    chunk_overlap: int = 64
    retrieval_k: int = 4
    retrieval_fetch_k: int = 20
    retrieval_lambda: float = 0.5
    # Below this best-match score a question is treated as outside the corpus,
    # and the coach says it has no material rather than inventing an answer.
    #
    # CALIBRATED, NOT GUESSED, and re-derived against the real corpus.
    # bge embeddings have a high similarity floor — unrelated text does not
    # score near zero. Measured over 1057 chunks with bge-small-en-v1.5
    # (backend/scripts/calibrate_gate.py), 10 in-corpus and 8 out-of-corpus
    # questions:
    #
    #   in corpus       min 0.696   median 0.720   max 0.814
    #   out of corpus   min 0.434   median 0.532   max 0.619
    #
    # An earlier 0.55, tuned on a 3-document fixture, would have ANSWERED 3 of
    # the 8 out-of-corpus questions once the real corpus was indexed: a larger
    # corpus offers more chances for a spurious high match ("how do I train for
    # a marathon" reaches 0.619). Corpus size moves this number — always
    # re-derive, never carry it over.
    #
    # 0.65 sits in the 0.077 gap, deliberately nearer the out-of-corpus side.
    # The errors are not symmetric: refusing a real question costs one unhelpful
    # turn, while accepting a false one puts confident wrong technique advice in
    # front of someone practising their speech.
    retrieval_min_score: float = 0.65

    # ---- LLM (cascade, CPU via llama.cpp) ----
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_model: str = "qwen2.5-3b-instruct-q4_k_m"
    llm_api_key: str = "not-needed"
    llm_max_tokens: int = 420
    llm_temperature: float = 0.6
    llm_timeout_s: float = 120.0
    # Track M swaps this to "finetuned" when M8 lands. Keeping both addressable
    # is what makes the base-vs-fine-tuned comparison (M9) runnable in-app.
    llm_variant: str = "base"

    # ---- conversation (A13) ----
    history_turns: int = 8
    max_transcript_chars: int = 4_000

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    def model_post_init(self, __context: object) -> None:
        if not self.database_url:
            db_path = (self.data_dir / "coach.db").as_posix()
            object.__setattr__(self, "database_url", f"sqlite+aiosqlite:///{db_path}")

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.chroma_dir, self.corpus_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
