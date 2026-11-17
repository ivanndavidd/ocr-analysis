"""Claude Code CLI extractor with a fake runner. No test here starts the real CLI or touches the network."""

import json
import subprocess
from pathlib import Path

import pytest

from contract_extractor.common.config import ExtractorProvider, ExtractorSettings, Settings
from contract_extractor.common.models import TokenUsage
from contract_extractor.extractor_method.llm import claude_code as claude_code_module
from contract_extractor.extractor_method.llm.base import ExtractorError, ExtractorUnavailable, ImageTask
from contract_extractor.extractor_method.llm.claude import DEFAULT_MODEL
from contract_extractor.extractor_method.llm.claude_code import ClaudeCodeExtractor, resolve_cli
from contract_extractor.extractor_method.llm.factory import build_extractor
from contract_extractor.extractor_method.llm.prompt import IMAGE_PROMPTS, IMAGE_SCHEMAS, SYSTEM_PROMPT, user_message

PAYLOAD = "Nama : Budi Santoso\nNIK : [REDACTED-ID]\nberlaku mulai 1 Januari 2026 s/d 31 Desember 2026"
PERIOD = "mulai 1 Januari 2026 s/d 31 Desember 2026"
ANSWER = {
    "nama_karyawan": {"kutipan": "Nama : Budi Santoso", "nilai": "Budi Santoso", "confidence": 0.93},
    "posisi": {"kutipan": None, "nilai": None, "confidence": 0.0},
    "tanggal_mulai": {"kutipan": PERIOD, "nilai": "2026-01-01", "confidence": 0.95},
    "tanggal_selesai": {"kutipan": PERIOD, "nilai": "2026-12-31", "confidence": 0.95},
    "durasi_bulan": {"kutipan": None, "nilai": None, "confidence": 0.0},
}
CLI = Path("claude.exe")


class FakeCli:
    """Records each call and what the CLI would have seen: argv, stdin, the prompt file, the working folder."""

    def __init__(self, stdout=None, code=0, error=None):
        self.stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": ANSWER}) if stdout is None else stdout
        self.code, self.error = code, error
        self.calls: list[dict] = []

    def __call__(self, argv, stdin, cwd, timeout_s):
        prompt_file = argv[argv.index("--system-prompt-file") + 1] if "--system-prompt-file" in argv else None
        self.calls.append(
            {
                "argv": argv,
                "stdin": stdin,
                "timeout": timeout_s,
                "cwd_files": sorted(p.name for p in Path(cwd).iterdir()),
                "prompt": Path(prompt_file).read_text(encoding="utf-8") if prompt_file else None,
            }
        )
        if self.error:
            raise self.error
        return self.code, self.stdout


def test_payload_goes_through_stdin_into_a_tool_less_session():
    cli = FakeCli()
    result = ClaudeCodeExtractor(CLI, run=cli).extract(PAYLOAD)

    assert (result.nama_karyawan.nilai, str(result.tanggal_selesai.nilai)) == ("Budi Santoso", "2026-12-31")
    [call] = cli.calls
    argv = call["argv"]
    assert call["stdin"] == user_message(PAYLOAD)
    assert not any("Budi" in arg or "REDACTED" in arg for arg in argv)  # never on the command line
    assert call["prompt"] == SYSTEM_PROMPT  # replaces Claude Code's own system prompt
    assert call["cwd_files"] == []  # nothing for Claude Code to discover
    assert argv[argv.index("--tools") + 1] == ""
    for flag in ("-p", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert (argv[argv.index("--model") + 1], argv[argv.index("--effort") + 1]) == (DEFAULT_MODEL, "max")
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert "\n" not in argv[argv.index("--json-schema") + 1] and schema["required"][0] == "nama_karyawan"


def test_prompt_file_is_removed_after_the_call(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code_module.tempfile, "tempdir", str(tmp_path))
    ClaudeCodeExtractor(CLI, run=FakeCli()).extract(PAYLOAD)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("cli", "kind"),
    [
        (FakeCli(stdout=json.dumps({"subtype": "error_max_turns", "is_error": True})), "cli:error_max_turns"),
        (FakeCli(stdout=json.dumps({"subtype": "success", "is_error": True})), "cli:success"),
        (FakeCli(stdout=json.dumps({"subtype": "Weird/Kind <x>", "is_error": True})), "cli:weird_kind__x_"),
        (FakeCli(stdout="Error: not signed in", code=1), "cli:exit_1"),
        (FakeCli(stdout=""), "cli:invalid_json"),
        (FakeCli(stdout="[]"), "cli:invalid_json"),
        (FakeCli(stdout=json.dumps({"subtype": "success", "is_error": False, "result": "ok"})), "no_output"),
        (FakeCli(stdout=json.dumps({"subtype": "success", "is_error": False, "structured_output": {"nama_karyawan": "Budi"}})), "invalid_output"),
        (FakeCli(error=subprocess.TimeoutExpired("claude", 60)), "timeout"),
        (FakeCli(error=FileNotFoundError()), "cli:FileNotFoundError"),
    ],
)
def test_cli_failures_become_extractor_errors_without_document_text(cli, kind):
    with pytest.raises(ExtractorError) as caught:
        ClaudeCodeExtractor(CLI, run=cli).extract(PAYLOAD)
    assert caught.value.kind == kind
    assert "Budi" not in str(caught.value)


