"""Explicit OpenRouter route, native-cost budget and Responses-shaped Runtime adapter.

No credential lookup, fallbacks, redirects, plugins, server tools or retries.
Public metadata preflight is not inference/compatibility proof.
"""
from decimal import Decimal
import hashlib,io,json,re,time,uuid,urllib.request,urllib.error

ENDPOINT='https://openrouter.ai/api/v1/chat/completions'
class RouterError(ValueError):pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**k):raise RouterError('Redirect refused.')
def opener():return urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def secret_present(value,secret,depth=0):
    if depth>12:return True
    if isinstance(value,dict):return any(secret_present(k,secret,depth+1) or secret_present(v,secret,depth+1) for k,v in value.items())
    if isinstance(value,list):return any(secret_present(v,secret,depth+1) for v in value)
    if isinstance(value,str):
        if secret in value:return True
        try:decoded=json.loads(value)
        except (ValueError,TypeError):return False
        return decoded!=value and secret_present(decoded,secret,depth+1)
    return False

def route(model,tag,fetch=None):
    if model.startswith('openrouter/') or not re.fullmatch(r'[a-z0-9._-]+/[a-z0-9._-]+',model):raise RouterError('Explicit model ID required; router/online variants are disabled.')
    url='https://openrouter.ai/api/v1/models/'+model+'/endpoints'
    if fetch is None:
        with opener().open(url,timeout=20) as response:
            raw=response.read(2000001)
        if len(raw)>2000000:raise RouterError('Metadata too large.')
        body=json.loads(raw)
    else:body=fetch(url)
    data=body.get('data',{});matches=[e for e in data.get('endpoints',[]) if e.get('tag')==tag]
    if data.get('id')!=model or len(matches)!=1:raise RouterError('Exact provider route unavailable or ambiguous.')
    endpoint=matches[0];supported=set(endpoint.get('supported_parameters',[]))
    if not {'response_format','structured_outputs','tools','tool_choice'}<=supported or not {'max_tokens','max_completion_tokens'}&supported:raise RouterError('Route lacks required strict-schema/tool/output-bound parameters.')
    pricing=endpoint.get('pricing',{})
    try:
        rates={k:Decimal(str(pricing.get(k,'0'))) for k in ('prompt','completion','request','internal_reasoning')}
        if 'prompt' not in pricing or 'completion' not in pricing or any(not v.is_finite() or v<0 for v in rates.values()):raise ValueError()
        context=int(endpoint['context_length']);limit=min(5000,int(endpoint.get('max_completion_tokens') or 5000))
        if context<=0 or limit<=0:raise ValueError()
    except (ValueError,TypeError,KeyError):raise RouterError('Verified finite route pricing/context limits required.') from None
    return dict(model=model,tag=tag,provider=endpoint['provider_name'],endpoint_name=endpoint['name'],context=context,max_output=limit,max_parameter='max_tokens' if 'max_tokens' in supported else 'max_completion_tokens',rates={k:str(v) for k,v in rates.items()},supported_parameters=sorted(supported),fetched_at=time.time(),metadata_sha256=hashlib.sha256(json.dumps(endpoint,sort_keys=True).encode()).hexdigest())

