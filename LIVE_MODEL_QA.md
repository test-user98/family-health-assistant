# Hidden-input synthetic model QA

> Recovery update: the user explicitly supplied project `.env` credentials. `--key-file /Users/jaisipani/Desktop/q/.env` reads only the selected named key into RAM, without shell expansion, environment mutation, file edits or extra copies. A real pilot subsequently ran; see `qa_20261025/ROUTER_RECOVERY.md`. `--resume-accounted` never resets the cap: it carries prior charges/reserves forward. HTTP diagnostics retain only safe status/category codes. Further paid calls are currently paused on an unresolved diagnostic-receipt accounting hold; the available keys are no longer the blocker. Initial no-key/no-inference statements below describe preparation, not the later live run.

The existing `qa_20261025/live_runner.py` is the direct-OpenAI 20-turn conversation/recall runner. It does **not** run DSPy optimization or model comparisons. The consolidated `qa_20261025/live_learning.py` adds two explicit modes: `benchmark` or `optimize`. It never changes app settings or promotes a prompt/model automatically.

## Verified OpenRouter command

Public, credential-free preflight succeeded for `openai/gpt-5-mini@openai` and `openai/gpt-4.1-mini@openai`; metadata evidence is `qa_20261025/OPENROUTER_PREFLIGHT.json`. These are pilot candidates, not established winners. A different model/provider must pass its own preflight; not every OpenRouter model supports the required schema/tool contract.

Run manually on the Mac:

```sh
cd /Users/jaisipani/Desktop/q
DSPY_CACHEDIR=/tmp/family-health-dspy-cache /tmp/family-health-dspy-qa/bin/python qa_20261025/live_learning.py --provider openrouter --model openai/gpt-5-mini --route-provider openai --compare-model openai/gpt-4.1-mini@openai --mode benchmark
```

This opens a hidden OpenRouter-key prompt in that terminal. The key is transient RAM input, not a command argument, environment variable, saved setting or artifact. **The prompt has not been opened by the assistant.** The user must open/run it manually; the prior Terminal CUA restriction is not bypassed. The temporary QA venv currently exists; if removed after a reboot, recreate an isolated venv from `requirements-learning.txt` before running. Never paste a key into chat.

Add `--preflight` to check metadata without requesting a key or calling inference. Use `--self-test` alone for no-network fake-transport checks. No actual inference has run in preparing these commands.

For a separate selected-model DSPy run, use the same command without `--compare-model ...` and with `--mode optimize`. Proposal/task/evaluation use that same selected model/key; no separate reflection model is required. Do not run both paid modes under independent fresh budgets: the runner refuses another run if prior QA artifacts show paid attempts, pending total-spend review. A lock also blocks a simultaneous/interrupted learning run. An increased budget is not inferred from an offered key.

## Compatibility and security

`openrouter_client.py` checks the current public exact model endpoint metadata for `structured_outputs`, `response_format`, `tools`, `tool_choice` and an output-token-bound parameter. It pins an explicit provider tag, requests `require_parameters:true`, `allow_fallbacks:false`, `only`/`order`, `data_collection:deny` and route-specific price ceilings. No `:online`, automatic model router, model fallback, server plugin, external search, email or retries is enabled. Only text messages/function schemas are submitted. The HTTPS endpoint is hardcoded to OpenRouter; proxies/redirects are disabled. This authenticates to OpenRouter, which routes synthetic prompts to the explicitly selected model provider; no credentials are forwarded by this client to another host.

Metadata capability support is necessary, not proof that every provider handles the app's exact schema/tool cycle correctly. Local fixtures verify translation of schema requests, function calls and observations into the existing Runtime envelope. Real structured-output/tool quality can still fail; such failures are recorded, not bypassed with an unapproved fallback. The app's browser Model settings remains OpenAI-specific; OpenRouter is supported by injected Runtime/optimizer clients and this QA entrypoint, not silently configured in the user-owned app.

## Accounting and model-comparison plan

One shared budget covers all models/phases: original **$1 total authorization**, $0.90 working ceiling, maximum 60 provider requests, one in-flight request, no automatic retry. OpenRouter accounting uses freshly captured **selected-route native prices**, context/output ceilings and returned native usage/cost. It does not reuse OpenAI prices. Before dispatch it reserves full route-context × input rate plus bounded output/reasoning and request fees; if that worst case cannot fit, it refuses before requesting a key/dispatch. Each success accounts at least the returned USD cost and conservatively computed native-token price. Missing/nonfinite cost, wrong model/provider, excessive usage or uncertain transport outcome stops the run with reserve retained.

Current public metadata showed gpt-5-mini OpenAI route input $0.25/M, output $2/M; gpt-4.1-mini OpenAI route input $0.40/M, output $1.60/M. The runner re-fetches prices on every preflight and imposes those ceilings. Prices are evidence from this preflight, not permanent configuration. Account credit-purchase fees/new purchases are not performed or included as test inference; no account/security settings are changed.

The benchmark runs the same six frozen synthetic cases for exactly two models, three repetitions each: corrected age, recorded allergy, unknown temperature, resolved-vs-active episode, noncritical review and emergency. Factual state is seeded identically in owned SQLite. No benchmark input enters DSPy training. It captures actual model/provider/version metadata, required schema behavior, route outcomes, native input/output/reasoning/cached tokens, reported USD cost, conservatively accounted USD cost and end-to-end timings. It separates conversational p50/p95 (`n=12` per model if completed) from urgent-route timing (`n=6`), and fresh app context from reused context. Provider-cache coldness stays unknown unless usage supports a narrower claim. A fast reply alone is not a cache hit.

No streaming is implemented: TTFT is explicitly unmeasured, not substituted with time to complete JSON. Fixed model order, small samples and simple factual rubrics limit causal/statistical conclusions. The live tool challenge is not included in this tiny pilot; tool metadata/protocol tests are separately labelled. Any safety hard failure excludes a model. Only complete, all-correct pilot models enter a cost/latency Pareto recommendation; `winner` and app activation remain none. A tiny pilot cannot prove global superiority. Review additional quality/tool/latency evidence before selecting a production/default model. The existing gpt-5-mini default is historical, not an empirically proven winner.

The budget may stop a comparison or MIPRO run before completion. No repeated larger sweep or price/endpoint fallback is authorized automatically. No real family health data is used.

## Current readiness

New runner dry-run checks cover shared optimizer/evaluation accounting, a complete fake paired evaluation, real DSPy adapter wiring to a fake bounded LM, two-model pilot counts/metrics, no automatic activation, context limits, empty/noninteractive input, secret refusal before SQLite, prior-spend refusal and lock cleanup. Actual key-bearing OpenRouter inference, live optimization, model quality/latency superiority and app winner configuration remain pending supported local key entry and any necessary total-budget decision.
