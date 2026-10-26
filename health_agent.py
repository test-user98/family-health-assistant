"""Small stateful agent: untrusted interpretation -> validated state -> fixed policy."""
import argparse
import copy
import json
import math
import re
import sqlite3
import time
import uuid
from pathlib import Path
from profiles import conversational_turn
from continuity import remember, reported_anatomy
from question_selection import choose as choose_question

CONFIG_PATH = Path(__file__).with_name('config.json')
NUMBERS = {'duration_hours': (0, 87600), 'temperature_c': (30, 45)}
BOOLEANS = {'red_flags', 'hydration_ok', 'breathing_difficulty', 'worsening', 'fever',
            'severe_breathing', 'blue_lips', 'unresponsive', 'stroke_signs',
            'throat_swelling', 'nonblanching_rash', 'chest_pain'}
EXAMPLE = {'member_id': None, 'episode_id': None, 'intent': 'symptom', 'topic': 'fever',
           'observations': [{'key': 'severity', 'value': 'mild', 'evidence': 'mild'}],
           'memories': [], 'reply': ''}


def explicit_members(text, members):
    text = text.lower()
    found = []
    for mid, member in members.items():
        for alias in [member['name']] + member['aliases']:
            if alias == 'i' and not re.search(r"\bi (?:have|am|feel|was|had|can't|cannot)\b", text):
                continue
            if re.search(r'\b' + re.escape(alias.lower()) + r'\b', text):
                found.append(mid)
                break
    return found


def educational_query(text):
    """Recognize clear education/contingency wording, not the presence of 'my'.

    This only scopes the lexical safety fast path. The model still assesses
    ambiguous questions; explicit reports embedded in education stay clinical.
    """
    lower = text.lower().replace('’', "'")
    if re.match(r'\s*(?:what\s+)?if\b', lower):
        return not re.search(r'\b(?:now|currently|today|just|but|however)\b', lower)
    education = re.match(r'\s*(?:what (?:is|are|causes)|how (?:does|do)|explain|tell me about|'
                         r'why do (?:people|children|adults)|does (?:chest pain|a fever|a cough)|'
                         r'can (?:someone|people|a person))\b', lower)
    report = re.search(r"\b(?:i|he|she|they|(?:my|our|his|her)\s+\w+|dad|mom)\s+"
                       r"(?:have|has|had|feel|feels|is|are|was|were|cannot|can't)\b", lower)
    hypothetical_cause_question = bool(re.match(r'\s*(?:synthetic|fictional) example:',lower)
        and '?' in lower and re.search(r'\b(?:cause|explanation|hypothesis|trigger)\b',lower)
        and not re.search(r'\b(?:now|currently|actually|today)\b',lower))
    return bool((education or hypothetical_cause_question) and not report)


def current_symptoms(text):
    return bool(not educational_query(text) and re.search(r'\b(fever|feverish|cough(?:ing)?|headache|vomit(?:ing)?|dizzy|rash|pain|breathless|bleeding|symptoms|sick|unwell|getting worse|worsening|not drinking|no urine|not peeing|short of breath|difficulty breathing)\b', text, re.I))


def resolution_confirmed(text):
    for hit in re.finditer(r'\b(resolved|fully recovered|symptoms gone|all better)\b', text.lower()):
        if not re.search(r"\b(not|never|isn't|aren't|wasn't|hasn't|haven't)\b.{0,15}$", text.lower()[:hit.start()]):
            return True
    return False


def emergency_signals(text, config):
    """Conservative fast path; scoped negation, not a natural-language triage engine."""
    found = []
    sentences = re.split(r'[.!?;\n]', text.lower().replace('’', "'"))
    clauses = [clause for sentence in sentences if not educational_query(sentence)
               for clause in re.split(r',|\b(?:but|and|despite|however)\b', sentence)]
    for clause in clauses:
        if educational_query(clause):
            continue
        personal = explicit_members(clause, config['members']) or re.search(r'\b(my|his|her|he|she|they)\b', clause)
        if re.search(r'\b(what if|if someone|what causes|what is|what are|explain)\b', clause) and not personal:
            continue
        if re.search(r'\b(history|used to|years? ago|last year|in 20\d\d)\b', clause) and not re.search(r'\b(now|currently|today|just)\b', clause):
            continue
        for pattern in config['emergency_patterns']:
            for hit in re.finditer(pattern, clause):
                prefix = clause[:hit.start()]
                negated = re.search(r"\b(?:no|not|without|denies|denied|doesn't|isn't|aren't|wasn't|weren't)\s+(?:(?:any|have|having|has|currently|are|is|look|seem|longer)\s+|signs? of\s+)*$", prefix)
                if not negated:
                    found.append(hit.group())
    return sorted(set(found))


