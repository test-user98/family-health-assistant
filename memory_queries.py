"""Recognize questions about saved facts without treating new symptoms as recall."""
import re


def household_recall(message, state):
    """Match a family recap, excluding new reports or requested actions."""
    text = message.strip().replace('’', "'")
    if not re.match(
        r'^(?:recall|summarize|summarise)\b.*\b(?:facts|corrections|remember|family|conversations)\b',
        text, re.I,
    ):
        return False
    if re.search(r'\b(?:new|now|severe|worse|breathing|take|dose|send|schedule|delete)\b', text, re.I):
        return False
    for member in state['members'].values():
        for name in [member['name'], *member.get('aliases', [])]:
            if re.search(r'\b' + re.escape(name) + r'\s+(?:is|has|feels)\b', text, re.I):
                return False
    if re.search(r'[;.!]\s*(?:he|she|they|his|her|their)\b.*\b(?:has|is|feels)\b', text, re.I):
        return False
    return True


def target(message, state):
    from health_agent import explicit_members

    text = message.strip().replace('’', "'")
    first, separator, tail = text.partition('?')
    if separator and tail.strip():
        # Extra text may set the answer format, but must not add a new report.
        if not re.fullmatch(
            r'\s*(?:please\s+)?(?:say|state|answer|reply)\s+(?:unknown|briefly|concisely)(?:\s+if (?:it is|it\'s) not recorded)?[.!]?\s*',
            tail, re.I,
        ):
            return None
    age = re.match(r'^how old (?:is|are|was|were)\b|^what age\b', first, re.I)
    stored = re.match(r'^what (?:allergy|allergies|medications?|conditions?)\b', first, re.I) and re.search(r'\b(?:reported|recorded|told|on file|know)\b', first, re.I)
    status = re.match(r'^what is the status of\b', first, re.I) and re.search(r'\b(?:older|past|previous|resolved|closed)\b', first, re.I)
    unknown = re.match(r'^is\b', first, re.I) and re.search(r'\b(?:temperature|age|allergy)\b.*\b(?:known|recorded)\b', first, re.I)
    if not (age or stored or status or unknown):
        return None
    if re.search(r'\b(?:now|currently|severe|worse|breathing|take|dose|give|safe|should)\b', first, re.I):
        return None
    names = explicit_members(first, state['members'])
    return names[0] if len(names) == 1 else None
