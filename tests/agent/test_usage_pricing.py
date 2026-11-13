"""Tests for agent/usage_pricing.py (token metering only).

Pricing was removed 2026-11-13: no price snapshot table, no cost
estimation, no billing-route resolution. This file keeps only the
metering tests (CanonicalUsage / normalize_usage); all pricing tests
were deleted with the feature.
"""

from types import SimpleNamespace

import pytest

from agent.usage_pricing import CanonicalUsage, normalize_usage
def test_normalize_usage_reads_deepseek_native_cache_hit_tokens():
    """DeepSeek's native API (api.deepseek.com) reports context-cache hits as
    top-level prompt_cache_hit_tokens / prompt_cache_miss_tokens (with
    prompt_tokens = hit + miss), not OpenAI's nested
    prompt_tokens_details.cached_tokens. Before this fix, direct DeepSeek
    sessions always normalized to cache_read_tokens=0 — cache hits were
    invisible in accounting and billed at the full input rate (#61871)."""
    usage = SimpleNamespace(
        prompt_tokens=2000,
        completion_tokens=400,
        prompt_cache_hit_tokens=1500,
        prompt_cache_miss_tokens=500,
    )

    normalized = normalize_usage(usage, provider="deepseek", api_mode="chat_completions")

    assert normalized.cache_read_tokens == 1500
    # prompt_tokens includes cached; input = 2000 - 1500 = the miss bucket
    assert normalized.input_tokens == 500
    assert normalized.output_tokens == 400

def test_normalize_usage_openai_reads_top_level_anthropic_cache_fields():
    """Some OpenAI-compatible proxies (OpenRouter, Vercel AI Gateway, Cline) expose
    Anthropic-style cache token counts at the top level of the usage object when
    routing Claude models, instead of nesting them in prompt_tokens_details.

    Regression guard for the bug fixed in cline/cline#10266 — before this fix,
    the chat-completions branch of normalize_usage() only read
    prompt_tokens_details.cache_write_tokens and completely missed the
    cache_creation_input_tokens case, so cache writes showed as 0 and reflected
    inputTokens were overstated by the cache-write amount.
    """
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=200,
        prompt_tokens_details=SimpleNamespace(cached_tokens=500),
        cache_creation_input_tokens=300,
    )

    normalized = normalize_usage(usage, provider="openrouter", api_mode="chat_completions")

    # Expected: cache read from prompt_tokens_details.cached_tokens (preferred),
    # cache write from top-level cache_creation_input_tokens (fallback).
    assert normalized.cache_read_tokens == 500
    assert normalized.cache_write_tokens == 300
    # input_tokens = prompt_total - cache_read - cache_write = 1000 - 500 - 300 = 200
    assert normalized.input_tokens == 200
    assert normalized.output_tokens == 200

def test_normalize_usage_reads_kimi_top_level_cached_tokens():
    """Kimi/Moonshot's native API reports context-cache hits as a top-level
    usage.cached_tokens, not OpenAI's nested
    prompt_tokens_details.cached_tokens and not DeepSeek's
    prompt_cache_hit_tokens. Neither existing fallback matches that name, so
    direct Kimi sessions normalized to cache_read_tokens=0 — the hits were
    invisible in accounting and billed at the full input rate (#65722)."""
    usage = SimpleNamespace(
        prompt_tokens=3000,
        completion_tokens=250,
        cached_tokens=1800,
    )

    normalized = normalize_usage(usage, provider="kimi", api_mode="chat_completions")

    assert normalized.cache_read_tokens == 1800
    # prompt_tokens includes the cached prefix: 3000 - 1800 = fresh input
    assert normalized.input_tokens == 1200
    assert normalized.output_tokens == 250

def test_kimi_fallback_does_not_override_the_nested_openai_shape():
    """A provider that reports BOTH shapes must keep the nested value.

    The new branch is last in the chain, so it only fills a genuine zero.
    """
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=100,
        prompt_tokens_details=SimpleNamespace(cached_tokens=400),
        cached_tokens=999,  # must be ignored
    )

    normalized = normalize_usage(usage, provider="kimi", api_mode="chat_completions")

    assert normalized.cache_read_tokens == 400

def test_kimi_fallback_does_not_override_deepseek_hit_tokens():
    usage = SimpleNamespace(
        prompt_tokens=2000,
        completion_tokens=100,
        prompt_cache_hit_tokens=1500,
        cached_tokens=999,  # must be ignored
    )

    normalized = normalize_usage(usage, provider="deepseek", api_mode="chat_completions")

    assert normalized.cache_read_tokens == 1500

