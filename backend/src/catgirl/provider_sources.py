from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatCompletionSourceSpec:
    kind: str
    base_url: str = ""


CHAT_COMPLETION_SOURCE_SPECS = {
    "custom": ChatCompletionSourceSpec("openai_compatible"),
    "openai": ChatCompletionSourceSpec("openai_compatible", "https://api.openai.com/v1"),
    "ai21": ChatCompletionSourceSpec("openai_compatible", "https://api.ai21.com/studio/v1"),
    "aimlapi": ChatCompletionSourceSpec("openai_compatible", "https://api.aimlapi.com/v1"),
    "azure_openai": ChatCompletionSourceSpec("openai_compatible"),
    "chutes": ChatCompletionSourceSpec("openai_compatible", "https://llm.chutes.ai/v1"),
    "claude": ChatCompletionSourceSpec("anthropic", "https://api.anthropic.com/v1"),
    "workers_ai": ChatCompletionSourceSpec("openai_compatible"),
    "cohere": ChatCompletionSourceSpec(
        "openai_compatible", "https://api.cohere.ai/compatibility/v1"
    ),
    "deepseek": ChatCompletionSourceSpec("openai_compatible", "https://api.deepseek.com/v1"),
    "electronhub": ChatCompletionSourceSpec(
        "openai_compatible", "https://api.electronhub.ai/v1"
    ),
    "fireworks": ChatCompletionSourceSpec(
        "openai_compatible", "https://api.fireworks.ai/inference/v1"
    ),
    "groq": ChatCompletionSourceSpec("openai_compatible", "https://api.groq.com/openai/v1"),
    "makersuite": ChatCompletionSourceSpec(
        "google_gemini", "https://generativelanguage.googleapis.com/v1beta"
    ),
    "vertexai": ChatCompletionSourceSpec("google_gemini"),
    "mistralai": ChatCompletionSourceSpec("openai_compatible", "https://api.mistral.ai/v1"),
    "minimax": ChatCompletionSourceSpec("openai_compatible", "https://api.minimax.io/v1"),
    "moonshot": ChatCompletionSourceSpec("openai_compatible", "https://api.moonshot.ai/v1"),
    "nanogpt": ChatCompletionSourceSpec("openai_compatible", "https://nano-gpt.com/api/v1"),
    "openrouter": ChatCompletionSourceSpec(
        "openai_compatible", "https://openrouter.ai/api/v1"
    ),
    "perplexity": ChatCompletionSourceSpec(
        "openai_compatible", "https://api.perplexity.ai"
    ),
    "pollinations": ChatCompletionSourceSpec(
        "openai_compatible", "https://gen.pollinations.ai/v1"
    ),
    "siliconflow": ChatCompletionSourceSpec(
        "openai_compatible", "https://api.siliconflow.com/v1"
    ),
    "xai": ChatCompletionSourceSpec("openai_compatible", "https://api.x.ai/v1"),
    "zai": ChatCompletionSourceSpec(
        "openai_compatible", "https://api.z.ai/api/paas/v4"
    ),
}

SUPPORTED_CHAT_COMPLETION_SOURCES = frozenset(CHAT_COMPLETION_SOURCE_SPECS)


def chat_completion_source_spec(source: str) -> ChatCompletionSourceSpec:
    return CHAT_COMPLETION_SOURCE_SPECS.get(source, CHAT_COMPLETION_SOURCE_SPECS["custom"])