def validate(data, text, config):
    """Reject malformed, fabricated, out-of-range, and unexpected model fields."""
    if not isinstance(data, dict) or set(data) != set(EXAMPLE):
        raise ValueError('invalid extraction shape')
    for key in ('member_id', 'episode_id'):
        if data[key] is not None and (not isinstance(data[key], str) or not data[key]):
            raise ValueError('invalid identifier')
    if data['intent'] not in ('symptom', 'general', 'resolve', 'unsupported'):
        raise ValueError('invalid intent')
    if not isinstance(data['topic'], str) or not re.fullmatch(r'[a-z][a-z0-9_ -]{0,59}', data['topic']) or data['topic'] == 'unknown' or not isinstance(data['reply'], str) or len(data['reply']) > 2000:
        raise ValueError('invalid topic or reply')
    for field in ('observations', 'memories'):
        if not isinstance(data[field], list) or len(data[field]) > 30:
            raise ValueError('invalid evidence list')
        seen = set()
        for item in data[field]:
            if not isinstance(item, dict) or set(item) != {'key', 'value', 'evidence'}:
                raise ValueError('invalid evidence shape')
            key, value, evidence = item['key'], item['value'], item['evidence']
            if not isinstance(key, str) or key in seen or not isinstance(evidence, str) or not evidence.strip() or evidence not in text:
                raise ValueError('duplicate key or ungrounded evidence')
            seen.add(key)
            if field == 'memories':
                if key not in ('allergies', 'conditions', 'medications') or not isinstance(value, str) or not 0 < len(value) <= 200 or value.lower() not in evidence.lower():
                    raise ValueError('invalid memory')
            elif key in BOOLEANS:
                if type(value) is not bool:
                    raise ValueError('boolean required')
            elif key in NUMBERS:
                low, high = NUMBERS[key]
                if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                    raise ValueError('invalid measurement')
            elif key != 'severity' or value not in ('mild', 'moderate', 'severe'):
                raise ValueError('unknown observation')
    return data


def matches(value, operator, expected):
    if value is None:
        return False
    return {'eq': lambda: value == expected, 'lt': lambda: value < expected,
            'gte': lambda: value >= expected, 'in': lambda: value in expected}[operator]()


