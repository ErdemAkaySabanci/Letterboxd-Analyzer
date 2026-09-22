"""
Taste profile
=============
Writes the short portrait that sits under the result score, from the facts in
analyzer.taste_facts(). This is the one analysis step that runs on somebody
else's model rather than in pandas, so it is kept behind an environment
variable: with no GEMINI_API_KEY set the feature does not exist, the server
reports it as disabled and the page shows nothing.

Gemini is called over plain REST with `requests` — already a dependency, and
a one-request feature does not earn an SDK on a 512 MB box. The free tier is
the whole budget: ~1,500 requests a day on the Flash models, at zero cost, so
the server caches every profile with its session and never asks twice.
"""

import json
import os
import re

import requests

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
ENABLED = bool(API_KEY)

ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# Anything shorter than this is a refusal, an apology or a truncated reply,
# none of which belongs on the page.
MIN_WORDS = 40

SYSTEM = """You write a short portrait of someone's film taste for a page called Close-Up, from a JSON summary of their Letterboxd library.

Rules:
- Only use the facts given. Never name a film, director, actor, genre, country or number that is not in the JSON. If a field is null or empty, say nothing about it.
- 120 to 170 words, two short paragraphs, plain prose. No title, no bullet points, no emoji, no markdown.
- Address the reader as "you". Never mention Close-Up, Letterboxd, JSON, data, statistics or that you are an AI.
- Name at most two directors, one actor and two genres. Write every name exactly as it is spelled in the JSON.
- Ratings are out of 5. The crowd comparison uses the same films, so a gap there is a real disagreement.
- Be specific and a little wry, never gushing. Lead with what is distinctive, not with totals. A contradiction (a genre watched a lot but rated low, a director watched most but not rated highest) is more interesting than a superlative.
- The second paragraph should end on one sharp observation, not a summary."""

_MARKUP = re.compile(r"[*_#`>]+")


class ProfileUnavailable(Exception):
    """The model could not be reached, or refused. The caller hides the block."""


def _clean(text: str) -> str | None:
    text = _MARKUP.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text.split()) < MIN_WORDS:
        return None
    return text


def write_profile(facts: dict) -> str:
    """One paragraph pair for one library. Raises ProfileUnavailable on any failure."""
    if not ENABLED:
        raise ProfileUnavailable("no API key")

    config = {"temperature": 0.8, "maxOutputTokens": 600}
    # The 2.5 generation spends "thinking" tokens out of maxOutputTokens
    # before writing a word; left on, a 600-token cap can come back empty.
    if "2.5" in MODEL:
        config["thinkingConfig"] = {"thinkingBudget": 0}

    body = {
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(facts, ensure_ascii=False)}]}],
        "generationConfig": config,
    }
    try:
        res = requests.post(
            ENDPOINT,
            headers={"x-goog-api-key": API_KEY, "Content-Type": "application/json"},
            json=body,
            timeout=25,
        )
    except requests.RequestException as e:
        raise ProfileUnavailable(f"request failed: {e}") from e

    if res.status_code != 200:
        # 429 is the free tier's per-minute or per-day quota; anything else
        # is worth a line in the log because it means the key or model is wrong.
        detail = res.text[:300].replace("\n", " ")
        print(f"[taste] Gemini {res.status_code}: {detail}")
        raise ProfileUnavailable(f"HTTP {res.status_code}")

    try:
        candidate = res.json()["candidates"][0]
        text = "".join(part.get("text", "") for part in candidate["content"]["parts"])
    except (ValueError, KeyError, IndexError, TypeError) as e:
        print(f"[taste] unexpected response shape: {res.text[:300]}")
        raise ProfileUnavailable("bad response") from e

    cleaned = _clean(text)
    if cleaned is None:
        print(f"[taste] reply too short or empty (finish={candidate.get('finishReason')})")
        raise ProfileUnavailable("empty reply")
    return cleaned
