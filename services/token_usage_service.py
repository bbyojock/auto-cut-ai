"""Best-effort token counting and cost estimation.

None of AutoCutAI's providers (see ``ai/*_provider.py``) return exact
token counts today, and pulling in a per-provider tokenizer library for
every supported model would be exactly the kind of hardcoding the project
rules forbid. Instead this service uses one transparent, documented
heuristic (~4 characters per token, the same rule of thumb
:class:`ai.prompt_builder.PromptBuilder` already used pre-Version-4) and
is honest everywhere about being an *estimate* -- every UI label and log
line that shows a token count says "estimated tokens", never just
"tokens".

Cost estimation is opt-in and table-driven (see :data:`_PRICING_USD_PER_1K`):
an unknown provider/model combination returns ``None`` rather than a
fabricated number, since guessing at cost would be worse than not showing
one at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Chars-per-token used for the estimate. Never hardcoded into a provider
# class -- this one heuristic constant is shared by every provider/model.
_CHARS_PER_TOKEN = 4.0

# (input_usd_per_1k_tokens, output_usd_per_1k_tokens), keyed by
# (provider_name.lower(), model_substring.lower()). Matching is by
# substring so e.g. "gemini-3.6-flash-8b" still matches "gemini-3.6-flash".
# Deliberately small and best-effort: an unmatched provider/model simply
# reports no cost estimate rather than an invented one. This table is data,
# not logic -- adding a new priced model is a one-line addition here, never
# a code change to a provider class.
_PRICING_USD_PER_1K = {
    ("gemini", "gemini-3.6-flash"): (0.0, 0.0),  # unpriced/preview at time of writing
    ("claude", "opus"): (0.015, 0.075),
    ("claude", "sonnet"): (0.003, 0.015),
    ("claude", "haiku"): (0.0008, 0.004),
    ("openai", "gpt-5"): (0.005, 0.015),
    ("openai", "gpt-4o"): (0.005, 0.015),
    ("openai", "gpt-4o-mini"): (0.00015, 0.0006),
    ("openrouter", "claude"): (0.003, 0.015),
    ("openrouter", "gpt"): (0.005, 0.015),
}


@dataclass(slots=True)
class TokenUsage:
    """Estimated token usage (and, if known, cost) for one AI request."""

    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Optional[float]

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class PromptReduction:
    """Before/after size comparison for prompt optimization (Feature 4)."""

    original_chars: int
    optimized_chars: int
    original_tokens: int
    optimized_tokens: int

    @property
    def reduction_percent(self) -> float:
        if self.original_chars <= 0:
            return 0.0
        return max(0.0, (self.original_chars - self.optimized_chars) / self.original_chars * 100.0)


class TokenUsageService:
    """Stateless helper: character/token estimation + best-effort cost lookup."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough ``~len(text) / 4`` estimate. Documented as an estimate everywhere it's shown."""
        if not text:
            return 0
        return max(1, int(len(text) / _CHARS_PER_TOKEN))

    @classmethod
    def estimate_usage(cls, provider: str, model: str, prompt_text: str, response_text: str) -> TokenUsage:
        input_tokens = cls.estimate_tokens(prompt_text)
        output_tokens = cls.estimate_tokens(response_text)
        cost = cls._estimate_cost(provider, model, input_tokens, output_tokens)
        return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost_usd=cost)

    @classmethod
    def _estimate_cost(cls, provider: str, model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
        provider_key = (provider or "").lower()
        model_key = (model or "").lower()
        for (price_provider, price_model_substring), (in_price, out_price) in _PRICING_USD_PER_1K.items():
            if price_provider in provider_key and price_model_substring in model_key:
                return (input_tokens / 1000.0) * in_price + (output_tokens / 1000.0) * out_price
        return None

    @classmethod
    def measure_reduction(cls, original_text: str, optimized_text: str) -> PromptReduction:
        """Compare an optimized prompt against what the un-optimized version would be."""
        return PromptReduction(
            original_chars=len(original_text),
            optimized_chars=len(optimized_text),
            original_tokens=cls.estimate_tokens(original_text),
            optimized_tokens=cls.estimate_tokens(optimized_text),
        )
