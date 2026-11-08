"""Extractor lewat Claude Code CLI (`claude -p`) dengan login `claude auth login` milik pengguna sendiri.

Extractor ini tidak pernah membaca, menyimpan, atau meneruskan token login: ia menjalankan binary Claude Code
yang tidak dimodifikasi, dan binary itu yang mengautentikasi. Ketentuan Anthropic membatasi login claude.ai
untuk pemakaian Claude Code oleh pemilik login itu sendiri, jadi provider ini untuk uji lokal oleh pengguna
yang sedang login; layanan produksi memakai API key (provider `claude`) atau Vertex AI.

Claude Code berjalan tanpa tool, MCP, skill, setting pengguna, dan transkrip sesi, di folder kosong, dengan
prompt sistem ekstraksi menggantikan prompt bawaannya. Teks kontrak hanya bisa menghasilkan jawaban JSON.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from contract_extractor.common.models import TokenUsage
from contract_extractor.extractor_method.llm.base import (
    ExtractionResult,
    ExtractorError,
    ExtractorUnavailable,
    FullPageAnswer,
    ImageAnswer,
    ImageTask,
)
from contract_extractor.extractor_method.llm.claude import DEFAULT_EFFORT, DEFAULT_MODEL, IMAGE_EFFORT, jpeg_block
from contract_extractor.extractor_method.llm.prompt import (
    FULL_PAGE_PROMPT,
    FULL_PAGE_SCHEMA,
    IMAGE_PROMPTS,
    IMAGE_REQUEST,
    IMAGE_SCHEMAS,
    OUTPUT_SCHEMA,
    SYSTEM_PROMPT,
    page_content,
    parse_full_page_output,
    parse_image_output,
    parse_output,
    user_message,
)

log = logging.getLogger(__name__)

AUTH_CHECK_TIMEOUT_S = 30
# Claude Code then sends no telemetry, error reports, or update checks: only the extraction request leaves.
QUIET_ENV = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
_SAFE_KIND = re.compile(r"[^a-z0-9_]")

# (argv, stdin, cwd, timeout_s) -> (exit code, stdout). Tests replace it; stderr is never read or logged.
Runner = Callable[[list[str], str, str, float], tuple[int, str]]


def _run(argv: list[str], stdin: str, cwd: str, timeout_s: float) -> tuple[int, str]:
    done = subprocess.run(
        argv,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        timeout=timeout_s,
        env={**os.environ, **QUIET_ENV},
        check=False,
    )
    return done.returncode, done.stdout


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def cli_usage(result: dict) -> TokenUsage | None:
    """The tokens Claude Code reports for the run: summed over every model it used (`modelUsage`), else the main
    model's `usage`. None when it reports neither."""
    per_model = result.get("modelUsage")
    if isinstance(per_model, dict) and per_model:
        rows = [m for m in per_model.values() if isinstance(m, dict)]
        keys = ("inputTokens", "cacheCreationInputTokens", "cacheReadInputTokens", "outputTokens")
    elif isinstance(result.get("usage"), dict):
        rows = [result["usage"]]
        keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    else:
        return None
    masuk, tulis, baca, keluar = (sum(_count(row.get(key)) for row in rows) for key in keys)
    return TokenUsage(masuk=masuk, cache_tulis=tulis, cache_baca=baca, keluar=keluar)


def resolve_cli(configured: Path | None) -> Path:
    """The native Claude Code binary: config, then CLAUDE_CODE_CMD, then PATH."""
    found = configured or os.environ.get("CLAUDE_CODE_CMD") or shutil.which("claude")
    if not found:
        raise ExtractorUnavailable("Claude Code CLI not found: install it and run `claude auth login`")
    path = Path(found)
    if path.suffix.lower() in {".cmd", ".bat"}:
        # npm's Windows shim re-parses every argument through cmd.exe, which cuts a JSON schema or prompt
        # short without an error. Call the native binary the shim wraps instead.
        native = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if not native.is_file():
            raise ExtractorUnavailable("Claude Code is a cmd.exe shim here: set extractor.claude_code_cmd to claude.exe")
        path = native
    if not path.is_file():
        raise ExtractorUnavailable(f"Claude Code CLI not found at {path}")
    return path


