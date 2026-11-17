"""Extractor clients with fake SDK clients. No test here touches the network or a real API key."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import anthropic
import pytest
from google.genai import errors as genai_errors
from google.genai import types

from contract_extractor.common.config import ExtractorProvider, ExtractorSettings, Settings
from contract_extractor.common.models import TokenUsage
from contract_extractor.extractor_method.llm import claude as claude_module
from contract_extractor.extractor_method.llm import claude_code as claude_code_module
from contract_extractor.extractor_method.llm import gemini as gemini_module
from contract_extractor.extractor_method.llm.base import ExtractorError, ExtractorUnavailable, ImageTask
from contract_extractor.extractor_method.llm.claude import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    IMAGE_EFFORT,
    IMAGE_MAX_TOKENS,
    MAX_TOKENS,
    REFUSAL_FALLBACK_BETA,
    ClaudeExtractor,
)
from contract_extractor.extractor_method.llm.claude_code import ClaudeCodeExtractor
from contract_extractor.extractor_method.llm.factory import build_extractor
from contract_extractor.extractor_method.llm.gemini import GeminiVertexExtractor, build_gemini_vertex
from contract_extractor.extractor_method.llm.prompt import IMAGE_PROMPTS, IMAGE_SCHEMAS, OUTPUT_SCHEMA, SYSTEM_PROMPT

PAYLOAD = "Nama : Budi Santoso\nNIK : [REDACTED-ID]\nberlaku mulai 1 Januari 2026 s/d 31 Desember 2026"
PERIOD = "mulai 1 Januari 2026 s/d 31 Desember 2026"
ANSWER = {
    "nama_karyawan": {"kutipan": "Nama : Budi Santoso", "nilai": "Budi Santoso", "confidence": 0.93},
    "posisi": {"kutipan": None, "nilai": None, "confidence": 0.0},
    "tanggal_mulai": {"kutipan": PERIOD, "nilai": "2026-01-01", "confidence": 0.95},
    "tanggal_selesai": {"kutipan": PERIOD, "nilai": "2026-12-31", "confidence": 0.95},
    "durasi_bulan": {"kutipan": None, "nilai": None, "confidence": 0.0},
}


class FakeAnthropic:
    def __init__(self, text=json.dumps(ANSWER), stop_reason="end_turn", error=None, retrieve_error=None):
        self.requests: list[dict] = []
        self._text, self._stop, self._error, self._retrieve_error = text, stop_reason, error, retrieve_error
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self.models = SimpleNamespace(retrieve=self._retrieve)

    @contextmanager
    def _stream(self, **request):
        self.requests.append(request)
        if self._error:
            raise self._error
        content = [SimpleNamespace(type="thinking", thinking="")]
        if self._text is not None:
            content.append(SimpleNamespace(type="text", text=self._text))
        usage = SimpleNamespace(input_tokens=40, cache_read_input_tokens=2500, output_tokens=900)
        message = SimpleNamespace(stop_reason=self._stop, content=content, usage=usage)
        yield SimpleNamespace(get_final_message=lambda: message)

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

    assert (result.nama_karyawan.nilai, result.nama_karyawan.confidence) == ("Budi Santoso", 0.93)
    [request] = client.requests
    assert (request["model"], DEFAULT_EFFORT) == (DEFAULT_MODEL, "max") == ("claude-haiku-5-5", "max")
    # The prompt is the same for every file, so it is cached after the first one.
    assert request["system"] == [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    assert request["messages"] == [{"role": "user", "content": f"<kontrak>\n{PAYLOAD}\n</kontrak>"}]
    assert request["output_config"] == {"effort": "max", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
    assert request["thinking"] == {"type": "adaptive"}
    assert request["max_tokens"] == MAX_TOKENS
    # Haiku has no server-side refusal fallback, so the parameter is not sent.
    assert not {"betas", "fallbacks", "temperature", "top_p", "top_k"} & request.keys()


def test_claude_refusal_fallback_is_sent_for_models_that_have_one_and_can_be_switched_off():
    client = FakeAnthropic()
    ClaudeExtractor(client, model="claude-sonnet-5-5", effort="high").extract(PAYLOAD)
    request = client.requests[0]
    assert (request["betas"], request["fallbacks"]) == ([REFUSAL_FALLBACK_BETA], "default")
    assert request["output_config"]["effort"] == "high"

    client = FakeAnthropic()
    ClaudeExtractor(client, model="claude-sonnet-5-5", refusal_fallback=False).extract(PAYLOAD)
    assert not {"betas", "fallbacks"} & client.requests[0].keys()


@pytest.mark.parametrize(
    ("client", "kind"),
    [
        (FakeAnthropic(stop_reason="refusal"), "refusal"),
        (FakeAnthropic(stop_reason="max_tokens"), "truncated"),
        (FakeAnthropic(text=None), "no_output"),
        (FakeAnthropic(text="not json"), "invalid_output"),
        (FakeAnthropic(text=json.dumps({**ANSWER, "nama_karyawan": "Budi Santoso"})), "invalid_output"),
        (FakeAnthropic(error=anthropic.AnthropicError("boom")), "api:AnthropicError"),
    ],
)
def test_claude_failures_become_extractor_errors(client, kind):
    with pytest.raises(ExtractorError) as caught:
        ClaudeExtractor(client).extract(PAYLOAD)
    assert caught.value.kind == kind
    # A reply that came back still used tokens; a call that never got one reports none.
    assert caught.value.usage == (None if kind.startswith("api:") else TokenUsage(masuk=40, cache_baca=2500, keluar=900))


def test_claude_reports_the_tokens_it_used():
    result = ClaudeExtractor(FakeAnthropic()).extract(PAYLOAD)
    assert result.usage == TokenUsage(masuk=40, cache_tulis=0, cache_baca=2500, keluar=900)


PNG = b"fake png crop"


@pytest.mark.parametrize("task", list(ImageTask))
def test_image_schemas_are_in_the_subset_the_claude_api_accepts(task):
    assert anthropic.transform_schema(IMAGE_SCHEMAS[task]) == IMAGE_SCHEMAS[task]


def test_claude_reads_a_crop_as_one_image_at_low_effort():
    client = FakeAnthropic(text=json.dumps({"nik": "3199 9901 0190 0001", "confidence": 0.9}))
    answer = ClaudeExtractor(client).read_image(ImageTask.NIK, PNG)

    assert (answer.nik, answer.status, answer.confidence) == ("3199990101900001", None, 0.9)
    assert answer.usage == TokenUsage(masuk=40, cache_baca=2500, keluar=900)
    [request] = client.requests
    [message] = request["messages"]
    image, text = message["content"]
    assert image == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "ZmFrZSBwbmcgY3JvcA=="}}
    assert text["type"] == "text" and "Budi" not in text["text"]
    assert request["system"] == [{"type": "text", "text": IMAGE_PROMPTS[ImageTask.NIK]}]
    assert request["output_config"] == {
        "effort": IMAGE_EFFORT, "format": {"type": "json_schema", "schema": IMAGE_SCHEMAS[ImageTask.NIK]}
    }
    assert request["max_tokens"] == IMAGE_MAX_TOKENS


def test_a_crop_answer_keeps_only_what_its_task_asked():
    signature = json.dumps({"status": "signed", "confidence": 0.95, "nik": "3199990101900001"})
    answer = ClaudeExtractor(FakeAnthropic(text=signature)).read_image(ImageTask.SIGNATURE, PNG)
    assert (answer.status, answer.nik) == ("signed", None)
    short = ClaudeExtractor(FakeAnthropic(text=json.dumps({"nik": "31999901", "confidence": 0.9})))
    assert short.read_image(ImageTask.NIK, PNG).nik is None  # not 16 digits: no NIK


def test_a_refused_crop_still_reports_its_tokens():
    with pytest.raises(ExtractorError) as caught:
        ClaudeExtractor(FakeAnthropic(stop_reason="refusal")).read_image(ImageTask.SIGNATURE, PNG)
    assert caught.value.kind == "refusal" and caught.value.usage == TokenUsage(masuk=40, cache_baca=2500, keluar=900)


def test_gemini_reads_no_crops():
    with pytest.raises(ExtractorError) as caught:
        GeminiVertexExtractor(FakeGenai(), "gemini-x").read_image(ImageTask.NIK, PNG)
    assert caught.value.kind == "image_unsupported"


def test_invalid_output_error_never_echoes_document_text():
    leaky = json.dumps({**ANSWER, "tanggal_mulai": {"kutipan": PERIOD, "nilai": "Budi Santoso", "confidence": 1}})
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

    assert result.tanggal_selesai.nilai.isoformat() == "2026-12-31"
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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    fake = FakeAnthropic(retrieve_error=anthropic.AnthropicError("no key"))
    monkeypatch.setattr(claude_module.anthropic, "Anthropic", lambda **kwargs: fake)
    with pytest.raises(ExtractorUnavailable):
        build_extractor(Settings(extractor=ExtractorSettings(enabled=True)))
    assert fake.requests == []  # the check sends no document text


def test_claude_uses_the_api_when_a_key_is_set_and_the_claude_code_login_otherwise(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_module.anthropic, "Anthropic", lambda **kwargs: FakeAnthropic())
    cli = tmp_path / "claude.exe"
    cli.write_bytes(b"")
    monkeypatch.setattr(claude_code_module.ClaudeCodeExtractor, "check", lambda self: None)  # signed in
    settings = Settings(extractor=ExtractorSettings(enabled=True, claude_code_cmd=cli, model="claude-sonnet-5-5"))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    api = build_extractor(settings)
    assert isinstance(api, ClaudeExtractor) and api.model == "claude-sonnet-5-5"

    for unset in ("", "   "):
        monkeypatch.setenv("ANTHROPIC_API_KEY", unset)
        login = build_extractor(settings)
        assert isinstance(login, ClaudeCodeExtractor)
        assert (login.cli, login.model, login.effort, login.timeout_s) == (cli, "claude-sonnet-5-5", "max", 120)


def test_model_id_is_validated_by_config():
    with pytest.raises(ValueError):
        ExtractorSettings(model="claude; rm -rf /")
