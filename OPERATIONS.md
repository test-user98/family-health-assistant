# Local operations

The base harness starts without external mail. The optional Gmail worker is deliberately limited to **neutral synthetic self-mail** for the account explicitly approved during local setup. It is not a general email sender or a doctor identity service.

## Gmail connection

Existing owner-only `.local_credentials/gmail_client.json` and `gmail_token.json` must contain an approved Desktop OAuth client and a user-mediated grant for exactly Gmail send + read-only. The directory is 0700 and files 0600. Use `gmail_oauth.DesktopPKCE` with a loopback callback, state/S256, exact mailbox/scope validation and private refresh persistence. Google required the Desktop client secret in the observed token exchange; PKCE did not remove that requirement. Never paste credentials in chat or commit them. The setup is account-specific and must not be recreated automatically from this repository.

The actually verified local mailbox is sipanijai@gmail.com. Work accounts are excluded. No mailbox-modification/delete scope is requested. Google's Testing-mode refresh grant may expire; consent/setup errors are surfaced rather than bypassed. The worker refreshes only through fixed Google endpoints and rejects redirects/account/scope mismatches.

## Enable the synthetic review worker

Make a **local, ignored** `review.local.json` copy of config.json. Add:

```json
"human_review": {
  "enabled": true,
  "synthetic_only": true,
  "reviewer": "sipanijai@gmail.com",
  "inbox": "sipanijai@gmail.com"
}
```

Run on a deliberately isolated synthetic database:

```sh
python3 server.py --db synthetic_review.db --config review.local.json --port 8001 --gmail-review-worker --reminder-worker
```

Supply a model through Model settings when needed. This opts in to sending neutral test notifications for bound non-emergency review cases. No name/symptom/history is copied into mail. A loop runs every 60 seconds while the server runs; the UI may close. The Mac must remain awake/connected. No launch agent, always-on service or sleep/wake guarantee is installed.

## Durable state and reply trust

1. A non-emergency doctor-status episode creates a queued case.
2. Transactional claim prevents competing sends. Acceptance is saved durably before the case acknowledgement. A send that may have executed is never blindly retried; an observed durable receipt reconciles a local acknowledgement crash.
3. Only the recorded Gmail message/thread is read; no inbox-wide search or enumeration. Gmail's canonical Message-ID is retained because it may rewrite the submitted ID.
4. A reply must match canonical In-Reply-To, thread, subject, approved sender/recipient, own-mailbox SENT evidence and plain-text bounds. Ambiguous/forged/forwarded/unbound input fails closed. This establishes provider/case binding, **not human authorship**.
5. The reply remains pending. In the same household's local UI, the owner explicitly clicks **I wrote this test reply**. Only then is its unchanged plain text accepted and attributed to a test human reviewer with authored/received timestamps. No doctor badge is generated.
6. Duplicate/replayed replies deduplicate; wrong household/member, revoked/stale case, and emergency hold block acceptance. Human content is never tool instructions or LLM memory. This minimal worker handles the first accepted response per case; ongoing multi-reply threads need a separately reviewed extension.

The UI polls state every five seconds while visible. `/api/health` exposes worker heartbeat/degraded status, separately from app liveness. An expired OAuth grant or stale worker is not delivery success. Closing/stopping the process prevents delivery/polling; expired prealerts are not burst-sent.

## Evidence limits

One actual neutral email was provider-accepted and observed in the approved inbox. The user authored and confirmed a real reply; it was stored and read back through HTTP chat with exact text/hash and attribution. The new background scheduling, crash and confirmation paths are tested with controlled fake transports and actual SQLite/HTTP. Do not describe those fixtures as another real email send or an unattended live-mail trial.

The local UI is a single-user prototype, not a hosted authentication service. Do not expose it on a public interface. Do not enable this test worker against real household data. Broader reviewer identity, medical responsibility and unattended production operations are outside this extension.