def test_usage_without_any_cache_fields_still_normalizes():
    usage = SimpleNamespace(prompt_tokens=500, completion_tokens=50)

    normalized = normalize_usage(usage, provider="kimi", api_mode="chat_completions")

    assert normalized.cache_read_tokens == 0
    assert normalized.input_tokens == 500

def test_normalize_usage_handles_dict_shaped_usage():
    """Regression test for #74314: when the Responses API returns usage as a
    plain dict (e.g. from a middleware/proxy that deserialises JSON to dict
    instead of a typed SDK object), normalize_usage() must read the same
    token counts as it would from an attribute-style object.

    Before this fix, getattr() on a dict silently returned 0 for every field,
    so token counts and cost appeared as zero for dict-shaped usage.
    """
    # Same payload as both a dict and a SimpleNamespace
    payload = {
        "input_tokens": 100,
        "output_tokens": 20,
        "input_tokens_details": {"cached_tokens": 60, "cache_creation_tokens": 10},
    }
    ns = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        input_tokens_details=SimpleNamespace(cached_tokens=60, cache_creation_tokens=10),
    )

    dict_result = normalize_usage(payload, api_mode="codex_responses")
    ns_result = normalize_usage(ns, api_mode="codex_responses")

    assert dict_result.input_tokens == ns_result.input_tokens, f"input_tokens: dict={dict_result.input_tokens} vs ns={ns_result.input_tokens}"
    assert dict_result.output_tokens == ns_result.output_tokens, f"output_tokens: dict={dict_result.output_tokens} vs ns={ns_result.output_tokens}"
    assert dict_result.cache_read_tokens == ns_result.cache_read_tokens, f"cache_read: dict={dict_result.cache_read_tokens} vs ns={ns_result.cache_read_tokens}"
    assert dict_result.cache_write_tokens == ns_result.cache_write_tokens, f"cache_write: dict={dict_result.cache_write_tokens} vs ns={ns_result.cache_write_tokens}"
    # Sanity: values must be non-zero (the whole point of the bug)
    assert dict_result.input_tokens > 0
    assert dict_result.cache_read_tokens > 0

def test_normalize_usage_handles_dict_openai_chat_completions():
    """Dict-shaped usage must also work in the default (OpenAI chat-completions)
    branch, not just the codex_responses branch.
    """
    payload = {
        "prompt_tokens": 500,
        "completion_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 200},
        "completion_tokens_details": {"reasoning_tokens": 30},
    }

    result = normalize_usage(payload, api_mode="chat_completions")

    assert result.output_tokens == 100
    assert result.cache_read_tokens == 200
    assert result.input_tokens == 500 - 200  # prompt_total - cache_read
    assert result.reasoning_tokens == 30

def test_normalize_usage_openai_reads_nested_cache_creation_tokens():
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=200,
        prompt_tokens_details=SimpleNamespace(
            cached_tokens=100,
            cache_creation_input_tokens=300,
        ),
    )

    normalized = normalize_usage(usage, provider="openrouter", api_mode="chat_completions")

    assert normalized.cache_read_tokens == 100
    assert normalized.cache_write_tokens == 300
    assert normalized.input_tokens == 600

def test_normalize_usage_openai_reads_mapping_cache_creation_tokens():
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "prompt_tokens_details": {"cache_creation_input_tokens": 300},
    }

    normalized = normalize_usage(usage, provider="openrouter", api_mode="chat_completions")

    assert normalized.cache_write_tokens == 300
    assert normalized.input_tokens == 700

def test_normalize_usage_openai_prefers_nested_cache_write_tokens():
    usage = SimpleNamespace(
        prompt_tokens=1000,
        prompt_tokens_details=SimpleNamespace(
            cache_write_tokens=200,
            cache_creation_input_tokens=300,
        ),
        cache_creation_input_tokens=400,
        cache_write_tokens=500,
    )

    normalized = normalize_usage(usage, provider="openrouter", api_mode="chat_completions")

    assert normalized.cache_write_tokens == 200

def test_normalize_usage_mapping_preserves_reasoning_tokens():
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 40},
        "completion_tokens_details": {"reasoning_tokens": 12},
    }

    normalized = normalize_usage(usage, provider="openrouter", api_mode="chat_completions")

    assert normalized.reasoning_tokens == 12

def test_normalize_usage_mapping_anthropic_fields():
    usage = {
        "input_tokens": 80,
        "output_tokens": 20,
        "cache_read_input_tokens": 50,
        "cache_creation_input_tokens": 10,
        "output_tokens_details": {"reasoning_tokens": 7},
    }

    normalized = normalize_usage(usage, provider="anthropic", api_mode="anthropic_messages")

    assert normalized.cache_read_tokens == 50
    assert normalized.cache_write_tokens == 10
    assert normalized.reasoning_tokens == 7

