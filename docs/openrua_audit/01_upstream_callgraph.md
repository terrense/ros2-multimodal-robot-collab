# 01 — Upstream Callgraph

Pinned at `b972237dcf1baa0c0a5cdf34ba062fcfba619f83`.

Scope: the harness spine is read line-by-line; peripheral packages are read for
contract shape. Every claim carries `path:line`. Claims marked **UNVERIFIED**
were not read directly in this pass.

## 1. Runtime order

The order the code actually executes, not the order the README presents:

```
cli.main                     cli.py:177
  build_parser               cli.py:28-174
  _probe_integration_name    cli.py:21-25     (imports the integration before parsing)
  resolve_config             config.py:36
    resolve_settings         config.py:48
  Session._from_resolved     session.py:276-280
  session.start()            session.py:299
    create_agent             session.py:314
    assemble_session         session.py:66
  session.run_agent()        session.py:337
    _run_agent               session.py:739
      ProviderGateway        session.py:761
      agent.run(...)         session.py:811
  finish()/finalize          session.py:391, 500-598
```

### 1.1 CLI and config

- `main()` probes `--integration` in a pre-parser (`cli.py:21-25`) because each
  integration contributes its own CLI options; the named integration module is
  imported *before* the real parser is built (`cli.py:179-188`). Consequence:
  an integration that fails to import fails the CLI before argument errors are
  reported.
- Every backend contributes options into one shared parser
  (`cli.py:94-98`), and `resolve_config` rejects options that belong to a
  different backend (`config.py:38-42`). This is the mechanism that keeps a
  multi-backend CLI honest.
- `resolve_settings` (`config.py:48`) is the only place that reads environment
  variables; everything downstream receives resolved values. The docstring says
  so explicitly and the code follows it (`config.py:50-52`).
- `SessionConfig` is a pydantic model (`config.py:251`) whose `resolve()`
  (`config.py:275`) is the SDK entry point. **UNVERIFIED**: exact field set at
  `config.py:251-300`.

### 1.2 Session assembly — the important part

`assemble_session` (`session.py:66-220`) is one function that builds the entire
runtime. In order:

| Step | Code | What it owns |
|---|---|---|
| 1 | `load_integration` + `create_session(IntegrationContext(...))` `session.py:84-93` | integration owns environment + episode |
| 2 | `session.start()` `session.py:94` | integration connects to the native world |
| 3 | tools from every provider + `WorkspaceProvider` `session.py:105-106` | toolkit contents |
| 4 | visibility filter by `integration_spec.eval_tool_names` `session.py:107-115` | what the model may see |
| 5 | optional observation profile; requires per-resource scheduling `session.py:117-128` | observation sidecar |
| 6 | `Toolkit.register_tools(sorted(tools))` `session.py:140-142` | stable schema order |
| 7 | `ParallelToolExecutor(toolkit, policies, ...)` `session.py:148-153` | scheduling + background jobs |
| 8 | `toolkit.set_solved_fn(session.task_succeeded)` `session.py:164` | **task success belongs to the environment** |
| 9 | `build_prompt` closure `session.py:184-192` | prompt assembly |

Two decisions here matter more than the rest of the file:

1. **The model never decides whether the task succeeded.** `task_succeeded` is a
   callback into the integration (`session.py:164`, `tools/toolkit.py:97-106`),
   read only at agent completion (`session.py:866-872`).
2. **Tool visibility is derived from the integration, not from a prompt.**
   `eval_tool_names` decides what the model sees (`session.py:108-115`).

### 1.3 Agent execution

`_run_agent` (`session.py:739-887`):

- Creates a `ProviderGateway` only if a model endpoint is configured
  (`session.py:759-777`), so a scripted/human session runs without a provider.
- `code_as_policy` mode swaps direct tool calls for a local HTTP bridge
  (`session.py:790-810`) — the model writes code, the code calls tools.
- On any exit path, cancels the agent and closes the agent/gateway
  (`session.py:814-837`), accumulating cleanup errors rather than raising the
  first one.
- Emits `AgentRunFinished` with both `success` and `task_success`
  (`session.py:873-884`) — two different fields, deliberately.

### 1.4 Finalization — where correctness lives

`_finalize_run` (`session.py:500-598`) is the most opinionated function in the
repository:

1. Drain background physical work first (`assembly.executor.close()`,
   `session.py:514-523`); if it fails, the outcome becomes
   `execution_uncertain` (`session.py:517-523`).
2. Ask the integration to seal the episode and produce the official outcome
   (`session.py:531-534`). The comment states the reason: persistent simulators
   and hardware both require `finalize` before `close`, and the agent's own
   claim of success is not the outcome (`session.py:527-529`).
3. Provider failures are classified as infrastructure errors
   (`session.py:546-555`).
4. If the integration forbids automatic retry, `retryable` is forced false so a
   physical episode is never replayed by a policy override
   (`session.py:556-558`).
5. Exit code 75 marks a retryable outcome, 0 success, 1 otherwise
   (`session.py:726-736`).

## 2. Tool layer

### 2.1 Declaration and validation

- `ToolSpec` (`tools/toolkit.py:183-191`) is the model-facing schema; `ToolDef`
  (`tools/toolkit.py:15-23`) binds it to a handler.
- `bind_tools` (`tools/toolkit.py:222-245`) validates arguments with
  `jsonschema` before dispatch and returns a structured failure with
  `no_execution: True` (`tools/toolkit.py:229-235`) — an invalid call is
  distinguishable from an executed-but-failed one.
