#!/usr/bin/env python3
"""Isolated QA only. Key via getpass; no environment credentials, retries or searches."""
import argparse, copy, getpass, hashlib, json, os, pathlib, statistics, subprocess, sys, time, urllib.request, urllib.error
from decimal import Decimal
ROOT=pathlib.Path(__file__).resolve().parent
SOURCE=ROOT/'verified_source'
PROJECT=ROOT.parent
MANIFEST=ROOT/'verified_build.json'
sys.path.insert(0,str(SOURCE))
from runtime import Runtime
from model_client import ResponsesClient

MODEL='gpt-5-mini'
# Official context ceiling used as a conservative pre-request INPUT ceiling.
INPUT_CEILING=400000
OUTPUT_CEILING=5000
INPUT_RATE=Decimal('0.25')/1000000
OUTPUT_RATE=Decimal('2')/1000000
CAP=Decimal('0.90')  # $0.10 headroom beneath authorization
MAX_CALLS=60
WORST=INPUT_CEILING*INPUT_RATE+OUTPUT_CEILING*OUTPUT_RATE
ENDPOINT='https://api.openai.com/v1/responses'
class StopQA(Exception): pass

def cost(i,o): return i*INPUT_RATE+o*OUTPUT_RATE
class Budget:
 def __init__(self): self.spent=Decimal(0); self.reserved=Decimal(0); self.attempts=0; self.stopped=False; self.rows=[]
 def reserve(self):
  if self.stopped or self.reserved or self.attempts>=MAX_CALLS or self.spent+WORST>CAP: raise StopQA('Budget refuses another request.')
  self.reserved=WORST; self.attempts+=1
 def fail(self): self.stopped=True # Keep whole worst-case reserve; uncertain billing is never retried.
 def finish(self,result,stage,elapsed):
  try:
   u=result['usage']; i=u['input_tokens']; o=u['output_tokens']
   if type(i)is not int or type(o)is not int or not 0<=i<=INPUT_CEILING or not 0<=o<=OUTPUT_CEILING: raise ValueError()
   model=result['model']
   if model != MODEL and not model.startswith(MODEL+'-'): raise ValueError()
   actual=cost(i,o)
   if actual>self.reserved: raise ValueError()
   row={'attempt':self.attempts,'stage':stage,'model':model,'response_id':str(result.get('id',''))[:150], 'status':str(result.get('status',''))[:40], 'input_tokens':i,'output_tokens':o,'cached_tokens':u.get('input_tokens_details',{}).get('cached_tokens'), 'reasoning_tokens':u.get('output_tokens_details',{}).get('reasoning_tokens'),'elapsed_ms':elapsed,'cost_uncached_usd':str(actual)}
  except (KeyError,ValueError,TypeError,AttributeError): self.fail(); raise StopQA('Missing or invalid usage/model; stopped with reservation retained.') from None
  self.spent+=actual; self.reserved=Decimal(0); self.rows.append(row)
 def summary(self): return {'attempts':self.attempts,'cost_uncached_usd':str(self.spent),'uncertain_reserve_usd':str(self.reserved),'maximum_accounted_usd':str(self.spent+self.reserved),'cap_usd':str(CAP),'authorization_usd':'1.00','stopped':self.stopped,'calls':self.rows}

