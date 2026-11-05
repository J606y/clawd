# CLI display reference and delivery boundary

## Observable turn output and evidenced retry status

This update supersedes the initial delivery's unchanged retry-display scope below. Execution/retry policy, permissions and model configuration remain unchanged.

- The active turn displays `~N tokens`: observable generated response/commentary, available thinking snapshots and structured tool input, approximated from UTF-16 string length divided by four. It is not billing usage, input/context size or a complete count of unavailable hidden output. No estimate is displayed for zero/invalid data, event gaps or incomplete replay. RunStore owns the estimate and formatted label; duplicate sequences and echoed commentary do not increase it.
- The normal English spinner uses orange shimmer. It does not turn red because of token volume or silence. Reduced-motion disables both shimmer and rotation without hiding the status.
- Real retry evidence replaces the spinner with an error-colored label, such as `Rate limited (429)`, and `Retrying in … · attempt …/…`. HTTP codes and attempt counts must be evidenced; a generic RateLimitError does not manufacture 429. Missing deadlines use `Retrying…`, elapsed deadlines use `Awaiting retry…`. Final errors continue through the existing run outcome/error UI.
- A cached, read-only native runStatus response must match both session and run IDs and provide server-created time before log observation. The server timestamp, not the browser clock, defines the log window. The sidecar returns that requested boundary, observation/deadline timestamps and availability, using bounded, rotation-aware cached log tails. This is log evidence within a validated time window, not a native structured retry event protocol.
- Stop and approval take precedence. A tool can suppress the ordinary idle indicator while the read-only observer remains mounted, allowing a real retry to replace a stale busy tool. Old-run responses, aborted requests and disconnected views cannot publish status; hidden views suspend observation. Missing/old sidecar evidence degrades to ordinary status, with a diagnostic tooltip, never a manufactured error.
- Deployment dependency for the reviewer: publish the repository sidecar update as well as the frontend if retry colors/countdowns are required. A frontend-only release with the old sidecar intentionally omits unscoped retry evidence. This delivery does not install or restart a production sidecar.

Validation on the combined hk 441a177 snapshot plus this patch: full Node command 238/238; sidecar unittest discovery 41 tests and robustness discovery 14 tests; typecheck via production build; isolated Chromium 1280/768/390/320 each 111 assertions; 320/390 native CDP touch, reduced-motion, existing thinking-browser, scroll-browser and stream-render-browser. Logs: `/root/hermes-workspace/spinner-combined-*.log`. Earlier baseline image-test failure and initial wrong Python invocation are retained, not overwritten by later successful logs. The inherited image test has a fixed original-workspace root; its command passing is not independent image-feature acceptance of this clone. No physical iPhone/WebKit, real model throttling, signed-in reference CLI or deployed-backend verification was performed. Independent final review remains the user's responsibility; no deployment is authorized by these checks.

Final publication snapshot additionally preserves c69a9ac's sidebar work: full Node 246/246, sidecar 41 tests, robustness 14 tests, active-session integration 11 tests, build/typecheck, four viewports each 111 assertions, touch/reduced-motion and thinking-browser passed. The inherited sidebar browser's first run passed functional checks but failed profile cleanup with ENOTEMPTY; its single complete rerun exited zero. Both logs remain in `/root/hermes-workspace/spinner-publish-sidebar*.log`; no unrelated fixture was changed to hide the failure. Scroll/stream gates were executed on the preceding 441a177 combined snapshot, not rerun after sidebar integration. Publication evidence is in `/root/hermes-workspace/spinner-publish-*.log`.

Documentation used for token/error conventions: https://code.claude.com/docs/en/costs and https://code.claude.com/docs/en/errors; implementation reference remains official CLI 2.1.309, not a guarantee of future-version parity.

## Initial CLI display delivery

Reference: official @anthropic-ai/claude-code-linux-x64 2.1.309. Binary --version returned `2.1.309 (Claude Code)`; SHA-256 `4d704a8aecbe038affa3000f498ae17c1b4f753add8facb8764120affc99b658`. Downloaded reference lives outside this repository; no CLI runtime, auth, SDK or copied implementation is bundled.

The 187 spinner words and eight completion words were checked against the official distribution, including order, accents and punctuation. `src/lib/spinner-verbs.ts` retains English spelling. Spinner wording is cosmetic, selected per run and stable across routine renders. Existing polling, stop, approval, stream admission and task state remain unchanged.

Visible changes: English tool names, structured MCP service/tool labels, Calling/Called service summaries, per-service call counts, always-visible failed count, collapsible tool detail and Input, three-line/4000-unit Unicode-safe output preview, `⎿` branch marker, an animated `✻` wait indicator honoring reduced motion, compact elapsed time, Thought disclosure, deterministic past-tense turn summaries, English approval choices with unchanged permission values.

This is a browser adaptation of the CLI's display conventions, not a verified pixel-perfect terminal renderer. Calling wrappers are interpreted only from structured `tool_call.calls` and `parallel.tool_uses`, depth capped at three and fifty entries per wrapper; no script/prose guessing. Unknown or malformed tool identifiers stay visible. Explicit failed runs use Failed and cancelled/interrupted runs use Interrupted; whole-turn duration is never labeled reasoning duration. No fake progress percentage, token count, per-tool timing or unknown reasoning duration is generated.

Unchanged: sidebar/navigation, backend protocols and authorization, model execution, retry error classification, task/subagent scheduling, real-browser viewing/ownership, diff data generation, raw transcripts and steering offsets. Existing raw diff output remains available; this change does not add a synthetic editor/diff engine or a global Ctrl+O terminal emulator. The backend does not expose every Claude Code-specific state, so the UI does not fabricate them. Existing Chinese diagnostic explanations remain intact; status/CLI vocabulary is English.

Validation: Node full frontend suite, production typecheck/build, real isolated Chromium 1280/390 viewport interactions (MCP aggregation, failure visibility, expansion, preview/full output, long-identifier overflow, watch preservation, waiting/streaming/stopping, reconnect timer cleanup, completion semantics, permission scopes), existing thinking preservation Chromium regression. Browser tests use synthetic data and intercept backend calls, not a live production model or real iPhone. The reference CLI was not signed in; no live reference conversation or side-by-side visual comparison was performed. Independent final review belongs to the user's local reviewer. Never claim exact visual parity or deployment from these checks.

Official sources:
- https://code.claude.com/docs/en/interactive-mode
- https://code.claude.com/docs/en/permissions
- https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md
- https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-2.1.309.tgz

Rollback: reviewer can revert the task commit and rebuild through the existing release process. No production state is changed by this task; no production rollback was exercised.
