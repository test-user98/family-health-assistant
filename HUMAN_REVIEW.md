# Optional test human review

The core deterministic review route does not contact a clinician. This extension adds durable queued/notified/answered cases, neutral optional mail and attributed human replies. It is disabled by default and does not certify a reviewer as a doctor.

See [operation and trust requirements](OPERATIONS.md). `human_review.py` owns case binding, transactional dispatch and authenticated ingress; `gmail_worker.py` is the opt-in synthetic self-mail background adapter. `gmail_transport.py` restricts Google operations and known-thread parsing. `server.py` installs household-scoped pending/confirmation routes only when explicitly enabled. `web/index.html` uses textContent and requires owner confirmation before a review appears.

`queued` means saved locally; `notified` means transport acceptance, not inbox receipt; `answered` means an accepted case-bound response. Provider acceptance, mailbox observation and human authorship remain distinct. Plain From/thread/DKIM is not a human-identity assertion. Forwarded/forged/stale/cross-household replies and unsafe episode states fail closed. HMAC is a trusted local/server ingress contract, not a Gmail or clinical login. Authored timestamps and fresh signed ingress timestamps are separately checked.

Emergency instructions are immediate and never wait for mail. Pending notifications and response acceptance recheck the episode's state; no human response clears a deterministic safety hold. No real health details are emailed. OAuth grants and credentials are never included in Git.
