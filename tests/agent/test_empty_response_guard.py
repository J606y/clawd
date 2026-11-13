"""Tests for agent/empty_response_guard.py (NS-503).

The guard exists to stop the empty-retry loop re-billing large inputs for
deterministic empties (unsignaled provider refusals with zero output
tokens) while never tightening behaviour on ambiguous evidence.

Fail-open contract under test:
- Missing usage + no observed generation -> deterministic after two attempts.
- Missing usage + observed reasoning -> never deterministic.
- Any generated tokens (output or reasoning) -> never deterministic.
- Different model/provider/finish_reason across attempts -> not deterministic.
- Guard disabled via config (agent.empty_response_guard.enabled: false) ->
  everything falls back to defaults.
"""

from types import SimpleNamespace

from agent import empty_response_guard as guard


def _agent(**overrides):
    base = dict(
        model="anthropic/claude-fable-5",
        provider="nous",
        api_mode="chat_completions",
        base_url=None,
        api_key=None,
        _empty_content_retries=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _response(prompt_tokens=25_900, completion_tokens=0, usage_present=True):
    if not usage_present:
        return SimpleNamespace(usage=None)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(usage=usage)


def _record_streak(
    agent, responses, finish_reasons=None, observed_generations=None
):
    """Record attempts the way the loop does: record, then increment."""
    finish_reasons = finish_reasons or ["stop"] * len(responses)
    observed_generations = observed_generations or [False] * len(responses)
    for resp, reason, observed_generation in zip(
        responses, finish_reasons, observed_generations
    ):
        guard.record_empty_attempt(
            agent,
            finish_reason=reason,
            response=resp,
            observed_generation=observed_generation,
        )
        agent._empty_content_retries += 1


class TestDeterministicEmpty:
    def test_two_zero_output_attempts_same_signature_is_deterministic(self):
        agent = _agent()
        _record_streak(agent, [_response(), _response()])
        assert guard.deterministic_empty(agent) is True

    def test_single_attempt_is_never_deterministic(self):
        """One empty could be a transient blip — retry #1 must always run."""
        agent = _agent()
        _record_streak(agent, [_response()])
        assert guard.deterministic_empty(agent) is False

    def test_missing_usage_without_observed_generation_is_deterministic(self):
        agent = _agent()
        _record_streak(
            agent,
            [_response(usage_present=False), _response(usage_present=False)],
        )
        assert guard.deterministic_empty(agent) is True

    def test_missing_usage_with_observed_reasoning_fails_open(self):
        agent = _agent()
        _record_streak(
            agent,
            [_response(usage_present=False), _response(usage_present=False)],
            observed_generations=[True, True],
        )
        assert guard.deterministic_empty(agent) is False

    def test_mixed_usage_presence_fails_open(self):
        agent = _agent()
        _record_streak(agent, [_response(), _response(usage_present=False)])
        assert guard.deterministic_empty(agent) is False

    def test_nonzero_output_tokens_fails_open(self):
        """Model generated something (whitespace, stripped think-blocks) —
        that's the flaky-but-recoverable class, keep full retries."""
        agent = _agent()
        _record_streak(
            agent,
            [_response(completion_tokens=42), _response(completion_tokens=42)],
        )
        assert guard.deterministic_empty(agent) is False

    def test_zero_then_nonzero_fails_open(self):
        agent = _agent()
        _record_streak(
            agent,
            [_response(completion_tokens=0), _response(completion_tokens=7)],
        )
        assert guard.deterministic_empty(agent) is False

    def test_signature_change_resets_determinism(self):
        """Fallback switched model mid-streak — new model deserves retries."""
        agent = _agent()
        guard.record_empty_attempt(agent, finish_reason="stop", response=_response())
        agent._empty_content_retries += 1
        agent.model = "other/model"
        guard.record_empty_attempt(agent, finish_reason="stop", response=_response())
        agent._empty_content_retries += 1
        assert guard.deterministic_empty(agent) is False

    def test_finish_reason_change_fails_open(self):
        agent = _agent()
        _record_streak(
            agent,
            [_response(), _response()],
            finish_reasons=["stop", "length"],
        )
        assert guard.deterministic_empty(agent) is False

    def test_new_streak_clears_history(self):
        """Counter reset to 0 (turn start / tool success / compaction /
        fallback) starts a fresh streak — prior attempts must not leak."""
        agent = _agent()
        _record_streak(agent, [_response(), _response()])
        assert guard.deterministic_empty(agent) is True

        agent._empty_content_retries = 0  # any existing reset site
        guard.record_empty_attempt(agent, finish_reason="stop", response=_response())
        agent._empty_content_retries += 1
        assert guard.deterministic_empty(agent) is False

    def test_guard_disabled_via_config(self):
        agent = _agent(_empty_guard_enabled=False)
        _record_streak(agent, [_response(), _response()])
        assert guard.deterministic_empty(agent) is False

    def test_reasoning_tokens_count_as_generation(self):
        """Reasoning-only responses are owned by the prefill path; the
        guard must not classify them as deterministic empties.

        Exercises the real _zero_output/normalize_usage path: a chat
        completions response with completion_tokens == 0 but
        completion_tokens_details.reasoning_tokens > 0 (hidden thinking,
        no visible text) counts as generation."""

        def _reasoning_only_response():
            usage = SimpleNamespace(
                prompt_tokens=25_900,
                completion_tokens=0,
                total_tokens=25_900,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=128),
            )
            return SimpleNamespace(usage=usage)

        agent = _agent()
        present, zero = guard._zero_output(agent, _reasoning_only_response())
        assert present is True
        assert zero is False  # reasoning tokens are real generation

        _record_streak(
            agent, [_reasoning_only_response(), _reasoning_only_response()]
        )
        assert guard.deterministic_empty(agent) is False


