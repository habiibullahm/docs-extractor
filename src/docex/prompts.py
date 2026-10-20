"""Versioned prompt files (prompts/<name>.md). The version id includes a content hash."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache

from docex.config import PROMPT_DIR


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str

    @property
    def version(self) -> str:
        return f"{self.name}@{hashlib.sha256(self.text.encode()).hexdigest()[:8]}"

    def render(self, **kwargs: str) -> str:
        out = self.text
        for k, v in kwargs.items():
            out = out.replace("{" + k + "}", v)
        return out


@lru_cache(maxsize=32)
def load_prompt(name: str) -> Prompt:
    return Prompt(name, (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8"))
