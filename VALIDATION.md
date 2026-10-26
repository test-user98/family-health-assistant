# Validation and remaining limits

## Current implementation

The assignment core implements scoped family/episode memory, grounded corrections/uncertainty, focused intake, bounded model/web_search iteration, deterministic emergency/review/home routes and exact fixed escalation text. Optional cache, durable reminder/outbox, human-review and DSPy evaluation modules are configuration-driven. No live doctor transfer is claimed.

The October 26 completion adds opt-in background neutral synthetic self-mail dispatch and known-thread polling, durable acceptance reconciliation, pending reply quarantine and household-scoped explicit owner confirmation in the normal UI. Closing the UI does not stop a running server worker; stopping/sleeping the Mac does. No always-on system service or real clinical delivery guarantee is installed.

## Evidence tiers

- Current app regression command: `.venv-qa/bin/python -m unittest discover -s tests -q`. **569 passed**, no skips; repeated successfully from a clean staged-tree export without private files (local Python 3.14.7; DSPy 3.4.1, Optuna 5.0.0, tiktoken 0.14.0). These are offline software/fixture tests, not clinical validation.
- Controlled runner checks: `python3 tools/freeze_qa.py`, then `.venv-qa/bin/python -m unittest discover -s qa_20261025 -p 'test_live*.py' -q`. **32 passed**, also repeated from the clean export. Fake transports only. Includes conservative accounting, missing usage, interruptions, refusal, secret redaction and consumed-holdout boundaries.
- Eight Runtime enforcement probes and 29 owned HTTP requests rerun against current code with fixture model/replayed search. No email or inference calls in that rerun. Exactly four submission transcripts: [SCENARIOS.md](SCENARIOS.md).
- Gmail: one real neutral mail was previously sent and observed in the approved inbox. The user wrote and confirmed a real reply, stored with unchanged wording and test-human attribution. New worker verification read only that known thread using actual Gmail, quarantined it in a disposable DB clone, accepted it through the local HTTP confirmation route and verified unchanged chat readback. No second email or model call occurred. Broader concurrency/fault cases use fake providers, actual SQLite and owned HTTP.
- Earlier live model evidence: a genuine 20-turn conversation plus five recall/restart probes passed 12 scoped checks. A small paired pilot gave both models 18/18; GPT-4.1-mini was faster/cheaper (n=12 general responses/model: median 3.91 s vs 11.11 s; p95 4.52 s vs 18.86 s). These earlier build-specific results are not a fresh benchmark of every final edit. Streaming/TTFT was not measured.
- Earlier actual DSPy MIPROv2 experiment: reviewed baseline/candidate 5/6 each. The original verdicts and false-positive reviews remain in private local evidence. No improvement or prompt promotion. Real health data is not automatically training data; clinical rules and action permissions remain outside optimization.

## Failure review and export safeguards

A native-OpenAI runner refusal fixture initially inherited the app's newer OpenRouter default and dispatched zero fake requests. Attribution: test/harness configuration, not model failure. It now explicitly selects its pinned native model; original error-route, fake-call and accounting assertions remain intact. A subsequent stale-snapshot rejection was honored and the source was re-frozen before testing.

The exported accounting floor preserves $2.30972285 of conservative cumulative model exposure against the authorized $5, including retained uncertain reservations. This is not confirmed charged spend. No further paid calls were made for repository completion. Cloning cannot erase the exported floor. Consumed optimizer holdout hashes are exported without private datasets/receipts and block reuse as fresh evaluation.

## Remaining limits

This is a local assignment prototype. No medical/clinical validation, clinician integration, guaranteed notifications during sleep, general external-reviewer identity verification, token streaming or proven automatic quality growth. Self-mail replies still require explicit owner authorship confirmation. The worker handles the first accepted response per case. A candidate without verified gains/regression/safety checks cannot be promoted; more use does not establish intelligence growth.

Credentials, local household databases, actual email/reply captures, private provider receipts and full internal QA logs are intentionally not published. Run the included suites to reproduce software checks; do not label fixture checks as live-model results. See [operations](OPERATIONS.md) and [learning](LEARNING.md).
