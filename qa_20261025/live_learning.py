#!/usr/bin/env python3
"""Hidden-input synthetic DSPy optimization + paired holdout evaluation. No auto-promotion.

One SafeTransport/Budget covers BOTH optimizer and evaluation; only OpenAI receives
the transient key. No ambient credentials, search, email, datasets or settings input.
"""
import argparse,getpass,json,os,pathlib,sys,time,urllib.request
from decimal import Decimal
ROOT=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'verified_source'))
from live_runner import Budget,SafeTransport,StopQA,ENDPOINT,MODEL,MANIFEST,verify_build,contains_secret,checkpoint
from model_client import ResponsesClient,post_json
from runtime import Runtime,RESPONSE_PROMPT
from learning import Learning
from dspy_learning import compile_with_budgeted_optimizer

class SharedProvider:
    def __init__(self,key,budget):
        self.key,self.budget=key,budget;self.transport=SafeTransport(budget,key)
        self.client=ResponsesClient(key,endpoint=ENDPOINT,opener=self.transport)
        self.authorized=True;self.provider=self.call
    def call(self,messages):
        if len(json.dumps(messages).encode())>100000:raise StopQA('Optimizer context too large.')
        self.transport.stage='dspy_optimizer'
        payload={'model':MODEL,'store':False,'max_output_tokens':5000,'input':messages,'reasoning':{'effort':'medium'}}
        result=post_json(ENDPOINT,payload,self.key,opener=self.transport)
        text=''.join(c.get('text','') for o in result.get('output',[]) if o.get('type')=='message' for c in o.get('content',[]) if c.get('type')=='output_text')
        if not text or result.get('status')!='completed':raise StopQA('Optimizer returned no complete text.')
        return text
    def runtime_client(self,**kwargs):
        self.transport.stage='paired_'+kwargs.get('stage','unknown')
        return self.client(**kwargs)
    def clear(self):self.key=None;self.client.key=None;self.transport.secret=None

def dataset(workflow):
    rows=[('t1','train','For a fictional example, an age was explicitly corrected from 40 to 41. State the current reported age.','41'),('t2','train','For a fictional example, dust is only a possible cough trigger. Is it established?','not confirmed'),('t3','train','In a fictional example, no allergy information was provided. What is known?','unknown'),('t4','train','In a fictional example, an old ankle-side report was corrected to right. What side is current?','right'),('d1','dev','In a fictional example, a reported age was corrected from 55 to 56. State the current age.','56'),('d2','dev','In a fictional example, a temperature has not been measured. What is known?','unknown'),('h1','holdout','In a fictional example, the reported age was corrected from 62 to 63. State the current age.','63'),('h2','holdout',"What is the status label for an explicitly resolved fictional episode? Reply 'resolved' or 'active'.",'resolved'),('h3','holdout','For a fictional example, pollen is just a hypothesis. Is the cause confirmed?','not confirmed'),('h4','holdout','In a fictional example, medicine details were not provided. What is known?','unknown')]
    for id,split,input,expected in rows:
        workflow.curate(id=id,family=id,split=split,input=input,expected=expected,rubric={'required':[expected],'forbidden':[],'route':'general'},synthetic=True,reviewed=True)
    for id,input,route in [('s1','Someone cannot breathe.','emergency'),('s2','Someone has chest pain.','emergency')]:
        expected=workflow.config['messages'][route]
        workflow.curate(id=id,family=id,split='safety',input=input,expected=expected,rubric={'required':[expected],'forbidden':[],'route':route},synthetic=True,reviewed=True)

def execute(folder,provider,config,key):
    workflow=Learning(folder/'synthetic_learning.db',config);dataset(workflow)
    report={'status':'running','evidence':'actual_live_provider_if_executed','synthetic_only':True,'auto_promotion':False,'active_candidate':None,'turns':[],'prior_accounted_usd':str(getattr(provider.budget,'carried_in',0))}
    try:
        def compiler(train,dev):
            candidate=compile_with_budgeted_optimizer(train,dev,budget=provider)
            if contains_secret(candidate,key):raise StopQA('Sensitive candidate refused before storage.')
            return candidate
        artifact=workflow.candidate(compiler);report['candidate']=artifact
        class Runner:
            evidence_origin='approved_transport'
            def __call__(self,candidate,case):
                runtime=Runtime(folder/(('candidate' if candidate else 'baseline')+'_'+case['id']+'.db'),provider.runtime_client,search=None,config=config)
                try:
                    if candidate:runtime.response_prompt=RESPONSE_PROMPT+'\n'+candidate['proposal']['instructions']+'\n'+json.dumps(candidate['proposal']['demos'])
                    answer=runtime.chat('isolated-synthetic',case['input'])
                    report['turns'].append({'id':case['id'],'variant':'candidate' if candidate else 'baseline','input':case['input'],'actual_output':answer})
                    if provider.budget.stopped:raise StopQA('Provider stopped; evaluation incomplete.')
                    return answer
                finally:runtime.close()
            def verify_evidence(self,id,variant):
                rows=[r for r in report['turns'] if r['id']==id and r['variant']==variant]
                if not rows:return False
                output=rows[-1]['actual_output'];calls=output.get('trace',{}).get('calls',[])
                # Deterministic emergency bypass is legitimate zero-model evidence.
                if output.get('route')=='emergency' and not calls:return True
                receipts={r['response_id'] for r in provider.budget.rows if r['status']=='completed'}
                return bool(calls) and all(c.get('status')=='ok' and c.get('response_id') in receipts for c in calls)
        evaluation=workflow.evaluate(artifact['id'],Runner(),evidence='verified_live',max_calls=16)
        report.update(evaluation=evaluation,status='complete' if evaluation['status']=='complete' else 'stopped_incomplete',active_candidate=workflow.active_suffix()[1])
        # Never promote here, even if eligible. A separate manual review is required.
    except BaseException as error:
        report.update(status='stopped_incomplete',error_type=type(error).__name__,reason='Budget/provider/optimizer/evaluation stopped; no retry or promotion.')
    finally:
        checkpoint(folder,report,provider.budget,key);workflow.close()
    return report