class Agent:
    def __init__(self, db_path, backend, config=None, clock=None):
        self.config = copy.deepcopy(config) if config is not None else json.loads(CONFIG_PATH.read_text())
        self.backend, self.clock = backend, clock or time.time
        self.db = sqlite3.connect(str(db_path), timeout=5)
        self.db.execute('CREATE TABLE IF NOT EXISTS households (id TEXT PRIMARY KEY, state TEXT NOT NULL, revision INTEGER NOT NULL)')
        self.db.commit()

    def close(self):
        self.db.close()

    def _load(self, household_id):
        row = self.db.execute('SELECT state, revision FROM households WHERE id=?', (household_id,)).fetchone()
        return (json.loads(row[0]), row[1]) if row else ({'members': copy.deepcopy(self.config['members']),
                'episodes': {}, 'active_member_id': None, 'active_episode_id': None,
                'pending_message': None, 'pending_at': None, 'registration': None,
                'question_key': None, 'history': [], 'emergencies': []}, None)

    def state(self, household_id):
        return self._load(household_id)[0]

    def _save(self, household_id, state, revision):
        with self.db:
            if revision is None:
                self.db.execute('INSERT INTO households VALUES (?, ?, 0)', (household_id, json.dumps(state)))
            else:
                result = self.db.execute('UPDATE households SET state=?, revision=revision+1 WHERE id=? AND revision=?',
                                         (json.dumps(state), household_id, revision))
                if result.rowcount != 1:
                    raise ValueError('conversation changed; repeat turn')

    def _result(self, route, member_id=None, episode_id=None, reasons=(), message=None):
        return {'route': route, 'message': message or self.config['messages'].get(route, ''),
                'member_id': member_id, 'episode_id': episode_id, 'reasons': list(reasons)}

    def _finish(self, household_id, state, revision, message, answer, *, preserve_question=False):
        if not preserve_question:
            state['question_key'] = next((key for key in answer['reasons'] if key in self.config['questions']), None) if answer['route'] == 'ask' else None
        eid=answer.get('episode_id')
        if eid and answer['route']=='ask':
            state.setdefault('open_questions',{})[eid]={'field':state['question_key'],'member_id':answer.get('member_id'),'asked_at':self.clock(),'status':'open'}
        if eid and answer['route'] in ('home','resolved') and eid in state.get('open_questions',{}):
            state['open_questions'][eid]['status']='answered'
        state['history'] = (state['history'] + [{'user': message, 'assistant': answer['message'], 'route': answer['route']}])[-self.config['history_turns']:]
        self._save(household_id, state, revision)
        return answer

    def chat(self, household_id, message):
        if not isinstance(household_id, str) or not household_id.strip() or len(household_id) > 100:
            return self._result('error', reasons=['invalid_household'])
        if not isinstance(message, str) or not message.strip():
            return self._result('error', reasons=['empty_message'])
        # Scan before size limits, model calls, database reads, and entity questions.
        signals = emergency_signals(message, self.config)
        if signals:
            return self._emergency(household_id, message, signals)
        if len(message) > self.config['max_message_chars']:
            return self._result('error', reasons=['message_too_long'])
        try:
            state, revision = self._load(household_id)
            smalltalk = conversational_turn(state, message, clinical=current_symptoms(message), now=self.clock())
            if smalltalk is not None:
                state.pop('registration_completed', None)
                return self._finish(household_id, state, revision, message, smalltalk)
            names = explicit_members(message, state['members'])
            source = message
            registered = state.pop('registration_completed', False)
            if registered:
                if state.get('pending_message') and self.clock() - (state.get('pending_at') or 0) < self.config['freshness_hours'] * 3600:
                    source = state['pending_message'] + '\n' + message if state['pending_message'] != message else message
                self._save(household_id, state, revision)
                state, revision = self._load(household_id)
            question = state.get('question_key')
            if question in ('duration_hours', 'severity') and message.lower().strip(' .!') in ('no', 'yes', 'nope'):
                episode = state['episodes'].get(state['active_episode_id'])
                invalid = episode.setdefault('invalid_answers', {}) if episode else {}
                invalid[question] = invalid.get(question, 0) + 1
                if invalid[question] >= 2 and episode:
                    episode.setdefault('unanswered', []).append(question)
                else:
                    explanation = 'I meant the timing—roughly how many hours or days have the symptoms been present? An estimate is fine, or say "not sure".' if question == 'duration_hours' else 'Could you describe the symptoms as mild, moderate, or severe? You can also say "not sure".'
                    return self._finish(household_id, state, revision, message, self._result('ask', state['active_member_id'], state['active_episode_id'], [question], explanation))
            if question and message.lower().strip(' .!') in ('not sure', "i don't know", 'unknown', 'skip'):
                episode = state['episodes'].get(state['active_episode_id'])
                if episode:
                    episode.setdefault('unanswered', []).append(question)
            identity_only = re.sub(r"^(?:it is|it's|my|about|for)\s+", '', message.lower().strip(' .!'))
            if not registered and state['pending_message'] and len(names) == 1:
                member = state['members'][names[0]]
                if identity_only in [member['name'].lower()] + member['aliases']:
                    if self.clock() - (state.get('pending_at') or 0) < self.config['freshness_hours'] * 3600:
                        source = state['pending_message'] + '\n' + message
                    else:
                        state['pending_message'], state['pending_at'] = None, None
                        self._save(household_id, state, revision)
                        return self._result('ask', names[0], reasons=['expired_intake'], message='The earlier intake is out of date. Please describe the current symptoms again.')
            context = {'state': copy.deepcopy(state), 'message': source,
                       'topics': self.config['topics'], 'example': copy.deepcopy(EXAMPLE), 'now': self.clock()}
            data = validate(self.backend(context), source, self.config)
            # Positive extracted danger also wins over unresolved identity and episode IDs.
            danger = [x['key'] for x in data['observations'] if x['key'] in self.config['emergency_flags'] and x['value'] is True]
            if danger:
                return self._emergency(household_id, message, danger, data['member_id'])
            if len(names) > 1:
                data['member_id'] = None
            elif names and data['member_id'] != names[0]:
                raise ValueError('interpretation contradicts explicit member')
            if data['intent'] == 'general' and (data['observations'] or current_symptoms(message)):
                data['intent'] = 'symptom'
            adverse = any((x['key'] in ('red_flags', 'breathing_difficulty', 'worsening') and x['value'] is True)
                          or (x['key'] == 'hydration_ok' and x['value'] is False)
                          or (x['key'] == 'severity' and x['value'] != 'mild') for x in data['observations'])
            if data['intent'] == 'resolve' and (not resolution_confirmed(message) or adverse):
                data['intent'] = 'symptom'
            answer = self._advance(state, data, message)
            return self._finish(household_id, state, revision, message, answer)
        except Exception:
            # The backend is an extension boundary. Unanticipated plugin failures also fail closed.
            return self._result('error', reasons=['assessment_failed'])

    def _emergency(self, household_id, message, reasons, member_id=None):
        # Emergency instructions still return if storage is unavailable.
        try:
            state, revision = self._load(household_id)
            names = explicit_members(message, state['members'])
            mid = names[0] if len(names) == 1 else (member_id if not names else None)
            if mid is None and not names:
                active = state['active_member_id']
                pronoun = re.search(r'\b(she|her|he|him|they|them)\b', message.lower())
                if active and pronoun and pronoun[0] in state['members'][active].get('pronouns', []):
                    mid = active
            if mid not in state['members']:
                mid = None
            if mid:
                state['members'][mid]['safety_hold'] = 'emergency'
            else:
                state['unassigned_emergency'] = True
            state['active_member_id'], state['active_episode_id'] = mid, None
            state['emergencies'] = (state.get('emergencies', []) + [{'member_id': mid, 'at': self.clock(), 'reasons': list(reasons)}])[-20:]
            self._save(household_id, state, revision)
            return self._result('emergency', mid, reasons=reasons)
        except (ValueError, OSError, sqlite3.Error):
            return self._result('emergency', reasons=reasons)

    def _advance(self, state, data, message):
        if data['intent'] == 'unsupported':
            return self._result('unsupported')
        if state.get('unassigned_emergency') and data['intent'] != 'general':
            return self._result('emergency', reasons=['unresolved_emergency_identity'])
        mid = data['member_id']
        if data['intent'] == 'general' and mid is None and not data['memories']:
            return self._result('general', message=data['reply'] or 'What would you like to know about health or wellbeing?')
        if mid is None:
            state['pending_message'] = message
            state['pending_at'] = self.clock()
            names = ', '.join(m['name'] for m in state['members'].values())
            return self._result('clarify_member', message=f'Who is this about: {names}, or someone else?' if names else 'Who is feeling unwell? Tell me their name, relationship to you, and age so I can keep their information separate.')
        if mid not in state['members']:
            raise ValueError('unknown member')
        member = state['members'][mid]
        state['pending_message'], state['pending_at'] = None, None
        if member.get('safety_hold') and data['intent'] != 'general':
            return self._result('emergency', mid, reasons=['existing_emergency'])
        if member.get('review_hold') and data['intent'] != 'general':
            return self._result('doctor', mid, reasons=['existing_review'])
        eid = data['episode_id']
        if eid is not None:
            if eid not in state['episodes'] or state['episodes'][eid]['member_id'] != mid:
                raise ValueError('episode belongs to a different member')
            if state['episodes'][eid]['topic'] != data['topic']:
                raise ValueError('episode topic mismatch')
        if data['intent'] == 'general':
            self._remember(member, data['memories'])
            state['active_member_id'] = mid
            return self._result('general', mid, message=data['reply'] or 'What general health question would you like to discuss?')
        site = reported_anatomy(message) if data['topic'] == 'other' else None
        candidates = [i for i, e in state['episodes'].items() if e['member_id'] == mid and e['topic'] == data['topic'] and e['status'] not in ('closed','retracted_misattribution') and (site is None or e.get('body_site') in (None, site))]
        new_pattern = r'\bnew (?:episode|bout|' + re.escape(data['topic']) + r')\b'
        duplicate = any(turn['user'].strip().casefold() == message.strip().casefold() for turn in state['history'][-2:])
        new_episode = not duplicate and any(not re.search(r"\b(no|not|isn't)\s+(?:a |another )?$", message.lower()[:hit.start()])
                          for hit in re.finditer(new_pattern, message.lower()))
        prior_escalation = next((state['episodes'][i]['status'] for i in candidates if state['episodes'][i]['status'] in ('doctor', 'emergency')), None)
        if new_episode:
            eid, candidates = None, []
        if eid is None and state['active_episode_id'] in candidates and not re.search(r'\b(previous|earlier|other|first)\b', message.lower()):
            eid = state['active_episode_id']
        if eid is None and len(candidates) > 1:
            return self._result('clarify_episode', mid, message='Which episode do you mean? Please specify when it started.')
        eid = eid or (candidates[0] if candidates else None)
        if eid is None:
            if data['intent'] == 'resolve':
                return self._result('clarify_episode', mid, message='Which existing episode has resolved?')
            eid = uuid.uuid4().hex[:12]
            state['episodes'][eid] = {'member_id': mid, 'topic': data['topic'], 'status': prior_escalation if new_episode and prior_escalation else 'open',
                                      'observations': {}, 'created_at': self.clock(), 'updated_at': self.clock(), 'body_site': site}
        episode = state['episodes'][eid]
        if site and episode.get('body_site') and site != episode['body_site']:
            return self._result('clarify_episode', mid, message='That body site differs from the selected episode. Which episode do you mean?')
        if site and not episode.get('body_site'):
            episode['body_site'] = site
        state['active_member_id'], state['active_episode_id'] = mid, eid
        # An escalation cannot be cancelled by an LLM or a reassuring follow-up.
        if episode['status'] in ('emergency', 'doctor'):
            return self._result(episode['status'], mid, eid, ['existing_escalation'])
        if episode['status'] == 'closed':
            raise ValueError('closed episode cannot be reused')
        episode['observations'] = {key: value for key, value in episode['observations'].items()
                                   if self.clock() - value['at'] < self.config['freshness_hours'] * 3600}
        self._remember(member, data['memories'])
        observations = episode['observations']
        for item in data['observations']:
            observations[item['key']] = {'value': item['value'], 'evidence': item['evidence'], 'at': self.clock()}
        episode['updated_at'] = self.clock()
        facts = {key: x['value'] for key, x in observations.items()}
        if 'duration_hours' in facts:
            facts['duration_hours'] += max(0, self.clock() - observations['duration_hours']['at']) / 3600
        facts['age_months'] = member['age_months']
        if facts.get('temperature_c', 0) >= 38:
            facts['fever'] = True
        reasons = [r['id'] for r in self.config['doctor_rules'] if all(matches(facts.get(k), op, v) for k, op, v in r['all'])]
        # Reported chronic risks are retained and used without inventing drug advice.
        if member['facts'].get('conditions'):
            reasons.append('reported_chronic_condition')
        if reasons:
            episode['status'] = 'doctor'
            return self._result('doctor', mid, eid, reasons)
        if data['intent'] == 'resolve':
            episode['status'] = 'closed'
            return self._result('resolved', mid, eid, message='I have marked this episode as resolved. If symptoms return, please describe them again.')
        missing = [key for key in self.config['questions'] if key not in facts and key not in episode.get('unanswered', [])]
        if missing:
            key = choose_question(missing,data['topic'],self.config)
            return self._result('ask', mid, eid, [key], self.config['questions'][key])
        if episode.get('unanswered') or member['age_months'] is None:
            episode['status'] = 'doctor'
            return self._result('doctor', mid, eid, ['incomplete_intake'])
        if facts['red_flags'] is not False or facts['severity'] != 'mild' or facts['hydration_ok'] is not True or facts['duration_hours'] >= 48:
            episode['status'] = 'doctor'
            return self._result('doctor', mid, eid, ['outside_home_monitoring_policy'])
        return self._result('home', mid, eid)

    def _remember(self, member, memories):
        remember(member, memories, self.clock())


