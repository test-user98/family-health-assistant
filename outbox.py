"""Versioned reminder occurrences, local atomic sink, leases and conservative email attempts."""
import json
import sqlite3
import uuid
from delivery import (DeliveryError, NotAuthorized, OutcomeUnknown, RetryableBeforeSend,
                      evidence_summary, verify_webhook, validated_mailbox_link)


class Outbox:
    def __init__(self, db, clock, grace_seconds=86400):
        self.db, self.clock, self.grace = db, clock, grace_seconds
        db.executescript('''
          CREATE TABLE IF NOT EXISTS reminder_occurrences(
            id TEXT PRIMARY KEY, reminder_id TEXT NOT NULL, household TEXT NOT NULL,
            revision INTEGER NOT NULL, offset_minutes INTEGER NOT NULL, due REAL NOT NULL,
            expires REAL NOT NULL, state TEXT NOT NULL, token TEXT, lease_until REAL,
            attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
            UNIQUE(reminder_id,revision,offset_minutes));
          CREATE TABLE IF NOT EXISTS occurrence_sink(
            occurrence_id TEXT PRIMARY KEY, reminder_id TEXT NOT NULL, version INTEGER NOT NULL,
            household TEXT NOT NULL, text TEXT NOT NULL, due REAL NOT NULL, received REAL NOT NULL,
            offset_minutes INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS delivery_attempts(
            id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL, household TEXT NOT NULL,
            started REAL NOT NULL, state TEXT NOT NULL, error_code TEXT);
          CREATE TABLE IF NOT EXISTS delivery_evidence(
            id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL, household TEXT NOT NULL,
            kind TEXT NOT NULL, at REAL NOT NULL, provider_id TEXT, origin TEXT NOT NULL,
            UNIQUE(occurrence_id,id));
          CREATE TABLE IF NOT EXISTS scheduler_health(
            worker TEXT PRIMARY KEY, heartbeat REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS email_routes(reminder_id TEXT PRIMARY KEY, household TEXT NOT NULL, sender_hash TEXT NOT NULL, recipient_hash TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS mailbox_links(occurrence_id TEXT PRIMARY KEY, household TEXT NOT NULL, url TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS reminder_requests(
            household TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            reminder_id TEXT NOT NULL, PRIMARY KEY(household,request_id));
        ''')
        if 'channel' not in {r[1] for r in db.execute('PRAGMA table_info(reminder_occurrences)')}:
            db.execute("ALTER TABLE reminder_occurrences ADD COLUMN channel TEXT NOT NULL DEFAULT 'local'")
            db.commit()

    def route_email(self, household, rid, authorization):
        import hashlib
        authorization.check(household)
        row=self.db.execute('SELECT id FROM reminders WHERE id=? AND household=?',(rid,household)).fetchone()
        if not row:raise NotAuthorized('No authorized reminder.')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO email_routes VALUES (?,?,?,?)',(rid,household,hashlib.sha256(authorization.sender.encode()).hexdigest(),hashlib.sha256(authorization.recipient.encode()).hexdigest()))
            self.db.execute("UPDATE reminder_occurrences SET channel='email' WHERE reminder_id=? AND household=? AND state='pending'",(rid,household))

    def route_local(self, household, rid):
        with self.db:
            self.db.execute('DELETE FROM email_routes WHERE reminder_id=? AND household=?',(rid,household))
            self.db.execute("UPDATE reminder_occurrences SET channel='local' WHERE reminder_id=? AND household=? AND state IN ('pending','claimed')",(rid,household))

    def occurrences(self, household, reminder_id=None):
        sql, args = 'SELECT * FROM reminder_occurrences WHERE household=?', [household]
        if reminder_id:
            sql += ' AND reminder_id=?'; args.append(reminder_id)
        return [dict(x) for x in self.db.execute(sql + ' ORDER BY revision,due', args)]

    def revise(self, event, offsets):
        offsets = sorted(set(offsets), reverse=True)
        if not offsets or len(offsets) > 8 or any(type(x) is not int or not 0 <= x <= 1440 for x in offsets):
            raise ValueError('Use one to eight offsets from zero to 1440 minutes.')
        now = self.clock()
        self.db.execute("UPDATE reminder_occurrences SET state='superseded' WHERE reminder_id=? AND state IN ('pending','claimed')", (event['id'],))
        for offset in offsets:
            due = event['due'] - offset * 60
            # A prealert is never emitted after its event; already-past newly added alerts are skipped.
            expiry = event['due'] if offset else event['due'] + self.grace
            state = 'skipped_past' if offset and due < now else 'pending'
            self.db.execute('INSERT INTO reminder_occurrences(id,reminder_id,household,revision,offset_minutes,due,expires,state) VALUES (?,?,?,?,?,?,?,?)',
                            (uuid.uuid4().hex, event['id'], event['household'], event['version'], offset, due, expiry, state))
        self.db.execute('UPDATE reminders SET offsets=? WHERE id=?', (json.dumps(offsets), event['id']))

    def cancel(self, household, rid):
        self.db.execute("UPDATE reminder_occurrences SET state='cancelled' WHERE household=? AND reminder_id=? AND state IN ('pending','claimed')", (household, rid))
        return self.db.execute("SELECT count(*) FROM reminder_occurrences WHERE household=? AND reminder_id=? AND state IN ('dispatching','outcome_unknown')", (household, rid)).fetchone()[0]

    def evidence(self, oid, household, kind, provider_id=None, origin='local', event_id=None):
        self.db.execute('INSERT OR IGNORE INTO delivery_evidence VALUES (?,?,?,?,?,?,?)',
                        (event_id or uuid.uuid4().hex, oid, household, kind, self.clock(), provider_id, origin))

    def _expire(self):
        now = self.clock()
        rows = self.db.execute("SELECT * FROM reminder_occurrences WHERE state='pending' AND (expires<? OR (offset_minutes>0 AND expires<=?))", (now,now)).fetchall()
        for row in rows:
            self.db.execute("UPDATE reminder_occurrences SET state='expired' WHERE id=?", (row['id'],))
            self.evidence(row['id'], row['household'], 'expired')

    def tick_local(self):
        now = self.clock()
        try:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT OR REPLACE INTO scheduler_health VALUES (?,?)', ('local-sink', now))
            self._expire()
            rows = self.db.execute("""SELECT o.*,r.text FROM reminder_occurrences o JOIN reminders r ON r.id=o.reminder_id
              WHERE o.state='pending' AND o.channel='local' AND o.due<=? AND o.next_attempt<=? AND r.status='pending' AND r.version=o.revision""", (now, now)).fetchall()
            for row in rows:
                self.db.execute('INSERT OR IGNORE INTO occurrence_sink VALUES (?,?,?,?,?,?,?,?)',
                                (row['id'], row['reminder_id'], row['revision'], row['household'], row['text'], row['due'], now, row['offset_minutes']))
                if row['offset_minutes'] == 0:
                    self.db.execute('INSERT OR IGNORE INTO notification_sink VALUES (?,?,?,?,?,?)',
                                    (row['reminder_id'], row['revision'], row['household'], row['text'], row['due'], now))
                self.evidence(row['id'], row['household'], 'local_recorded')
                self.db.execute("UPDATE reminder_occurrences SET state='local_recorded' WHERE id=?", (row['id'],))
            self.db.execute("""UPDATE reminders SET status='delivered' WHERE status='pending' AND NOT EXISTS
              (SELECT 1 FROM reminder_occurrences o WHERE o.reminder_id=reminders.id AND o.revision=reminders.version AND o.state IN ('pending','claimed','dispatching','outcome_unknown')) AND EXISTS (SELECT 1 FROM reminder_occurrences o WHERE o.reminder_id=reminders.id AND o.revision=reminders.version AND o.state='local_recorded')""")
            self.db.execute("""UPDATE reminders SET status='expired' WHERE status='pending' AND NOT EXISTS (SELECT 1 FROM reminder_occurrences o WHERE o.reminder_id=reminders.id AND o.revision=reminders.version AND o.state IN ('pending','claimed','dispatching','outcome_unknown','local_recorded','provider_accepted'))""")
            self.db.execute("""UPDATE reminders SET status='provider_accepted' WHERE status='pending' AND NOT EXISTS (SELECT 1 FROM reminder_occurrences o WHERE o.reminder_id=reminders.id AND o.revision=reminders.version AND o.state IN ('pending','claimed','dispatching','outcome_unknown')) AND EXISTS (SELECT 1 FROM reminder_occurrences o WHERE o.reminder_id=reminders.id AND o.revision=reminders.version AND o.state='provider_accepted')""")
            self.db.commit(); return len(rows)
        except BaseException:
            self.db.rollback(); raise

    def health(self, household=None):
        now = self.clock()
        last = self.db.execute('SELECT max(heartbeat) FROM scheduler_health').fetchone()[0]
        oldest = self.db.execute("SELECT min(due) FROM reminder_occurrences WHERE state='pending' AND due<=? AND (? IS NULL OR household=?)", (now,household,household)).fetchone()[0]
        age = None if last is None else now - last
        email_pending=self.db.execute("SELECT count(*) FROM reminder_occurrences WHERE channel='email' AND state IN ('pending','claimed','dispatching','outcome_unknown') AND (? IS NULL OR household=?)",(household,household)).fetchone()[0]
        email_heartbeat=self.db.execute("SELECT max(heartbeat) FROM scheduler_health WHERE worker LIKE 'email:%'").fetchone()[0]
        email_degraded=bool(email_pending and (email_heartbeat is None or not 0<=now-email_heartbeat<=15))
        return {'worker': 'degraded' if age is None or age < 0 or age > 15 or email_degraded else 'recent_heartbeat', 'email_worker_degraded':email_degraded,
                'heartbeat_at': last, 'clock_anomaly': age is not None and age < 0,
                'oldest_due_age_seconds': None if oldest is None else max(0, now-oldest),
                'guaranteed_delivery': False}

    def claim(self, worker, lease_seconds=30):
        import re
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',worker):raise ValueError('Use an opaque technical worker ID.')
        now = self.clock()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT OR REPLACE INTO scheduler_health VALUES (?,?)',('email:'+worker,now))
            # A crash before send can be reclaimed. After send started, outcome must be reconciled.
            self.db.execute("UPDATE reminder_occurrences SET state='pending',token=NULL WHERE state='claimed' AND lease_until<?", (now,))
            unknown=self.db.execute("SELECT * FROM reminder_occurrences WHERE state='dispatching' AND lease_until<?",(now,)).fetchall()
            for item in unknown:self.evidence(item['id'],item['household'],'outcome_unknown',event_id='unknown-'+item['id'])
            self.db.execute("UPDATE reminder_occurrences SET state='outcome_unknown' WHERE state='dispatching' AND lease_until<?", (now,))
            self._expire()
            row = self.db.execute("""SELECT o.* FROM reminder_occurrences o JOIN reminders r ON r.id=o.reminder_id
                WHERE o.state='pending' AND o.channel='email' AND o.due<=? AND o.next_attempt<=? AND o.attempts<3
                AND o.revision=r.version AND r.status='pending' ORDER BY o.due LIMIT 1""", (now, now)).fetchone()
            if not row: return None
            token = uuid.uuid4().hex
            self.db.execute("UPDATE reminder_occurrences SET state='claimed',token=?,lease_until=? WHERE id=?", (token, now+lease_seconds, row['id']))
            return dict(row, token=token, worker=worker)

    def dispatch(self, claim, adapter, *, after_accept=None):
        """Authorization and final revision check precede send. Cancellation cannot recall in-flight mail."""
        adapter.authorization.check(claim['household'])
        import hashlib
        route=self.db.execute('SELECT * FROM email_routes WHERE household=? AND reminder_id=?',(claim['household'],claim['reminder_id'])).fetchone()
        if not route or route['sender_hash']!=hashlib.sha256(adapter.authorization.sender.encode()).hexdigest() or route['recipient_hash']!=hashlib.sha256(adapter.authorization.recipient.encode()).hexdigest():
            raise NotAuthorized('Sender/recipient authorization does not match this reminder.')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute("""SELECT o.* FROM reminder_occurrences o JOIN reminders r ON r.id=o.reminder_id
              WHERE o.id=? AND o.token=? AND o.state='claimed' AND o.channel='email' AND r.status='pending' AND r.version=o.revision""",
              (claim['id'], claim['token'])).fetchone()
            if not row or row['expires'] < self.clock() or (row['offset_minutes']>0 and row['expires']<=self.clock()) or row['lease_until'] < self.clock():
                return False
            aid = uuid.uuid4().hex
            self.db.execute("UPDATE reminder_occurrences SET state='dispatching',attempts=attempts+1 WHERE id=?", (row['id'],))
            self.db.execute('INSERT INTO delivery_attempts VALUES (?,?,?,?,?,NULL)', (aid,row['id'],row['household'],self.clock(),'started'))
        try:
            accepted = adapter.send(row['household'], row['id'])
            if accepted.get('kind') != 'provider_accepted':
                raise OutcomeUnknown('No valid provider acceptance evidence.')
            if after_accept: after_accept()  # Testable accepted-before-ack crash boundary.
            with self.db:
                self.evidence(row['id'],row['household'],'provider_accepted',accepted.get('provider_id'),accepted.get('origin','provider'))
                self.db.execute("UPDATE reminder_occurrences SET state='provider_accepted' WHERE id=?", (row['id'],))
                self.db.execute("UPDATE delivery_attempts SET state='provider_accepted' WHERE id=?", (aid,))
            return True
        except Exception as error:
            safe_retry = isinstance(error, RetryableBeforeSend) and row['attempts']+1 < 3
            state = 'pending' if safe_retry else 'failed' if isinstance(error, DeliveryError) and not isinstance(error, OutcomeUnknown) else 'outcome_unknown'
            code = error.code if isinstance(error, DeliveryError) else 'unclassified_submission_failure'
            with self.db:
                self.db.execute('UPDATE reminder_occurrences SET state=?,next_attempt=? WHERE id=?', (state,self.clock()+min(300,5*2**row['attempts']),row['id']))
                self.db.execute('UPDATE delivery_attempts SET state=?,error_code=? WHERE id=?', (state,code,aid))
                if state == 'outcome_unknown': self.evidence(row['id'],row['household'],state)
            return False

    def reconcile(self, household, oid, adapter):
        adapter.authorization.check(household)
        row = self.db.execute("SELECT * FROM reminder_occurrences WHERE id=? AND household=? AND state='outcome_unknown'", (oid,household)).fetchone()
        import hashlib
        route=self.db.execute('SELECT * FROM email_routes WHERE household=? AND reminder_id=?',(household,row['reminder_id'] if row else '')).fetchone()
        if not route or route['recipient_hash']!=hashlib.sha256(adapter.authorization.recipient.encode()).hexdigest() or route['sender_hash']!=hashlib.sha256(adapter.authorization.sender.encode()).hexdigest():raise NotAuthorized('Reconciliation is not authorized for that route.')
        if not row or not getattr(adapter,'supports_reconciliation',False): return 'outcome_unknown'
        result = adapter.reconcile(oid)  # Must be a documented provider lookup, never an inferred retry.
        if result and result.get('kind') == 'provider_accepted':
            with self.db:
                self.evidence(oid,household,'provider_accepted',result.get('provider_id'),result.get('origin','provider'))
                self.db.execute("UPDATE reminder_occurrences SET state='provider_accepted' WHERE id=?", (oid,))
            return 'provider_accepted'
        return 'outcome_unknown'

    def ingest(self, body, signature, secret):
        data = verify_webhook(body, signature, secret)
        if set(data) != {'id','occurrence_id','provider_id','kind'} or data['kind'] not in ('receiving_server_delivered','bounced','deferred'):
            raise ValueError('Unsupported delivery event.')
        row = self.db.execute("SELECT * FROM delivery_evidence WHERE occurrence_id=? AND provider_id=? AND kind='provider_accepted'", (data['occurrence_id'],data['provider_id'])).fetchone()
        if not row: raise NotAuthorized('No matching authorized submission.')
        with self.db:
            self.evidence(row['occurrence_id'],row['household'],data['kind'],data['provider_id'],'mock' if row['origin']=='mock' else 'provider',data['id'])

    def proof(self, household, rid):
        rows = [dict(r) for r in self.db.execute('SELECT e.* FROM delivery_evidence e JOIN reminder_occurrences o ON o.id=e.occurrence_id WHERE e.household=? AND o.reminder_id=? ORDER BY e.at,e.id',(household,rid))]
        link=self.db.execute('SELECT l.url FROM mailbox_links l JOIN reminder_occurrences o ON o.id=l.occurrence_id WHERE l.household=? AND o.reminder_id=?',(household,rid)).fetchone()
        return {'summary':evidence_summary(rows),'evidence':rows,'mailbox_link':link[0] if link else None,'occurrences':self.occurrences(household,rid)}

    def observe_mailbox(self, household, oid, *, provider_id, account, authorized_account, url, verified=False, origin='mailbox'):
        link=validated_mailbox_link(url,observed_account=account,authorized_account=authorized_account,verified=verified)
        row=self.db.execute("SELECT 1 FROM delivery_evidence WHERE household=? AND occurrence_id=? AND provider_id=? AND kind='provider_accepted'",(household,oid,provider_id)).fetchone()
        if not row:raise NotAuthorized('No matching authorized submission to observe.')
        with self.db:
            self.evidence(oid,household,'mailbox_observed',provider_id,origin)
            self.db.execute('INSERT OR REPLACE INTO mailbox_links VALUES (?,?,?)',(oid,household,link))

    def invalidate_mailbox_link(self, household, oid):
        with self.db:
            self.db.execute('DELETE FROM mailbox_links WHERE household=? AND occurrence_id=?',(household,oid))

    def dispatch_due(self, adapter, worker='authorized-email-worker'):
        adapter.authorization.check(adapter.authorization.household)
        claim=self.claim(worker)
        return bool(claim and self.dispatch(claim,adapter))
