"""Small, versioned SystemDecoded editorial and visual quality baseline."""

from __future__ import annotations

QUALITY_RULES_VERSION = "2026-10-14.2"

EDITORIAL_RULES = (
    "Explain unfamiliar concepts before using jargon.",
    "Never invent technical statistics.",
    "Distinguish verified facts from simplified explanations.",
    "Avoid misleading absolute claims.",
)

VISUAL_RULES = (
    "Use short visual labels instead of narration sentences.",
    "Prefer visual transformations and mechanism changes to static text cards.",
    "Require mobile-readable, unclipped layouts inside the caption-safe region.",
    "Require an intentional SystemDecoded visual ending.",
    "Use typed directed edges whose arrows follow the true information flow.",
    "Keep connector labels clear of paths, arrowheads, nodes, and other labels.",
)


def prompt_rules() -> str:
    rules = " ".join((*EDITORIAL_RULES, *VISUAL_RULES))
    return f"SystemDecoded quality rules {QUALITY_RULES_VERSION}: {rules}"
