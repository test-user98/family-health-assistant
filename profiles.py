"""Small conversational front door: greetings and user-supplied family profiles."""
import re
import uuid

ROLES = {
    'self': ['me', 'myself', 'i'],
    'father': ['father', 'dad', 'papa'], 'mother': ['mother', 'mom', 'mum', 'mama'],
    'daughter': ['daughter'], 'son': ['son'], 'child': ['child', 'baby', 'infant'],
    'wife': ['wife'], 'husband': ['husband'], 'partner': ['partner'],
    'brother': ['brother'], 'sister': ['sister'],
    'grandmother': ['grandmother', 'grandma'], 'grandfather': ['grandfather', 'grandpa'],
    'uncle': ['uncle'], 'aunt': ['aunt'], 'cousin': ['cousin'], 'friend': ['friend'],
}
ROLE_PATTERN = r'\b(' + '|'.join(alias for aliases in ROLES.values() for alias in aliases if alias != 'i') + r')\b'
WORD = r"[^\W\d_]+(?:[-'][^\W\d_]+)*"
STOP_NAMES = {'is', 'am', 'my', 'name', 'age', 'aged', 'years', 'year', 'months', 'old',
              'and', 'he', 'she', 'they', 'who', 'has', 'have', 'with', 'not', 'no',
              'i', 'having', 'coughing', 'vomiting', 'ill', 'unwell',
              'feeling', 'feel', 'sick', 'fine', 'tired', 'worried', 'anxious', 'healthy',
              'a', 'an', 'the', 'want', 'would', 'like', 'add'}


def age_from(text):
    for hit in re.finditer(r'(?<![\w.])(-?\d+(?:\.\d+)?)\s*(years?|yrs?|months?|mos?|weeks?)\b', text, re.I):
        if not re.search(r'\b(?:for|since|over)\s*$', text[:hit.start()], re.I):
            return float(hit[1]) * (1 if hit[2].lower().startswith(('month', 'mo')) else 12 / 52 if hit[2].lower().startswith('week') else 12)
    for hit in re.finditer(r"\b(?:age|aged|is|am)\s*:?\s*(-?\d+(?:\.\d+)?)\b|[,=:]\s*(-?\d+(?:\.\d+)?)\b|^\s*(-?\d+(?:\.\d+)?)\s*$", text, re.I):
        if not re.match(r'\s*(?:°?\s*[cf]\b|hours?\b|days?\b)', text[hit.end():], re.I):
            return float(next(g for g in hit.groups() if g is not None)) * 12
    return None


def person_name(text):
    text = re.sub(r"^\s*(?:'s\s+)?(?:name\s+)?(?:(?:is|called)\s+|:\s*)?", '', text.strip(), flags=re.I)
    words = []
    for hit in re.finditer(WORD, text):
        if text[:hit.start()].strip() and not words:
            break
        if hit[0].lower() in STOP_NAMES or len(words) == 4:
            break
        words.append(hit[0])
        suffix = text[hit.end():]
        if not suffix.startswith(' ') or re.match(r'\s+(?:\d|[,.])', suffix):
            break
    name = ' '.join(words)
    return (name.title() if name.islower() else name) if 0 < len(name) <= 80 else None


def parse_profile(text, draft=None):
    lower = text.lower().replace('’', "'")
    draft = dict(draft or {})
    role = re.search(ROLE_PATTERN, lower)
    self_intro = re.search(r"\b(?:my name is|call me|i am called|i am|i'm)\s+", lower)
    explicit_add = bool(re.search(r'\b(add|register|introduce|family member)\b', lower))
    introduction = bool(self_intro or explicit_add or (role and (re.search(r'\b(my|is|name|age|aged)\b', lower) or role[0] == lower.strip())))
    if not (draft or introduction):
        return None
    if role:
        relationship = next(key for key, aliases in ROLES.items() if role[1] in aliases)
        if draft.get('relationship') and draft['relationship'] != relationship:
            draft = {}
        draft['relationship'] = relationship
        if not draft.get('name'):
            after = person_name(text[role.end():])
            before = re.match(r'^\s*(' + WORD + r'(?:\s+' + WORD + r')?)\s+is\s+my\s+', text, re.I)
            draft['name'] = person_name(before[1]) if before else after
    if self_intro and not (role and role[1] not in ROLES['self']):
        draft['relationship'] = 'self'
        draft['name'] = person_name(text[self_intro.end():]) or draft.get('name')
    if not draft.get('name') and not introduction:
        draft['name'] = person_name(text)
    age = age_from(text)
    if age is not None:
        draft['age_months'] = age
    if self_intro and not draft.get('name') and age is None and not explicit_add and 'age_months' not in draft:
        return None
    if re.fullmatch(r"(?:skip|not sure|unknown|i don't know)(?: the age)?[.!]?", lower.strip()):
        draft['age_months'] = None
    return draft