def contains_secret(value,secret,depth=0):
 if not secret:return False
 if depth>12:return True  # Refuse opaque/deeply nested provider output conservatively.
 if isinstance(value,dict):return any(contains_secret(k,secret,depth+1) or contains_secret(v,secret,depth+1) for k,v in value.items())
 if isinstance(value,list):return any(contains_secret(v,secret,depth+1) for v in value)
 if isinstance(value,str):
  if secret in value:return True
  try:decoded=json.loads(value)
  except (ValueError,TypeError):return False
  if decoded!=value:return contains_secret(decoded,secret,depth+1)
 return False

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): raise StopQA('Redirect refused.')
class SafeTransport:
 def __init__(self,budget,secret): self.budget=budget; self.secret=secret; self.turn=0
 def __call__(self,request,timeout=40):
  # This callback sees the existing client's request only in RAM; never log headers/body.
  if request.full_url!=ENDPOINT or request.get_method()!='POST': raise StopQA('Destination refused.')
  try: p=json.loads(request.data)
  except Exception: raise StopQA('Invalid payload refused.') from None
  if p.get('model')!=MODEL or p.get('max_output_tokens')!=OUTPUT_CEILING or p.get('store')is not False: raise StopQA('Model/output/storage change refused.')
  if p.get('service_tier') not in (None,'default') or any(t.get('type')!='function' for t in p.get('tools',[])): raise StopQA('Paid tool or service tier refused.')
  p['service_tier']='default'
  request.data=json.dumps(p).encode()
  self.budget.reserve(); t=time.perf_counter()
  # Disable proxies and redirects: authenticate only to the exact OpenAI endpoint.
  try:
   with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=min(timeout,40)) as r:
    data=r.read(1000001)
   if len(data)>1000000: raise StopQA('Oversized response.')
   if self.secret.encode() in data: raise StopQA('Sensitive provider output refused.')
   result=json.loads(data)
   if contains_secret(result,self.secret): raise StopQA('Sensitive decoded provider output refused.')
   self.budget.finish(result,self.stage,round((time.perf_counter()-t)*1000,3))
   if result.get('service_tier') not in (None,'default'): self.budget.fail(); raise StopQA('Unexpected service tier; stopped.')
   import io
   return io.BytesIO(data)
  except BaseException:
   self.budget.fail(); raise StopQA('Provider failure; no retry; worst-case reservation retained.') from None

class Client:
 def __init__(self,key,budget):
  self.transport=SafeTransport(budget,key); self.client=ResponsesClient(api_key=key,endpoint=ENDPOINT,opener=self.transport)
 def __call__(self,**kwargs):
  if kwargs.get('model')!=MODEL: raise StopQA('Unexpected model.')
  self.transport.stage=kwargs.get('stage','unknown')
  return self.client(**kwargs)

def no_search(query): raise StopQA('External search disabled for this QA run.')
TURNS=[
 'Please keep your answers concise throughout our conversation.',
 'Add my mother Meera, age 52.',
 'Meera is allergic to penicillin.',
 'Meera has a mild headache since this morning.',
 'Meera has no red flags, is drinking and urinating normally, and the headache started six hours ago.',
 'Add my brother Arjun, age 28. He has a mild cough since yesterday.',
 'Arjun has no red flags and is drinking and urinating normally. I do not know his temperature.',
 'Dust might explain Arjun\'s cough, but that is only my guess, not a confirmed cause.',
 'Correction: Meera is 53 years old, not 52.',
 'Meera also has pain in her left ankle after a minor twist today.',
 'Correction: Meera\'s painful ankle is the right ankle, not the left.',
 'Add my daughter Tara, age 16. She has a new mild headache that started two hours ago.',
 'Tara has no red flags and is drinking and urinating normally.',
 'Meera\'s headache has now completely resolved. Her ankle is still sore.',
 'Arjun\'s cough is still mild. His temperature remains unknown.',
 'Tara\'s headache is still present; this is separate from her grandmother Meera\'s resolved headache.',
 'Hello, how are you?',
 'Meera\'s right ankle pain is mild, started four hours ago, with no red flags. She is drinking and urinating normally.',
 'Arjun is not getting worse; dust remains only a possible explanation.',
 'Tara\'s headache remains mild with no new symptoms.',
]
RECALL='Recall our corrected facts: Meera\'s age, allergy and ankle side; whose headache resolved and whose is still active; Arjun\'s cough onset, known temperature and whether dust is confirmed; and my early answer-style preference. State any uncertainty without guessing.'

def checkpoint(folder,report,budget,key):
 # Serialize fictional content + allowlisted metadata only. Fail before writing if key appears.
 text=json.dumps(dict(report,budget=budget.summary()),indent=2,ensure_ascii=False)
 if key and (key in text or contains_secret(report,key)): raise StopQA('Sensitive output refused before disk write.')
 tmp=folder/'result.tmp'; tmp.write_text(text); tmp.replace(folder/'result.json')

def verify_build():
 try:
  manifest=json.loads(MANIFEST.read_text())
  for name,expected in manifest['files'].items():
   if hashlib.sha256((SOURCE/name).read_bytes()).hexdigest()!=expected or hashlib.sha256((PROJECT/name).read_bytes()).hexdigest()!=expected:
    raise StopQA('Source changed since verification; rerun QA before using a key.')
 except (OSError,ValueError,KeyError):
  raise StopQA('Verified build manifest unavailable; no credential requested.') from None

