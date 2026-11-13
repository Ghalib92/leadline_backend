"""Versioned prompts, one folder per agent: apps/ai/prompts/<agent>/v<N>.md.

Placeholders are written {{ name }}. Single braces are left alone, so prompts can
contain JSON examples. Never edit a released version in place: copy it to v<N+1>
so runs stay comparable, since every run records the prompt id it used.
"""

from .loader import Prompt, available_versions, load_prompt

__all__ = ["Prompt", "available_versions", "load_prompt"]
