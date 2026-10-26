"""Deterministic, scoped retrieval and source-derived consolidation; no semantic model.

Budgets cover the serialized dynamic memory projection, not fixed instructions/schema
or subsequent tool observations. UTF-8 bytes provide a conservative token upper bound
for the byte-based model tokenizer; no network/tokenizer dependency is required.
"""
import copy
import hashlib
import json
import re
from token_count import meter
from conversation import view

class ContextOverflow(ValueError):pass

def encoded(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()

def retrieve(state,message,config,allowed=None):
    measure,counter_name=meter(config,encoded)
    options=config.get('context',{})
    limits={k:options.get(k,v) for k,v in {'recent_turns':6,'max_facts':20,'token_budget':3000}.items()}
    if any(type(v) is not int or v<0 for v in limits.values()) or limits['token_budget']<128:raise ContextOverflow('Invalid memory context budget.')
    payload_budget=limits['token_budget']-400  # Reserve accounting/provenance manifest space before filling optionals.
    scoped=view(state,allowed,2**63-1)
    from health_agent import explicit_members
    selected=explicit_members(message,scoped['members'])
    from memory_queries import household_recall
    broad=household_recall(message,scoped) or bool(re.search(r'\b(?:whose|both|everyone|all (?:members|people)|each person|whole family|which symptoms belong|summarize (?:the |my )?family)\b',message,re.I))
    if broad:selected=list(scoped['members'])
    if not selected and scoped.get('active_member_id') in scoped['members']:selected=[scoped['active_member_id']]
    selected=set(selected)
    # Identity roster remains available without copying another person's health data.
    result={k:copy.deepcopy(scoped[k]) for k in ('active_member_id','active_episode_id','question_key','pending_profile','pending_message','unresolved_subject','unassigned_emergency','unassigned_review','timezone','notification_policy','medication_context') if k in scoped}
    result['members']={}
    critical=[];optional=[]
    for mid,member in scoped['members'].items():
        roster={k:copy.deepcopy(member[k]) for k in ('name','name_confirmed','relationship','age_months','aliases','pronouns','safety_hold','review_hold') if k in member}
        roster['facts']={k:[] for k in member.get('facts',{})} if mid in selected else {};roster['fact_records']=[];result['members'][mid]=roster
        if mid not in selected:continue
        if member.get('profile_records'):roster['profile_records']=copy.deepcopy(member['profile_records'][-2:])
        for category,values in member.get('facts',{}).items():
            for value in values:
                rows=[r for r in member.get('fact_records',[]) if r.get('key')==category and r.get('value')==value and r.get('status')=='active']
                row=copy.deepcopy(rows[-1]) if rows else dict(key=category,value=value,evidence=None,status='active',certainty='legacy_report_unverified')
                row.update(member_id=mid)
                critical.append(('member',mid,category,value,row))
    result['episodes']={}
    topics={t for t in config['topics'] if re.search(r'\b'+re.escape(t)+r'\b',message,re.I)}
    episodes=[(eid,e) for eid,e in scoped.get('episodes',{}).items() if e['member_id'] in selected and e.get('status')!='retracted_misattribution']
    episodes.sort(key=lambda item:(item[0]!=scoped.get('active_episode_id'),item[1].get('status')=='closed',item[1].get('topic') not in topics,-item[1].get('updated_at',0)))
    for eid,episode in episodes:
        active=episode.get('status') in ('open','doctor','emergency')
        if not active and topics and episode.get('topic') not in topics:continue
        shell={k:copy.deepcopy(episode[k]) for k in ('member_id','topic','status','body_site','created_at','updated_at','unanswered','question_counts','superseded_by','unavailable_records') if k in episode}
        shell['observations']={}
        shell['unknown_fields']=[key for key in config['questions'] if key not in episode.get('observations',{})]
        if active:result['episodes'][eid]=shell
        else:optional.append(('episode',eid,None,None,shell))
        for key,observation in episode.get('observations',{}).items():
            (critical if active else optional).append(('observation',eid,key,None,copy.deepcopy(observation)))
    result['context_records']=[]
    seen=set()
    # Current structured facts outrank raw reports. Never use recency to merge
    # conflicting clinical values or resurrect a superseded/deleted assertion.
    past_ids={item[1] for item in optional if item[0]=='episode'}
    terms={t for t in re.findall(r'[a-z]{4,}',message.lower()) if t not in {'what','which','about','please','reported','remember','summarize','current','person'}}
    terms-={t for mid in selected for t in re.findall(r'[a-z]{4,}',scoped['members'][mid]['name'].lower())}
    def rank(row):
        text=(str(row.get('value',''))+' '+str(row.get('evidence',''))).lower()
        overlap=sum(bool(re.search(r'\b'+re.escape(t)+r'\b',text)) for t in terms)
        return (row.get('key')=='reported_context',-overlap,row.get('certainty')!='user_hypothesis',-row.get('at',0))
    rows=sorted(scoped.get('context_records',[]),key=rank)
    for row in rows:
        if row.get('status','active')!='active' or row.get('member_id') not in selected|{None}:continue
        if hashlib.sha256(str(row.get('evidence','')).encode()).hexdigest() in state.get('deleted_evidence',[]):continue
        if allowed is not None and row.get('member_id') is None:
            if row.get('key')!='answer_style':continue
            row={**row,'evidence':'Reported household response-style preference; original free text omitted from this restricted view.'}
        if row.get('episode_id') and row['episode_id'] not in result['episodes'] and row['episode_id'] not in past_ids:continue
        key=(row.get('member_id'),row.get('episode_id'),row.get('key'),' '.join(str(row.get('value')).split()).casefold())
        if key in seen:continue
        seen.add(key)
        item=('record',None,None,None,copy.deepcopy(row))
        (critical if row.get('key')!='reported_context' and row.get('episode_id') in result['episodes'] else optional).append(item)
    result['open_questions']={eid:copy.deepcopy(q) for eid,q in scoped.get('open_questions',{}).items() if eid in result['episodes']}
    if len(selected)==1:
        result['active_member_id']=next(iter(selected))
        if result.get('active_episode_id') not in result['episodes']:
            result['active_episode_id']=next(iter(result['episodes']),None)
    elif result.get('active_episode_id') not in result['episodes']:result['active_episode_id']=None
    result['history']=[];result['source_records']=[]
    def apply(item):
        kind,id,key,value,row=item
        if kind=='member':
            result['members'][id]['facts'].setdefault(key,[]).append(value);result['members'][id]['fact_records'].append(row)
        elif kind=='episode':result['episodes'][id]=row
        elif kind=='observation':
            if id not in result['episodes']:return False
            result['episodes'][id]['observations'][key]=row
        else:result['context_records'].append(row)
        return True
    for item in critical:apply(item)
    if len(critical)>limits['max_facts'] or measure(result)>payload_budget:raise ContextOverflow('Active safety-critical memory exceeds the configured fact/token budget; no critical fact was discarded. Specify one member/episode or review the budget.')
    count=len(critical)
    for item in optional:
        if count>=limits['max_facts']:break
        before=copy.deepcopy(result)
        if apply(item) and measure(result)<=payload_budget:count+=1
        else:result=before
    recent=scoped.get('history',[])[-limits['recent_turns']:] if limits['recent_turns'] else []
    for turn in reversed(recent):
        result['history'].insert(0,copy.deepcopy(turn))
        if measure(result)>payload_budget:result['history'].pop(0)
    if re.search(r'\b(?:source|guidance|article|quote|link|reference)\b',message,re.I):
        for source in reversed(scoped.get('source_records',[])):
            row=copy.deepcopy(source)
            if 'content' in row:row['content']=row['content'][:600]
            result['source_records'].append(row)
            if measure(result)>payload_budget:result['source_records'].pop()
            if len(result['source_records'])==3:break
    # A compact manifest rather than another lossy summary; hash current source
    # records, so correction/deletion changes derived context immediately.
    manifest=dict(selected_members=sorted(selected),facts=count,critical_facts=len(critical),recent_turns=len(result['history']),token_counter=counter_name,token_budget=limits['token_budget'],source_hash=hashlib.sha256(encoded({k:v for k,v in result.items() if k!='history'})).hexdigest())
    result['memory_manifest']=manifest
    # Reserve the manifest itself: remove only optional material, never critical.
    while measure(result)>limits['token_budget'] and result['history']:result['history'].pop(0)
    while measure(result)>limits['token_budget'] and result['source_records']:result['source_records'].pop()
    if measure(result)>limits['token_budget']:raise ContextOverflow('Memory manifest and preserved safety context exceed budget; no provider call was made.')
    manifest['recent_turns']=len(result['history']);manifest['token_upper_bound']=measure(result)+32
    if manifest['token_upper_bound']>limits['token_budget']:raise ContextOverflow('Safety context plus accounting exceeds budget; narrow the request.')
    return result
