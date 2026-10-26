"""Optional Desktop PKCE OAuth, exact Google endpoints, no mail sending.

Public-client secret omission follows Google's Desktop OAuth documentation.
Actual client compatibility is established only by a successful exchange.
Authorization codes/verifier/tokens never enter logs or QA reports.
"""
import base64,hashlib,hmac,json,math,os,re,secrets,stat,threading,urllib.parse,urllib.request,urllib.error
from http.server import ThreadingHTTPServer

class LoopbackServer(ThreadingHTTPServer):
    daemon_threads=True
    def get_request(self):
        connection,address=super().get_request();connection.settimeout(5);return connection,address
from pathlib import Path
AUTH='https://accounts.google.com/o/oauth2/v2/auth'
TOKEN='https://oauth2.googleapis.com/token'
PROFILE='https://gmail.googleapis.com/gmail/v1/users/me/profile'
SCOPES=frozenset(('https://www.googleapis.com/auth/gmail.send','https://www.googleapis.com/auth/gmail.readonly'))
class OAuthError(ValueError):pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**k):raise OAuthError('redirect_refused')
def google_json(url,*,fields=None,access_token=None,opener=None):
    if url not in (TOKEN,PROFILE):raise OAuthError('endpoint_refused')
    if (url==TOKEN)!=(fields is not None):raise OAuthError('method_refused')
    headers={'Accept':'application/json'}
    data=None
    if fields is not None:data=urllib.parse.urlencode(fields).encode();headers['Content-Type']='application/x-www-form-urlencoded'
    if access_token:headers['Authorization']='Bearer '+access_token
    request=urllib.request.Request(url,data,headers)
    try:
        with (opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())).open(request,timeout=20) as response:raw=response.read(65537)
        if len(raw)>65536:raise OAuthError('response_too_large')
        result=json.loads(raw)
        if not isinstance(result,dict):raise OAuthError('invalid_response')
        return result
    except urllib.error.HTTPError as error:
        try:
            result=json.loads(error.read(65536));message=str(result.get('error_description','')).lower()
            if 'client_secret' in message and ('missing' in message or 'required' in message):raise OAuthError('client_secret_required_by_server') from None
            kind=result.get('error')
            if kind in ('invalid_client','invalid_grant','invalid_request','access_denied','unauthorized_client'):raise OAuthError(kind) from None
        except (ValueError,TypeError) as parsed:
            if isinstance(parsed,OAuthError):raise
        finally:error.close()
        raise OAuthError('google_http_error') from None
    except OAuthError:raise
    except Exception:raise OAuthError('network_or_response_outcome_unknown_no_retry') from None

class DesktopPKCE:
    def __init__(self,client_id,redirect_uri,email,client_secret=None):
        if not re.fullmatch(r'[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com',client_id):raise OAuthError('invalid_public_client_id')
        u=urllib.parse.urlsplit(redirect_uri)
        if u.scheme!='http' or u.hostname!='127.0.0.1' or not u.port or u.path!='/oauth/callback' or u.query or u.fragment or u.username:raise OAuthError('loopback_required')
        self.client_id,self.redirect_uri,self.email=client_id,redirect_uri,email.casefold()
        if client_secret is not None and (not isinstance(client_secret,str) or not 1<=len(client_secret)<=4096):raise OAuthError('invalid_client_secret')
        self.client_secret=client_secret
        self.verifier=secrets.token_urlsafe(64);self.state=secrets.token_urlsafe(32);self.used=False;self.lock=threading.Lock()
    def authorization_url(self):
        challenge=base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b'=').decode()
        return AUTH+'?'+urllib.parse.urlencode({'client_id':self.client_id,'redirect_uri':self.redirect_uri,'response_type':'code','scope':' '.join(sorted(SCOPES)),'state':self.state,'code_challenge':challenge,'code_challenge_method':'S256','access_type':'offline','prompt':'consent','login_hint':self.email})
    def exchange(self,query,request=google_json):
        with self.lock:return self._exchange(query,request)
    def _exchange(self,query,request):
        if self.used:raise OAuthError('authorization_already_consumed')
        values=urllib.parse.parse_qs(query,keep_blank_values=True,max_num_fields=10)
        if len(values.get('state',[]))!=1 or not hmac.compare_digest(values['state'][0],self.state):raise OAuthError('state_mismatch')
        if 'error' in values:self.used=True;raise OAuthError('user_denied_or_google_rejected')
        if len(values.get('code',[]))!=1 or not 1<=len(values['code'][0])<=8192:raise OAuthError('invalid_code')
        self.used=True
        try:
            fields={'client_id':self.client_id,'code':values['code'][0],'code_verifier':self.verifier,'redirect_uri':self.redirect_uri,'grant_type':'authorization_code'}
            if self.client_secret is not None:fields['client_secret']=self.client_secret
            token=request(TOKEN,fields=fields)
            if token.get('token_type','').casefold()!='bearer' or not isinstance(token.get('scope'),str) or frozenset(token['scope'].split())!=SCOPES:raise OAuthError('granted_scopes_mismatch')
            if any(not isinstance(token.get(k),str) or not token[k] or len(token[k])>16384 for k in ('access_token','refresh_token')):raise OAuthError('offline_tokens_missing')
            expiry=token.get('expires_in')
            if type(expiry) not in (int,float) or not math.isfinite(expiry) or not 0<expiry<=86400:raise OAuthError('invalid_expiry')
            profile=request(PROFILE,access_token=token['access_token'])
            if str(profile.get('emailAddress','')).casefold()!=self.email:raise OAuthError('wrong_mailbox')
            return {'client_id':self.client_id,'email':self.email,'refresh_token':token['refresh_token'],'scopes':sorted(SCOPES),'token_uri':TOKEN}
        finally:self.verifier='';self.state='';self.client_secret=None

def save_refresh(path,record):
    path=Path(path);parent=path.parent
    if parent.is_symlink() or path.is_symlink() or path.exists():raise OAuthError('credential_path_exists_or_symlink')
    parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=parent.stat()
    if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:raise OAuthError('private_directory_required')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        with os.fdopen(fd,'w') as stream:json.dump(record,stream);stream.flush();os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True);raise


def load_client_secret(path,expected_client_id):
    """Read only an explicitly supplied private installed-client configuration."""
    path=Path(path)
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():raise OAuthError('private_client_file_required')
    info=path.stat();parent=path.parent.stat()
    if info.st_uid!=os.getuid() or parent.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or stat.S_IMODE(parent.st_mode)!=0o700 or not 0<info.st_size<=65536:raise OAuthError('private_client_file_permissions_required')
    try:body=json.loads(path.read_text());client=body['installed']
    except (ValueError,KeyError,TypeError):raise OAuthError('invalid_installed_client_file') from None
    if not isinstance(client,dict) or client.get('client_id')!=expected_client_id:raise OAuthError('wrong_client_configuration')
    if client.get('token_uri') not in (TOKEN,'https://accounts.google.com/o/oauth2/token') or client.get('auth_uri') not in (AUTH,'https://accounts.google.com/o/oauth2/auth'):raise OAuthError('client_endpoint_mismatch')
    secret=client.get('client_secret')
    if not isinstance(secret,str) or not 1<=len(secret)<=4096:raise OAuthError('client_secret_missing')
    return secret
