"""Optional offline o200k counter. Never downloads data during a chat request.

The o200k pattern/special-token definition follows OpenAI tiktoken (MIT):
https://github.com/openai/tiktoken/blob/main/tiktoken_ext/openai_public.py
"""
import base64,functools,hashlib,json,os,pathlib,re

@functools.lru_cache(maxsize=1)
def cached_o200k():
    try:import tiktoken
    except ImportError:return None
    url='https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'
    path=pathlib.Path(os.environ.get('TIKTOKEN_CACHE_DIR','/tmp/family-health-tokenizer-cache'))/hashlib.sha1(url.encode()).hexdigest()
    if not path.is_file() or path.stat().st_size>8000000:return None
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!='446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d':return None
    pattern='|'.join([r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?",r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?",r'\p{N}{1,3}',r' ?[^\s\p{L}\p{N}]+[\r\n/]*',r'\s*[\r\n]+',r'\s+(?!\S)',r'\s+'])
    ranks={base64.b64decode(token):int(rank) for token,rank in (line.split() for line in raw.splitlines() if line)}
    return tiktoken.Encoding(name='o200k_base',pat_str=pattern,mergeable_ranks=ranks,special_tokens={'<|endoftext|>':199999,'<|endofprompt|>':200018})

def meter(config,fallback):
    models=[p.get('model','').removeprefix('openai/') for p in config.get('models',{}).values()]
    supported=models and all(re.fullmatch(r'(?:gpt-5-mini|gpt-4\.1-mini)(?:-\d{4}-\d{2}-\d{2})?',m) for m in models)
    encoder=cached_o200k() if config.get('context',{}).get('token_counter')=='cached_tiktoken' and supported else None
    if encoder:return lambda value:len(encoder.encode(json.dumps(value,ensure_ascii=True),disallowed_special=())), 'cached_tiktoken_o200k_base'
    return lambda value:len(fallback(value)), 'utf8_byte_upper_bound'
