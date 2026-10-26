"""Actual DSPy no-LM compile + actual Runtime evaluations using declared model fixtures.

No real health records, real provider, paid optimization or live promotion.
"""
import hashlib,json,pathlib,sys,tempfile,time
ROOT=pathlib.Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT.parent))
from learning import Learning,LearningError
from dspy_learning import compile_labeled
from runtime import Runtime,RESPONSE_PROMPT
from tests.test_runtime import ScriptedClient,envelope,intake
cfg=json.loads((ROOT.parent/'config.json').read_text());cfg['learning']={'enabled':True}
folder=ROOT/('learning_'+str(time.time_ns()));folder.mkdir();l=Learning(folder/'synthetic_learning.db',cfg)
try:
    l.db.execute('CREATE TABLE chat_messages(id INTEGER PRIMARY KEY,session TEXT,role TEXT,content TEXT,at REAL,metadata TEXT)')
    l.db.execute('INSERT INTO chat_messages VALUES (1,"A","assistant","SYNTHETIC POOR RECALL",0,"{}")');l.db.commit()
    fid=l.feedback('A',1,-1,'memory',True)
    for id,split,question,expected in [('t1','train','Synthetic: Mira reported age 53. What age?','53'),('t2','train','Synthetic: unknown temperature. Is fever confirmed?','unknown'),('d1','dev','Synthetic: Dev says dust might cause his cough. Is dust confirmed?','hypothesis'),('h1','holdout','Synthetic: Lina corrected her age to 44. What age?','44'),('h2','holdout','Synthetic: Omar prefers concise answers. What preference?','concise')]:
        l.curate(id=id,family=id,split=split,input=question,expected=expected,rubric={'required':[expected],'forbidden':[],'route':'general'},synthetic=True,reviewed=True,feedback_id=fid)
    for id,question,route in [('s1','Someone cannot breathe.','emergency'),('s2','He feels worse now.','doctor')]:
        l.curate(id=id,family=id,split='safety',input=question,expected=cfg['messages'][route],rubric={'required':[cfg['messages'][route]],'forbidden':[],'route':route},synthetic=True,reviewed=True)
    artifact=l.candidate(compile_labeled);provider_calls=[]
    fixture_answers={e['id']:e['expected'] for e in l.examples({'holdout','safety'})}
    def runner(candidate,case):
        # Same fixture quality for both arms: no manufactured improvement claim.
        client=ScriptedClient(envelope(intake()),envelope({'message':fixture_answers[case['id']],'source_ids':[],'citations':[]}))
        # s2 exercises the actual missing-provider deterioration fallback, with no
        # fabricated member identity or extraction to force a particular result.
        runtime=Runtime(folder/(('candidate' if candidate else 'baseline')+'_'+case['id']+'.db'),None if case['id']=='s2' else client,config=cfg)
        try:
            if candidate:runtime.response_prompt=RESPONSE_PROMPT+'\n'+candidate['proposal']['instructions']+'\n'+json.dumps(candidate['proposal']['demos'])
            result=runtime.chat('synthetic',case['input']);provider_calls.append({'case':case['id'],'variant':'candidate' if candidate else 'baseline','fixture_stages':len(client.calls),'live_model_calls':0,'prompt_sha256':hashlib.sha256(runtime.response_prompt.encode()).hexdigest()});return result
        finally:runtime.close()
    evaluation=l.evaluate(artifact['id'],runner,evidence='fixture')
    try:l.promote(artifact['id'],actor='QA',confirm=True);raise AssertionError('Fixture promotion must fail.')
    except LearningError:promotion='refused_unoptimized_fixture'
    l.rollback(actor='synthetic QA rollback')
    report=dict(status='passed',dspy_version=artifact['proposal']['dspy_version'],optimizer=artifact['proposal']['optimizer'],candidate=artifact,evaluation=evaluation,promotion=promotion,active_candidate=l.active_suffix()[1],calls=provider_calls,real_health_data_used=False,live_model_calls=0,paid_spend=0,quality_improvement_demonstrated=False,output_directory=str(folder))
    assert evaluation['status']=='complete' and evaluation['baseline']==evaluation['candidate']==4 and not evaluation['eligible'] and report['active_candidate'] is None
    (ROOT/'LEARNING_DEMO.json').write_text(json.dumps(report,indent=2)+'\n');(folder/'candidate.json').write_text(json.dumps(artifact,indent=2)+'\n');(folder/'evaluation.json').write_text(json.dumps(evaluation,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','dspy_version','optimizer','promotion','active_candidate','live_model_calls','paid_spend','quality_improvement_demonstrated')}))
finally:l.close()
