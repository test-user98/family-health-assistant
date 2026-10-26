"""Evidence-linked continuity and conservative extraction checks, not clinical diagnosis."""
import hashlib
import json
import re
import uuid
from pathlib import Path


def negated(prefix):
    prefix = re.split(r'[.;!?\n]|\b(?:but|however)\b', prefix.lower())[-1]
    if re.search(r'\bnot only\s+(?:\w+\s+){0,2}$', prefix):
        return False
    return bool(re.search(r"\b(?:no|not|never|without|denies|denied|isn't|wasn't|doesn't|don't)\s+(?:\w+\s+){0,2}$", prefix))


def safety_reports(text, patterns):
    """Only asserted reports; education and explicit negation do not raise review."""
    from health_agent import educational_query
    findings = []
    for clause in re.split(r'[.;!?\n]|\b(?:but|however)\b', text.lower().replace('’', "'")):
        if educational_query(clause) or re.search(r'\b(?:what if|if someone|earlier report|last year|years ago)\b', clause):
            continue
        for field, pattern in patterns.items():
            for hit in re.finditer(pattern, clause):
                if not negated(clause[:hit.start()]):
                    findings.append({'key': field, 'value': False if field == 'hydration_ok' else True,
                                     'evidence': text})
    return {item['key']: item for item in findings}


def incompatible_member(state, member_id, text):
    """A leading person pronoun cannot contradict a supplied known member ID."""
    member = state['members'].get(member_id)
    pronoun = re.match(r'\s*(?:and\s+)?(he|she|him|her|they|them)\b', text, re.I)
    active = state.get('active_member_id')
    return bool(member and pronoun and (pronoun[1].lower() not in member.get('pronouns', []) or (active is not None and member_id != active)))


def historical_update(text):
    explicit = re.search(r'\b(?:earlier report|previous report|at that time|previously reported|yesterday.s report)\b', text, re.I)
    past_report = re.match(r'\s*(?:yesterday|last (?:week|month|year))\b', text, re.I) and re.search(r'\bhad\b', text, re.I) and not re.search(r'\b(?:still|now|today|currently)\b', text, re.I)
    return bool(explicit or past_report)


def record_context(state, text, now, member_id=None, episode_id=None):
    """Retain explicit assertions as user reports, never as verified diagnoses."""
    records = state.setdefault('context_records', [])
    preference_request = re.search(r'\b(?:keep|make|give|prefer|like|want|would like|use)\b', text, re.I)
    if preference_request and re.search(r'\b(?:answers?|replies|responses?)\b.*\b(?:concise|brief|short|detailed|long)\b|\b(?:concise|brief|short|detailed)\b.*\b(?:answers?|replies|responses?)\b', text, re.I):
        words = list(re.finditer(r'\b(concise|brief|short|detailed|long)\b', text, re.I))
        asserted = [hit for hit in words if not negated(text[:hit.start()])]
        chosen = asserted[-1] if asserted else words[-1]
        style = 'concise' if chosen[1].lower() in ('concise', 'brief', 'short') else 'detailed'
        if not asserted:
            style = 'avoid ' + style
        record_fact(records, 'answer_style', style, text, now, None, None, 'reported')
    if member_id is None:
        return
    # Raw assertions preserve anatomical and uncertainty details beyond the rolling chat.
    if not re.match(r'\s*(?:what|which|why|how|can you|could you|please recall)\b', text, re.I):
        record_fact(records, 'reported_context', text, text, now, member_id, episode_id,
                    'historical' if historical_update(text) else 'user_hypothesis' if re.search(r'\b(?:might|maybe|guess|hypothesis|possible|not confirmed)\b', text, re.I) else 'reported')
    if not re.match(r'\s*(?:what|which|why|how|can|could|does|is it)\b',text,re.I):
        for hit in re.finditer(r'\b(headache|cough|fever|nausea|nauseated)\b',text,re.I):
            label='nausea' if hit[1].lower() in ('nausea','nauseated') else hit[1].lower()
            denied=negated(text[:hit.start()])
            record_fact(records,'symptom:'+label,not denied,text,now,member_id,episode_id,'reported')
            if denied:
                for older in records:
                    if older['key']=='reported_context' and older.get('member_id')==member_id and older.get('evidence')!=text and re.search(r'\b'+label+r'(?:ted)?\b',older.get('evidence',''),re.I):older['status']='superseded'
    locations = {}
    if not historical_update(text):
        for hit in re.finditer(r'\b(left|right)\s+(ankle|knee|leg|arm|ear|eye|hand|foot|shoulder|wrist)\b', text, re.I):
            if not negated(text[:hit.start()]):
                locations.setdefault(hit[2].lower(), set()).add(hit[1].lower())
        for part, sides in locations.items():
            record_fact(records, part + '_side', next(iter(sides)) if len(sides) == 1 else 'both', text, now, member_id, episode_id, 'reported')
    # Bounded durable records, independent of the 12-turn dialogue window.
    if len(records) > 256:
        active = [r for r in records if r['key'] != 'reported_context' and r['status'] == 'active']
        older = [r for r in records if r not in active]
        state['context_records'] = (older[-(256 - len(active)):] if len(active) < 256 else []) + active[-256:]