def main():
    parser = argparse.ArgumentParser(description='Family health agent evaluation CLI')
    parser.add_argument('--backend', choices=['llm', 'demo'], default='llm')
    parser.add_argument('--db', default='family_health.db')
    parser.add_argument('--household', default='my-family')
    parser.add_argument('--config', type=Path, default=CONFIG_PATH)
    parser.add_argument('--message', help='Run one turn and emit JSON; otherwise enter interactive chat')
    args = parser.parse_args()
    from backends import DemoBackend
    config = json.loads(args.config.read_text())
    try:
        if args.backend == 'demo':
            agent = Agent(args.db, DemoBackend(), config)
        else:
            from credentials import load_openai_key
            from model_client import ResponsesClient, FirecrawlSearch
            from runtime import Runtime
            key = load_openai_key()
            if not key:
                raise ValueError('Set OPENAI_API_KEY or DOPPLER_TOKEN, or use the browser Model settings.')
            agent = Runtime(args.db, ResponsesClient(key) if key else None, FirecrawlSearch(), config)
    except ValueError as error:
        parser.exit(2, f'{error}\nFor an offline demonstration, add --backend demo.\n')
    try:
        if args.message is not None:
            print(json.dumps(agent.chat(args.household, args.message), indent=2))
            return
        print(f'Family health assistant ({args.backend}). Your family starts empty. Introduce yourself or ask a question. /state to inspect; /quit to exit.')
        while True:
            try:
                message = input('You: ')
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if message == '/quit':
                break
            if message == '/state':
                print(json.dumps(agent.state(args.household), indent=2))
            else:
                result = agent.chat(args.household, message)
                print(f"Assistant: {result['message']}")
    finally:
        agent.close()


if __name__ == '__main__':
    main()
