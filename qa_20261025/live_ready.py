"""Explicit $5 cumulative continuation. Synthetic only; no automatic promotion."""
import argparse,contextlib,hashlib,json,os,pathlib,sqlite3,subprocess,sys,tempfile,time
from decimal import Decimal
ROOT=pathlib.Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT.parent));sys.path.insert(0,str(ROOT))
from runtime import Runtime
from openrouter_client import RouterBudget,OpenRouterSession,route,secret_present
from stage_trace import SyntheticTrace
import live_learning as ll
from live_runner import TURNS,RECALL,verify_build

WORKING=Decimal('4.80');AUTHORIZATION=Decimal('5.00')

def guard_consumed_holdout(config):
    from learning import Learning,digest
    with tempfile.TemporaryDirectory() as temporary:
        workflow=Learning(pathlib.Path(temporary)/'planned.db',config)
        try:ll.dataset(workflow);planned=digest(workflow.examples({'holdout','safety'}))
        finally:workflow.close()
    seals=ROOT.parent/'evaluation_seals.json'
    if seals.exists() and planned in json.loads(seals.read_text()).get('consumed_dataset_hashes',[]):
        raise RuntimeError('This held-out dataset was already consumed. Prepare a fresh reviewed set before another optimizer run; no key requested.')
    # A new QA output folder must not reset the single-use evaluation seal.
    for path in ROOT.glob('live_*/synthetic_learning.db'):
        with contextlib.closing(sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)) as db:
            if db.execute("SELECT name FROM sqlite_master WHERE name='learning_holdout_runs'").fetchone() and db.execute('SELECT 1 FROM learning_holdout_runs WHERE dataset_hash=?',(planned,)).fetchone():
                raise RuntimeError('This held-out dataset was already consumed. Prepare a fresh reviewed set before another optimizer run; no key requested.')
    return planned

