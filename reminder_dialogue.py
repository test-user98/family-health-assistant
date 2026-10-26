"""Small stateful reminder clarification grammar. Never enables email or infers a recipient."""
import json
import re
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

WORDS={'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10,'eleven':11,'twelve':12}

def clock_in(text):
    lower=text.lower()
    for word,number in WORDS.items():lower=re.sub(r'\b'+word+r'\b',str(number),lower)
    match=re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?',lower)
    if not match and re.search(r'\bafternoon|evening\b',lower):
        match=re.search(r'\b(\d{1,2})(?::(\d{2}))?\b',lower)
        if match and 1<=int(match[1])<=12:return f'{int(match[1])%12+12:02}:{int(match[2] or 0):02}'
    if match and 1<=int(match[1])<=12 and int(match[2] or 0)<60:
        return f'{int(match[1])%12+(12 if match[3]=="p" else 0):02}:{int(match[2] or 0):02}'
    match=re.search(r'\b(\d{1,2}):(\d{2})\b',lower)
    if match and int(match[1])<24 and int(match[2])<60:return f'{int(match[1]):02}:{match[2]}'
    return None

def describe(store,household,rid):
    row=store.get(household,rid)
    occurrences=[x for x in store.outbox.occurrences(household,rid) if x['revision']==row['version']]
    notices=', '.join(('on time' if x['offset_minutes']==0 else str(x['offset_minutes'])+' minutes before')+' ('+x['state']+')' for x in occurrences)
    return f"Local reminder {rid}: {row['day']} {row['local_time']} {row['zone']}; {notices}. SQLite sink only, not email."

def respond(store, household, text, now, enabled, context):
    lower=text.lower().strip();draft=context.get('reminder_draft');rid=context.get('last_reminder_id')
    zone=re.search(r'\b([A-Za-z_]+/[A-Za-z_0-9/+-]+|UTC)\b',text)
    if zone and re.search(r'\btimezone\b',lower):
        try:ZoneInfo(zone[1])
        except ZoneInfoNotFoundError:return 'That timezone is not recognized; no reminder was saved.'
        context['timezone']=zone[1]
        if not draft:return 'Saved the reported timezone '+zone[1]+'. No reminder was scheduled.'
    if re.search(r'\b(?:do not email|keep it local|neutral reminder emails)\b',lower):
        context['notification_policy']={'channel':'local','sensitive_details':False,'evidence':text,'at':now}
        if rid:store.outbox.route_local(household,rid)
        return 'Kept local-only. No email is enabled or authorized; notification content stays neutral.'
    if rid and re.search(r'\b(?:emails?|inbox|proof|read it|received|did you send|gmail|smtp|message id)\b',lower):
        proof=store.outbox.proof(household,rid)
        return proof['summary']+' No mailbox link is available.'
    if rid and re.search(r'\b(?:missed it|counting on|never reminded)\b',lower):
        return "I'm sorry you were counting on that reminder. "+store.outbox.proof(household,rid)['summary']+' I can show its recorded schedule and worker status; I will not resend or suggest a medication catch-up dose.'
    if re.search(r'\bdo not resend|don.t resend\b',lower):
        context['no_resend']=True;return 'I will not resend old notices. Existing delivery history is unchanged.'
    if rid and lower in ('cancel the rest.','cancel the rest','cancel remaining reminders'):
        row=store.cancel(household,rid)
        return 'Cancelled pending notices; delivered history remains.'+(' A submission is already in flight or unknown; cancellation cannot recall it.' if row['inflight_warning'] else '')
    if rid and re.search(r'\b(?:current status|what.*scheduled|what.*happened|laptop.s timezone|travel)\b',lower):
        if 'timezone' in lower or 'travel' in lower:return 'The reminder stays anchored to its saved timezone and UTC instant until you explicitly edit it.'
        return describe(store,household,rid)+' Worker: '+store.outbox.health(household)['worker']+'. Future delivery is not guaranteed.'
    if rid and re.search(r'\b(?:minutes? before|minute alert|on.time(?: local)? notice|right at the time)\b',lower):
        row=store.get(household,rid);offsets=json.loads(row['offsets']);minutes=[int(x) for x in re.findall(r'\b(\d{1,4})\s*(?:minutes?|minute)',lower)]
        if re.search(r'\bonly|just\b',lower):offsets=minutes[:1] if minutes else [0]
        elif re.search(r'\bremove\b',lower):offsets=[x for x in offsets if x not in minutes]
        else:
            offsets=sorted(set(offsets+minutes),reverse=True)
            if 'right at the time' in lower or 'on time' in lower or 'on-time' in lower:offsets=sorted(set(offsets+[0]),reverse=True)
        if row['status']=='cancelled' and ('again' in lower):
            new=store.create(household,row['text'],row['day'],clock_in(text) or row['local_time'],row['zone'],offsets,new_event=True,fold=row['fold']);context['last_reminder_id']=new['id']
        else:store.set_offsets(household,rid,offsets)
        return describe(store,household,context['last_reminder_id'])
    if rid and re.search(r'\b(?:change|move)\b',lower) and clock_in(text):
        row=store.get(household,rid);store.edit(household,rid,row['day'],clock_in(text),row['zone'],fold=row['fold'])
        return describe(store,household,rid)
    if lower.startswith(('remind me about','remind me tomorrow','set a reminder about')):
        if not enabled:return 'Local reminders are disabled; nothing was scheduled.'
        if re.search(r'\bevery|daily|weekly\b',lower):return 'Only one-shot reminders are supported. No recurring reminder was created.'
        label=re.search(r'(?:remind me|set a reminder) about (.+?)(?= tomorrow| today| at |$)',text,re.I)
        draft={'anchor':now,'label':label[1].strip() if label else 'Your check-in','date_hint':'tomorrow' if 'tomorrow' in lower else None,'zone':context.get('timezone'),'time':None,'fold':None}
        context['reminder_draft']=draft
    if draft:
        if zone:draft['zone']=zone[1]
        if clock_in(text):draft['time']=clock_in(text)
        iso=re.search(r'\b\d{4}-\d{2}-\d{2}\b',text)
        if iso:draft['day']=iso[0]
        if re.search(r'\bfirst (?:occurrence|one)|\bEDT\b',text):draft['fold']=0
        if re.search(r'\bsecond (?:occurrence|one)|\bEST\b',text):draft['fold']=1
        if not draft.get('zone') or not draft.get('time'):
            return 'Which IANA timezone and AM/PM should I use?' if not draft.get('zone') and not draft.get('time') else 'Which IANA timezone should I use?' if not draft.get('zone') else 'Is that AM or PM?'
        if not draft.get('day'):
            if draft.get('date_hint')!='tomorrow':return 'Which calendar date should I use?'
            draft['day']=(datetime.fromtimestamp(draft['anchor'],ZoneInfo(draft['zone'])).date()+timedelta(days=1)).isoformat()
        row=store.create(household,draft['label'],draft['day'],draft['time'],draft['zone'],fold=draft['fold'])
        context['last_reminder_id']=row['id'];context.pop('reminder_draft',None)
        return describe(store,household,row['id'])
    return None
