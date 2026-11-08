"""Extractor lewat Claude Code CLI (`claude -p`) dengan login `claude auth login` milik pengguna sendiri.

Extractor ini tidak pernah membaca, menyimpan, atau meneruskan token login: ia menjalankan binary Claude Code
yang tidak dimodifikasi, dan binary itu yang mengautentikasi. Ketentuan Anthropic membatasi login claude.ai
untuk pemakaian Claude Code oleh pemilik login itu sendiri, jadi provider ini untuk uji lokal oleh pengguna
yang sedang login; layanan produksi memakai API key (provider `claude`) atau Vertex AI.

Claude Code berjalan tanpa tool, MCP, skill, setting pengguna, dan transkrip sesi, di folder kosong, dengan
prompt sistem ekstraksi menggantikan prompt bawaannya. Teks kontrak hanya bisa menghasilkan jawaban JSON.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from contract_extractor.extractors.base import ExtractionResult, ExtractorError, ExtractorUnavailable
from contract_extractor.extractors.claude import DEFAULT_MODEL
from contract_extractor.extractors.prompt import OUTPUT_SCHEMA, SYSTEM_PROMPT, parse_output, user_message

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

    def __init__(self, cli: Path, model: str = DEFAULT_MODEL, effort: str = "high", timeout_s: float = 60, run: Runner = _run):
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

    def argv(self, prompt_file: Path) -> list[str]:
        return [
            str(self.cli),
            "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(OUTPUT_SCHEMA, separators=(",", ":")),
            "--system-prompt-file", str(prompt_file),
            "--model", self.model,
            "--effort", self.effort,
            "--tools", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--setting-sources", "project",
            "--no-session-persistence",
        ]  # fmt: skip

    def extract(self, masked_text: str) -> ExtractionResult:
        with tempfile.TemporaryDirectory(prefix="ce-") as tmp:
            prompt_file = Path(tmp) / "system-prompt.txt"
            prompt_file.write_text(SYSTEM_PROMPT, encoding="utf-8")
            work = Path(tmp) / "work"  # empty, so there is no CLAUDE.md or project setting to pick up
            work.mkdir()
            try:
                # The payload goes through stdin, never argv, so other local processes cannot read it.
                code, out = self._run(self.argv(prompt_file), user_message(masked_text), str(work), self.timeout_s)
            except subprocess.TimeoutExpired:
                raise ExtractorError("timeout") from None
            except (OSError, subprocess.SubprocessError) as exc:
                raise ExtractorError(f"cli:{type(exc).__name__}") from None
        try:
            result = json.loads(out)
        except json.JSONDecodeError:
            raise ExtractorError(f"cli:exit_{code}" if code else "cli:invalid_json") from None
        if not isinstance(result, dict):
            raise ExtractorError("cli:invalid_json")
        if result.get("is_error") or result.get("subtype") != "success":
            subtype = _SAFE_KIND.sub("_", str(result.get("subtype") or "error").lower())[:40]
            raise ExtractorError(f"cli:{subtype}")
        answer = result.get("structured_output")
        if not isinstance(answer, dict):
            raise ExtractorError("no_output")
        return parse_output(json.dumps(answer))


def build_claude_code(cli: Path | None, model: str | None, effort: str, timeout_s: int) -> ClaudeCodeExtractor:
    return ClaudeCodeExtractor(resolve_cli(cli), model or DEFAULT_MODEL, effort, float(timeout_s))
