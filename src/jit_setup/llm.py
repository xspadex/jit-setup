"""LLM calling — OpenAI-compatible streaming with tool use. Zero dependencies."""

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass

from . import __version__

# ── Community LLM (zero prerequisites) ───────────────────────────────────────

# TODO: Replace with your own Worker URL after deployment
COMMUNITY_LLM_URL = "https://jit-llm-proxy.jitsetup.workers.dev"
COMMUNITY_MODEL = "Pro/moonshotai/Kimi-K2-Instruct"
COMMUNITY_CHAT_ENDPOINT = "/v1/chat/completions"

DEFAULT_MAX_TOKENS = 2048

# Signing key — rotated with each CLI release. Not a secret (open source),
# but adds friction for casual abuse. The real protection is payload
# validation on the Worker side.
_SIGN_KEY = b"jit-setup-v0.1.0-public-signing-key"


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


class RateLimitError(Exception):
    """Raised when the free API quota is exhausted."""
    def __init__(self, message: str, remaining: int = 0):
        self.remaining = remaining
        super().__init__(message)


def get_llm_config(user_config: dict = None) -> dict:
    """Return LLM connection params. User config overrides community defaults."""
    if user_config and user_config.get("llm", {}).get("api_key"):
        llm = user_config["llm"]
        return {
            "base_url": llm.get("base_url", "https://api.openai.com/v1"),
            "api_key": llm["api_key"],
            "model": llm.get("model", "gpt-4o"),
            "chat_endpoint": llm.get("chat_endpoint", "/chat/completions"),
            "is_community": False,
        }
    return {
        "base_url": COMMUNITY_LLM_URL,
        "api_key": "community",
        "model": COMMUNITY_MODEL,
        "chat_endpoint": COMMUNITY_CHAT_ENDPOINT,
        "is_community": True,
    }


def _make_signature(device_id: str, timestamp: int, body_bytes: bytes) -> str:
    """HMAC-SHA256 request signature.

    Sign = HMAC(key, "{device_id}.{timestamp}.{sha256(body)}")

    The Worker verifies this to filter out requests not from the jit CLI.
    Not cryptographically secure against determined attackers (key is in source),
    but blocks casual curl/script abuse.
    """
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    message = f"{device_id}.{timestamp}.{body_hash}"
    return hmac.new(_SIGN_KEY, message.encode(), hashlib.sha256).hexdigest()


def call_llm(
    messages: list,
    system_prompt: str,
    tools: list = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    chat_endpoint: str = "/chat/completions",
    stream_callback=None,
    device_id: str = "",
    is_community: bool = False,
) -> tuple:
    """Call OpenAI-compatible API. Returns (text, tool_calls, usage).

    For community API: adds device fingerprint, request signature,
    and handles 429 rate limit responses.
    """
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("API key not set")

    api_messages = [{"role": "system", "content": system_prompt}] + messages
    body: dict = {
        "model": model,
        "max_tokens": min(max_tokens, DEFAULT_MAX_TOKENS),
        "messages": api_messages,
        "stream": True,
    }
    if tools:
        body["tools"] = tools

    data = json.dumps(body).encode()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": f"jit-setup/{__version__}",
    }

    # Community API: add signing headers
    if is_community and device_id:
        ts = int(time.time())
        sig = _make_signature(device_id, ts, data)
        headers.update({
            "X-Jit-Device": device_id,
            "X-Jit-Timestamp": str(ts),
            "X-Jit-Signature": sig,
            "X-Jit-Version": __version__,
        })

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{chat_endpoint}",
        data=data,
        headers=headers,
    )

    text_parts: list[str] = []
    tool_calls_map: dict = {}   # index -> {id, name, arguments}
    usage = {"input_tokens": 0, "output_tokens": 0}

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            buffer = ""
            try:
                for chunk in iter(lambda: resp.read(1024), b""):
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload == "[DONE]":
                            break
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            continue

                        choices = event.get("choices", [])
                        if not choices:
                            u = event.get("usage", {})
                            if u:
                                usage["input_tokens"] = u.get("prompt_tokens", 0)
                                usage["output_tokens"] = u.get("completion_tokens", 0)
                            continue

                        delta = choices[0].get("delta", {})

                        # Text
                        if delta.get("content"):
                            text_parts.append(delta["content"])
                            if stream_callback:
                                stream_callback(delta["content"])

                        # Tool calls
                        for tc_delta in delta.get("tool_calls", []):
                            idx = tc_delta.get("index", 0)
                            if idx not in tool_calls_map:
                                tool_calls_map[idx] = {
                                    "id": tc_delta.get("id", ""),
                                    "name": tc_delta.get("function", {}).get("name", ""),
                                    "arguments": "",
                                }
                            if tc_delta.get("id"):
                                tool_calls_map[idx]["id"] = tc_delta["id"]
                            fn = tc_delta.get("function", {})
                            if fn.get("name"):
                                tool_calls_map[idx]["name"] = fn["name"]
                            if fn.get("arguments"):
                                tool_calls_map[idx]["arguments"] += fn["arguments"]
            except (ConnectionError, OSError):
                pass  # Server closed after stream end — normal for SSE

    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""

        # Rate limit hit
        if e.code == 429:
            try:
                detail = json.loads(err_body)
                msg = detail.get("error", "Daily free quota exhausted.")
            except json.JSONDecodeError:
                msg = "Daily free quota exhausted."
            raise RateLimitError(
                f"{msg}\n"
                f"  Set your own API key for unlimited use:\n"
                f"  jit config --llm-provider siliconflow --llm-key YOUR_KEY"
            )

        raise RuntimeError(f"LLM API error ({e.code}): {err_body[:500]}")
    except urllib.error.URLError as e:
        if text_parts or tool_calls_map:
            pass  # partial response, return what we have
        else:
            raise RuntimeError(f"LLM connection error: {e}")

    # Parse tool calls
    tool_calls = []
    for idx in sorted(tool_calls_map.keys()):
        tc = tool_calls_map[idx]
        try:
            inp = json.loads(tc["arguments"]) if tc["arguments"] else {}
        except json.JSONDecodeError:
            inp = {}
        tool_calls.append(ToolCall(id=tc["id"], name=tc["name"], input=inp))

    return "".join(text_parts), tool_calls, usage


def to_openai_messages(messages: list) -> list:
    """Convert internal message format (with tool_use/tool_result blocks)
    to OpenAI-compatible message format."""
    out = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Simple text message
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        # Complex content blocks (assistant with tool_use, user with tool_result)
        if role == "assistant":
            text_parts = []
            tool_calls = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        tool_calls.append({
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        })
            oai_msg: dict = {"role": "assistant"}
            if text_parts:
                oai_msg["content"] = "".join(text_parts)
            else:
                oai_msg["content"] = None
            if tool_calls:
                oai_msg["tool_calls"] = tool_calls
            out.append(oai_msg)

        elif role == "user":
            # Check if it's tool results
            if isinstance(content, list) and content and isinstance(content[0], dict) \
                    and content[0].get("type") == "tool_result":
                for block in content:
                    out.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": block.get("content", ""),
                    })
            else:
                # Mixed content — extract text
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block["text"])
                    elif isinstance(block, str):
                        parts.append(block)
                out.append({"role": "user", "content": "".join(parts) or str(content)})

    return out
