"""Versioned system prompts. The current one is SYSTEM_PROMPT_VERSION.

v3 has a {{SEMANTIC_LAYER}} placeholder, filled from the database (agent/semantic_layer.py).
"""

from pathlib import Path

SYSTEM_PROMPT_VERSION = "v3"
PLACEHOLDER = "{{SEMANTIC_LAYER}}"


def prompt_template(version: str = SYSTEM_PROMPT_VERSION) -> str:
    return (Path(__file__).parent / f"system_{version}.md").read_text()


def system_prompt(semantic_layer: str, version: str = SYSTEM_PROMPT_VERSION) -> str:
    template = prompt_template(version)
    if PLACEHOLDER not in template:
        raise ValueError(f"system_{version}.md has no {PLACEHOLDER} placeholder")
    return template.replace(PLACEHOLDER, semantic_layer.strip())
