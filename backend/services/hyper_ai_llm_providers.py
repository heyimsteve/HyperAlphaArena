"""
Hyper AI LLM Provider Configuration

Preset configurations for popular LLM providers with their base URLs and recommended models.
Users can select a provider during onboarding or configure a custom endpoint.

Supported providers:
- OpenAI (GPT-4o, GPT-4-turbo)
- Anthropic (Claude 3.5 Sonnet, Claude 3 Opus)
- Google Gemini (Gemini 2.0 Flash, Gemini 1.5 Pro)
- Deepseek (Deepseek Chat, Deepseek Reasoner)
- Z.ai/智谱 (GLM-5.1, GLM-5, GLM-5-Turbo)
- MiniMax Global/CN (M2.7, M2.5)
- OpenRouter (Multi-provider gateway)
- Qwen/通义千问 (Qwen-Max, Qwen-Plus)
- Moonshot/月之暗面 (Moonshot-v1-128k)
- Custom (User-defined endpoint)
"""
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class LLMProvider:
    """Configuration for an LLM provider."""
    id: str                      # Unique identifier (e.g., "openai", "anthropic")
    name: str                    # Display name (e.g., "OpenAI", "Anthropic")
    base_url: str                # API base URL
    models: List[str]            # Available models (first is recommended)
    api_format: str              # "openai" or "anthropic"
    description: str             # Brief description for UI
    requires_api_key: bool = True


