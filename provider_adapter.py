"""App OpenRouter adapter: explicit routes, RAM-only key and shared session cap.

No ambient key lookup, fallback, redirect, retry or optimizer activation. QA can
inject a durable cumulative budget; ordinary app caps last for this connection
in this server process, not an account-wide spending limit.
"""
import threading
from openrouter_client import OpenRouterSession,RouterBudget,RouterError,route

class RouterAppClient:
    def __init__(self,key,tag='openai',cap='.90',*,budget=None,fetch=None,transport=None):
        self.key,self.tag=key,tag
        self.budget=budget if budget is not None else RouterBudget(cap=cap,authorization=cap,max_calls=120)
        self.fetch,self.transport=fetch,transport
        self.sessions={};self.lock=threading.RLock()
    def __call__(self,**kwargs):
        # Effort is never silently ignored by this tested route adapter.
        if kwargs.get('effort') not in (None,'','none'):raise RouterError('OpenRouter app route currently supports provider-default effort only.')
        with self.lock:
            model=kwargs['model']
            if model not in self.sessions:
                selected=route(model,self.tag,self.fetch)
                self.sessions[model]=OpenRouterSession(self.key,selected,self.budget,self.transport)
            return self.sessions[model].runtime_client(**kwargs)
    def status(self):
        with self.lock:
            s=self.budget.summary()
            return {k:s[k] for k in ('maximum_accounted_usd','uncertain_reserve_usd','cap_usd','stopped')}
    def clear(self):
        with self.lock:
            for session in self.sessions.values():session.clear()
            self.key=None
