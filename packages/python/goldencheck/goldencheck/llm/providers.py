"""LLM provider wrappers for Anthropic and OpenAI."""
from __future__ import annotations
import logging
import os
from goldencheck.llm.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
}


def check_llm_available(provider: str) -> None:
    """Check that LLM dependencies and API key are available. Raises on failure."""
    if provider == "anthropic":
        try:
            import anthropic  # noqa: F401
        except ImportError:
            raise SystemExit("LLM boost requires extra dependencies. Install with: pip install goldencheck[llm]")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("LLM boost requires ANTHROPIC_API_KEY environment variable.")
    elif provider == "openai":
        try:
            import openai  # noqa: F401
        except ImportError:
            raise SystemExit("LLM boost requires extra dependencies. Install with: pip install goldencheck[llm]")
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("LLM boost requires OPENAI_API_KEY environment variable.")
    else:
        raise SystemExit(f"Unknown LLM provider: {provider}. Use 'anthropic' or 'openai'.")


def call_llm(provider: str, user_prompt: str) -> tuple[str, int, int]:
    """Send prompt to LLM and return (text, input_tokens, output_tokens)."""
    model = os.environ.get("GOLDENCHECK_LLM_MODEL", DEFAULT_MODELS.get(provider, ""))

    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text, response.usage.input_tokens, response.usage.output_tokens

    elif provider == "openai":
        import openai
        client = openai.OpenAI()
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content, response.usage.prompt_tokens, response.usage.completion_tokens

    raise ValueError(f"Unknown provider: {provider}")
