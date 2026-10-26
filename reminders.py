"""Durable one-shot local reminders. Delivery is an atomic SQLite test-sink receipt.

No OS notifications, email, SMS or medical dosing. A sleeping/stopped Mac cannot fire
on time; an enabled worker catches up on its next poll. DST gaps/folds need a new time.
"""
import argparse
import hashlib
import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from outbox import Outbox


class ReminderError(ValueError):
    pass


def utc_due(day, clock, zone, fold=None):
    try:
        tz = ZoneInfo(zone)
        local = datetime.fromisoformat(day + 'T' + clock)
        if local.tzinfo is not None:
            raise ValueError()
        aware = local.replace(tzinfo=tz, fold=0 if fold is None else fold)
        if aware.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != local:
            raise ReminderError('That local time does not exist because the clock changes. Choose another time.')
        if fold is None and aware.utcoffset() != local.replace(tzinfo=tz, fold=1).utcoffset():
            raise ReminderError('That local time occurs twice because the clock changes. Choose an unambiguous time.')
        return aware.timestamp()
    except ReminderError:
        raise
    except (ValueError, ZoneInfoNotFoundError):
        raise ReminderError('Use an ISO date, HH:MM time, and an IANA timezone such as Asia/Kolkata.') from None


class Reminders:
    def __init__(self, path, clock=time.time, grace_seconds=86400):
        self.db = sqlite3.connect(str(path), timeout=5)
        self.clock = clock
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS reminders(
            id TEXT PRIMARY KEY, household TEXT NOT NULL, text TEXT NOT NULL,
            day TEXT NOT NULL, local_time TEXT NOT NULL, zone TEXT NOT NULL, due REAL NOT NULL,
            status TEXT NOT NULL, version INTEGER NOT NULL, dedup TEXT NOT NULL,
            UNIQUE(household,dedup));
          CREATE TABLE IF NOT EXISTS notification_sink(
            reminder_id TEXT NOT NULL, version INTEGER NOT NULL, household TEXT NOT NULL,
            text TEXT NOT NULL, due REAL NOT NULL, received REAL NOT NULL,
            PRIMARY KEY(reminder_id,version));
        ''')
        self.db.row_factory = sqlite3.Row
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(reminders)')}
        for name, ddl in [('offsets', "TEXT NOT NULL DEFAULT '[0]'"), ('fold', 'INTEGER')]:
            if name not in columns:
                self.db.execute('ALTER TABLE reminders ADD COLUMN ' + name + ' ' + ddl)
        self.db.commit()
        self.outbox = Outbox(self.db, self.clock, grace_seconds)
        with self.db:
            for row in self.db.execute("SELECT * FROM reminders r WHERE status='pending' AND NOT EXISTS (SELECT 1 FROM reminder_occurrences o WHERE o.reminder_id=r.id)").fetchall():
                self.outbox.revise(dict(row), json.loads(row['offsets']))

    def close(self):
        self.db.close()

    def create(self, household, text, day, clock, zone, offsets=None, *, request_id=None, new_event=False, fold=None):
        if not isinstance(household, str) or not household or not isinstance(text, str) or not 0 < len(text.strip()) <= 500:
            raise ReminderError('Supply a household and a short reminder label.')
        due = utc_due(day, clock, zone, fold)
        if due <= self.clock():
            raise ReminderError('Choose a future time. No reminder was saved.')
        offsets = [0] if offsets is None else offsets
        fingerprint = hashlib.sha256(json.dumps([text.strip(), day, clock, zone, offsets, fold], sort_keys=True).encode()).hexdigest()
        if request_id:
            previous = self.db.execute('SELECT * FROM reminder_requests WHERE household=? AND request_id=?', (household,request_id)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint: raise ReminderError('Request ID was already used for another reminder.')
                return self.get(household,previous['reminder_id'])
        dedup = hashlib.sha256(json.dumps([text.strip(), due], sort_keys=True).encode()).hexdigest()
        if new_event: dedup += ':' + uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO reminders(id,household,text,day,local_time,zone,due,status,version,dedup,offsets,fold) VALUES (?,?,?,?,?,?,?,?,1,?,?,?)',
                            (uuid.uuid4().hex[:12],household,text.strip(),day,clock,zone,due,'pending',dedup,json.dumps(offsets),fold))
            row = self.db.execute('SELECT * FROM reminders WHERE household=? AND dedup=?',(household,dedup)).fetchone()
            if not self.db.execute('SELECT 1 FROM reminder_occurrences WHERE reminder_id=?',(row['id'],)).fetchone():
                self.outbox.revise(dict(row), offsets)
            if request_id:
                self.db.execute('INSERT INTO reminder_requests VALUES (?,?,?,?)',(household,request_id,fingerprint,row['id']))
        return self.get(household,row['id'])

    def get(self, household, rid):
        row = self.db.execute('SELECT * FROM reminders WHERE household=? AND id=?', (household, rid)).fetchone()
        if not row:
            raise ReminderError('No reminder with that ID exists in this household.')
        return dict(row)

    def edit(self, household, rid, day, clock, zone, offsets=None, fold=None):
        due = utc_due(day, clock, zone, fold)
        if due <= self.clock():
            raise ReminderError('Choose a future time.')
        current = self.get(household, rid)
        dedup = hashlib.sha256(json.dumps([current['text'], due], sort_keys=True).encode()).hexdigest()
        try:
            with self.db:
                changed = self.db.execute("UPDATE reminders SET day=?,local_time=?,zone=?,due=?,dedup=?,status='pending',version=version+1 WHERE household=? AND id=? AND status='pending'",
                                          (day, clock, zone, due, dedup, household, rid))
                if changed.rowcount == 1:
                    self.db.execute('UPDATE reminders SET fold=? WHERE id=?',(fold,rid))
                    event = self.get(household,rid)
                    self.outbox.revise(event, json.loads(current['offsets']) if offsets is None else offsets)
        except sqlite3.IntegrityError:
            raise ReminderError('Another reminder already has that label and time; no edit was saved.') from None
        if changed.rowcount != 1:
            raise ReminderError('Only a pending reminder in this household can be edited.')
        return self.get(household, rid)

    def cancel(self, household, rid):
        with self.db:
            changed = self.db.execute("UPDATE reminders SET status='cancelled',version=version+1 WHERE household=? AND id=? AND status='pending'", (household, rid))
            if changed.rowcount != 1:
                raise ReminderError('No pending reminder with that ID exists in this household.')
            inflight = self.outbox.cancel(household,rid)
        result = self.get(household,rid)
        result['inflight_warning'] = bool(inflight)
        return result

    def tick(self):
        """Atomic local sink only. External email needs a separately authorized adapter."""
        return self.outbox.tick_local()

    def receipts(self, household):
        return [dict(r) for r in self.db.execute('SELECT * FROM occurrence_sink WHERE household=? ORDER BY due', (household,))]

    def set_offsets(self, household, rid, offsets):
        row = self.get(household,rid)
        return self.edit(household,rid,row['day'],row['local_time'],row['zone'],offsets,fold=row['fold'])


    def list(self, household):
        return [dict(r) for r in self.db.execute('SELECT * FROM reminders WHERE household=? ORDER BY due', (household,))]


def command(store, household, message, now, enabled=False, context=None):
    """Small explicit local grammar; ambiguous timezones/dates are never guessed."""
    if context is not None:
        from reminder_dialogue import respond
        response = respond(store,household,message,now,enabled,context)
        if response is not None: return response
    lower = message.lower().strip()
    if not re.search(r'\bremind(?:er|ers)?\b', lower):
        return None
    if not enabled:
        return 'Local reminders are disabled. Start the server with --reminder-worker to enable the SQLite delivery sink; no reminder was saved.'
    if lower in ('list reminders', 'show reminders'):
        return json.dumps(store.list(household), ensure_ascii=False)
    match = re.fullmatch(r'cancel reminder ([a-f0-9]{12})[.!]?', lower)
    if match:
        row=store.cancel(household, match[1])
        return 'Cancelled pending notices for local reminder '+match[1]+'.'+(' A submission is in flight or unknown; cancellation cannot recall it.' if row['inflight_warning'] else '')
    # Normalize a narrow time/date grammar; the user's timezone determines tomorrow.
    original = message
    zone_match = re.search(r'\bin ([A-Za-z_]+/[A-Za-z_/+-]+|UTC)\b', message)
    if zone_match and re.search(r'\b(?:today|tomorrow)\b', message, re.I):
        try:
            local_day = datetime.fromtimestamp(now, ZoneInfo(zone_match[1])).date()
            def day_word(hit):
                day = local_day + timedelta(days=1 if hit[0].lower() == 'tomorrow' else 0)
                return 'on ' + day.isoformat()
            message = re.sub(r'\b(?:today|tomorrow)\b', day_word, message, count=1, flags=re.I)
        except ZoneInfoNotFoundError:
            raise ReminderError('Use a valid IANA timezone.') from None
    def clock_word(hit):
        hour, minute, half = int(hit[1]), int(hit[2] or 0), hit[3].lower()
        if not 1 <= hour <= 12 or minute > 59:
            raise ReminderError('Use a valid clock time.')
        return f'{hour % 12 + (12 if half == "p" else 0):02}:{minute:02}'
    message = re.sub(r'\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?', clock_word, message, count=1, flags=re.I)
    # Explicit-date grammar also supports editing a pending reminder.
    match = re.fullmatch(r'(?:remind me|set (?:a )?reminder|edit reminder ([a-f0-9]{12})) (?:at|to|for) (\d{1,2}):(\d{2}) on (\d{4}-\d{2}-\d{2}) in ([A-Za-z_]+/[A-Za-z_/+-]+|UTC)(?: to (.+?))?[.!]?', message.strip(), re.I)
    if not match:
        return 'Use an explicit date, time and timezone: “Remind me at 06:00 on 2026-10-25 in Asia/Kolkata to take my medicine.” To edit/cancel, specify the reminder ID. No reminder was saved.'
    rid, hour, minute, day, zone, text = match.groups()
    clock = f'{int(hour):02}:{minute}'
    row = store.edit(household, rid.lower(), day, clock, zone) if rid else store.create(household, text or '', day, clock, zone)
    return f"Local reminder {row['id']} is {row['status']} for {row['day']} at {row['local_time']} {row['zone']}. Delivery is only to the SQLite sink while the worker runs; it does not send a phone or OS notification."


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--household', required=True)
    parser.add_argument('--tick', action='store_true')
    parser.add_argument('--receipts', action='store_true')
    args = parser.parse_args()
    sink = Reminders(args.db)
    try:
        print(json.dumps(sink.receipts(args.household) if args.receipts else {'delivered': sink.tick()} if args.tick else sink.list(args.household), indent=2))
    finally:
        sink.close()