class RouterBudget:
    def __init__(self,spent_before='0',*,cap='.90',authorization='1.00',persist=None,max_calls=60):
        self.spent=Decimal(str(spent_before));self.carried_in=self.spent;self.reserved=Decimal(0);self.attempts=0;self.stopped=False;self.rows=[];self.failures=[]
        self.cap=Decimal(cap);self.authorization=Decimal(authorization);self.persist=persist;self.max_calls=max_calls;self.reservations=[]
        if not all(v.is_finite() for v in (self.spent,self.cap,self.authorization)) or not 0<=self.spent<=self.cap<=self.authorization<=Decimal('5.00') or not 1<=max_calls<=120:raise RouterError('Invalid prior budget accounting or authorization.')
    def save(self):
        if self.persist:
            try:self.persist(self.summary())
            except BaseException:
                self.stopped=True
                raise RouterError('Durable accounting write failed; dispatch refused or receipt retained in memory. No retry.') from None
    def reserve(self,route,stage='unknown'):
        rate=route['rates'];worst=Decimal(rate['prompt'])*route['context']+max(Decimal(rate['completion']),Decimal(rate['internal_reasoning']))*route['max_output']+Decimal(rate['request'])
        if self.stopped or self.reserved or self.attempts>=self.max_calls or self.spent+worst>self.cap:raise RouterError('Shared safety budget refuses request before dispatch.')
        self.reserved=worst;self.attempts+=1
        self.reservations.append(dict(local_request_id=uuid.uuid4().hex,stage=stage,model=route['model'],provider=route['provider'],maximum_usd=str(worst),at=time.time(),status='reserved_before_dispatch'))
        self.save()
    def finish(self,result,route,stage,elapsed):
        try:
            usage=result['usage'];i=usage['prompt_tokens'];o=usage['completion_tokens'];reported=Decimal(str(usage['cost']))
            if type(i)is not int or type(o)is not int or not 0<=i<=route['context'] or not 0<=o<=route['max_output'] or not reported.is_finite() or reported<0:raise ValueError()
            if result.get('provider')!=route['provider'] or not (result.get('model')==route['model'] or str(result.get('model','')).startswith(route['model']+'-')):raise ValueError()
            rate=route['rates'];conservative=Decimal(rate['prompt'])*i+max(Decimal(rate['completion']),Decimal(rate['internal_reasoning']))*o+Decimal(rate['request']);charged=max(reported,conservative)
            if charged>self.reserved:raise ValueError()
        except (KeyError,ValueError,TypeError):self.stopped=True;raise RouterError('Route/cost/usage unverified; reserve retained, no retry.') from None
        self.spent+=charged;self.reserved=Decimal(0)
        self.rows.append(dict(response_id=str(result.get('id',''))[:150],model=result['model'],provider=result['provider'],stage=stage,status='completed',input_tokens=i,output_tokens=o,reasoning_tokens=usage.get('completion_tokens_details',{}).get('reasoning_tokens'),cached_tokens=usage.get('prompt_tokens_details',{}).get('cached_tokens'),reported_cost_usd=str(reported),conservative_accounted_usd=str(charged),elapsed_ms=elapsed))
        self.reservations[-1].update(status='receipt_recorded',response_id=self.rows[-1]['response_id'])
        self.save()
    def summary(self):return dict(attempts=self.attempts,cost_uncached_usd=str(self.spent),uncertain_reserve_usd=str(self.reserved),maximum_accounted_usd=str(self.spent+self.reserved),carried_in_usd=str(self.carried_in),cap_usd=str(self.cap),authorization_usd=str(self.authorization),stopped=self.stopped,calls=self.rows,failures=self.failures,reservations=self.reservations)

def messages_from_inputs(input):
    if isinstance(input,str):return [{'role':'user','content':input}]
    rows=[];pending=[]
    def flush():
        if pending:rows.append({'role':'assistant','content':None,'tool_calls':pending.copy()});pending.clear()
    for item in input:
        if item.get('type')=='function_call':pending.append({'id':item['call_id'],'type':'function','function':{'name':item['name'],'arguments':item['arguments']}});continue
        flush()
        if item.get('type')=='function_call_output':rows.append({'role':'tool','tool_call_id':item['call_id'],'content':item['output']})
        elif 'role' in item:rows.append({'role':item['role'],'content':item['content']})
        elif item.get('type')=='message':rows.append({'role':'assistant','content':''.join(x.get('text','') for x in item.get('content',[]))})
        else:raise RouterError('Unsupported history item.')
    flush();return rows

