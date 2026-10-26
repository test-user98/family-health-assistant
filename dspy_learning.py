"""Optional real DSPy compiler. No ambient LM configuration or provider calls.

LabeledFewShot is a no-LM compilation baseline, NOT evidence of learned improvement.
An actual provider optimizer requires a separately budgeted trusted callable.
"""
def fact_metric(expected,text):
    """Bounded nonclinical dev-label matching, not a general semantic judge."""
    import re
    label=expected.strip().casefold();text=text.strip().casefold()
    if label=='unknown':
        if re.search(r'\bnot\s+unknown\b',text):return False
        return bool(re.search(r"\b(?:cannot|can't) report (?:an? )?(?:exact|precise) (?:temperature|measurement|reading)\b|\bunknown\b|\b(?:unspecified|unrecorded)\b|\bnot (?:been )?(?:known|recorded|provided|measured|specified)\b|\bno (?:available |specific )?(?:data|information)\b|\bdon't have (?:any )?information\b",text))
    if label=='not confirmed':
        return bool(re.search(r'\b(?:unconfirmed|not (?:been )?(?:confirmed|established|proven))\b',text)) and not re.search(r'(?<!not )(?<!not been )\bconfirmed\b',text)
    if re.fullmatch(r'\d+',label):
        return bool(re.search(r'(?<![\d.])'+re.escape(label)+r'(?!\d|\.\d)',text)) and not re.search(r'\bnot\s+'+re.escape(label)+r'\b|\bor\b',text)
    return bool(re.search(r'\b'+re.escape(label)+r'\b',text)) and not re.search(r'\bnot\s+'+re.escape(label)+r'\b',text)

def compile_labeled(train,dev):
    import dspy
    class Conversation(dspy.Signature):
        """Give concise grounded conversational wording; never alter clinical policy or claim external actions."""
        context=dspy.InputField()
        answer=dspy.OutputField()
    program=dspy.Predict(Conversation)
    examples=[dspy.Example(context=e['input'],answer=e['expected']).with_inputs('context') for e in train]
    compiled=dspy.LabeledFewShot(k=min(4,len(examples))).compile(program,trainset=examples,sample=False)
    return dict(instructions=compiled.signature.instructions,demos=[{'input':d.context,'expected':d.answer} for d in compiled.demos],optimizer='DSPy LabeledFewShot (no-LM baseline)',dspy_version=dspy.__version__)


class ProviderBudget:
    """Shared conservative reserve for optimization AND subsequent evaluations.

    The explicit provider callback must use the requested pinned model/limits,
    no retries/tools and authenticate only to OpenAI; no ambient transport exists.
    At most eight $0.11 worst-case requests; uncertain usage stops the run.
    """
    def __init__(self,provider=None,*,authorized=False,spent_before='0'):
        from decimal import Decimal
        self.provider,self.authorized=provider,authorized
        self.reserved=Decimal(spent_before);self.stopped=False;self.calls=[]
        if self.reserved<0 or self.reserved>Decimal('.90'):raise ValueError('Invalid existing spend.')

    def call(self,messages):
        from decimal import Decimal
        import json
        if not self.authorized or self.provider is None:raise RuntimeError('No explicitly authorized isolated provider transport.')
        if self.stopped or len(json.dumps(messages).encode())>100000 or self.reserved+Decimal('.11')>Decimal('.90'):raise RuntimeError('Optimization budget/context exhausted before provider dispatch.')
        self.reserved+=Decimal('.11')
        record={'status':'reserved','maximum_usd':'.11'};self.calls.append(record)
        try:
            result=self.provider(messages=messages,model='gpt-5-mini',max_output_tokens=5000,tools=[],retries=0)
            usage=result.get('usage',{})
            if not str(result.get('model','')).startswith('gpt-5-mini') or not isinstance(result.get('text'),str) or any(type(usage.get(k)) is not int for k in ('input_tokens','output_tokens')) or not 0<=usage['input_tokens']<=400000 or not 0<=usage['output_tokens']<=5000:raise RuntimeError('Uncertain provider model or usage.')
            record.update(status='complete',input_tokens=usage['input_tokens'],output_tokens=usage['output_tokens'])
            return result['text']
        except BaseException:
            self.stopped=True;record['status']='outcome_unknown_reserve_retained'
            raise RuntimeError('Optimization interrupted or provider outcome unverified; no retries.') from None


def compile_with_budgeted_optimizer(train,dev,*,budget):
    """Small MIPROv2 run; impossible without an explicit authorized transport.

    Reuse this SAME budget for holdout/model evaluation; never reset its allowance.
    Clinical rules and held-out inputs are not supplied to the optimizer.
    """
    import dspy
    if not budget.authorized or budget.provider is None:raise RuntimeError('Isolated authorized provider transport is unavailable.')
    # DSPy imports this only after paid instruction proposal; fail before any
    # provider call when its optional optimizer dependency is missing.
    import importlib
    try:importlib.import_module('optuna')
    except ImportError:raise RuntimeError('MIPROv2 optional dependency optuna is required before provider dispatch; install dspy[optuna].') from None
    class GuardedLM(dspy.BaseLM):
        def __init__(self):super().__init__(getattr(budget,'model_id','gpt-5-mini'),max_tokens=5000,cache=False,num_retries=0)
        def __call__(self,prompt=None,messages=None,**kwargs):
            if kwargs.get('n',1)!=1:raise RuntimeError('Multiple completions are disabled.')
            return [budget.call(messages if messages is not None else [{'role':'user','content':prompt}])]
    class Conversation(dspy.Signature):
        """Grounded concise general conversation. Clinical safety remains external."""
        context=dspy.InputField()
        answer=dspy.OutputField()
    examples=lambda rows:[dspy.Example(context=e['input'],answer=e['expected']).with_inputs('context') for e in rows]
    lm=GuardedLM()
    metric=lambda example,prediction,trace=None:fact_metric(example.answer,prediction.answer)
    with dspy.context(lm=lm,disable_history=True):
        optimizer=dspy.MIPROv2(metric=metric,prompt_model=lm,task_model=lm,auto=None,num_candidates=2,max_bootstrapped_demos=0,max_labeled_demos=2,num_threads=1,max_errors=1,verbose=False)
        compiled=optimizer.compile(dspy.Predict(Conversation),trainset=examples(train),valset=examples(dev),num_trials=2,minibatch=False,program_aware_proposer=False)
    return dict(instructions=compiled.signature.instructions,demos=[{'input':d.context,'expected':d.answer} for d in compiled.demos],optimizer='DSPy injected budgeted optimizer',dspy_version=dspy.__version__)
