# clawd deep-audit parent review — current status

Scope: read-only review of deleg_dd4f5bde reports while approved bug fixes/project resources are being implemented. No tests have been executed at this stage. Static findings below are not runtime-confirmed defects. No expansion of source-fix scope is implied.

Source target: MOSS HUAWEI CLOUD SG SERVER, /root/hermes-workspace/clawd. Parent directly read current source with tool output; api_server.py e653b26b4bcb8e816cc673c62304727b511bbb24b980cfff8024d35beeab7a5c; api_server_runs.py 554cb28b411b323fab038d4f24dedc7e4876b7266f2044bc5f3730e528fa122b; hermes_state_sessions.py c149236afa6f58cab44a50d1e32f0e9f9d4f7883770aa0d5d7a0d7bf9d0ada1c.

Corrections to child reports:

P1-01 (fork loses project): rejected on current source. hermes_state_sessions.py _insert_session_row lines 430–439 calls _copy_child_project_binding when parent_session_id is present and the row is new. create_session calls that helper. The original audit only followed create_session to the insert wrapper and missed the wrapper body. This is not evidence that runtime inheritance is verified; it disproves the claimed missing call.

CORE-03 (stop during synchronous create_agent): the proposed race is unsupported. In _execute_run, between the stopping check and _active_run_agents assignment the legacy branch calls synchronous _create_agent without await. The report supplies no execution model in which another coroutine can enter stop in this interval. Do not count it as a confirmed race. Separate suppression of request_hard_interrupt exceptions remains a static observation, requiring a real route failure probe and correct acceptance semantics before calling it a defect.

CORE-04 (provider auth failure completed on both chat and runs): narrowed. _run_agent returns a final_response-only result for _ProviderAuthResolutionError; _response_status treats it as completed, so chat/same caller seam deserves a probe. But api_server_runs._execute_run separately catches _ProviderAuthResolutionError and explicitly finishes failed at lines 1224–1227. Do not claim /v1/runs exhibits the same outcome without proving the real error path traverses the final_response wrapper.

CORE-05 (queued handoff closes stream): closure of this in-process stream is visible in source, but that alone does not establish product failure when ownership changes and status polling may be the intended recovery. Requires checking downstream recovery/reconnect contract; retained as risk, not confirmed user-visible bug.

Directly re-read, still awaiting runtime validation:

CORE-01: fork creates child, ends source, then copies messages/sets title; title ValueError returns 400 without child id. Failure atomicity and visible effects require isolated database/HTTP failure injection.
CORE-02: history helper returns [] on unavailable DB or history-read exception; chat and legacy run callers pass its return onward. Need probe at actual admission route, not only mock helper, before asserting a model actually ran without history.
P1-04: config-tab commit invalidates settings only; saveYaml invalidates config only. Need actual two-tab readback and stale-write test.
P2-01: admin_settings _KEY_MAP maps model_context_length to model.context_length for writes; userSet reads raw keys without mapping. Needs schema fixture and actual GET/PUT readback.
P2-02: files-page uploadFile call omits current directory; verify uploadFile signature and sidecar target semantics before runtime probe.
P2-03: browser_native target_closed returns result without setting ended; verify _view status and task-dock behavior before runtime probe.

Not yet independently closed by parent: P1-02 cleanup orphan binding, P1-03 standalone background registry, P2-04 draft restoration, CORE-06 retry presentation. Their reports remain leads. Current owner files may change, so hashes and call paths must be rechecked after implementation.

Pending sequence: collect all implementation owners; integrate resources component without overwriting session fix; concentrate targeted tests/typecheck/build and mobile UI checks; run bounded isolated audit probes; classify pass/fail/blocked and summarize confirmed defects separately from hypotheses. Do not call the project deeply scanned without listing remaining areas (TUI, model/MCP etc.).