def run():
 verify_build()
 if not sys.stdin.isatty(): raise StopQA('Run in a local interactive terminal; refusing non-hidden input.')
 key=getpass.getpass('OpenAI key (hidden, RAM only; approved QA cap $1): ').strip()
 if not key: raise StopQA('No key entered; no provider request.')
 folder=ROOT/('live_'+time.strftime('%Y%m%d_%H%M%S')+'_'+str(os.getpid())); folder.mkdir(mode=0o700)
 budget=Budget(); client=Client(key,budget); config=json.loads((SOURCE/'config.json').read_text()); config['models']={k:{'model':MODEL,'effort':'medium'} for k in ['conversation','assessment']}
 config['runtime']['max_tool_calls']=0
 db=folder/'synthetic.db'; household='qa-synthetic-household'; r=Runtime(db,client,search=no_search,config=config)
 report={'status':'running','evidence':'actual_live_provider','turns':[],'config':config,'build':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE.glob('*.py')},'notes':['No searches. Complete responses; no UI/streaming first-output metric. Budget uses full context ceiling before each dispatch; actual usage reconciles at uncached rate.']}
 def chat(msg,phase):
  t=time.perf_counter(); answer=r.chat(household,msg); elapsed=(time.perf_counter()-t)*1000
  report['turns'].append({'number':len(report['turns'])+1,'phase':phase,'user':msg,'actual_output':answer,'elapsed_ms':round(elapsed,3)})
  # Preserve only this synthetic DB. If provider echoes key anywhere, do not persist transcript.
  checkpoint(folder,report,budget,key)
  print('Turn',len(report['turns']),'route',answer.get('route'),'time',round(elapsed/1000,2),'s; cost <= $'+str(budget.spent+budget.reserved),flush=True)
  if budget.stopped or answer.get('route')=='setup_required': raise StopQA('Provider unavailable; run stopped without retry.')
 try:
  for msg in TURNS: chat(msg,'conversation')
  chat(RECALL,'delayed_recall')
  chat('Is Meera\'s headache the same episode as Tara\'s? Please distinguish them.','episode_recall')
  r.close(); r=Runtime(db,client,search=no_search,config=config)
  r.store.db.execute('SELECT 1').fetchone()
  chat(RECALL,'fresh_runtime')
  r.close()
  # Fresh process reload WITHOUT transcript replay. No credential passed to child.
  code='import json,sys; sys.path.insert(0,sys.argv[1]); from runtime import Runtime; r=Runtime(sys.argv[2],None); print(json.dumps(r.state(sys.argv[3]))); r.close()'
  report['fresh_process_state']=json.loads(subprocess.check_output([sys.executable,'-c',code,str(SOURCE),str(db),household],text=True,timeout=10))
  r=Runtime(db,client,search=no_search,config=config)
  chat(RECALL,'post_process_reload_probe')
  report['other_household_state']=r.state('qa-other-household')
  report['status']='completed'
 except BaseException:
  report['status']='stopped_incomplete'; report['reason']='Run stopped on budget, provider, output-redaction or turn failure; no retry.'
 finally:
  try:r.close()
  except Exception:pass
  times=[x['elapsed_ms'] for x in report['turns']]
  def stats(values):
   vals=sorted(values)
   return {'n':len(vals),'p50_ms':statistics.median(vals),'p90_ms':vals[max(0,__import__('math').ceil(.9*len(vals))-1)]} if vals else {'n':0}
  report['latency']={'first_turn':stats(times[:1]),'subsequent_turns':stats(times[1:]),'note':'Runtime end-to-end, not browser rendering or streaming; first turn is not proof of provider cache coldness.'}
  report['error_turns']=sum(t['actual_output'].get('route') in ('error','setup_required') for t in report['turns'])
  checkpoint(folder,report,budget,key)
  key=None; client.client.key=None; client.transport.secret=None
  print('QA',report['status'],'Results:',folder/'result.json',flush=True)

if __name__=='__main__':
 parser=argparse.ArgumentParser(); parser.add_argument('--self-test',action='store_true'); args=parser.parse_args()
 if args.self_test:
  import unittest
  suite=unittest.defaultTestLoader.discover(str(ROOT),pattern='test_live_runner.py'); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())
 try:run()
 except BaseException: print('QA stopped safely. No credential or transport error details printed.',flush=True); sys.exit(1)
