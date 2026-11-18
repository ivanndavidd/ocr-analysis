"""Extractor Gemini lewat Vertex AI (Bagian 10 PRD). Google AI Studio dan API key tidak pernah dipakai:
free tier-nya tidak memenuhi NFR privasi, jadi client selalu dibuat dengan `vertexai=True` dan kredensial GCP."""

from __future__ import annotations

from typing import Any

import httpx
from google import genai
from google.auth import exceptions as google_auth_errors
from google.genai import errors as genai_errors
from google.genai import types

from contract_extractor.extractor_method.llm.base import (
    ExtractionResult,
    ExtractorError,
    ExtractorUnavailable,
    ImageAnswer,
    ImageTask,
)
from contract_extractor.extractor_method.llm.prompt import OUTPUT_SCHEMA, SYSTEM_PROMPT, parse_output, user_message

_GOOGLE_ERRORS = (genai_errors.APIError, google_auth_errors.GoogleAuthError, httpx.HTTPError)


class GeminiVertexExtractor:
    name = "gemini-vertex"

    def __init__(self, client: Any, model: str):
        self.client, self.model = client, model

    def check(self) -> None:
        """Verify credentials, project, and the model id without sending any document text."""
        try:
            self.client.models.get(model=self.model)
        except Exception as exc:  # google-auth raises its own hierarchy; only the type is reported
            raise ExtractorUnavailable(f"Vertex AI not usable with model {self.model}: {type(exc).__name__}") from exc

    def extract(self, masked_text: str) -> ExtractionResult:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_json_schema=OUTPUT_SCHEMA,
            temperature=0.0,
        )
        try:
            response = self.client.models.generate_content(
                model=self.model, contents=user_message(masked_text), config=config
            )
        except _GOOGLE_ERRORS as exc:
            raise ExtractorError(f"api:{type(exc).__name__}") from exc

        text = response.text  # None when the answer was blocked or empty
        if not text:
            raise ExtractorError("no_output")
        return parse_output(text)

    def read_image(self, task: ImageTask, png: bytes) -> ImageAnswer:
        """Crops are read by Claude only; the pipeline then keeps the local reading."""
        raise ExtractorError("image_unsupported")

    def read_page(self, jpegs: list[bytes]) -> ExtractionResult:
        """The 'claude_page' mode is Claude only; the pipeline then reads the masked text instead."""
        raise ExtractorError("image_unsupported")


def build_gemini_vertex(
    model: str | None, project: str | None, location: str | None, timeout_s: int, max_retries: int
) -> GeminiVertexExtractor:
    if not model:
        raise ExtractorUnavailable("extractor.model is required for gemini-vertex (a Vertex AI Gemini model id)")
    if not project or not location:
        raise ExtractorUnavailable(
            "Vertex AI needs a project and location: set extractor.vertex_project/vertex_location "
            "or GOOGLE_CLOUD_PROJECT/GOOGLE_CLOUD_LOCATION"
        )
    http_options = types.HttpOptions(
        timeout=timeout_s * 1000, retry_options=types.HttpRetryOptions(attempts=max_retries + 1)
    )
    try:
        client = genai.Client(vertexai=True, project=project, location=location, http_options=http_options)
    except Exception as exc:  # missing ADC raises google.auth errors; only the type is reported
        raise ExtractorUnavailable(f"Vertex AI client cannot start: {type(exc).__name__}") from exc
    return GeminiVertexExtractor(client, model)
