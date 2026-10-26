"""Membangun extractor dari konfigurasi. Extractor cloud mati secara default (`extractor.enabled: false`)."""

from __future__ import annotations

import os

from contract_extractor.config import ExtractorProvider, Settings
from contract_extractor.extractors.base import Extractor


def build_extractor(settings: Settings) -> Extractor | None:
    """None when disabled. Raises ExtractorUnavailable when enabled but not usable, before any file is read."""
    cfg = settings.extractor
    if not cfg.enabled:
        return None
    # SDK imports are deferred so a run with the extractor disabled never loads them.
    if cfg.provider is ExtractorProvider.CLAUDE:
        from contract_extractor.extractors.claude import build_claude

        claude = build_claude(cfg.model, cfg.effort, cfg.refusal_fallback, cfg.timeout_s, cfg.max_retries)
        claude.check()
        return claude

    if cfg.provider is ExtractorProvider.CLAUDE_CODE:
        from contract_extractor.extractors.claude_code import build_claude_code

        claude_code = build_claude_code(cfg.claude_code_cmd, cfg.model, cfg.effort, cfg.timeout_s)
        claude_code.check()
        return claude_code

    from contract_extractor.extractors.gemini import build_gemini_vertex

    gemini = build_gemini_vertex(
        cfg.model,
        cfg.vertex_project or os.environ.get("GOOGLE_CLOUD_PROJECT"),
        cfg.vertex_location or os.environ.get("GOOGLE_CLOUD_LOCATION"),
        cfg.timeout_s,
        cfg.max_retries,
    )
    gemini.check()
    return gemini