class TestEmptyRetryBudget:
    def test_budget_is_always_default(self):
        assert (
            guard.empty_retry_budget(_agent(), _response())
            == guard.DEFAULT_EMPTY_RETRY_BUDGET
        )

    def test_garbage_usage_still_returns_default(self):
        # Unparseable usage must not change the budget; there is no cost
        # input left to fail open on.
        agent = _agent(model=None, provider=None)
        resp = SimpleNamespace(usage=object())
        assert guard.empty_retry_budget(agent, resp) == guard.DEFAULT_EMPTY_RETRY_BUDGET


class TestZeroOutputExtraction:
    """_zero_output goes through the real normalize_usage path."""

    def test_openai_shape_zero_completion(self):
        agent = _agent()
        present, zero = guard._zero_output(agent, _response(completion_tokens=0))
        assert present is True
        assert zero is True

    def test_openai_shape_with_completion(self):
        agent = _agent()
        present, zero = guard._zero_output(agent, _response(completion_tokens=9))
        assert present is True
        assert zero is False

    def test_no_usage(self):
        agent = _agent()
        present, zero = guard._zero_output(agent, _response(usage_present=False))
        assert present is False
        assert zero is False

    def test_anthropic_shape_zero_output(self):
        agent = _agent(api_mode="anthropic_messages")
        usage = SimpleNamespace(
            input_tokens=25_900,
            output_tokens=0,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        present, zero = guard._zero_output(agent, SimpleNamespace(usage=usage))
        assert present is True
        assert zero is True

    def test_all_zero_usage_object_fails_open(self):
        """Proxies that emit an empty usage object (all fields absent →
        normalized to zeros) provide no evidence — must not classify."""
        agent = _agent()
        usage = SimpleNamespace()  # no token fields at all
        present, zero = guard._zero_output(agent, SimpleNamespace(usage=usage))
        assert present is False
        assert zero is False


class TestResolveGuardSettings:
    """resolve_guard_settings maps the additive agent.empty_response_guard
    config.yaml section into enabled, tolerating malformed input by
    falling back to schema defaults.
    """

    def test_missing_section_uses_defaults(self):
        assert guard.resolve_guard_settings(None) is guard.DEFAULT_GUARD_ENABLED

    def test_non_dict_section_uses_defaults(self):
        assert guard.resolve_guard_settings("nope") is guard.DEFAULT_GUARD_ENABLED

    def test_disabled(self):
        assert guard.resolve_guard_settings({"enabled": False}) is False

    def test_yaml_string_bool(self):
        assert guard.resolve_guard_settings({"enabled": "false"}) is False
        assert guard.resolve_guard_settings({"enabled": "true"}) is True

    def test_default_config_schema_matches(self):
        """The shipped DEFAULT_CONFIG section resolves to the module
        defaults — keeps config_defaults.py and this module in sync.
        """
        from hermes_cli.config_defaults import DEFAULT_CONFIG

        section = DEFAULT_CONFIG["agent"]["empty_response_guard"]
        assert guard.resolve_guard_settings(section) is guard.DEFAULT_GUARD_ENABLED
