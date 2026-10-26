"""Extractor Claude API dengan structured output (Bagian 10 PRD). Hanya menerima payload P_id yang sudah di-mask."""

from __future__ import annotations

from typing import Any

import anthropic

from contract_extractor.extractors.base import ExtractionResult, ExtractorError, ExtractorUnavailable
from contract_extractor.extractors.prompt import OUTPUT_SCHEMA, SYSTEM_PROMPT, parse_output, user_message

DEFAULT_MODEL = "claude-sonnet-5-5"  # PRD Bagian 13 picks Sonnet; override with extractor.model
# Re-runs a request that a safety classifier declined on a fallback model the API picks, inside the same call.
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000  # non-streaming ceiling; thinking counts toward it, the JSON answer itself is small


class ClaudeExtractor:
    name = "claude-api"

    def __init__(self, client: Any, model: str = DEFAULT_MODEL, effort: str = "high", refusal_fallback: bool = True):
        self.client, self.model, self.effort, self.refusal_fallback = client, model, effort, refusal_fallback

    def check(self) -> None:
        """Verify credentials and the model id without sending any document text."""
        try:
            self.client.models.retrieve(self.model)
        except anthropic.AnthropicError as exc:
            raise ExtractorUnavailable(f"Claude API not usable with model {self.model}: {type(exc).__name__}") from exc

    def extract(self, masked_text: str) -> ExtractionResult:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_message(masked_text)}],
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        }
        if self.refusal_fallback:
            request.update(betas=[REFUSAL_FALLBACK_BETA], fallbacks="default")
        try:
            response = self.client.beta.messages.create(**request)
        except anthropic.AnthropicError as exc:
            raise ExtractorError(f"api:{type(exc).__name__}") from exc

        if response.stop_reason == "refusal":
            raise ExtractorError("refusal")
        if response.stop_reason == "max_tokens":
            raise ExtractorError("truncated")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if not text:
            raise ExtractorError("no_output")
        return parse_output(text)


def build_claude(model: str | None, effort: str, refusal_fallback: bool, timeout_s: int, max_retries: int) -> ClaudeExtractor:
    """Credentials come from the environment (ANTHROPIC_API_KEY or an `ant auth login` profile), never from config."""
    try:
        client = anthropic.Anthropic(timeout=float(timeout_s), max_retries=max_retries)
    except anthropic.AnthropicError as exc:
        raise ExtractorUnavailable(f"Claude API client cannot start: {type(exc).__name__}") from exc
    return ClaudeExtractor(client, model or DEFAULT_MODEL, effort, refusal_fallback)
