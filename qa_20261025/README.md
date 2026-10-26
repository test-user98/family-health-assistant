# Controlled QA helpers

Only selected credential-free source helpers are distributed. Private actual transcripts, SQLite, token/receipt records and local interactive scripts are not in Git. See ../VALIDATION.md for the evidence summary.

The core test command is `.venv-qa/bin/python -m unittest discover -s tests -v` from the root. Before runner self-tests, run `python3 tools/freeze_qa.py` from the root. Then run `.venv-qa/bin/python -m unittest discover -s qa_20261025 -p "test_live*.py" -v`. The snapshot contains only source, no credentials or private receipts. Runner unit tests use injected fake transports and do not dispatch paid calls. Historical live runners fail closed unless their verified-source snapshot, reviewed budget ledger, explicit authorization and safe transient key input exist. Those private prerequisites are intentionally not bundled. Do not treat a fresh checkout as a fresh spending authorization or reuse consumed held-out datasets.

Full optimizer experiments require a new reviewed untouched holdout and separately verified cumulative accounting. An equal/worse candidate must not be promoted. Safety policy cannot be optimized.

The exported `accounting_floor.json` carries prior conservative exposure when private receipts are missing. `evaluation_seals.json` preserves the examined holdout seal across clean checkouts. Neither is a credential or a budget reset. Historical default experiment datasets cannot be reused as fresh holdout evidence.
