from __future__ import annotations

from functools import lru_cache

import tiktoken
from tiktoken import Encoding


@lru_cache(maxsize=128)
def encoding_for_model(model: str) -> Encoding:
    normalized = model.strip().lower()
    if normalized:
        try:
            return tiktoken.encoding_for_model(normalized)
        except KeyError:
            pass
    if any(name in normalized for name in ("gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "o4")):
        return tiktoken.get_encoding("o200k_base")
    return tiktoken.get_encoding("cl100k_base")


def tokenizer_name(model: str) -> str:
    return encoding_for_model(model).name


def count_text_tokens(text: str, model: str = "") -> int:
    if not text:
        return 0
    return len(encoding_for_model(model).encode(text, disallowed_special=()))
