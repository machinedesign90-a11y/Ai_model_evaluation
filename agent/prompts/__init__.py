"""Versioned system prompts. The current one is SYSTEM_PROMPT_VERSION."""

from pathlib import Path

SYSTEM_PROMPT_VERSION = "v2"


def system_prompt(version: str = SYSTEM_PROMPT_VERSION) -> str:
    return (Path(__file__).parent / f"system_{version}.md").read_text()