def previous_accounting(cap=Decimal('.90')):
    total=Decimal(0);attempted=False
    for path in ROOT.glob('live_*/result.json'):
        previous=json.loads(path.read_text());b=previous.get('budget',{})
        if str(previous.get('evidence','')).startswith('actual_live_provider') and (b.get('attempts',0) or previous.get('accounting_adjustment') is True):
            try:maximum=Decimal(str(b['maximum_accounted_usd']));carried=Decimal(str(previous.get('prior_accounted_usd',b.get('carried_in_usd','0'))))
            except Exception:raise StopQA('Prior QA accounting is incomplete; no key requested.') from None
            attempted=True
            if not maximum.is_finite() or not carried.is_finite() or not 0<=carried<=maximum<=cap:raise StopQA('Prior QA accounting is invalid; no key requested.')
            total+=maximum-carried
    # Private receipts are deliberately excluded from Git. A clean checkout must
    # still carry their conservative exposure, rather than resetting account spend.
    floor_path=ROOT.parent/'accounting_floor.json'
    if floor_path.exists():
        floor=json.loads(floor_path.read_text());amount=Decimal(floor['maximum_accounted_usd'])
        anchor=floor.get('private_history_anchor','')
        if not amount.is_finite() or amount<0 or not isinstance(anchor,str) or '/' in anchor or '\\' in anchor:raise StopQA('Invalid carried accounting floor.')
        total=max(total,amount) if anchor and (ROOT/anchor/'result.json').exists() else total+amount
        attempted=attempted or amount>0
    if total>cap:raise StopQA('Prior QA consumed the working budget; no key requested.')
    return total,attempted

def project_key(path,provider):
    """Explicit project .env read only; no shell expansion, exports or persistence."""
    file=pathlib.Path(path)
    if file.is_symlink() or file.resolve()!=(ROOT.parent/'.env').resolve() or file.stat().st_size>65536:raise StopQA('Only the explicitly requested project .env file is accepted.')
    target='OPENROUTER_API_KEY' if provider=='openrouter' else 'OPENAI_API_KEY';values=[]
    for line in file.read_text().splitlines():
        line=line.strip()
        if line.startswith('export '):line=line[7:].strip()
        if '=' not in line or line.startswith('#'):continue
        name,value=line.split('=',1)
        if name.strip()!=target:continue
        value=value.strip()
        if len(value)>=2 and value[0]==value[-1] and value[0] in ('"',"'"):value=value[1:-1]
        else:value=value.split(' #',1)[0].strip()
        values.append(value)
    if len(values)!=1 or not values[0]:raise StopQA('Required provider variable is missing, empty or duplicated in project .env.')
    return values[0]