def test_normalize_usage_mapping_codex_fields():
    usage = {
        "input_tokens": 100,
        "output_tokens": 20,
        "input_tokens_details": {
            "cached_tokens": 60,
            "cache_creation_tokens": 10,
        },
        "output_tokens_details": {"reasoning_tokens": 5},
    }

    normalized = normalize_usage(usage, provider="openai-codex", api_mode="codex_responses")

    assert normalized.input_tokens == 30
    assert normalized.cache_read_tokens == 60
    assert normalized.cache_write_tokens == 10
    assert normalized.reasoning_tokens == 5

def test_normalize_usage_clamps_negative_counters():
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=-5,
        prompt_tokens_details=SimpleNamespace(
            cached_tokens=-10,
            cache_write_tokens=-20,
        ),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=-3),
    )

    normalized = normalize_usage(usage, provider="openrouter", api_mode="chat_completions")

    assert normalized.input_tokens == 100
    assert normalized.output_tokens == 0
    assert normalized.cache_read_tokens == 0
    assert normalized.cache_write_tokens == 0
    assert normalized.reasoning_tokens == 0

def test_normalize_usage_clamps_inconsistent_cache_total():
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 10,
        "prompt_tokens_details": {
            "cached_tokens": 80,
            "cache_creation_input_tokens": 50,
        },
    }

    normalized = normalize_usage(usage, provider="openrouter", api_mode="chat_completions")

    assert normalized.input_tokens == 0
    assert normalized.prompt_tokens == 130

def test_normalize_usage_codex_responses_reads_cache_write_tokens():
    """GPT-5.6+ explicit prompt caching reports cache writes as
    input_tokens_details.cache_write_tokens (billed at 1.25x), per OpenAI's
    documented Responses API schema. Before this fix, the codex_responses
    branch only read the undocumented `cache_creation_tokens` name and always
    normalized cache writes to 0."""
    usage = SimpleNamespace(
        input_tokens=2006,
        output_tokens=400,
        input_tokens_details=SimpleNamespace(cached_tokens=1920, cache_write_tokens=50),
    )

    normalized = normalize_usage(usage, provider="openai", api_mode="codex_responses")

    assert normalized.cache_read_tokens == 1920
    assert normalized.cache_write_tokens == 50
    assert normalized.input_tokens == 2006 - 1920 - 50

def test_normalize_usage_codex_responses_falls_back_to_cache_creation_tokens():
    """If cache_write_tokens is absent, fall back to the legacy
    cache_creation_tokens name rather than reporting 0."""
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=100,
        input_tokens_details=SimpleNamespace(cached_tokens=200, cache_creation_tokens=80),
    )

    normalized = normalize_usage(usage, provider="openai", api_mode="codex_responses")

    assert normalized.cache_write_tokens == 80

def test_normalize_usage_reads_qwen_flat_cached_tokens():
    """Some Alibaba/Qwen regional endpoints report cache reads as a flat
    `usage.cached_tokens` field with no `prompt_tokens_details` wrapper at
    all. Before this fix, those responses fell through every branch and
    normalized to cache_read_tokens=0, undercounting cost."""
    usage = SimpleNamespace(
        prompt_tokens=2000,
        completion_tokens=300,
        cached_tokens=1200,
    )

    normalized = normalize_usage(usage, provider="qwen", api_mode="chat_completions")

    assert normalized.cache_read_tokens == 1200
    assert normalized.input_tokens == 800

def test_normalize_usage_nested_details_win_over_qwen_flat_top_level():
    """When both shapes are present, the nested OpenAI-style value wins and
    the flat Qwen field is not double-read."""
    usage = SimpleNamespace(
        prompt_tokens=2000,
        completion_tokens=100,
        prompt_tokens_details=SimpleNamespace(cached_tokens=900),
        cached_tokens=1200,
    )

    normalized = normalize_usage(usage, provider="qwen", api_mode="chat_completions")

    assert normalized.cache_read_tokens == 900
    assert normalized.input_tokens == 1100

def _anthropic_usage(speed=None):
    """Canonical usage from an Anthropic-shaped usage payload; ``speed`` rides ``raw_usage``."""
    payload = {"input_tokens": 100_000, "output_tokens": 10_000, "cache_read_input_tokens": 200_000, "cache_creation_input_tokens": 50_000}
    if speed:
        payload["speed"] = speed
    return normalize_usage(payload, provider="anthropic", api_mode="anthropic_messages")