MODEL_USAGE = {
    "claude-haiku-5-5": {"inputTokens": 30, "cacheCreationInputTokens": 2000, "cacheReadInputTokens": 0, "outputTokens": 700},
    "claude-other": {"inputTokens": 5, "cacheCreationInputTokens": 0, "cacheReadInputTokens": 100, "outputTokens": 20},
}
TOP_USAGE = {"input_tokens": 30, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 0, "output_tokens": 700}


@pytest.mark.parametrize(
    ("report", "usage"),
    [
        # Summed over every model Claude Code used, when it says per model.
        ({"modelUsage": MODEL_USAGE, "usage": TOP_USAGE}, TokenUsage(masuk=35, cache_tulis=2000, cache_baca=100, keluar=720)),
        ({"usage": TOP_USAGE}, TokenUsage(masuk=30, cache_tulis=2000, cache_baca=0, keluar=700)),
        ({"usage": {"input_tokens": True, "output_tokens": -3}}, TokenUsage()),  # nonsense counts as nothing
        ({}, None),
    ],
)
def test_cli_reports_the_tokens_it_used(report, usage):
    stdout = json.dumps({"subtype": "success", "is_error": False, "structured_output": ANSWER, **report})
    assert ClaudeCodeExtractor(CLI, run=FakeCli(stdout=stdout)).extract(PAYLOAD).usage == usage


def test_a_failed_cli_run_still_reports_its_tokens():
    stdout = json.dumps({"subtype": "error_max_turns", "is_error": True, "usage": TOP_USAGE})
    with pytest.raises(ExtractorError) as caught:
        ClaudeCodeExtractor(CLI, run=FakeCli(stdout=stdout)).extract(PAYLOAD)
    assert caught.value.usage == TokenUsage(masuk=30, cache_tulis=2000, keluar=700)


PNG = b"fake png crop"


def _stream(*events):
    return "\n".join(json.dumps(event) for event in events) + "\n"


def test_a_crop_goes_in_as_one_stream_json_message_through_stdin():
    result = {"type": "result", "subtype": "success", "is_error": False, "structured_output": {"status": "unsigned", "confidence": 0.85},
              "usage": TOP_USAGE}
    cli = FakeCli(stdout=_stream({"type": "system", "subtype": "init"}, {"type": "assistant"}, result))
    answer = ClaudeCodeExtractor(CLI, run=cli).read_image(ImageTask.SIGNATURE, PNG)

    assert (answer.status, answer.confidence, answer.nik) == ("unsigned", 0.85, None)
    assert answer.usage == TokenUsage(masuk=30, cache_tulis=2000, keluar=700)
    [call] = cli.calls
    argv = call["argv"]
    message = json.loads(call["stdin"])
    [image, text] = message["message"]["content"]
    assert message["type"] == "user" and image["source"]["data"] == "ZmFrZSBwbmcgY3JvcA==" and text["type"] == "text"
    assert "ZmFrZSBwbmcgY3JvcA==" not in " ".join(argv)  # the crop never goes on the command line
    assert call["prompt"] == IMAGE_PROMPTS[ImageTask.SIGNATURE] and call["cwd_files"] == []
    assert (argv[argv.index("--input-format") + 1], argv[argv.index("--output-format") + 1]) == ("stream-json", "stream-json")
    assert "--verbose" in argv and argv[argv.index("--effort") + 1] == "low"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == IMAGE_SCHEMAS[ImageTask.SIGNATURE]
    assert argv[argv.index("--tools") + 1] == "" and "--strict-mcp-config" in argv and argv.count("--output-format") == 1