def atomic(path,data,key=None):
    if key and secret_present(data,key):raise ValueError('Sensitive/deep report refused; accounting file remains independent.')
    raw=json.dumps(data,indent=2,ensure_ascii=False).encode();temp=path.with_suffix('.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)

def baseline(folder,session,config,key):
    report={'evidence':'actual_live_provider_synthetic_conversation','status':'running','turns':[],
            'source_digest':json.loads((ROOT/'current_build.json').read_text())['digest'],'route':session.route}
    runtime=Runtime(folder/'household.db',session.runtime_client,config=config)
    def send(text,kind='conversation'):
        answer=runtime.chat('synthetic-long-run',text);report['turns'].append({'kind':kind,'input':text,'actual_output':answer})
        atomic(folder/'run.json',report,key)
        print('Live',kind,len(report['turns']),'route',answer['route'],'accounted',session.budget.summary()['maximum_accounted_usd'],flush=True)
        if answer['route'] in ('error','context_overflow','setup_required','permission_denied'):
            report['status']='baseline_failed';return False
        return not session.budget.stopped
    try:
        for text in TURNS:
            if not send(text):return report
        if not send(RECALL,'recall'):return report
        runtime.close()
        # A separate process reloads SQLite with no key, model, or replayed transcript.
        code="""import json,sys;from runtime import Runtime;r=Runtime(sys.argv[1],None);s=r.state('synthetic-long-run');print(json.dumps({'members':s['members'],'episodes':s['episodes']}));r.close()"""
        result=subprocess.run([sys.executable,'-c',code,str(folder/'household.db')],cwd=ROOT.parent,capture_output=True,text=True,timeout=15,check=True)
        report['separate_process_reload']=json.loads(result.stdout)
        runtime=Runtime(folder/'household.db',session.runtime_client,config=config)
        if not send('What allergy was reported for Meera?','fresh_runtime_recall'):return report
        if not send('How old is Meera?','fresh_runtime_recall'):return report
        historical=json.loads((ROOT/'ARCHITECTURE_RUN.json').read_text())
        event=next(e for r in historical['requests'] for e in r['stage_events'] if e['stage']=='response' and isinstance(e['actual_input'],list) and any(x.get('type')=='function_call_output' for x in e['actual_input']))
        sources=json.loads(next(x['output'] for x in event['actual_input'] if x.get('type')=='function_call_output'))['sources']
        class ReplaySearch:
            domains=['nhs.uk'];calls=[]
            def __call__(self,query):
                if self.calls:raise RuntimeError('Only one fixture lookup allowed.')
                self.calls.append(query)
                return [{**s,'kind':'historical_replay_fixture'} for s in sources if s['url'].startswith('https://www.nhs.uk/')][:1]
        runtime.search=ReplaySearch();runtime.limits['max_tool_calls']=2
        if not send('Please search general sleep guidance.','live_model_with_replayed_search_tool'):return report
        report['search_evidence']={'provider_calls':'live model','source_content':'historical replay fixture, not a fresh online search','actual_queries':runtime.search.calls}
        if not send('Meera has chest pain and cannot breathe.','emergency'):return report
        report['status']='baseline_complete_requires_evidence_review'
        return report
    finally:
        runtime.close();atomic(folder/'run.json',report,key)

def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=('baseline','compare','optimize'),required=True);p.add_argument('--model',default='openai/gpt-5-mini');p.add_argument('--provider-tag',default='openai');p.add_argument('--approved-total',required=True,choices=('5',));p.add_argument('--preflight',action='store_true');p.add_argument('--fresh-holdout',action='store_true');args=p.parse_args()
    verify_build();prior,_=ll.previous_accounting(cap=WORKING)
    if args.mode=='optimize':guard_consumed_holdout(json.loads((ROOT.parent/'config.json').read_text()))
    models=[route(args.model,args.provider_tag)]
    if args.mode=='compare':models.append(route('openai/gpt-4.1-mini','openai'))
    phase_allowance={'baseline':Decimal('.80'),'compare':Decimal('.60'),'optimize':Decimal('.70')}[args.mode]
    phase_cap=min(WORKING,prior+phase_allowance)
    if args.mode=='optimize' and WORKING-prior<Decimal('2.30'):raise RuntimeError('Insufficient conservative room for $0.70 generation plus $1.60 reserved paired evaluation.')
    for r in models:RouterBudget(prior,cap=str(phase_cap),authorization='5.00').reserve(r)
    print(json.dumps({'mode':args.mode,'prior_accounted_usd':str(prior),'phase_cap_usd':str(phase_cap),'total_working_ceiling':'4.80','total_authorization':'5.00','evaluation_room_reserved_for_optimization':'1.60','routes':models,'activation':'none'}),flush=True)
    if args.preflight:return
    lock=ROOT/'live_learning.lock';fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    key=None;sessions=[]
    try:
        folder=ROOT/('live_ready_'+time.strftime('%Y%m%d_%H%M%S'));folder.mkdir(mode=0o700)
        key=ll.project_key(ROOT.parent/'.env','openrouter')
        def persist(summary):atomic(folder/'result.json',{'evidence':'actual_live_provider_durable_accounting','status':'accounted','mode':args.mode,'source_digest':json.loads((ROOT/'current_build.json').read_text())['digest'],'routes':models,'prior_accounted_usd':str(prior),'budget':summary},key)
        budget=RouterBudget(prior,cap=str(phase_cap),authorization='5.00',persist=persist,max_calls=80)
        budget.save()
        for index,r in enumerate(models):
            session=OpenRouterSession(key,r,budget,synthetic_diagnostics=True);session.runtime_client=SyntheticTrace(folder/('stages_'+str(index)+'.jsonl'),session.runtime_client,synthetic=True,secrets=(key,));sessions.append(session)
        cfg=json.loads((ROOT/'verified_source/config.json').read_text());cfg['models']={k:{'model':args.model,'effort':'none'} for k in ('conversation','assessment')}
        cfg.setdefault('context',{})['token_counter']='cached_tiktoken'
        for k in ('learning','human_review','reminders'):cfg[k]={'enabled':False}
        cfg['runtime']['max_tool_calls']=0
        if args.mode=='baseline':report=baseline(folder,sessions[0],cfg,key)
        elif args.mode=='compare':
            from model_benchmark import benchmark
            options={}
            if args.fresh_holdout:
                from comparison_holdout import CASES,seed_holdout
                options={'cases':CASES,'seed_fn':seed_holdout}
                atomic(folder/'frozen_holdout.json',{'cases':CASES,'seed':'Nila47/sesame/Omar34/unknown_temperature','excluded_from_optimizer':True},key)
            report=benchmark(folder,sessions,cfg,source_digest=json.loads((ROOT/'current_build.json').read_text())['digest'],checkpoint_fn=lambda report:atomic(folder/'run.json',report,key),**options)
            report['dataset']='fresh_frozen_comparison_holdout_v1' if args.fresh_holdout else 'known_regression_pilot'
        else:
            # DSPy may print raw synthetic proposal text. Keep its stdout/stderr local
            # and secret-screen it before writing; never show it in tool output.
            import io
            original=ll.compile_with_budgeted_optimizer;start=budget.attempts
            class OptimizerBudget:
                authorized=True;provider=True;model_id=args.model
                def call(self,messages):
                    if budget.attempts-start>=24:raise RuntimeError('Optimizer 24-call ceiling; validation room retained.')
                    answer=sessions[0].call(messages)
                    atomic(folder/('optimizer_stage_'+str(budget.attempts-start)+'.json'),{'synthetic_messages':messages,'actual_output':answer,'receipt':budget.rows[-1]},key)
                    return answer
            def compiler(train,dev,*,budget):
                candidate=original(train,dev,budget=OptimizerBudget())
                sessions[0].budget.cap=WORKING;sessions[0].budget.save()
                return candidate
            captured=io.StringIO();ll.compile_with_budgeted_optimizer=compiler
            try:
                with contextlib.redirect_stdout(captured),contextlib.redirect_stderr(captured):report=ll.execute(folder,sessions[0],cfg,key)
            finally:ll.compile_with_budgeted_optimizer=original
            output=captured.getvalue()
            if key not in output and len(output.encode())<=250000:atomic(folder/'optimizer_log.json',{'synthetic_optimizer_output':output},key)
        report['source_digest']=json.loads((ROOT/'current_build.json').read_text())['digest'];report['routes']=models
        atomic(folder/'run.json',report,key);budget.save()
        print('Saved',folder,'status',report['status'],'accounted',budget.summary()['maximum_accounted_usd'],'no activation',flush=True)
    finally:
        for session in sessions:session.clear()
        key=None;lock.unlink(missing_ok=True)

if __name__=='__main__':
    try:main()
    except BaseException as error:
        print('Continuation stopped:',type(error).__name__,'Inspect durable accounting and synthetic stage artifacts; no raw exception or secret printed.',flush=True);sys.exit(1)