def record_fact(records, key, value, evidence, now, member_id, episode_id, certainty):
    for row in records:
        if row['key'] == key and row.get('member_id') == member_id and row.get('episode_id') == episode_id and row.get('status') == 'active':
            if row['value'] == value and row['evidence'] == evidence:
                return
            if key != 'reported_context':
                row.update(status='superseded', superseded_at=now)
    records.append(dict(id=uuid.uuid4().hex[:12],key=key, value=value, evidence=evidence, at=now, member_id=member_id,
                        episode_id=episode_id, certainty=certainty, status='active'))


def retract_medical_facts(member, source, now):
    """Retract only an explicitly denied *known* fact; preserve its audit record."""
    for category, values in member.get('facts', {}).items():
        for value in list(values):
            pattern = re.compile(r'\b' + re.escape(value) + r'\b', re.I)
            denied = any(re.search(r"\b(?:not(?: allergic to| taking| on| diagnosed with| have| has)?|no (?:known )?(?:allergy to|history of)|denies(?: allergy to)?|stopped taking)\s*$", source[:hit.start()], re.I) and not re.search(r'\bnot only\s*$', source[:hit.start()], re.I) for hit in pattern.finditer(source))
            if denied:
                values.remove(value)
                for row in member.setdefault('fact_records', []):
                    if row['key'] == category and row['value'].casefold() == value.casefold() and row['status'] == 'active':
                        row.update(status='retracted', superseded_at=now, correction_evidence=source)
                member['fact_records'].append(dict(key=category, value=value, evidence=source, at=now, status='retracted'))


def remember(member, memories, now):
    for item in memories:
        evidence, value, key = item['evidence'], item['value'], item['key']
        hits = list(re.finditer(re.escape(value), evidence, re.I))
        if hits and all(negated(evidence[:hit.start()]) for hit in hits):
            continue
        values = member['facts'].setdefault(key, [])
        if value not in values:
            values.append(value)
        records = member.setdefault('fact_records', [])
        if not any(r['key'] == key and r['value'] == value and r['status'] == 'active' for r in records):
            records.append(dict(key=key, value=value, evidence=evidence, at=now, status='active'))


class ResponseCache:
    """SQLite-backed, household-scoped education cache. Never caches clinical routes."""
    def __init__(self, db, clock, config):
        self.db, self.clock, self.config = db, clock, config
        source = Path(__file__).parent
        self.implementation = hashlib.sha256(b''.join((source / name).read_bytes() for name in ('continuity.py','runtime.py','health_agent.py','profiles.py','conversation.py','retrieval.py','reminder_dialogue.py','reminders.py','outbox.py','delivery.py','action_claims.py','memory_context.py','memory_queries.py','token_count.py'))).hexdigest()
        if config.get('enabled', False):
            db.execute('CREATE TABLE IF NOT EXISTS response_cache (household TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, expires REAL NOT NULL, PRIMARY KEY(household,key))')
            db.commit()

    def key(self, context, config, prompts):
        context = json.loads(json.dumps(context))
        context['state'].pop('history', None)
        context['state'].pop('memory_manifest',None)  # Accounting only; selected facts remain in key.
        # No private chain of thought or credentials enter keys/values.
        data = dict(context=context, config=config, prompts=prompts, implementation=self.implementation)
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def get(self, household, key):
        if not self.config.get('enabled', False):
            return None
        row = self.db.execute('SELECT value FROM response_cache WHERE household=? AND key=? AND expires>?', (household, key, self.clock())).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, household, key, value):
        if not self.config.get('enabled', False):
            return
        with self.db:
            self.db.execute('DELETE FROM response_cache WHERE expires<=?', (self.clock(),))
            # Fixed per-household capacity; all results are validated plain response text.
            self.db.execute('INSERT OR REPLACE INTO response_cache VALUES (?,?,?,?)', (household, key, json.dumps(value), self.clock() + self.config.get('ttl_seconds', 60)))
            self.db.execute('DELETE FROM response_cache WHERE household=? AND key NOT IN (SELECT key FROM response_cache WHERE household=? ORDER BY expires DESC LIMIT ?)', (household, household, self.config.get('max_entries', 64)))


def reported_anatomy(text):
    """A user-named body site separates otherwise generic 'other' episodes."""
    parts = set(re.findall(r'\b(?:ankle|knee|leg|arm|ear|eye|hand|foot|shoulder|wrist)\b', text.lower()))
    return next(iter(parts)) if len(parts) == 1 else None
