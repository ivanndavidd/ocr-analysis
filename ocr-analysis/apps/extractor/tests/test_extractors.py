"""Extractor clients with fake SDK clients. No test here touches the network or a real API key."""

import json
from types import SimpleNamespace

import anthropic
import pytest
from google.genai import errors as genai_errors
from google.genai import types

from contract_extractor.config import ExtractorProvider, ExtractorSettings, Settings
from contract_extractor.extractors import claude as claude_module
from contract_extractor.extractors import gemini as gemini_module
from contract_extractor.extractors.base import ExtractorError, ExtractorUnavailable
from contract_extractor.extractors.claude import DEFAULT_MODEL, REFUSAL_FALLBACK_BETA, ClaudeExtractor
from contract_extractor.extractors.factory import build_extractor
from contract_extractor.extractors.gemini import GeminiVertexExtractor, build_gemini_vertex
from contract_extractor.extractors.prompt import OUTPUT_SCHEMA, SYSTEM_PROMPT

PAYLOAD = "Nama : Budi Santoso\nNIK : [REDACTED-ID]\nberlaku mulai 1 Januari 2026 s/d 31 Desember 2026"
ANSWER = {
    "nama_karyawan": "Budi Santoso",
    "tanggal_mulai": "2026-01-01",
    "tanggal_selesai": "2026-12-31",
    "durasi_bulan": None,
    "bukti": {"nama": "Nama : Budi Santoso", "periode": "mulai 1 Januari 2026 s/d 31 Desember 2026"},
    "confidence": 0.93,
}


class FakeAnthropic:
    def __init__(self, text=json.dumps(ANSWER), stop_reason="end_turn", error=None, retrieve_error=None):
        self.requests: list[dict] = []
        self._text, self._stop, self._error, self._retrieve_error = text, stop_reason, error, retrieve_error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(retrieve=self._retrieve)

    def _create(self, **request):
        self.requests.append(request)
        if self._error:
            raise self._error
        content = [SimpleNamespace(type="thinking", thinking="")]
        if self._text is not None:
            content.append(SimpleNamespace(type="text", text=self._text))
        return SimpleNamespace(stop_reason=self._stop, content=content)

    def _retrieve(self, model):
        if self._retrieve_error:
            raise self._retrieve_error
        return SimpleNamespace(id=model)


class FakeGenai:
    def __init__(self, text=json.dumps(ANSWER), error=None):
        self.requests: list[dict] = []
        self._text, self._error = text, error
        self.models = SimpleNamespace(generate_content=self._generate, get=lambda model: SimpleNamespace(name=model))

    def _generate(self, **request):
        self.requests.append(request)
        if self._error:
            raise self._error
        return SimpleNamespace(text=self._text)


def test_schema_is_already_in_the_subset_the_claude_api_accepts():
    assert anthropic.transform_schema(OUTPUT_SCHEMA) == OUTPUT_SCHEMA


