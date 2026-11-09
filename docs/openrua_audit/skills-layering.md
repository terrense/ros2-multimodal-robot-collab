# Skills Layering

How executable capabilities and guidance are separated in this repository.

## Three layers, one direction of authority

| Layer | Lives in | Authority |
|---|---|---|
| **Executable skill contracts** | `skills/*/schema.json`, `agent_env/schemas/agent_skills.json` | canonical: the transport, arguments and risk of every action |
| **Runtime capability registry** | `CapabilityRegistry.load()` | derived from the schemas; decides what the model may see |
| **Guidance** (markdown, prompts, docs) | `docs/agent_skills.md`, skill descriptions, prompt text | never control; describes intent and usage only |

Guidance may explain a skill, but it cannot add one. A capability exists for the
model only if a schema declares it and the registry exposes it for the platform.

## General-purpose vs environment-specific

- `skills/*/schema.json` describes ROS 2 operations against this workspace
  (`/skills/navigate_to_station`, `/mission/deliver_tool`, arm control, TTS).
  These are environment-specific and stay the canonical execution contracts.
- `agent_env/schemas/agent_skills.json` describes the L1 step vocabulary. It is
  frozen for the benchmark; Phase 1 established that the two vocabularies differ
  (6 callable ROS 2 actions vs 13 L1 actions, 5 shared names) and are merged,
  not renamed, in the registry.
- Reusable reasoning guidance (how to recover, when to ask for confirmation)
  belongs in prompts or markdown, never in a schema. The schema answers "can
  this be executed", not "should it be".

## Model-visible discovery

`CapabilityRegistry.describe_for(platform)` renders the list the model sees:

```
navigation.navigate_to_station(station_id, reason) [atomic, risk=base_motion, mutates=true]
system-state.query_state(requester) [atomic, risk=read_only, mutates=false]
```

Adding an action to a schema file makes it appear; marking it
`callable: false` keeps it out. There is no second list to maintain.

## What the model may not do

- invent a capability: unknown names are rejected by the registry
- bypass safety: `system_stop` is an operator request, not an E-stop, and the
  fail-closed gate lives in the runtime
- see evaluator-only state: `model_view` strips payload keys starting with `_`