class ClaudeCodeExtractor:
    name = "claude-code"

    def __init__(
        self, cli: Path, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT, timeout_s: float = 120, run: Runner = _run
    ):
        self.cli, self.model, self.effort, self.timeout_s, self._run = cli, model, effort, timeout_s, run

    def check(self) -> None:
        """Signed in, without sending any document text. Reports no account details."""
        with tempfile.TemporaryDirectory(prefix="ce-auth-") as work:
            try:
                code, out = self._run([str(self.cli), "auth", "status"], "", work, AUTH_CHECK_TIMEOUT_S)
            except (OSError, subprocess.SubprocessError) as exc:
                raise ExtractorUnavailable(f"Claude Code CLI cannot run: {type(exc).__name__}") from exc
        try:
            status = json.loads(out)
        except json.JSONDecodeError:
            status = {}
        if code != 0 or not isinstance(status, dict) or status.get("loggedIn") is not True:
            raise ExtractorUnavailable("Claude Code is not signed in: run `claude auth login`")

    def argv(self, prompt_file: Path, schema: dict | None = None, effort: str | None = None) -> list[str]:
        return [
            str(self.cli),
            "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema or OUTPUT_SCHEMA, separators=(",", ":")),
            "--system-prompt-file", str(prompt_file),
            "--model", self.model,
            "--effort", effort or self.effort,
            "--tools", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--setting-sources", "project",
            "--no-session-persistence",
        ]  # fmt: skip

    def image_argv(self, prompt_file: Path, task: ImageTask) -> list[str]:
        """An image cannot go in as plain text, so the crop goes in as one stream-json user message instead."""
        return _stream_json(self.argv(prompt_file, IMAGE_SCHEMAS[task], IMAGE_EFFORT))

    def pages_argv(self, prompt_file: Path) -> list[str]:
        """The no-OCR mode's two pages, as one stream-json user message, at the text call's effort."""
        return _stream_json(self.argv(prompt_file, FULL_PAGE_SCHEMA))

    def _call(self, system: str, argv_for: Callable[[Path], list[str]], stdin: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory(prefix="ce-") as tmp:
            prompt_file = Path(tmp) / "system-prompt.txt"
            prompt_file.write_text(system, encoding="utf-8")
            work = Path(tmp) / "work"  # empty, so there is no CLAUDE.md or project setting to pick up
            work.mkdir()
            try:
                # The payload goes through stdin, never argv, so other local processes cannot read it.
                return self._run(argv_for(prompt_file), stdin, str(work), self.timeout_s)
            except subprocess.TimeoutExpired:
                raise ExtractorError("timeout") from None
            except (OSError, subprocess.SubprocessError) as exc:
                raise ExtractorError(f"cli:{type(exc).__name__}") from None

    def _answer(self, result: object, code: int, what: str) -> tuple[dict, TokenUsage | None]:
        """The structured answer of a finished run and the tokens it used; a failed run still reports its tokens."""
        if not isinstance(result, dict):
            raise ExtractorError(f"cli:exit_{code}" if code else "cli:invalid_json")
        usage = cli_usage(result)
        if usage:
            log.info(
                "claude-code %s %s: %d input, %d cache write, %d cache read, %d output tokens",
                self.model, what, usage.masuk, usage.cache_tulis, usage.cache_baca, usage.keluar,
            )  # fmt: skip
        if result.get("is_error") or result.get("subtype") != "success":
            subtype = _SAFE_KIND.sub("_", str(result.get("subtype") or "error").lower())[:40]
            raise ExtractorError(f"cli:{subtype}", usage)
        structured = result.get("structured_output")
        if not isinstance(structured, dict):
            raise ExtractorError("no_output", usage)
        return structured, usage

    def extract(self, masked_text: str) -> ExtractionResult:
        code, out = self._call(SYSTEM_PROMPT, self.argv, user_message(masked_text))
        try:
            result = json.loads(out)
        except json.JSONDecodeError:
            raise ExtractorError(f"cli:exit_{code}" if code else "cli:invalid_json") from None
        structured, usage = self._answer(result, code, "teks")
        try:
            answer = parse_output(json.dumps(structured))
        except ExtractorError as exc:
            raise ExtractorError(exc.kind, usage) from None
        answer.usage = usage
        return answer

    def _stream(
        self, system: str, argv_for: Callable[[Path], list[str]], content: list[dict], what: str
    ) -> tuple[dict, TokenUsage | None]:
        """One stream-json run with images: its structured answer and the tokens it used."""
        message = {"type": "user", "message": {"role": "user", "content": content}}
        code, out = self._call(system, argv_for, json.dumps(message) + "\n")
        result = None
        for line in out.splitlines():  # one event per line; the last one says how the run ended
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                result = event
        return self._answer(result, code, what)

    def read_image(self, task: ImageTask, png: bytes) -> ImageAnswer:
        image = {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode("ascii")}
        content = [{"type": "image", "source": image}, {"type": "text", "text": IMAGE_REQUEST}]
        structured, usage = self._stream(
            IMAGE_PROMPTS[task], lambda prompt: self.image_argv(prompt, task), content, task.value
        )
        try:
            answer = parse_image_output(task, json.dumps(structured))
        except ExtractorError as exc:
            raise ExtractorError(exc.kind, usage) from None
        answer.usage = usage
        return answer

    def read_pages(self, first: bytes, last: bytes | None) -> FullPageAnswer:
        content = page_content(jpeg_block(first), jpeg_block(last) if last else None)
        structured, usage = self._stream(FULL_PAGE_PROMPT, self.pages_argv, content, "halaman")
        try:
            answer = parse_full_page_output(json.dumps(structured))
        except ExtractorError as exc:
            raise ExtractorError(exc.kind, usage) from None
        answer.usage = usage
        return answer


def _stream_json(argv: list[str]) -> list[str]:
    """The same run with its input and output as stream-json, so a message can carry images."""
    at = argv.index("--output-format")
    return argv[:at] + ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"] + argv[at + 2 :]


def build_claude_code(cli: Path | None, model: str | None, effort: str, timeout_s: int) -> ClaudeCodeExtractor:
    return ClaudeCodeExtractor(resolve_cli(cli), model or DEFAULT_MODEL, effort, float(timeout_s))
