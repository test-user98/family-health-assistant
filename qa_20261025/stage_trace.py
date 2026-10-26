"""Opt-in, bounded synthetic QA evidence; never enabled by the product server."""
import hashlib,json

class SyntheticTrace:
    def __init__(self,path,client,*,synthetic=False,secrets=(),budget_checkpoint=None,max_bytes=1000000):
        if synthetic is not True:raise ValueError('Synthetic-only trace requires explicit opt-in.')
        self.path,self.client,self.secrets=path,client,tuple(s for s in secrets if s)
        self.budget_checkpoint=budget_checkpoint;self.remaining=max_bytes;self.index=0

    def __call__(self,**kw):
        from runtime import decoded
        self.index+=1
        # Only these request fields are captured; no transport, headers or exceptions.
        record={'event':self.index,'evidence':'synthetic_provider_stage','stage':kw.get('stage'),
                'model':kw.get('model'),'effort':kw.get('effort'),
                'prompt':kw.get('instructions'), 'schema':kw.get('schema'),
                'actual_input':kw.get('input'),'tools':kw.get('tools',[])}
        # Validate before dispatch too: refuse accidental key-bearing synthetic input.
        self._encode(record)
        try:result=self.client(**kw)
        except Exception as error:
            record.update(status='client_error',error_type=type(error).__name__)
            self._write(record);raise
        # Billing must persist BEFORE optional rich evidence capture can fail.
        if self.budget_checkpoint:self.budget_checkpoint()
        record['response_id']=result.get('id') if isinstance(result,dict) else None
        try:
            data,calls=decoded(result)
            record.update(status='returned',decoded_output=data,tool_calls=calls)
        except Exception as error:record.update(status='decode_error',error_type=type(error).__name__)
        self._write(record)
        return result

    def _encode(self,record):
        from openrouter_client import secret_present
        if any(secret_present(record,s) for s in self.secrets):raise ValueError('Sensitive synthetic trace refused.')
        raw=json.dumps(record,ensure_ascii=False,separators=(',',':')).encode()
        if len(raw)>64000:
            # Preserve manifest attribution without partial clinical/provider JSON.
            raw=json.dumps({'event':record['event'],'stage':record['stage'],'status':'oversize_omitted',
                            'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}).encode()
        return raw+b'\n'

    def _write(self,record):
        raw=self._encode(record)
        if len(raw)>self.remaining:raise ValueError('Synthetic trace budget exhausted.')
        with self.path.open('ab') as stream:stream.write(raw)
        self.remaining-=len(raw)