- `constraint_summary` (`tools/toolkit.py:197-219`) copies numeric and string
  bounds into the description, because some model clients drop JSON-Schema
  constraints.

### 2.2 Results

- `ToolOutput` (`tools/toolkit.py:35-60`): `success` means "the call completed
  with a valid result, not that a subgoal or episode succeeded"
  (`tools/toolkit.py:39-41`).
- `to_dict(for_model=True)` renames that field to `call_ok`
  (`tools/toolkit.py:51-54`) — the wire name states what it actually means.
- `Toolkit.execute_tool` (`tools/toolkit.py:116-166`) converts a raised
  exception into a `ToolOutput(success=False, ...)` with a 10-line traceback in
  `extra` (`toolkit.py:128-139`), so a crashing handler still yields a receipt.

### 2.3 Receipt views

`tools/results.py` is a small, high-value file: pydantic models that **project**
a receipt without rejecting it (`results.py:7-25`). A malformed field becomes
`None`; the raw payload is retained separately. The docstring states the rule:
"Formatting cannot invalidate an acknowledged action or cause replay"
(`results.py:22-24`). Field-level semantics are unusually precise:

- `call_ok` "does not establish motion, grasp or task success" (`results.py:68`)
- `pose_reached` "not object attachment" (`results.py:30`)
- `opening_reached` "not proof of grasp/contact" (`results.py:36`)
- `gripper_command_submitted` "not measured opening or contact" (`results.py:41`)
- `ended` "does not disclose official task success" (`results.py:62`)

This three-way split — call ok / physical effect / task success — is the single
most reusable idea in the repository.

## 3. Execution and scheduling

- `ToolExecutionPolicy` (`execution/scheduler.py:21-29`): `resource_key`,
  `return_mode` (`immediate|background`), `concurrency` (`exclusive|parallel`),
  `mutates_world`.
- `JobRegistry` (`scheduler.py:63-153`) is the only writer of job state; every
  transition (`queued → running → succeeded|failed|cancelled`) is a locked
  method.
- `ResourceScheduler` (`scheduler.py:156-324`): one FIFO deque and one worker
  thread **per resource key** (`scheduler.py:196-214`), plus a bounded thread
  pool for parallel-eligible work (`scheduler.py:168-171`).
- `cancel` only cancels jobs still queued (`scheduler.py:240-244`,
  `registry.cancel_if_queued` `scheduler.py:131-139`) — a running physical
  action is not silently "cancelled" in the bookkeeping.
- `close(drain_timeout=30)` (`scheduler.py:298-324`) waits for workers, cancels
  what is still queued, and **returns the IDs of unfinished jobs** so the
  caller can report them; `ParallelToolExecutor.close` turns a non-empty list
  into a `RuntimeError` (`execution/parallel.py:296-303`).
- `ParallelToolExecutor._call` (`parallel.py:103-169`): unknown tools are
  rejected before scheduling (`parallel.py:111-112`); `background` returns an
  accepted `ExecutionResult` carrying `job_id`, status and queue position
  (`parallel.py:164-169`); `immediate` + `parallel` bypasses the queue entirely
  (`parallel.py:123-126`).
- Control tools (job status/result/cancel) are only exposed in background mode
  (`parallel.py:67-76`), and the names are reserved: a real tool colliding with
  a control name fails construction (`parallel.py:74-76`).

Defect worth recording: `ResourceScheduler._exclusive_worker`
(`scheduler.py:216-227`) keeps a thread per resource forever until `close()`;
that is fine for a bounded episode, but a long-lived process with dynamic
resource keys would accumulate threads.

## 4. Integration contract

`integrations/base.py` is only 91 lines and carries the episode semantics:

- `IntegrationSpec` (`base.py:32-45`): session factory, tool policies,
  `eval_tool_names`, `allow_automatic_retry`, `config_model`.
- `IntegrationSession` protocol (`base.py:73-91`): `tool_providers`, `episode`,
  `task_succeeded()`, `action_model_name`, `start()`, `finalize()`, `close()`.
- `EpisodeOutcome` (`base.py:58-70`): `success: bool | None`,
  `accepted`, `retryable`, `failure_category`, `metadata`, with a constructor
  invariant that a retryable outcome must not be `accepted` — i.e. it is
  excluded from the evaluation denominator (`base.py:68-70`).

`success=None` is a first-class value meaning "unknown", not "false". That is
the encoding our L1 `Outcome` enum does not have.

## 5. Artifacts, events, telemetry

- `ArtifactStore(session_dir)` is created before the integration session
  (`session.py:82`) and closed exactly once in `finish_artifacts`
  (`session.py:232-245`), which reports `pending`/`failed` instead of raising.
- `result.json` is written on every exit path, including startup failure
  (`session.py:205-220`).
- `EventBus` + subscribers: console and JSONL (`session.py:318-322`);
  `SessionOpened`/`SessionClosed` carry the official outcome
  (`session.py:326`, `session.py:561-567`).
- `ToolResult` events carry `tool_execution_seq` and `execution_id`
  (`tools/toolkit.py:149-164`).

## 6. Peripheral layers

**UNVERIFIED in this pass** — being collected by the subsystem audit and will
be folded in before this document is committed:

- `provider/` request path, retry and token policy
- `context/` prompt assembly and truncation
- `agents/` adapters, turn loop, cancellation
- `services/environment/` command → receipt path
- representative `integrations/` (IntegrationSpec/IntegrationSession instances)

## 7. Test coverage

Zero test files at the pinned SHA (search recorded in `00_baseline.md`). No
upstream behaviour can be adopted on the strength of an upstream test.