class OpenRouterSession:
    def __init__(self,key,route,budget=None,transport=None,*,synthetic_diagnostics=False):
        if not isinstance(key,str) or not key:raise RouterError('Transient OpenRouter key required.')
        self.key,self.route,self.model_id=key,route,route['model'];self.budget=budget or RouterBudget();self.transport=transport or opener();self.authorized=True;self.provider=self.call
        self.synthetic_diagnostics=synthetic_diagnostics is True
    def complete(self,messages,*,stage,schema=None,tools=(),tool_choice=None):
        r=self.route;rates=r['rates']
        payload={'model':r['model'],'messages':messages,r['max_parameter']:r['max_output'],'stream':False,'provider':{'only':[r['tag']],'order':[r['tag']],'allow_fallbacks':False,'require_parameters':True,'data_collection':'deny','max_price':{'prompt':float(Decimal(rates['prompt'])*1000000),'completion':float(Decimal(rates['completion'])*1000000),'request':float(Decimal(rates['request']))}}}
        if schema:payload['response_format']={'type':'json_schema','json_schema':{'name':'validated_response','strict':True,'schema':schema}}
        if tools:
            payload['tools']=[{'type':'function','function':{k:v for k,v in t.items() if k!='type'}} for t in tools]
            if tool_choice:payload['tool_choice']=tool_choice
        raw=json.dumps(payload).encode()
        if len(raw)>100000:raise RouterError('Request context bound exceeded.')
        request=urllib.request.Request(ENDPOINT,raw,{'Authorization':'Bearer '+self.key,'Content-Type':'application/json'},method='POST')
        self.budget.reserve(r,stage);start=time.perf_counter()
        try:
            with self.transport.open(request,timeout=40) as response:data=response.read(1000001)
            if len(data)>1000000:raise RouterError('Oversized response refused.')
            result=json.loads(data)
            receipt={k:result.get(k) for k in ('id','model','provider','usage')}
            if secret_present(receipt,self.key):raise RouterError('Sensitive receipt refused.')
            # Billing is durable before rich output/secret/decode diagnostics.
            self.budget.finish(receipt,r,stage,round((time.perf_counter()-start)*1000,3))
            if secret_present(result,self.key):raise RouterError('Sensitive decoded response refused.')
            return result
        except BaseException as error:
            diagnostic={'stage':stage,'category':'transport_or_validation','http_status':None}
            if isinstance(error,urllib.error.HTTPError):
                diagnostic.update(category='http_error',http_status=error.code)
                # Provider text can echo secrets or prompts. Keep only a tiny
                # allowlisted code, never message/body/headers/request payload.
                try:
                    body=json.loads(error.read(65536));code=body.get('error',{}).get('code')
                    if type(code)is int and 100<=code<=599:diagnostic['code']=code
                    elif code in ('invalid_request_error','insufficient_credits','rate_limit_exceeded','provider_error'):diagnostic['code']=code
                    details=body.get('error',{});param=details.get('param')
                    if isinstance(param,str) and param.split('.')[0] in ('response_format','messages','model','provider','max_tokens','max_completion_tokens','tools','tool_choice'):diagnostic['parameter']=param.split('.')[0]
                    # Classification only; never include the provider's message.
                    text=details.get('message','')
                    if isinstance(text,str) and 'schema' in text.lower():diagnostic['category']='schema_rejected'
                    nested=details.get('metadata',{}).get('raw') if isinstance(details.get('metadata'),dict) else None
                    if isinstance(nested,str) and len(nested)<=50000:
                        diagnostic['provider_error_sha256']=hashlib.sha256(nested.encode()).hexdigest()
                        decoded_error=json.loads(nested).get('error',{})
                        text=decoded_error.get('message','')
                        if decoded_error.get('type') in ('invalid_request_error','rate_limit_error','server_error'):diagnostic['provider_error_type']=decoded_error['type']
                        if isinstance(text,str):
                            low=text.lower()
                            if self.synthetic_diagnostics and len(text)<=2000 and not secret_present(text,self.key) and not re.search(r'sk-|bearer|authorization|password|api.key',text,re.I):diagnostic['synthetic_schema_reason']=text
                            for words,category in [(('enum','unique'),'schema_duplicate_enum'),(('enum','length'),'schema_enum_length'),(('enum','1000'),'schema_enum_count'),(('schema',),'schema_rejected'),(('context','length'),'context_limit'),(('reasoning',),'reasoning_parameter_rejected')]:
                                if all(w in low for w in words):diagnostic['category']=category;break
                except Exception:pass
                finally:error.close()
            self.budget.failures.append(diagnostic)
            if self.budget.reserved and self.budget.reservations:self.budget.reservations[-1]['status']='outcome_unknown_no_retry'
            self.budget.stopped=True;self.budget.save()
            raise RouterError('Provider request failed; safe status/category saved in result.json. No retries; uncertain reserve retained.') from None
    def call(self,messages):
        result=self.complete(messages,stage='dspy_optimizer');choice=result['choices'][0]
        if choice.get('finish_reason')!='stop' or not isinstance(choice['message'].get('content'),str):raise RouterError('Incomplete optimizer output.')
        return choice['message']['content']
    def runtime_client(self,*,stage,model,effort,instructions,input,schema,tools,tool_choice=None):
        if model!=self.model_id:raise RouterError('Unapproved model change.')
        result=self.complete([{'role':'system','content':instructions}]+messages_from_inputs(input),stage='paired_'+stage,schema=schema,tools=tools,tool_choice=tool_choice)
        choice=result['choices'][0];message=choice['message'];output=[]
        for call in message.get('tool_calls',[]):output.append({'type':'function_call','call_id':call['id'],'name':call['function']['name'],'arguments':call['function']['arguments']})
        if isinstance(message.get('content'),str):output.append({'type':'message','content':[{'type':'output_text','text':message['content']}]})
        u=result['usage'];usage={'input_tokens':u['prompt_tokens'],'output_tokens':u['completion_tokens'],'input_tokens_details':{'cached_tokens':u.get('prompt_tokens_details',{}).get('cached_tokens')},'output_tokens_details':{'reasoning_tokens':u.get('completion_tokens_details',{}).get('reasoning_tokens')}}
        return dict(id=result['id'],model=result['model'],usage=usage,status='completed' if choice.get('finish_reason') in ('stop','tool_calls') and not message.get('refusal') else 'incomplete',output=output)
    def clear(self):self.key=None
