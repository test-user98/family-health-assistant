"""Conservative claim gate: model wording never supplies evidence of an action.

Only trusted execution receipts may populate evidence. Current conversational stages
perform no clinician, mail or scheduling actions; deterministic action routes render
their own receipts outside this gate. This finite English guard is not a semantic proof.
"""
import re

DOMAINS = {
    'clinician': r'\b(?:doctor|clinician|physician|medical team|care team|referral|case)\b',
    'email': r'\b(?:e-?mail|message|inbox|recipient|notification)\b',
    'schedule': r'\b(?:reminder|alert|alarm|appointment|check-in|scheduled|booked)\b',
}
ACTION = re.compile(
    r'\b(?:notified|contacted|alerted|forwarded|transferred|referred|sent|emailed|'
    r'dispatched|delivered|received|reached|scheduled|booked|queued|accepted|'
    r'confirmed|arranged|read|informed|activated)\b|\b(?:all set|is now with|are booked|have booked|'
    r'(?:is|are|has arrived) in (?:your|the) inbox|(?:reminder|alert|alarm) is active)\b|'
    r'\b(?:will|shall|going to)\b.{0,60}\b(?:call|contact|email|send|receive|remind|notify|deliver)\b|'
    r'\b(?:i|we)\s+(?:have\s+)?(?:set|made|created|enabled)\b|'
    r'\b(?:being|am|are|is)\s+(?:transferring|sending|emailing|notifying|booking|scheduling)\b|'
    r"\b(?:i'll|we'll)\s+(?:remind|email|send|notify|book|schedule)\b|"
    r'\b(?:doctor|clinician|care team)\b.{0,30}\b(?:knows|will reach out)\b', re.I)
NONASSERTION = re.compile(
    r"\b(?:not|no|never|cannot|can't|haven't|hasn't|wasn't|isn't|aren't|unconfirmed|unknown)\b|"
    r'\b(?:if|whether|unless|could|would|might|may|should|please|can assess|can contact|can book)\b|'
    r'\b(?:does not|do not|did not|cannot confirm)\b', re.I)


def action_claims(text):
    """Find affirmative completion/promises, including passive voice and paraphrases.

    Split conjunctions so a disclaimer cannot license a later positive assertion.
    Questions, guidance, conditional examples and explicitly uncertain statements
    remain available. False positives are preferable to unsupported action promises.
    """
    found=set()
    for sentence in re.split(r'[.!;\n]+', text):
        if '?' in sentence:
            continue
        for clause in re.split(r'\b(?:but|however|yet|although|and)\b|,',sentence,flags=re.I):
            clause=re.sub(r'\bnot only\b','',clause,flags=re.I)
            if NONASSERTION.search(clause) or not ACTION.search(clause):
                continue
            for domain,pattern in DOMAINS.items():
                if re.search(pattern,clause,re.I):found.add(domain)
            # Objectless promises can refer back to an earlier message/reminder.
            if re.search(r"\b(?:(?:i|we)\s+(?:will|have|already)|i'll|we'll)\b.*\b(?:email|send|schedule|notify|book|remind)\b",clause,re.I):
                found.add('action')
    return found


def validate_action_claims(text, *, evidence=()):
    """Replace model claims with trusted evidence wording, never promote its status.

    Evidence is a sequence of application-authored receipt summaries, not model
    fields or accumulated household history. Even real SMTP acceptance must use
    the receipt's limited wording rather than a model's stronger inbox assertion.
    """
    claims=action_claims(text)
    if not claims:return text,False
    if evidence:
        return 'Verified action evidence: '+' '.join(evidence),True
    return ('No clinician was contacted. No email send or delivery is confirmed, '
            'and no reminder or appointment was created by this conversational turn. '
            'An existing reminder, if any, must be checked in its saved status.'),True
