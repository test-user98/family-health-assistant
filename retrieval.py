"""Bounded read-source records and a public-HTTPS-only fetch adapter.

No ambient credentials, proxies or redirects to private networks. Tool/source text is data.
"""
import hashlib
import html
import http.client
import ipaddress
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit,urljoin

OFFICIAL=('nhs.uk','cdc.gov','who.int','medlineplus.gov','nice.org.uk')
class SourceError(ValueError):pass

def safe_url(url, domains=OFFICIAL):
    try:p=urlsplit(url)
    except ValueError:raise SourceError('Invalid source URL.') from None
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443) or any(c in url for c in '\r\n'):
        raise SourceError('Only public official HTTPS source URLs are allowed.')
    host=p.hostname.lower().rstrip('.')
    try:
        ipaddress.ip_address(host)
        raise SourceError('Literal IP source URLs are blocked.')
    except ValueError as e:
        if isinstance(e,SourceError):raise
    if not any(host==d or host.endswith('.'+d) for d in domains):raise SourceError('Source domain is not allowed.')
    return p

class TextOnly(HTMLParser):
    def __init__(self):super().__init__();self.depth=0;self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'):self.depth+=1
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'):self.depth=max(0,self.depth-1)
    def handle_data(self,data):
        if not self.depth:self.parts.append(data)

def text_only(text):
    parser=TextOnly();parser.feed(str(text));return ' '.join(html.unescape(' '.join(parser.parts)).split())

class PublicFetcher:
    def __init__(self,domains=OFFICIAL,resolver=socket.getaddrinfo,timeout=8,max_bytes=128000):
        self.domains,self.resolver,self.timeout,self.max_bytes=domains,resolver,timeout,max_bytes
    def __call__(self,url):
        for _ in range(4):
            parsed=safe_url(url,self.domains)
            records=self.resolver(parsed.hostname,443,type=socket.SOCK_STREAM)
            ips=sorted({r[4][0] for r in records})
            if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):raise SourceError('Source resolved to a non-public address.')
            host,ip=parsed.hostname,ips[0]
            # Connect to the validated IP while verifying TLS against the original hostname.
            class PinnedHTTPS(http.client.HTTPSConnection):
                def connect(inner):
                    sock=socket.create_connection((ip,443),timeout=inner.timeout)
                    inner.sock=ssl.create_default_context().wrap_socket(sock,server_hostname=host)
            conn=PinnedHTTPS(host,timeout=self.timeout)
            try:
                conn.request('GET',parsed.path+('?' + parsed.query if parsed.query else ''),headers={'Accept':'text/html,text/plain','User-Agent':'FamilyHealthResearch/1.0'})
                response=conn.getresponse()
                if response.status in (301,302,303,307,308):
                    url=urljoin(url,response.getheader('Location',''));safe_url(url,self.domains);continue
                if response.status!=200:raise SourceError('Source fetch failed.')
                if not any(t in response.getheader('Content-Type','').lower() for t in ('text/html','text/plain')):raise SourceError('Unsupported source type.')
                raw=response.read(self.max_bytes+1)
                if len(raw)>self.max_bytes:raise SourceError('Source exceeds the read limit.')
                return {'url':url,'content':text_only(raw.decode('utf-8','replace'))[:16000],'kind':'fetched','retrieved_at':time.time()}
            finally:conn.close()
        raise SourceError('Source redirect limit exceeded.')


def sanitize_query(query,state):
    query=re.sub(r'\S*@\S+|\b\d{4}-\d{2}-\d{2}\b|\+?\d[\d ()-]{7,}\d','',query)
    for member in state['members'].values():
        for token in [member['name'],*member.get('aliases',[])]:
            if len(token)>1:query=re.sub(r'\b'+re.escape(token)+r'\b','',query,flags=re.I)
    query=re.sub(r'\S+@\S+|\b\d{4}-\d{2}-\d{2}\b|\+?\d[\d ()-]{7,}\d','',query)
    # Do not send arbitrary full chat or instructions merely because they were proposed as a query.
    query=' '.join(query.split())[:300]
    if not query or re.search(r'\b(?:api.key|password|token|send records|email records)\b',query,re.I):raise SourceError('A minimal non-personal research query is required.')
    return query


def source_record(row, sid, now):
    safe_url(row['url'])
    content=str(row.get('content',''))[:8000]
    content=text_only(content) if '<' in content and '>' in content else content
    kind=row.get('kind','adapter_passage')
    return {'id':sid,'url':row['url'],'title':text_only(row.get('title',row['url']))[:300],
            'content':content,'kind':kind,'read':bool(content.strip()) and kind!='snippet',
            'sha256':hashlib.sha256(content.encode()).hexdigest(),'source_key':hashlib.sha256((row['url']+'\n'+content).encode()).hexdigest(),'retrieved_at':row.get('retrieved_at',now),
            'reviewed_at':row.get('reviewed_at'),'population':row.get('population','unknown'),
            'jurisdiction':row.get('jurisdiction','UK' if '.nhs.uk' in row['url'] or 'nhs.uk/' in row['url'] else 'unknown')}


def grounded_citations(final,sources,message):
    """Strict adapters display exact short source passages, not unverified model paraphrases."""
    by_id={s['id']:s for s in sources};ids=final['source_ids']
    if any(i not in by_id for i in ids):return 'I could not substantiate the proposed citation; no supporting source is confirmed.',[]
    strict=any(s.get('kind') in ('fetched','snippet') for s in sources)
    if not strict:
        chosen=[by_id[i] for i in ids if by_id[i]['read']]
        if not chosen:return final['message'],[]
        passages=[]
        for source in chosen:
            words=list(re.finditer(r'\S+',source['content']))
            quote=source['content'][:words[min(24,len(words)-1)].end()]
            if len(quote)>1000:return 'The available passage is too large to quote safely; no supporting citation is shown.',[]
            passages.append('“'+quote+'” — ['+source['title']+']('+source['url']+')')
        return 'Provided source passage:\n'+'\n'.join(passages),chosen
    approved=[];passages=[]
    for citation in final.get('citations',[]):
        if not isinstance(citation,dict) or set(citation)!={'source_id','quote'}:continue
        s=by_id.get(citation['source_id']);quote=citation['quote']
        if not s or not s['read'] or not isinstance(quote,str) or not quote.strip() or len(quote.split())>25 or quote not in s['content']:continue
        if re.search(r'\bchild|pediatric|baby\b',message,re.I) and s['population']=='adult':continue
        s['supporting_quote']=quote
        approved.append(s);passages.append('“'+quote+'” — ['+s['title']+']('+s['url']+')')
    if ids and not approved:return 'I retrieved results but could not verify supporting passages for that answer. Snippets or mismatched sources are not enough.',[]
    if approved:return 'The retrieved source states:\n'+'\n'.join(passages)+'\nThis is source information, not a diagnosis. Review dates are unknown unless explicitly supplied.',approved
    return final['message'],[]
