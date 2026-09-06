"""Generate synthetic coaching instruction/response pairs for M6, scoped down
for a same-day presentation deadline: a few hundred pairs with a fast sanity
pass, not the full spec's 2-5k human-curated set.

Grounds generation in the A10 corpus (data/corpus/*.txt, public-domain
public-speaking texts) by feeding random passages as context to a locally
running Ollama model, prompted to write a short user question about speaking
confidence/fluency and a coaching-style answer grounded in that passage.

Requires Ollama running locally (`ollama serve`, default http://localhost:11434)
with a model already pulled (default: qwen3.5:4b).

Usage:
    python generate_coaching_pairs.py --count 400
    python generate_coaching_pairs.py --count 50 --dry-run   # smoke test
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "data" / "corpus"
DEFAULT_OUT_PATH = REPO_ROOT / "data" / "finetuning" / "coaching_pairs.jsonl"
OLLAMA_URL = "http://localhost:11434/api/generate"
PASSAGE_WORDS = 200

SYSTEM_PROMPT = """You are generating training data for a speaking-confidence coach \
aimed at people who stutter or otherwise have speech differences. You are NOT a \
clinician and must never use diagnostic or treatment language -- this is a \
practice/confidence tool, not therapy.

Given a passage from a public-domain public-speaking book, write ONE realistic user \
question about speaking confidence, pacing, pausing, or delivery, and ONE short \
coaching-style answer (2-4 sentences) that draws on the ideas in the passage but is \
written in your own words, in a warm, encouraging, practical tone. Never mention \
stuttering as something to "cure" or "fix" -- frame everything around building \
confidence and technique.

Respond with ONLY valid JSON, no other text, in exactly this shape:
{"instruction": "<user question>", "response": "<coach answer>"}
"""


def load_passages(corpus_dir: Path, passage_words: int = PASSAGE_WORDS) -> list[str]:
    """Split every corpus text file into non-overlapping ~passage_words chunks."""
    passages = []
    for path in sorted(corpus_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        words = text.split()
        for i in range(0, len(words) - passage_words, passage_words):
            chunk = " ".join(words[i : i + passage_words])
            # Skip chunks that are mostly boilerplate/table-of-contents noise.
            if len(re.findall(r"[.!?]", chunk)) >= 3:
                passages.append(chunk)
    return passages


def generate_pair(passage: str, model: str, client: httpx.Client) -> dict | None:
    prompt = f"{SYSTEM_PROMPT}\n\nPassage:\n{passage}\n\nJSON:"
    try:
        resp = client.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
            timeout=60.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"  WARNING: request failed: {e}", file=sys.stderr)
        return None

    raw = resp.json().get("response", "")
    try:
        pair = json.loads(raw)
    except json.JSONDecodeError:
        print(f"  WARNING: model did not return valid JSON, skipping: {raw[:100]!r}", file=sys.stderr)
        return None

    if not isinstance(pair, dict) or "instruction" not in pair or "response" not in pair:
        print(f"  WARNING: missing expected keys, skipping: {pair!r}", file=sys.stderr)
        return None
    if not pair["instruction"].strip() or not pair["response"].strip():
        return None
    return {"instruction": pair["instruction"].strip(), "response": pair["response"].strip()}


def fast_sanity_check(pair: dict) -> bool:
    """Cheap heuristic filter in place of full human curation (scope-down for
    the same-day deadline): reject obviously broken or clinical-sounding output."""
    text = (pair["instruction"] + " " + pair["response"]).lower()
    banned_terms = ["diagnos", "treatment", "cure", "disorder", "therapy", "patholog"]
    if any(term in text for term in banned_terms):
        return False
    if len(pair["response"]) < 20 or len(pair["response"]) > 1000:
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=400)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true", help="generate but don't write output")
    args = parser.parse_args()

    passages = load_passages(CORPUS_DIR)
    print(f"Loaded {len(passages)} candidate passages from {CORPUS_DIR}")
    if not passages:
        print("ERROR: no passages found -- run backend/scripts/fetch_corpus.py first", file=sys.stderr)
        sys.exit(1)

    rng = random.Random(args.seed)
    rng.shuffle(passages)

    accepted: list[dict] = []
    rejected = 0
    client = httpx.Client()
    start = time.time()

    for i, passage in enumerate(passages):
        if len(accepted) >= args.count:
            break
        pair = generate_pair(passage, args.model, client)
        if pair is None:
            rejected += 1
            continue
        if not fast_sanity_check(pair):
            rejected += 1
            continue
        accepted.append(pair)
        if len(accepted) % 25 == 0:
            elapsed = time.time() - start
            print(f"  {len(accepted)}/{args.count} accepted ({rejected} rejected), {elapsed:.0f}s elapsed")

    print(f"Done: {len(accepted)} accepted, {rejected} rejected, {time.time() - start:.0f}s total")

    if args.dry_run:
        print("Dry run -- not writing output. Sample pairs:")
        for pair in accepted[:3]:
            print(f"  Q: {pair['instruction']}")
            print(f"  A: {pair['response']}\n")
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for pair in accepted:
            f.write(json.dumps(pair) + "\n")
    print(f"Wrote {len(accepted)} pairs to {args.out}")


if __name__ == "__main__":
    main()