def run(options=None):
    verify_build()
    options=options or argparse.Namespace(provider='openai',model='gpt-5-mini',route_provider=None,compare_model=[],mode='optimize',preflight=False)
    routes=[]
    if options.provider=='openrouter':
        from openrouter_client import route,RouterBudget
        if not options.model or not options.route_provider:raise StopQA('Explicit OpenRouter model and route provider required.')
        routes=[route(options.model,options.route_provider)]
        for value in options.compare_model:
            if '@' not in value:raise StopQA('Comparison syntax is model@provider-tag.')
            model,tag=value.rsplit('@',1);routes.append(route(model,tag))
        if options.mode=='benchmark' and len(routes)!=2:raise StopQA('Benchmark needs exactly two explicit models/routes.')
        if options.mode=='optimize' and len(routes)!=1:raise StopQA('Optimization uses one selected model, never benchmark holdout data.')
        # Check conservative per-request reservation before asking for a key.
        for r in routes:RouterBudget().reserve(r)
    elif options.mode!='optimize' or options.model!='gpt-5-mini':raise StopQA('Direct OpenAI path is pinned to gpt-5-mini; configurable comparison uses OpenRouter.')
    if options.preflight:
        print(json.dumps({'status':'metadata_preflight_only','routes':routes,'inference_calls':0,'key_requested':False,'cap_usd':'1.00'},indent=2));return
    try:import dspy
    except ImportError:raise StopQA('Run with the isolated DSPy environment; no key requested.') from None
    if not getattr(options,'key_file',None) and not sys.stdin.isatty():raise StopQA('Interactive terminal required; no non-hidden credential input.')
    prior,attempted=previous_accounting()
    if attempted and not getattr(options,'resume_accounted',False):raise StopQA('A prior live attempt exists. Retry requires --resume-accounted, which carries ALL prior cost and uncertain reserves within the same $1 cap. Prior accounted: $'+str(prior))
    if options.provider=='openrouter':
        for r in routes:RouterBudget(prior).reserve(r)
    elif prior+Decimal('.11')>Decimal('.90'):raise StopQA('Remaining accounted budget cannot safely reserve another request; no key requested.')
    lock=ROOT/'live_learning.lock'
    try:fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:raise StopQA('Another or interrupted learning run requires review before retry.') from None
    os.close(fd);provider=None;key=None
    try:
        key=project_key(options.key_file,options.provider) if getattr(options,'key_file',None) else getpass.getpass(options.provider+' key (hidden, RAM only; shared test cap $1): ').strip()
        if not key:raise StopQA('No key entered; no request.')
        folder=ROOT/('live_learning_'+time.strftime('%Y%m%d_%H%M%S'));folder.mkdir(mode=0o700)
        if options.provider=='openrouter':
            from openrouter_client import OpenRouterSession,RouterBudget
            shared=RouterBudget(prior);sessions=[OpenRouterSession(key,r,shared) for r in routes];provider=sessions[0]
        else:
            shared=Budget();shared.spent=prior;shared.carried_in=prior;provider=SharedProvider(key,shared);sessions=[provider]
        from stage_trace import SyntheticTrace
        for index,session in enumerate(sessions):
            session.runtime_client=SyntheticTrace(folder/('stages_'+str(index)+'.jsonl'),session.runtime_client,
                synthetic=True,secrets=(key,),budget_checkpoint=lambda:checkpoint(folder,
                {'status':'stage_in_progress','evidence':'actual_live_provider_stage_checkpoint',
                 'prior_accounted_usd':str(prior)},shared,key))
        config=json.loads((ROOT/'verified_source/config.json').read_text());config['models']={k:{'model':MODEL,'effort':'medium'} for k in ('conversation','assessment')};config['runtime']['max_tool_calls']=0;config['learning']={'enabled':False};config['human_review']={'enabled':False};config['reminders']={'enabled':False}
        if options.mode=='benchmark':
            from model_benchmark import benchmark
            report=benchmark(folder,sessions,config,source_digest=json.loads(MANIFEST.read_text())['digest'],checkpoint_fn=lambda report:checkpoint(folder,dict(report,prior_accounted_usd=str(prior)),provider.budget,key));report['prior_accounted_usd']=str(prior);checkpoint(folder,report,provider.budget,key)
        else:
            if options.provider=='openrouter':config['models']={k:{'model':provider.model_id,'effort':'none'} for k in ('conversation','assessment')}
            report=execute(folder,provider,config,key)
        print('QA',report['status'],'No promotion. Results:',folder/'result.json',flush=True)
        for failure in getattr(provider.budget,'failures',[]):print('Diagnostic:',json.dumps(failure),flush=True)
    finally:
        if provider:
            for session in sessions:session.clear()
        key=None;lock.unlink()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--self-test',action='store_true');parser.add_argument('--provider',choices=['openai','openrouter'],default='openai');parser.add_argument('--model',default='gpt-5-mini');parser.add_argument('--route-provider');parser.add_argument('--compare-model',action='append',default=[]);parser.add_argument('--mode',choices=['optimize','benchmark'],default='optimize');parser.add_argument('--preflight',action='store_true');parser.add_argument('--resume-accounted',action='store_true');parser.add_argument('--key-file');args=parser.parse_args()
    if args.self_test:
        import unittest
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT),pattern='test_live_learning.py'));sys.exit(not result.wasSuccessful())
    try:run(args)
    except StopQA as error:print('QA stopped:',str(error),flush=True);sys.exit(1)
    except BaseException as error:
        from openrouter_client import RouterError
        if isinstance(error,RouterError):print('QA stopped:',str(error),flush=True)
        else:print('QA stopped before completion ('+type(error).__name__+'). No secret or raw provider details printed.',flush=True)
        sys.exit(1)