@pytest.mark.parametrize(
    ("stdout", "kind"),
    [
        (_stream({"type": "system"}), "cli:invalid_json"),  # the run ended without a result
        ("not json\n", "cli:invalid_json"),
        (_stream({"type": "result", "subtype": "error_max_turns", "is_error": True}), "cli:error_max_turns"),
    ],
)
def test_a_crop_run_without_an_answer_is_an_extractor_error(stdout, kind):
    with pytest.raises(ExtractorError) as caught:
        ClaudeCodeExtractor(CLI, run=FakeCli(stdout=stdout)).read_image(ImageTask.NIK, PNG)
    assert caught.value.kind == kind


@pytest.mark.parametrize(
    ("stdout", "code"),
    [
        (json.dumps({"loggedIn": False}), 0),
        (json.dumps({"loggedIn": True}), 1),
        ("not json", 0),
        ("[]", 0),
    ],
)
def test_check_requires_a_signed_in_cli(stdout, code):
    cli = FakeCli(stdout=stdout, code=code)
    with pytest.raises(ExtractorUnavailable, match="claude auth login"):
        ClaudeCodeExtractor(CLI, run=cli).check()
    assert cli.calls[0]["argv"][1:] == ["auth", "status"]
    assert cli.calls[0]["stdin"] == ""


def test_check_passes_and_reveals_no_account_details():
    status = {"loggedIn": True, "authMethod": "claude.ai", "email": "someone@example.com", "subscriptionType": "team"}
    ClaudeCodeExtractor(CLI, run=FakeCli(stdout=json.dumps(status))).check()  # no exception, nothing returned


def _npm_layout(root: Path, with_native: bool = True) -> Path:
    shim = root / "claude.cmd"
    shim.write_text("@ECHO off\n", encoding="utf-8")
    if with_native:
        native = root / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        native.parent.mkdir(parents=True)
        native.write_bytes(b"MZ")
    return shim


def test_npm_cmd_shim_resolves_to_the_native_binary(tmp_path):
    native = resolve_cli(_npm_layout(tmp_path))
    assert native.name == "claude.exe" and native.parent.name == "bin"


def test_cmd_shim_without_a_native_binary_is_refused(tmp_path):
    with pytest.raises(ExtractorUnavailable, match="claude_code_cmd"):
        resolve_cli(_npm_layout(tmp_path, with_native=False))


def test_missing_cli_is_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_CMD", raising=False)
    monkeypatch.setattr(claude_code_module.shutil, "which", lambda name: None)
    with pytest.raises(ExtractorUnavailable, match="not found"):
        resolve_cli(None)
    with pytest.raises(ExtractorUnavailable, match="not found"):
        resolve_cli(tmp_path / "nothing.exe")


def test_enabled_claude_code_is_checked_before_any_file_is_read(tmp_path, monkeypatch):
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"MZ")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
        return subprocess.CompletedProcess(argv, 0, json.dumps({"loggedIn": False}), "")

    monkeypatch.setattr(claude_code_module.subprocess, "run", fake_run)
    settings = Settings(extractor=ExtractorSettings(enabled=True, provider=ExtractorProvider.CLAUDE_CODE, claude_code_cmd=exe))
    with pytest.raises(ExtractorUnavailable, match="claude auth login"):
        build_extractor(settings)
    assert calls == [[str(exe), "auth", "status"]]