# Preset LLM providers with their configurations
# Models are listed with newest/recommended first, covering ~2 years
PRESET_PROVIDERS: Dict[str, LLMProvider] = {
    "openai": LLMProvider(
        id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        models=[
            # Latest reasoning models
            "o3", "o3-mini", "o1", "o1-mini", "o1-preview",
            # GPT-4o series (multimodal)
            "gpt-4o", "gpt-4o-mini", "gpt-4o-2024-11-20", "gpt-4o-2024-08-06",
            # GPT-4 Turbo
            "gpt-4-turbo", "gpt-4-turbo-2024-04-09", "gpt-4-turbo-preview",
            # GPT-4 legacy
            "gpt-4", "gpt-4-0613",
            # GPT-3.5
            "gpt-3.5-turbo", "gpt-3.5-turbo-0125",
        ],
        api_format="openai",
        description="GPT-4o and o-series reasoning models"
    ),
    "anthropic": LLMProvider(
        id="anthropic",
        name="Anthropic",
        base_url="https://api.anthropic.com/v1",
        models=[
            # Claude 4 series (latest)
            "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5",
            "claude-sonnet-4-5-20250514", "claude-opus-4-5-20250414",
            "claude-sonnet-4-20250514", "claude-opus-4-20250414",
            # Claude 3.5 series
            "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20240620",
            "claude-3-5-haiku-20241022",
            # Claude 3 series
            "claude-3-opus-20240229", "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
        ],
        api_format="anthropic",
        description="Claude models with excellent instruction following"
    ),
    "gemini": LLMProvider(
        id="gemini",
        name="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        models=[
            # Gemini 3 series (preview)
            "gemini-3.1-pro-preview", "gemini-3-pro-preview", "gemini-3-flash-preview",
            # Gemini 2.5 series (latest stable)
            "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
            # Gemini 2.0 series
            "gemini-2.0-flash", "gemini-2.0-flash-lite",
            # Gemini 1.5 series
            "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-8b",
        ],
        api_format="openai",
        description="Google's multimodal AI with large context window"
    ),
    "deepseek": LLMProvider(
        id="deepseek",
        name="Deepseek",
        base_url="https://api.deepseek.com/v1",
        models=[
            # V3 series (latest)
            "deepseek-chat", "deepseek-reasoner",
            # R1 reasoning model
            "deepseek-r1",
            # Coder models
            "deepseek-coder",
        ],
        api_format="openai",
        description="Cost-effective AI with strong coding and reasoning"
    ),
    "zhipu": LLMProvider(
        id="zhipu",
        name="Z.ai / 智谱",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        models=[
            # GLM-5 series (latest, 2026)
            "glm-5.1", "glm-5", "glm-5-turbo", "glm-5v-turbo",
            # GLM-4 series
            "glm-4-plus", "glm-4-0520", "glm-4",
            "glm-4-air", "glm-4-airx", "glm-4-flash",
            "glm-4-long",
        ],
        api_format="openai",
        description="GLM-5 series with strong tool calling and reasoning"
    ),
    "minimax": LLMProvider(
        id="minimax",
        name="MiniMax (Global)",
        base_url="https://api.minimax.io/v1",
        models=[
            "MiniMax-M2.7", "MiniMax-M2.7-highspeed",
            "MiniMax-M2.5", "MiniMax-M2.5-highspeed",
            "MiniMax-M2.1", "MiniMax-M2.1-highspeed",
        ],
        api_format="openai",
        description="For users outside China mainland"
    ),
    "minimax-cn": LLMProvider(
        id="minimax-cn",
        name="MiniMax (CN / 国内)",
        base_url="https://api.minimaxi.com/v1",
        models=[
            "MiniMax-M2.7", "MiniMax-M2.7-highspeed",
            "MiniMax-M2.5", "MiniMax-M2.5-highspeed",
            "MiniMax-M2.1", "MiniMax-M2.1-highspeed",
        ],
        api_format="openai",
        description="For users in China mainland (国内用户)"
    ),
    "openrouter": LLMProvider(
        id="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        models=[
            # Popular models via OpenRouter
            "anthropic/claude-sonnet-4", "anthropic/claude-opus-4",
            "openai/gpt-4o", "openai/o1",
            "google/gemini-2.5-pro", "google/gemini-2.0-flash-exp:free",
            "deepseek/deepseek-r1", "deepseek/deepseek-chat",
            "meta-llama/llama-3.3-70b-instruct",
        ],
        api_format="openai",
        description="Multi-provider gateway with unified API"
    ),
    "qwen": LLMProvider(
        id="qwen",
        name="Qwen / 通义千问",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=[
            # Qwen 2.5 series (latest)
            "qwen-max", "qwen-max-latest", "qwen-plus", "qwen-plus-latest",
            "qwen-turbo", "qwen-turbo-latest",
            # Long context
            "qwen-long",
            # Qwen 2 legacy
            "qwen2.5-72b-instruct", "qwen2.5-32b-instruct",
            "qwen2-72b-instruct",
        ],
        api_format="openai",
        description="Alibaba's AI with strong multilingual capabilities"
    ),
    "moonshot": LLMProvider(
        id="moonshot",
        name="Moonshot / 月之暗面",
        base_url="https://api.moonshot.cn/v1",
        models=[
            "moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k",
            "moonshot-v1-auto",
        ],
        api_format="openai",
        description="Chinese AI with ultra-long context window (128K)"
    ),
    "custom": LLMProvider(
        id="custom",
        name="Custom / 自定义",
        base_url="",  # User provides
        models=[],    # User provides
        api_format="openai",
        description="Custom OpenAI-compatible endpoint",
        requires_api_key=True
    ),
}


def get_provider(provider_id: str) -> Optional[LLMProvider]:
    """Get a provider configuration by ID."""
    return PRESET_PROVIDERS.get(provider_id)


def get_all_providers() -> List[Dict[str, Any]]:
    """Get all providers as a list of dicts for API response."""
    return [
        {
            "id": p.id,
            "name": p.name,
            "base_url": p.base_url,
            "models": p.models,
            "api_format": p.api_format,
            "description": p.description,
            "requires_api_key": p.requires_api_key,
        }
        for p in PRESET_PROVIDERS.values()
    ]


def get_recommended_model(provider_id: str) -> Optional[str]:
    """Get the recommended (first) model for a provider."""
    provider = get_provider(provider_id)
    if provider and provider.models:
        return provider.models[0]
    return None
