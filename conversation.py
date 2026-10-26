"""Grounded subject preferences, scoped context, medication boundaries and explicit deletion."""
import copy
import hashlib
import json
import re
from continuity import record_fact


def subject_updates(state, message, now):
    from health_agent import explicit_members
    names=explicit_members(message,state['members'])
    # Relationship + explicit pronoun preference confirms the same immutable member ID.
    if len(names)==1:
        member=state['members'][names[0]]
        choice=re.search(r'\b(?:uses?|pronouns?(?: are|:)?|goes by)\s+(he/him|she/her|they/them)\b',message,re.I)
        if choice:
            member['pronouns']=choice[1].lower().split('/')
            record_fact(state.setdefault('context_records',[]),'pronouns',choice[1].lower(),message,now,names[0],None,'reported')
        if re.search(r'\bstill (?:mean )?my\b|\bi (?:still )?mean\b',message,re.I) and state.get('unresolved_subject'):
            state['active_member_id']=names[0]
            state['confirmed_subject']=names[0]
    return names


def medication_boundary(message, state):
    lower=message.lower()
    brand_question=bool(re.search(r'\b(?:take|add|combine|mix)\s+[A-Z][A-Za-z0-9-]+',message))
    medical=brand_question or bool(re.search(r'\b(?:medicin\w*|medication\w*|prescription|pharmacist|ingredient\w*|dos(?:e|age)|spoonful\w*|milligrams?|otc|pregnan\w*|breastfeeding)\b',lower))
    if medical:state['medication_context']=True
    followup=state.get('medication_context') and re.search(r'\b(?:can (?:she|he|they|i) (?:take|add)|take both|double|just say yes|stop all|how many mg|same active ingredient|is (?:it|that|this) safe|safe together)\b',lower)
    if not medical and not followup:return None
    if re.search(r'\b(?:double|missed (?:a |the )?dose|missed a reminder)\b',lower):
        return 'Do not take an extra or double dose based on this chat. Check the actual medicine-specific missed-dose instructions or ask a pharmacist; I cannot invent a catch-up rule.'
    if 'spoon' in lower or re.search(r'\bhow many (?:mg|milligrams)\b',lower):
        return 'I cannot calculate the amount without both the measured volume in mL and the concentration on the label. An unknown spoon size is not a reliable volume; ask a pharmacist if the amount taken is uncertain.'
    if re.search(r'\b(?:same|duplicate) active ingredient|take both\b',lower):
        return 'Those products may duplicate an active ingredient. I cannot approve taking them together or choose a combined dose; check the exact labels with a pharmacist.'
    if re.search(r'\bstop all|stop (?:her|his|their|my) (?:medicines|medications)\b',lower):
        return 'I cannot direct someone to stop all their medicines. A prescriber or pharmacist should review the actual list and explain any changes.'
    if re.search(r'\b(?:dose|dosage|automatically safe|any otc|can .*take|can .*add|just say yes)\b',lower) or followup:
        return 'I cannot establish medication safety from a brand name or incomplete history. Please provide the exact active ingredients, strength/formulation and current medicines; a pharmacist or clinician should advise on the specific product. I will not guess a dose or infer pregnancy from a relationship or pronouns.'
    return None


def view(state, allowed=None, budget=100000):
    """Bounded byte budget, not a tokenizer. Current durable facts outrank dialogue/derived summaries."""
    keys=('members','episodes','active_member_id','active_episode_id','pending_message','pending_at','question_key','pending_profile','history','context_records','unresolved_subject','open_questions','timezone','notification_policy','medication_context','source_records','unassigned_emergency','unassigned_review')
    result=copy.deepcopy({k:state[k] for k in keys if k in state})
    result['history']=result.get('history',[])[-12:]
    result.setdefault('source_records',[])
    result['context_records']=result.get('context_records',[])[-256:]
    if allowed is not None:
        allowed=set(allowed)
        result['members']={k:v for k,v in result['members'].items() if k in allowed}
        result['episodes']={k:v for k,v in result['episodes'].items() if v['member_id'] in allowed}
        result['context_records']=[x for x in result['context_records'] if x.get('member_id') is None or x.get('member_id') in allowed]
        # Household-wide free text may mention protected members: no raw dialogue/source/pending leakage.
        for key in ('history','source_records'):result[key]=[]
        for key in ('pending_message','unresolved_subject','open_questions','pending_profile','unassigned_review','notification_policy'):result.pop(key,None)
        if result.get('active_member_id') not in allowed:result['active_member_id']=result['active_episode_id']=None
    # Large source passages remain durable but only bounded excerpts enter a model request.
    for source in result.get('source_records',[]):
        if 'content' in source:source['content']=source['content'][:2000]
    def size():return len(json.dumps(result,ensure_ascii=False).encode())
    while size()>budget and result['history']:result['history'].pop(0)
    while size()>budget:
        raw=next((x for x in result['context_records'] if x.get('key')=='reported_context'),None)
        if raw is None:break
        result['context_records'].remove(raw)
    if size()>budget:raise ValueError('Critical durable context exceeds the configured byte budget; narrow the request.')
    return result


def delete_note(store, household, note_id, allowed=None):
    """Delete the scoped note plus derived copies; retain only an opaque tombstone digest."""
    state,revision=store._load(household)
    rows=state.get('context_records',[])
    row=next((x for x in rows if x.get('id')==note_id),None)
    if not row or (allowed is not None and row.get('member_id') not in set(allowed)|{None}):
        raise ValueError('No accessible note with that ID.')
    evidence=row['evidence'];digest=hashlib.sha256(evidence.encode()).hexdigest()
    state['context_records']=[x for x in rows if x.get('evidence')!=evidence and x.get('value')!=evidence]
    state['history']=[]  # Drop derived rolling context, including possible paraphrases.
    for episode in state['episodes'].values():
        episode['observations']={k:v for k,v in episode['observations'].items() if v.get('evidence')!=evidence}
    for member in state['members'].values():
        removed=[r for r in member.get('fact_records',[]) if r.get('evidence')==evidence]
        member['fact_records']=[r for r in member.get('fact_records',[]) if r not in removed]
        for r in removed:
            member.get('facts',{}).get(r['key'],[])[:]=[v for v in member.get('facts',{}).get(r['key'],[]) if v!=r['value']]
    for key in ('summary','summaries','derived_summaries','pending_message','unresolved_subject'):state.pop(key,None)
    state.setdefault('deleted_evidence',[]).append(digest)
    store._save(household,state,revision)
    with store.db:
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_requests'").fetchone():
            cleared=json.dumps({'route':'data_management','message':'That earlier response was invalidated by a scoped deletion; no action was replayed.','trace':{}})
            store.db.execute("UPDATE chat_requests SET result=? WHERE household=? AND status='complete'",(cleared,household))
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='response_cache'").fetchone():store.db.execute('DELETE FROM response_cache WHERE household=?',(household,))
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_messages'").fetchone():
            store.db.execute('DELETE FROM chat_messages WHERE session=? AND instr(content,?)>0',(household,evidence))
    return 'Deleted that note and matching derived context/cache/chat entries. A content-free audit digest remains; external backups and prior recipients are not erased.'
