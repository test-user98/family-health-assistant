"""Opt-in neutral synthetic review worker. No general mailbox search or automatic review badge.

Only persisted, known test threads are read. Bound replies wait for the household
owner to attest authorship in the local UI. Credentials never enter the database.
"""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time

from gmail_transport import NeutralGmail, bound_self_reply, GmailError
from human_review import HumanReview, HMACReplyAdapter, ReviewError


class GmailReviewWorker:
    def __init__(self, path, directory, *, config, factory=NeutralGmail, clock=time.time):
        options = config.get('human_review', {})
        if not (options.get('enabled') and options.get('synthetic_only') is True
                and options.get('reviewer') == options.get('inbox') == 'sipanijai@gmail.com'):
            raise ReviewError('Explicit synthetic-only personal self-review configuration required.')
        self.db = sqlite3.connect(str(path), timeout=10)
        self.clock, self.directory, self.factory = clock, directory, factory
        self.service = HumanReview(self.db, reviewer=options['reviewer'], inbox=options['inbox'], clock=clock)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS gmail_review_receipts(
            case_id TEXT PRIMARY KEY, receipt TEXT NOT NULL, observed REAL);
        CREATE TABLE IF NOT EXISTS gmail_pending_replies(
            event_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL, found REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS gmail_worker_status(
            id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL NOT NULL, outcome TEXT NOT NULL);
        ''')
        self.db.commit()

    def close(self):
        self.db.close()

    def _receipt(self, case_id, receipt):
        # Persist acceptance before dispatch acknowledgement; never resend after a crash.
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO gmail_review_receipts VALUES (?,?,NULL)',
                            (case_id, json.dumps(receipt)))

    def tick(self):
        mail = None
        outcome = 'idle'
        try:
            queued = self.db.execute("SELECT id FROM human_cases WHERE status='queued' AND revoked=0 ORDER BY created LIMIT 5").fetchall()
            known = self.db.execute("SELECT g.case_id,g.receipt FROM gmail_review_receipts g JOIN human_cases c ON c.id=g.case_id WHERE c.revoked=0 AND c.status IN ('notified','notification_pending','notification_unknown') ORDER BY c.created LIMIT 10").fetchall()
            if not queued and not known:
                return
            mail = self.factory(self.directory, synthetic=True)
            mail.authenticate()
            for (case_id,) in queued:
                mail.receipt_sink = lambda receipt, cid=case_id: self._receipt(cid, receipt)
                try:
                    self.service.dispatch(case_id, mail)
                except ReviewError:
                    # A concurrent claim or uncertain send is never an automatic retry.
                    pass
            known = self.db.execute("SELECT g.case_id,g.receipt FROM gmail_review_receipts g JOIN human_cases c ON c.id=g.case_id WHERE c.revoked=0 AND c.status IN ('notified','notification_pending','notification_unknown') ORDER BY c.created LIMIT 10").fetchall()
            for case_id, raw in known:
                mail.receipt = json.loads(raw)
                if 'provider_rfc_message_id' not in mail.receipt:
                    mail.receipt = mail.observe_sent_message()
                    with self.db:
                        self.db.execute('UPDATE gmail_review_receipts SET receipt=?,observed=? WHERE case_id=?',
                                        (json.dumps(mail.receipt), self.clock(), case_id))
                # Durable provider acceptance reconciles the local acknowledgement crash.
                with self.db:
                    self.db.execute("UPDATE human_cases SET status='notified' WHERE id=? AND revoked=0 AND status IN ('notification_pending','notification_unknown')", (case_id,))
                thread = mail.read_known_thread()
                if len(thread.get('messages', [])) <= 1:
                    continue
                reply = bound_self_reply(thread, mail.receipt, mailbox='sipanijai@gmail.com')
                with self.db:
                    self.db.execute("INSERT OR IGNORE INTO gmail_pending_replies VALUES (?,?,?,'pending_owner_confirmation',?)",
                                    (reply['event_id'], case_id, json.dumps(reply), self.clock()))
            outcome = 'checked_known_threads'
        except Exception:
            # Exception strings can contain provider payloads; expose only a safe state.
            outcome = 'degraded_check_local_configuration_or_known_thread'
        finally:
            if mail:
                mail.clear()
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO gmail_worker_status VALUES (1,?,?)', (self.clock(), outcome))

    def pending(self, household, allowed=None):
        rows = self.db.execute("SELECT p.event_id,p.case_id,p.payload,c.member_id FROM gmail_pending_replies p JOIN human_cases c ON p.case_id=c.id WHERE c.household=? AND c.revoked=0 AND c.status='notified' AND p.status='pending_owner_confirmation'", (household,)).fetchall()
        return [dict(event_id=eid, case_id=cid, text=json.loads(payload)['text'], status='pending_owner_confirmation')
                for eid, cid, payload, member in rows if allowed is None or member in allowed]

    def confirm(self, household, event_id, *, attest=False, allowed=None):
        if attest is not True:
            raise ReviewError('Explicit authorship confirmation required.')
        row = self.db.execute('SELECT p.case_id,p.payload,c.member_id,c.episode_id,c.challenge,c.household FROM gmail_pending_replies p JOIN human_cases c ON c.id=p.case_id WHERE p.event_id=?', (event_id,)).fetchone()
        if not row or row[5] != household or allowed is not None and row[2] not in allowed:
            raise ReviewError('No reply in this household.')
        cid, payload, member, episode, challenge, _ = row
        reply = json.loads(payload)
        old = self.db.execute('SELECT sequence FROM human_replies WHERE event_id=?', (event_id,)).fetchone()
        seq = old[0] if old else self.db.execute('SELECT coalesce(max(sequence),0)+1 FROM human_replies WHERE case_id=?', (cid,)).fetchone()[0]
        event = dict(event_id=event_id, case_id=cid, household=household, member_id=member,
                     episode_id=episode, challenge=challenge, principal='sipanijai@gmail.com',
                     recipient='sipanijai@gmail.com', reply_to='sipanijai@gmail.com', forwarded=False,
                     sequence=seq, at=reply['at'], issued_at=self.clock(), text=reply['text'])
        event['from'] = event['principal']
        body, key = json.dumps(event).encode(), secrets.token_bytes(32)
        result = self.service.accept(body, hmac.new(key, body, hashlib.sha256).hexdigest(),
                                     HMACReplyAdapter(key, clock=self.clock))
        with self.db:
            self.db.execute("UPDATE gmail_pending_replies SET status='accepted_owner_confirmed' WHERE event_id=?", (event_id,))
        return result

    def health(self):
        row = self.db.execute('SELECT heartbeat,outcome FROM gmail_worker_status WHERE id=1').fetchone()
        return dict(worker='alive' if row and self.clock()-row[0] <= 180 else 'stale_or_not_started',
                    last_check=row[0] if row else None, outcome=row[1] if row else 'not_started',
                    guaranteed_delivery=False, human_authorship='explicit owner confirmation required')
