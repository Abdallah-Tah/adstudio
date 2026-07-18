"""Thin OpenAI client: one structured-output call, cost metered.

Every stage call goes through structured_call() — structured outputs only,
never free-text parsing (hard rule). Pricing verified against
https://developers.openai.com/api/docs/pricing on 2026-07-14.
"""
import math
import os
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from app.pricing import TOKEN_PRICES as PRICES

# Pinned model snapshot for stages 1-4 (vision + structured outputs).
STAGE_MODEL = "gpt-5.4-mini-2026-03-17"

T = TypeVar("T", bound=BaseModel)

_client: OpenAI | None = None

# Hard network timeout (seconds) + no SDK-level retries: a hung provider call
# fails fast with a clear error instead of stalling a worker forever. Our own
# attempt cap + watchdog handle retries/timeouts.
REQUEST_TIMEOUT = 90.0


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            timeout=REQUEST_TIMEOUT,
            max_retries=0,
        )
    return _client


def cost_cents(model: str, prompt_tokens: int, completion_tokens: int) -> int:
    """Token cost in cents, rounded up (conservative — never undercount)."""
    inp, out = PRICES[model]
    dollars = prompt_tokens * inp / 1_000_000 + completion_tokens * out / 1_000_000
    return math.ceil(dollars * 100)


def structured_call(
    response_model: type[T],
    messages: list[dict],
    model: str = STAGE_MODEL,
) -> tuple[T, int]:
    """One LLM call with structured output. Returns (parsed model, cost in cents)."""
    completion = client().chat.completions.parse(
        model=model,
        messages=messages,
        response_format=response_model,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError(
            f"structured call returned no parsed {response_model.__name__} "
            f"(refusal: {completion.choices[0].message.refusal})"
        )
    usage = completion.usage
    return parsed, cost_cents(model, usage.prompt_tokens, usage.completion_tokens)
