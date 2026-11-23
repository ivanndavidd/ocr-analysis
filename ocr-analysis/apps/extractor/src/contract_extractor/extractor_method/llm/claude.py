"""Extractor Claude API dengan structured output (Bagian 10 PRD). Hanya menerima payload P_id yang sudah di-mask."""

from __future__ import annotations

import base64
import logging
from typing import Any

import anthropic

from contract_extractor.common.models import TokenUsage
from contract_extractor.extractor_method.llm.base import (
    ExtractionResult,
    ExtractorError,
    ExtractorUnavailable,
    ImageAnswer,
    ImageTask,
)
from contract_extractor.extractor_method.llm.prompt import (
    IMAGE_PROMPTS,
    IMAGE_REQUEST,
    IMAGE_SCHEMAS,
    OUTPUT_SCHEMA,
    PAGE_PROMPT,
    PAGE_SCHEMA,
    SYSTEM_PROMPT,
    page_content,
    parse_image_output,
    parse_page_output,
    parse_output,
    user_message,
)

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-5-5"  # owner decision 2026-11-02; override with extractor.model
DEFAULT_EFFORT = "max"
# Re-runs a request that a safety classifier declined on a fallback model the API picks, inside the same call.
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Streamed, so a long think at max effort cannot hit an HTTP timeout; thinking counts toward it, the answer is small.
MAX_TOKENS = 64000
# A crop asks one small question (16 digits, or signed or not), so it runs at a low effort with a small budget.
IMAGE_EFFORT = "low"
IMAGE_MAX_TOKENS = 8000


def api_usage(usage: Any) -> TokenUsage:
    """The Messages API's usage block as counts; a field the response leaves out counts as 0."""
    return TokenUsage(
        masuk=getattr(usage, "input_tokens", 0) or 0,
        cache_tulis=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        cache_baca=getattr(usage, "cache_read_input_tokens", 0) or 0,
        keluar=getattr(usage, "output_tokens", 0) or 0,
    )


def has_refusal_fallback(model: str) -> bool:
    """Haiku has no server-side fallback: the parameter does nothing there, or is refused, so it is not sent."""
    return not model.startswith("claude-haiku")


class ClaudeExtractor:
    name = "claude-api"

    def __init__(
        self, client: Any, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT, refusal_fallback: bool = True
    ):
        self.client, self.model, self.effort, self.refusal_fallback = client, model, effort, refusal_fallback

    def check(self) -> None:
        """Verify credentials and the model id without sending any document text."""
        try:
            self.client.models.retrieve(self.model)
        except anthropic.AnthropicError as exc:
            raise ExtractorUnavailable(f"Claude API not usable with model {self.model}: {type(exc).__name__}") from exc

    def request(self, masked_text: str) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            # The prompt is the same for every file, so after the first file it is read from the cache.
            "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user_message(masked_text)}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        }
        if self.refusal_fallback and has_refusal_fallback(self.model):
            request.update(betas=[REFUSAL_FALLBACK_BETA], fallbacks="default")
        return request

    def image_request(self, task: ImageTask, png: bytes) -> dict[str, Any]:
        image = {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode("ascii")}
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": IMAGE_MAX_TOKENS,
            "system": [{"type": "text", "text": IMAGE_PROMPTS[task]}],
            "messages": [
                {"role": "user", "content": [{"type": "image", "source": image}, {"type": "text", "text": IMAGE_REQUEST}]}
            ],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": IMAGE_EFFORT, "format": {"type": "json_schema", "schema": IMAGE_SCHEMAS[task]}},
        }
        if self.refusal_fallback and has_refusal_fallback(self.model):
            request.update(betas=[REFUSAL_FALLBACK_BETA], fallbacks="default")
        return request

    def page_request(self, jpegs: list[bytes]) -> dict[str, Any]:
        """The 'claude_page' mode: the identity page(s) with the NIK blacked out, at the text call's effort, since it
        reads the same fields."""
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": [{"type": "text", "text": PAGE_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": page_content([jpeg_block(jpeg) for jpeg in jpegs])}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": PAGE_SCHEMA}},
        }
        if self.refusal_fallback and has_refusal_fallback(self.model):
            request.update(betas=[REFUSAL_FALLBACK_BETA], fallbacks="default")
        return request

    def _send(self, request: dict[str, Any], what: str) -> tuple[str, TokenUsage]:
        """One streamed call: the answer's text and the tokens it used. A reply that came back still reports its tokens
        when it fails."""
        try:
            with self.client.beta.messages.stream(**request) as stream:
                response = stream.get_final_message()
        except anthropic.AnthropicError as exc:
            raise ExtractorError(f"api:{type(exc).__name__}") from exc

        usage = api_usage(response.usage)
        log.info(
            "claude %s %s: %d input, %d cache write, %d cache read, %d output tokens",
            self.model, what, usage.masuk, usage.cache_tulis, usage.cache_baca, usage.keluar,
        )  # fmt: skip
        if response.stop_reason == "refusal":
            raise ExtractorError("refusal", usage)
        if response.stop_reason == "max_tokens":
            raise ExtractorError("truncated", usage)
        text = next((block.text for block in response.content if block.type == "text"), None)
        if not text:
            raise ExtractorError("no_output", usage)
        return text, usage

    def extract(self, masked_text: str) -> ExtractionResult:
        text, usage = self._send(self.request(masked_text), "teks")
        try:
            answer = parse_output(text)
        except ExtractorError as exc:
            raise ExtractorError(exc.kind, usage) from None
        answer.usage = usage
        return answer

    def read_image(self, task: ImageTask, png: bytes) -> ImageAnswer:
        text, usage = self._send(self.image_request(task, png), task.value)
        try:
            answer = parse_image_output(task, text)
        except ExtractorError as exc:
            raise ExtractorError(exc.kind, usage) from None
        answer.usage = usage
        return answer

    def read_page(self, jpegs: list[bytes]) -> ExtractionResult:
        text, usage = self._send(self.page_request(jpegs), "halaman_identitas")
        try:
            answer = parse_page_output(text)
        except ExtractorError as exc:
            raise ExtractorError(exc.kind, usage) from None
        answer.usage = usage
        return answer


def jpeg_block(jpeg: bytes) -> dict[str, Any]:
    """A JPEG page image as a Messages API image block."""
    data = base64.b64encode(jpeg).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def build_claude(model: str | None, effort: str, refusal_fallback: bool, timeout_s: int, max_retries: int) -> ClaudeExtractor:
    """The API key comes from ANTHROPIC_API_KEY in the environment, never from config."""
    try:
        client = anthropic.Anthropic(timeout=float(timeout_s), max_retries=max_retries)
    except anthropic.AnthropicError as exc:
        raise ExtractorUnavailable(f"Claude API client cannot start: {type(exc).__name__}") from exc
    return ClaudeExtractor(client, model or DEFAULT_MODEL, effort, refusal_fallback)
