# What's in scope

This is an assignment demo, not a production medical service. The model reads the message; code checks the facts, keeps people's records separate and applies the safety rules. Memory is part of the required core.

## Submission checklist

1. **Code:** this repo. Start with [README.md](README.md) and [the two design diagrams](ARCHITECTURE.md).
2. **Future product spec:** [PRODUCT_SPEC.md](PRODUCT_SPEC.md).
3. **Local runs:** [SCENARIOS.md](SCENARIOS.md), exactly four transcripts. They use fictional people and supplied model replies, not live AI. Live experiments and their limits are listed separately in [VALIDATION.md](VALIDATION.md).

## Assignment core

| Requirement | What the app does |
|---|---|
| General health questions | Answers without inventing symptoms or starting intake on every turn. |
| Member and episode context | Uses names, relationships and recent context; asks when the person is unclear. Keeps different people and conditions in separate episodes. |
| Useful memory | Saves facts, their source, corrections and unknowns in SQLite. Retrieves relevant facts beyond the recent-chat window. Another household cannot borrow them. |
| Focused follow-ups | Asks for missing triage details, rather than repeating answered questions. Missing information is not treated as “no”. |
| Emergency | Returns fixed advice to contact local emergency services, without waiting for a model, search or identity setup. It does not place a call. |
| Doctor review | Returns the assignment's fixed transfer wording, with a clear demo disclaimer. It does not contact a clinician. |
| Home monitoring | Requires the relevant reported details and rule checks. A reassuring model answer cannot override escalation. |
| Extension points | Separate model, state, rule and tool boundaries. Lab analysis, a custom knowledge base and telemedicine are out of the core scope. |

The current response/search loop is bounded. Only search is model-callable; the model does not get direct permission to change memory, send email or schedule actions. See [ARCHITECTURE.md](ARCHITECTURE.md) for the actual flow and files.

## Extras requested later

- **Search and citations:** source text, links and visible lookup failures.
- **Cache and metrics:** checked education replies, hit/miss traces, timing and usage. This is separate from memory.
- **Reminders:** one-shot local schedules, edits, cancellation and delivery records. The worker must run and the Mac must be awake.
- **Test-human review:** neutral email, known-thread checks and owner-confirmed replies. A test reviewer is not a doctor. Without a bound case, no email is queued.
- **DSPy and feedback:** reviewed synthetic examples, unseen evaluation cases and manual promotion/rollback. The completed experiment tied; no optimized prompt is active. Normal use does not silently train the model.
- **Browser UI:** an easier way to test the app, not a required doctor dashboard.

These extras are optional. The core does not need Gmail setup, DSPy or a telemedicine account.

## What this does not prove

The repo is larger than a minimal assignment submission because it keeps the later extensions and tests. It is not presented as the smallest solution. There is one implementation; no extra framework or duplicate harness was added to make the design sound more complex.

Software tests, live model calls, source lookups and real email checks prove different things. The earlier tests did miss a breathing-wording and conversation-loop bug; both now have regressions. Passing tests is not clinical validation or a promise that no bugs remain.
