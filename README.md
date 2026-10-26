# Family Health Assistant

A local family health chat with saved member and episode context. The model reads messages and helps with conversation; code checks its output and applies fixed safety rules. Reminders, test-human review and prompt evaluation are optional. This is a demo, not a clinical service.

## Assignment deliverables and scope

Start with [SCOPE.md](SCOPE.md) for what is required, what is extra and what is still limited. See [ARCHITECTURE.md](ARCHITECTURE.md) for the high- and low-level design diagrams. The future spec is [PRODUCT_SPEC.md](PRODUCT_SPEC.md); exactly four local transcripts are in [SCENARIOS.md](SCENARIOS.md). No clinician dashboard, production service or extra framework is required. This expanded repository preserves later requested features; it is not presented as the fewest-lines solution.

## Run

Python 3.10+:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-runtime.txt
.venv/bin/python server.py --open
```

Open http://127.0.0.1:8000. Use `--port 8001` if occupied. Introduce fictional family members naturally. Model settings accepts a transient session key; it is never stored in SQLite or Git. Default: OpenRouter `openai/gpt-4.1-mini`, pinned OpenAI route, provider-default effort. Direct OpenAI is separately supported. No credential is bundled and the UI does not automatically read `.env`.

The default was faster/cheaper on a small paired pilot; it is not universally best. Complete JSON responses are used, without token streaming. In-app connection budgets last for that process/session; controlled QA has a separate cumulative ledger. Reconnecting the same credential does not reset its in-process accounting.

Explicit offline mode (limited pattern parser, **not an LLM**):

```sh
.venv/bin/python server.py --backend demo --db synthetic_demo.db --port 8001
```

## Core control flow

`message → immediate safety screening → scoped context → structured LLM intake → grounded updates → deterministic triage → fixed route or response model → optional web_search → observations → response`

The model/tool loop has configured call limits and stops on a final validated response, a budget/error condition, or terminal safety routing. Only search is model-callable; permissions, storage updates, routing and notifications are program-controlled. Retrieved pages are untrusted evidence, not instructions.

- Emergency: fixed instruction to contact local emergency services immediately, without waiting for model/search/email.
- Non-emergency review: the assignment's predetermined transfer wording **plus an explicit disclaimer that this demo does not contact a clinician**. Optional test-human notification status is separate.
- Home monitoring: deterministic outcome only when required reported triage fields support it.

SQLite keeps household/member/episode identities, sourced facts, corrections, explicit unknowns, preferences and episode status. Retrieval defaults to six recent turns, twenty ordinary facts and a roughly 3,000-token dynamic-memory target. Active safety facts are not silently dropped; overflow is explicit. Corrections supersede earlier values. No vector database is required. See [memory contracts](MEMORY_CONTEXT.md).

State-aware educational response caching has measurable hit/miss/bypass traces and household/state/policy/model/prompt invalidation. Clinical responses and searches bypass it. Provider prompt caching is separately reported through provider usage.

## Optional local operations

[Operations and Gmail review](OPERATIONS.md) documents the explicitly enabled background worker, neutral self-mail and owner-confirmed reply flow. Nothing sends mail merely because the server starts.

Local reminders:

```sh
.venv/bin/python server.py --db synthetic_demo.db --port 8001 --reminder-worker
```

Use an explicit future date/time/IANA timezone, e.g. `Remind me at 06:00 on 2027-12-25 in Asia/Kolkata to review a fictional check-in.` Offsets, revisions, cancellation, expiry and duplicate suppression are durable. Delivery is a **local SQLite sink**, not an email/phone notification. The worker must run and the Mac must be awake; sleeping/offline timely delivery is not guaranteed. No medication catch-up/dosing instruction is inferred.

## Learning and evaluation

[Learning workflow](LEARNING.md): consented local feedback → reviewed synthetic train/development examples → optional DSPy MIPROv2 → paired frozen held-out/safety evaluation → explicit manual promotion or rollback. Clinical rules and action permissions are immutable to optimization. Real health chats are not automatically training data. Held-out examples cannot enter optimization and consumed holdouts cannot be reused as fresh evidence.

A real bounded optimization experiment ran: after reviewing oracle false positives, baseline and candidate tied at 5/6. **No gain and no prompt promotion**. More usage adds grounded memory; it does not prove increasing model intelligence. The pipeline can produce and assess candidates; it cannot guarantee an improvement.

## Test and submission

```sh
python3 -m venv .venv-qa
.venv-qa/bin/python -m pip install -r requirements-qa.txt
TIKTOKEN_CACHE_DIR=/tmp/family-health-tokenizer-cache .venv-qa/bin/python -c "import tiktoken; tiktoken.encoding_for_model('gpt-4.1-mini')"
.venv-qa/bin/python -m unittest discover -s tests -v
```

Optional controlled runner checks: `python3 tools/freeze_qa.py`, followed by `.venv-qa/bin/python -m unittest discover -s qa_20261025 -p "test_live*.py" -v`. These use fake transports, not paid inference. See [final validation](VALIDATION.md) and exactly [four submitted scenarios](SCENARIOS.md). All scenario model/search fixtures are explicitly identified; real provider evidence is summarized separately, not conflated with fixture passes.

Private QA transcripts, databases, credentials, provider receipts and OAuth files are excluded from Git. No medical readiness or exhaustive correctness is claimed.