def conversational_turn(state, text, *, clinical=False, now=0):
    """Return a nonclinical response, or None to continue the clinical interpreter.

    Mutations are committed only by Agent. An incomplete profile never becomes a member.
    """
    lower = text.lower().replace('’', "'").strip(' .!?\n')
    def answer(route, message, member_id=None):
        return {'route': route, 'message': message, 'member_id': member_id,
                'episode_id': None, 'reasons': []}

    if lower in ('hi', 'hello', 'hey', 'hey there', 'hi there', 'good morning', 'good evening', 'namaste'):
        return answer('greeting', 'Hi! How can I help today? You can ask a health question or tell me who is in your family.' if not state['members'] else 'Hi! How can I help you or your family today?')
    if lower in ('thank you', 'thanks', 'thankyou', 'okay', 'ok') and not state.get('registration'):
        return answer('general', "You're welcome. What else would you like help with?")
    if lower in ('i have a health question', 'i need help', 'help', 'can you help', 'can you help me', 'what can you do'):
        return answer('general', "Of course. What's on your mind? You can tell me what's happening, or introduce someone in your family.")
    roles = {next(key for key, aliases in ROLES.items() if hit[1] in aliases) for hit in re.finditer(ROLE_PATTERN, lower)}
    self_intro = re.search(r"\b(my name is|i am|i'm|call me)\b", lower)
    if len(roles) > 1 or (self_intro and roles - {'self'}):
        return answer('clarify_member' if clinical else 'register', "Let's take one person at a time so I don't mix up anyone's information. Who should we start with? Tell me their name, relationship to you, and age.")
    if lower in ('cancel', 'stop adding', 'never mind', 'nevermind') or (lower == 'no' and state.get('registration')):
        state['registration'] = None
        state['pending_message'], state['pending_at'] = None, None
        state.pop('registration_intake_relationship', None)
        return answer('general', "No problem—I haven't added anyone. What would you like help with?")
    if lower in ('who is in my family', 'show my family', 'my family', 'list my family', 'family'):
        names = ', '.join(f"{m['name']} ({m.get('relationship', 'family member')})" for m in state['members'].values())
        return answer('general', 'Your family: ' + names + '.' if names else "Your family is empty so far. Tell me about yourself or someone you would like to add.")
    if re.fullmatch(r"(?:i (?:want|would like) to )?(?:add|register)(?: a| my| another)?(?: family)?(?: member| person| someone| myself)?", lower):
        state['registration'] = {}
        return answer('register', "Tell me their name, relationship to you, and age—for example, 'My father Raj is 56'. You can also introduce yourself.")

    draft = state.get('registration')
    # Distinguish real registration from a relationship in an ordinary symptom sentence.
    registration_hint = re.search(r"\b(add|register|my name is|call me|i'm|i am|name is)\b", lower)
    age_hint = age_from(text) is not None
    parsed = parse_profile(text, draft)
    if clinical and re.search(r"\b(?:i am|i'm)\b", lower) and not age_hint and not re.search(r'\b(add|register|my name is|call me)\b', lower):
        return None
    role = re.search(ROLE_PATTERN, lower)
    unknown_role = role and not any(role[1] in m['aliases'] for m in state['members'].values())
    if parsed is not None and (draft is not None or registration_hint or unknown_role or (age_hint and role and parsed.get('name')) or not clinical):
        previous_owner = state.get('registration_intake_relationship')
        if previous_owner and parsed.get('relationship') and previous_owner != parsed['relationship']:
            state['pending_message'], state['pending_at'] = None, None
            state.pop('registration_intake_relationship', None)
        if clinical:
            state['pending_message'], state['pending_at'] = text, now
            state['registration_intake_relationship'] = parsed.get('relationship')
        if 'age_months' in parsed and parsed['age_months'] is not None and not 0 <= parsed['age_months'] <= 1440:
            parsed.pop('age_months')
            state['registration'] = parsed
            return answer('register', "That age doesn't look right. How old are they, in years or months?")
        if not parsed.get('relationship'):
            state['registration'] = parsed
            return answer('register', f"How is {parsed.get('name') or 'this person'} related to you? You can say 'me', 'father', 'mother', 'daughter', or another relationship.")
        if not parsed.get('name'):
            state['registration'] = parsed
            return answer('register', 'What name should I use for you?' if parsed['relationship'] == 'self' else f"What is your {parsed['relationship']}'s name?")
        if 'age_months' not in parsed:
            state['registration'] = parsed
            return answer('register', f"How old is {parsed['name']}? Years or months are both fine. You can say 'skip' if you don't know.")
        matches = [(key, member) for key, member in state['members'].items()
                   if member['name'].casefold() == parsed['name'].casefold() and member.get('relationship') == parsed['relationship']]
        if parsed['relationship'] == 'self' and 'self' in state['members'] and not matches:
            return answer('register', f"I already have {state['members']['self']['name']} as you. Are you adding someone else? Tell me their relationship to you.")
        mid = matches[0][0] if matches else 'self' if parsed['relationship'] == 'self' else 'member_' + uuid.uuid4().hex[:10]
        aliases = ROLES[parsed['relationship']]
        pronouns = ['he', 'him'] if parsed['relationship'] in ('father', 'son', 'husband', 'brother', 'grandfather') else ['she', 'her'] if parsed['relationship'] in ('mother', 'daughter', 'wife', 'sister', 'grandmother') else ['they', 'them']
        member = state['members'].setdefault(mid, {'facts': {}})
        member.update(name=parsed['name'], relationship=parsed['relationship'], aliases=list(aliases),
                      age_months=parsed['age_months'], pronouns=pronouns)
        state['registration'] = None
        state.pop('registration_intake_relationship', None)
        state['registration_completed'] = True
        state['active_member_id'] = mid
        if state['episodes'].get(state['active_episode_id'], {}).get('member_id') != mid:
            state['active_episode_id'] = None
        # Keep symptom intake when a registration sentence also describes illness.
        if clinical:
            return None
        if state.get('pending_message') and now - (state.get('pending_at') or 0) < 86400:
            state['registration_completed'] = True
            return None
        age = parsed['age_months']
        label = 'age not provided' if age is None else f'{age / 12:g} years old' if age >= 12 else f'{age:g} months old'
        return answer('registered', f"{'Updated' if matches else 'Added'} {parsed['name']}, {label}, as {'you' if parsed['relationship'] == 'self' else 'your ' + parsed['relationship']}. What would you like help with?", mid)

    if clinical:
        return None
    # A bare name selects context; it does not invent an episode or symptoms.
    selected = [(mid, member) for mid, member in state['members'].items()
                if lower in [member['name'].lower()] + member['aliases']]
    if len(selected) == 1 and not state.get('pending_message'):
        mid, member = selected[0]
        state['active_member_id'] = mid
        state['active_episode_id'] = next((eid for eid, e in reversed(list(state['episodes'].items()))
                                           if e['member_id'] == mid and e['status'] != 'closed'), None)
        return answer('general', f"Sure—what would you like help with for {member['name']}?", mid)
    if state.get('pending_message') or state.get('question_key'):
        return None
    if lower in ('no', 'nope', 'yes', 'not now'):
        return answer('general', 'No problem. What would you like help with?')
    last_route = state['history'][-1]['route'] if state['history'] else None
    if (not state['members'] or last_route == 'greeting') and re.fullmatch(WORD + r'(?:\s+' + WORD + r'){0,2}', text.strip()) and lower not in ('what', 'help', 'how are you'):
        name = person_name(text)
        if name:
            state['registration'] = {'name': name}
            return answer('register', f"Would you like to add {name} to your family? Tell me how they're related to you and their age, or say 'cancel'.")
    return None