def test_claude_request_carries_only_the_masked_payload():
    client = FakeAnthropic()
    result = ClaudeExtractor(client).extract(PAYLOAD)

    assert result.nama_karyawan == "Budi Santoso" and result.confidence == 0.93
    [request] = client.requests
    assert request["model"] == DEFAULT_MODEL == "claude-sonnet-5-5"
    assert request["system"] == SYSTEM_PROMPT
    assert request["messages"] == [{"role": "user", "content": f"<kontrak>\n{PAYLOAD}\n</kontrak>"}]
    assert request["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
    assert (request["betas"], request["fallbacks"]) == ([REFUSAL_FALLBACK_BETA], "default")
    assert not {"temperature", "top_p", "top_k", "thinking"} & request.keys()


def test_claude_refusal_fallback_can_be_switched_off():
    client = FakeAnthropic()
    ClaudeExtractor(client, refusal_fallback=False).extract(PAYLOAD)
    assert not {"betas", "fallbacks"} & client.requests[0].keys()


@pytest.mark.parametrize(
    ("client", "kind"),
    [
        (FakeAnthropic(stop_reason="refusal"), "refusal"),
        (FakeAnthropic(stop_reason="max_tokens"), "truncated"),
        (FakeAnthropic(text=None), "no_output"),
        (FakeAnthropic(text="not json"), "invalid_output"),
        (FakeAnthropic(text=json.dumps({**ANSWER, "durasi_bulan": 500})), "invalid_output"),
        (FakeAnthropic(error=anthropic.AnthropicError("boom")), "api:AnthropicError"),
    ],
)
def test_claude_failures_become_extractor_errors(client, kind):
    with pytest.raises(ExtractorError) as caught:
        ClaudeExtractor(client).extract(PAYLOAD)
    assert caught.value.kind == kind


def test_invalid_output_error_never_echoes_document_text():
    leaky = json.dumps({**ANSWER, "nama_karyawan": "x" * 500})
    with pytest.raises(ExtractorError) as caught:
        ClaudeExtractor(FakeAnthropic(text=leaky)).extract(PAYLOAD)
    assert str(caught.value) == "invalid_output" and caught.value.__cause__ is None


def test_claude_check_reports_unusable_credentials():
    client = FakeAnthropic(retrieve_error=anthropic.AnthropicError("no key"))
    with pytest.raises(ExtractorUnavailable, match="AnthropicError"):
        ClaudeExtractor(client).check()


def test_gemini_request_uses_the_json_schema_and_no_api_key_path():
    client = FakeGenai()
    result = GeminiVertexExtractor(client, "gemini-test-model").extract(PAYLOAD)

    assert result.tanggal_selesai.isoformat() == "2026-12-31"
    [request] = client.requests
    assert request["model"] == "gemini-test-model"
    assert request["contents"] == f"<kontrak>\n{PAYLOAD}\n</kontrak>"
    config: types.GenerateContentConfig = request["config"]
    assert config.system_instruction == SYSTEM_PROMPT
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == OUTPUT_SCHEMA


@pytest.mark.parametrize(
    ("client", "kind"),
    [
        (FakeGenai(text=None), "no_output"),
        (FakeGenai(text="not json"), "invalid_output"),
        (FakeGenai(error=genai_errors.ClientError(429, {"error": {"message": "quota"}})), "api:ClientError"),
    ],
)
def test_gemini_failures_become_extractor_errors(client, kind):
    with pytest.raises(ExtractorError) as caught:
        GeminiVertexExtractor(client, "gemini-test-model").extract(PAYLOAD)
    assert caught.value.kind == kind


def test_gemini_client_is_always_vertex(monkeypatch):
    seen = {}

    def fake_client(**kwargs):
        seen.update(kwargs)
        return FakeGenai()

    monkeypatch.setattr(gemini_module.genai, "Client", fake_client)
    build_gemini_vertex("gemini-test-model", "my-project-1", "asia-southeast2", 60, 2)
    assert seen["vertexai"] is True
    assert (seen["project"], seen["location"]) == ("my-project-1", "asia-southeast2")
    assert "api_key" not in seen
    assert seen["http_options"].timeout == 60_000 and seen["http_options"].retry_options.attempts == 3


def test_gemini_needs_an_explicit_model_and_project(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    with pytest.raises(ExtractorUnavailable, match="extractor.model"):
        build_gemini_vertex(None, "my-project-1", "asia-southeast2", 60, 2)
    with pytest.raises(ExtractorUnavailable, match="project and location"):
        build_extractor(
            Settings(extractor=ExtractorSettings(enabled=True, provider=ExtractorProvider.GEMINI_VERTEX, model="g-1"))
        )


def test_extractor_is_off_by_default():
    assert build_extractor(Settings()) is None


def test_enabled_claude_is_checked_before_any_file_is_read(monkeypatch):
    fake = FakeAnthropic(retrieve_error=anthropic.AnthropicError("no key"))
    monkeypatch.setattr(claude_module.anthropic, "Anthropic", lambda **kwargs: fake)
    with pytest.raises(ExtractorUnavailable):
        build_extractor(Settings(extractor=ExtractorSettings(enabled=True)))
    assert fake.requests == []  # the check sends no document text


def test_model_id_is_validated_by_config():
    with pytest.raises(ValueError):
        ExtractorSettings(model="claude; rm -rf /")
