"""Membangun extractor dari konfigurasi. Extractor cloud mati secara default (`extractor.enabled: false`)."""

from __future__ import annotations

import logging
import os

from contract_extractor.common.config import ExtractorProvider, Settings
from contract_extractor.extractor_method.llm.base import Extractor

log = logging.getLogger(__name__)


def uses_api_key() -> bool:
    """Provider `claude` picks the Claude API when an API key is set, like the website picks SharePoint when its
    settings are filled in; otherwise it uses the Claude Code login of the person running it."""
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def build_extractor(settings: Settings) -> Extractor | None:
    """None when disabled. Raises ExtractorUnavailable when enabled but not usable, before any file is read."""
    cfg = settings.extractor
    if not cfg.enabled:
        return None
    # SDK imports are deferred so a run with the extractor disabled never loads them.
    if cfg.provider is ExtractorProvider.CLAUDE and uses_api_key():
        from contract_extractor.extractor_method.llm.claude import build_claude

        claude = build_claude(cfg.model, cfg.effort, cfg.refusal_fallback, cfg.timeout_s, cfg.max_retries)
        claude.check()
        log.info("extractor: Claude API with an API key, model %s, effort %s", claude.model, claude.effort)
        return claude

    if cfg.provider in (ExtractorProvider.CLAUDE, ExtractorProvider.CLAUDE_CODE):
        from contract_extractor.extractor_method.llm.claude_code import build_claude_code

        claude_code = build_claude_code(cfg.claude_code_cmd, cfg.model, cfg.effort, cfg.timeout_s)
        claude_code.check()
        log.info(
            "extractor: Claude Code login (local testing only), model %s, effort %s", claude_code.model, claude_code.effort
        )
        return claude_code

    from contract_extractor.extractor_method.llm.gemini import build_gemini_vertex

    gemini = build_gemini_vertex(
        cfg.model,
        cfg.vertex_project or os.environ.get("GOOGLE_CLOUD_PROJECT"),
        cfg.vertex_location or os.environ.get("GOOGLE_CLOUD_LOCATION"),
        cfg.timeout_s,
        cfg.max_retries,
    )
    gemini.check()
    return gemini
