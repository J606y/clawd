"""Deterministic-empty detection and retry budgets.

On an empty completion the loop retries up to 3 times, then walks the fallback chain —
each attempt re-sends the full input. Signaled refusals (``content_filter``, Anthropic
``refusal``, Bedrock guardrails) are already terminal; this handles *unsignaled* empties
(success, zero output tokens, generic finish reason — typical of portal-proxied refusals).

Deterministic-empty detection — two consecutive empty attempts from
the same (model, provider, finish_reason) are treated as deterministic
when usage proves zero output, or when usage is absent and the assembled
responses contain neither content nor reasoning. Remaining retries are
skipped and the loop proceeds straight to the fallback chain (a different
model may behave differently). Mixed evidence or any generated tokens keep
the full retry budget.

Configured via the additive ``agent.empty_response_guard`` section in
``config.yaml`` (resolved once at agent init by ``agent_init``)::

    agent:
      empty_response_guard:
        enabled: true            # false = legacy fixed 3-retry behaviour

Per project policy, no ``HERMES_*`` environment variables are involved —
``.env`` is reserved for credentials; behavioural settings live in
``config.yaml``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_EMPTY_RETRY_BUDGET = 3
DEFAULT_GUARD_ENABLED = True

# Agent-object attribute names. State is scoped to one consecutive empty streak: cleared
# whenever ``_empty_content_retries == 0`` at record time, so every existing counter-reset
# site (turn start, compaction, tool success, fallback activation) is honoured.
_ATTEMPTS_ATTR = "_empty_attempt_history"
_ENABLED_ATTR = "_empty_guard_enabled"


@dataclass(frozen=True)
class EmptyAttempt:
    """One observed empty completion within the current streak."""

    model: str
    provider: str
    finish_reason: str
    usage_present: bool
    zero_output: bool
    observed_generation: bool

    @property
    def signature(self) -> tuple:
        return (self.model, self.provider, self.finish_reason)


def resolve_guard_settings(section: Any) -> bool:
    """Resolve ``agent.empty_response_guard`` into enabled; malformed input → schema default."""
    if not isinstance(section, dict):
        return DEFAULT_GUARD_ENABLED

    enabled = section.get("enabled", DEFAULT_GUARD_ENABLED)
    if isinstance(enabled, str):  # YAML quoting can turn true/false into strings.
        enabled = enabled.strip().lower() not in ("0", "false", "no", "off")
    elif not isinstance(enabled, bool):
        enabled = DEFAULT_GUARD_ENABLED
    return enabled


def guard_enabled(agent: Any) -> bool:
    """Config-resolved enabled flag; agents built without config default to enabled."""
    value = getattr(agent, _ENABLED_ATTR, DEFAULT_GUARD_ENABLED)
    return value if isinstance(value, bool) else DEFAULT_GUARD_ENABLED




def _attempts(agent: Any) -> list[EmptyAttempt]:
    attempts = getattr(agent, _ATTEMPTS_ATTR, None)
    if attempts is None:
        attempts = []
        setattr(agent, _ATTEMPTS_ATTR, attempts)
    return attempts


def _normalized_usage(agent: Any, response: Any, what: str) -> Any:
    """Canonical usage for ``response`` or None (no usage / normalization failed)."""
    raw_usage = getattr(response, "usage", None)
    if not raw_usage:
        return None
    try:
        from agent.usage_pricing import normalize_usage
        return normalize_usage(raw_usage, provider=getattr(agent, "provider", None),
                               api_mode=getattr(agent, "api_mode", None))
    except Exception:
        logger.debug("empty-guard: %s failed", what, exc_info=True)
        return None




def _zero_output(agent: Any, response: Any) -> tuple:
    """Return (usage_present, zero_output) for a response, failing open."""
    canonical = _normalized_usage(agent, response, "usage normalization")
    if canonical is None:
        return (False, False)
    output = getattr(canonical, "output_tokens", None)
    # A present-but-empty usage object (some proxies) normalizes to all zeros;
    # a genuine completion always has input tokens — no evidence, fail open.
    if output is None or getattr(canonical, "prompt_tokens", 0) <= 0:
        return (False, False)
    # Reasoning tokens are real generation: a reasoning-only response is NOT
    # a deterministic empty (the prefill-continuation path owns that case).
    reasoning = getattr(canonical, "reasoning_tokens", 0) or 0
    return (True, (output + reasoning) == 0)


def record_empty_attempt(
    agent: Any,
    *,
    finish_reason: str,
    response: Any,
    observed_generation: bool = True,
) -> None:
    """Record one empty completion in the current streak.

    Call BEFORE ``_empty_content_retries`` is incremented: a counter of 0 marks a new
    streak and clears prior history."""
    attempts = _attempts(agent)
    if getattr(agent, "_empty_content_retries", 0) == 0:
        attempts.clear()

    usage_present, zero_output = _zero_output(agent, response)
    attempts.append(
        EmptyAttempt(
            model=str(getattr(agent, "model", "") or ""),
            provider=str(getattr(agent, "provider", "") or ""),
            finish_reason=str(finish_reason or ""),
            usage_present=usage_present,
            zero_output=zero_output,
            observed_generation=bool(observed_generation),
        )
    )



def deterministic_empty(agent: Any) -> bool:
    """True when the current streak looks deterministic.

    Requires >= 2 consecutive attempts with an identical (model, provider,
    finish_reason) signature. Usage-backed attempts must all prove zero output.
    Usage-absent attempts must all have no observed content or reasoning. Mixed
    evidence fails open so ambiguous transients keep their retries.
    """
    if not guard_enabled(agent):
        return False
    attempts = getattr(agent, _ATTEMPTS_ATTR, None) or []
    if len(attempts) < 2:
        return False
    first = attempts[0]
    same_signature = all(a.signature == first.signature for a in attempts)
    usage_proves_empty = all(a.usage_present and a.zero_output for a in attempts)
    response_proves_empty = all(
        not a.usage_present and not a.observed_generation for a in attempts
    )
    return same_signature and (usage_proves_empty or response_proves_empty)


def empty_retry_budget(agent: Any, response: Any) -> int:
    """Empty-retry budget for the current streak (always the default of 3)."""
    return DEFAULT_EMPTY_RETRY_BUDGET


