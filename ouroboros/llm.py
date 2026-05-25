"""
Ouroboros — LLM client.

The only module that communicates with the LLM API (OpenRouter).
Contract: chat(), default_model(), available_models(), add_usage().
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

DEFAULT_LIGHT_MODEL = "google/gemini-3-pro-preview"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def llm_base_url() -> str:
    """Base URL for the LLM API.

    Defaults to OpenRouter. Set OUROBOROS_LLM_BASE_URL to point at any
    OpenAI-compatible /chat/completions endpoint — e.g. a self-hosted
    model server: OUROBOROS_LLM_BASE_URL=http://127.0.0.1:8090
    """
    url = (os.environ.get("OUROBOROS_LLM_BASE_URL") or "").strip()
    return (url or DEFAULT_BASE_URL).rstrip("/")


def is_openrouter(base_url: Optional[str] = None) -> bool:
    """True when the endpoint is OpenRouter.

    Gates OpenRouter/Anthropic-only request features (reasoning effort,
    provider pinning, prompt-cache tags) that a generic OpenAI-compatible
    endpoint would reject.
    """
    url = base_url if base_url is not None else llm_base_url()
    return "openrouter.ai" in str(url).lower()


def llm_api_key() -> str:
    """API key for the LLM endpoint.

    OUROBOROS_LLM_API_KEY wins; falls back to OPENROUTER_API_KEY. A custom
    endpoint that needs no key (e.g. a local model server) can leave both unset.
    """
    return (
        os.environ.get("OUROBOROS_LLM_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or ""
    ).strip()


def llm_extra_headers() -> Dict[str, str]:
    """Extra HTTP headers sent with every LLM request.

    OUROBOROS_LLM_HEADERS (a JSON object) overrides everything — used for
    custom endpoints that require extra headers, e.g.:
        {"X-Custom-Header": "value", "User-Agent": "custom-client"}
    Falls back to OpenRouter ranking headers when talking to OpenRouter.
    """
    raw = (os.environ.get("OUROBOROS_LLM_HEADERS") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
            log.warning("OUROBOROS_LLM_HEADERS must be a JSON object — ignoring")
        except (ValueError, TypeError):
            log.warning("OUROBOROS_LLM_HEADERS is not valid JSON — ignoring")
    if is_openrouter():
        return {
            "HTTP-Referer": "https://colab.research.google.com/",
            "X-Title": "Ouroboros",
        }
    return {}


def normalize_reasoning_effort(value: str, default: str = "medium") -> str:
    allowed = {"none", "minimal", "low", "medium", "high", "xhigh"}
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def reasoning_rank(value: str) -> int:
    order = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5}
    return int(order.get(str(value or "").strip().lower(), 3))


def add_usage(total: Dict[str, Any], usage: Dict[str, Any]) -> None:
    """Accumulate usage from one LLM call into a running total."""
    for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "cache_write_tokens"):
        total[k] = int(total.get(k) or 0) + int(usage.get(k) or 0)
    if usage.get("cost"):
        total["cost"] = float(total.get("cost") or 0) + float(usage["cost"])


def fetch_openrouter_pricing() -> Dict[str, Tuple[float, float, float]]:
    """
    Fetch current pricing from OpenRouter API.

    Returns dict of {model_id: (input_per_1m, cached_per_1m, output_per_1m)}.
    Returns empty dict on failure.
    """
    import logging
    log = logging.getLogger("ouroboros.llm")

    # Custom (non-OpenRouter) endpoints have no OpenRouter pricing catalogue.
    if not is_openrouter():
        return {}

    try:
        import requests
    except ImportError:
        log.warning("requests not installed, cannot fetch pricing")
        return {}

    try:
        url = "https://openrouter.ai/api/v1/models"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()

        data = resp.json()
        models = data.get("data", [])

        # Prefixes we care about
        prefixes = ("anthropic/", "openai/", "google/", "meta-llama/", "x-ai/", "qwen/")

        pricing_dict = {}
        for model in models:
            model_id = model.get("id", "")
            if not model_id.startswith(prefixes):
                continue

            pricing = model.get("pricing", {})
            if not pricing or not pricing.get("prompt"):
                continue

            # OpenRouter pricing is in dollars per token (raw values)
            raw_prompt = float(pricing.get("prompt", 0))
            raw_completion = float(pricing.get("completion", 0))
            raw_cached_str = pricing.get("input_cache_read")
            raw_cached = float(raw_cached_str) if raw_cached_str else None

            # Convert to per-million tokens
            prompt_price = round(raw_prompt * 1_000_000, 4)
            completion_price = round(raw_completion * 1_000_000, 4)
            if raw_cached is not None:
                cached_price = round(raw_cached * 1_000_000, 4)
            else:
                cached_price = round(prompt_price * 0.1, 4)  # fallback: 10% of prompt

            # Sanity check: skip obviously wrong prices
            if prompt_price > 1000 or completion_price > 1000:
                log.warning(f"Skipping {model_id}: prices seem wrong (prompt={prompt_price}, completion={completion_price})")
                continue

            pricing_dict[model_id] = (prompt_price, cached_price, completion_price)

        log.info(f"Fetched pricing for {len(pricing_dict)} models from OpenRouter")
        return pricing_dict

    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning(f"Failed to fetch OpenRouter pricing: {e}")
        return {}


class LLMClient:
    """OpenAI-compatible API wrapper. All LLM calls go through this class.

    Defaults to OpenRouter; talks to any OpenAI-compatible /chat/completions
    endpoint when OUROBOROS_LLM_BASE_URL is set (e.g. a self-hosted model).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self._base_url = base_url or llm_base_url()
        self._is_openrouter = is_openrouter(self._base_url)
        self._api_key = api_key if api_key is not None else llm_api_key()
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=self._base_url,
                # The OpenAI SDK requires a non-empty key; a custom endpoint
                # that needs no auth simply ignores this placeholder.
                api_key=self._api_key or "ouroboros-local",
                default_headers=llm_extra_headers() or None,
            )
        return self._client

    def _fetch_generation_cost(self, generation_id: str) -> Optional[float]:
        """Fetch cost from OpenRouter Generation API as fallback."""
        try:
            import requests
            url = f"{self._base_url.rstrip('/')}/generation?id={generation_id}"
            resp = requests.get(url, headers={"Authorization": f"Bearer {self._api_key}"}, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("data") or {}
                cost = data.get("total_cost") or data.get("usage", {}).get("cost")
                if cost is not None:
                    return float(cost)
            # Generation might not be ready yet — retry once after short delay
            time.sleep(0.5)
            resp = requests.get(url, headers={"Authorization": f"Bearer {self._api_key}"}, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("data") or {}
                cost = data.get("total_cost") or data.get("usage", {}).get("cost")
                if cost is not None:
                    return float(cost)
        except Exception:
            log.debug("Failed to fetch generation cost from OpenRouter", exc_info=True)
            pass
        return None

    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        reasoning_effort: str = "medium",
        max_tokens: int = 16384,
        tool_choice: str = "auto",
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Single LLM call. Returns: (response_message_dict, usage_dict with cost)."""
        client = self._get_client()
        effort = normalize_reasoning_effort(reasoning_effort)

        # Optional output-token cap for endpoints with smaller limits than
        # OpenRouter (OUROBOROS_LLM_MAX_TOKENS).
        cap = os.environ.get("OUROBOROS_LLM_MAX_TOKENS")
        if cap:
            try:
                max_tokens = min(int(max_tokens), int(cap))
            except (ValueError, TypeError):
                pass

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        # Reasoning effort and Anthropic provider pinning are OpenRouter-only
        # request features — a generic OpenAI-compatible endpoint may reject
        # these unknown body fields, so only send them to OpenRouter.
        if self._is_openrouter:
            extra_body: Dict[str, Any] = {
                "reasoning": {"effort": effort, "exclude": True},
            }
            if model.startswith("anthropic/"):
                extra_body["provider"] = {
                    "order": ["Anthropic"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                }
            kwargs["extra_body"] = extra_body

        if tools:
            tools_payload = [t for t in tools]  # shallow copy
            # Add cache_control to last tool for Anthropic prompt caching
            # (caches all tool schemas — they never change between calls).
            # OpenRouter-only: a generic endpoint may reject the extra field.
            if self._is_openrouter and tools_payload:
                last_tool = {**tools_payload[-1]}  # copy last tool
                last_tool["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
                tools_payload[-1] = last_tool
            kwargs["tools"] = tools_payload
            kwargs["tool_choice"] = tool_choice

        resp = client.chat.completions.create(**kwargs)
        resp_dict = resp.model_dump()
        usage = resp_dict.get("usage") or {}
        choices = resp_dict.get("choices") or [{}]
        msg = (choices[0] if choices else {}).get("message") or {}

        # Extract cached_tokens from prompt_tokens_details if available
        if not usage.get("cached_tokens"):
            prompt_details = usage.get("prompt_tokens_details") or {}
            if isinstance(prompt_details, dict) and prompt_details.get("cached_tokens"):
                usage["cached_tokens"] = int(prompt_details["cached_tokens"])

        # Extract cache_write_tokens from prompt_tokens_details if available
        # OpenRouter: "cache_write_tokens"
        # Native Anthropic: "cache_creation_tokens" or "cache_creation_input_tokens"
        if not usage.get("cache_write_tokens"):
            prompt_details_for_write = usage.get("prompt_tokens_details") or {}
            if isinstance(prompt_details_for_write, dict):
                cache_write = (prompt_details_for_write.get("cache_write_tokens")
                              or prompt_details_for_write.get("cache_creation_tokens")
                              or prompt_details_for_write.get("cache_creation_input_tokens"))
                if cache_write:
                    usage["cache_write_tokens"] = int(cache_write)

        # Ensure cost is present in usage (OpenRouter includes it, but fallback
        # if missing). The Generation cost API is OpenRouter-only — skip it for
        # custom endpoints, which simply report no cost.
        if not usage.get("cost") and self._is_openrouter:
            gen_id = resp_dict.get("id") or ""
            if gen_id:
                cost = self._fetch_generation_cost(gen_id)
                if cost is not None:
                    usage["cost"] = cost

        return msg, usage

    def vision_query(
        self,
        prompt: str,
        images: List[Dict[str, Any]],
        model: str = "anthropic/claude-sonnet-4.6",
        max_tokens: int = 1024,
        reasoning_effort: str = "low",
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Send a vision query to an LLM. Lightweight — no tools, no loop.

        Args:
            prompt: Text instruction for the model
            images: List of image dicts. Each dict must have either:
                - {"url": "https://..."} — for URL images
                - {"base64": "<b64>", "mime": "image/png"} — for base64 images
            model: VLM-capable model ID
            max_tokens: Max response tokens
            reasoning_effort: Effort level

        Returns:
            (text_response, usage_dict)
        """
        # Build multipart content
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            if "url" in img:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": img["url"]},
                })
            elif "base64" in img:
                mime = img.get("mime", "image/png")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{img['base64']}"},
                })
            else:
                log.warning("vision_query: skipping image with unknown format: %s", list(img.keys()))

        messages = [{"role": "user", "content": content}]
        response_msg, usage = self.chat(
            messages=messages,
            model=model,
            tools=None,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
        )
        text = response_msg.get("content") or ""
        return text, usage

    def default_model(self) -> str:
        """Return the single default model from env. LLM switches via tool if needed."""
        return os.environ.get("OUROBOROS_MODEL", "anthropic/claude-sonnet-4.6")

    def available_models(self) -> List[str]:
        """Return list of available models from env (for switch_model tool schema)."""
        main = os.environ.get("OUROBOROS_MODEL", "anthropic/claude-sonnet-4.6")
        code = os.environ.get("OUROBOROS_MODEL_CODE", "")
        light = os.environ.get("OUROBOROS_MODEL_LIGHT", "")
        models = [main]
        if code and code != main:
            models.append(code)
        if light and light != main and light != code:
            models.append(light)
        return models

