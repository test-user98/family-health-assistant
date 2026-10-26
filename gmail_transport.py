"""Optional neutral synthetic Gmail review transport. No ambient mail/credential access.

Only the explicitly approved mailbox may send to itself. No message listing,
mailbox-wide polling, delete/modify calls, automatic retry or doctor identity claim.
"""
import base64,email.utils,json,os,re,stat,time,urllib.parse,urllib.request
from email.message import EmailMessage
from pathlib import Path
from gmail_oauth import SCOPES,TOKEN,PROFILE,NoRedirect,OAuthError,google_json,load_client_secret
BASE='https://gmail.googleapis.com/gmail/v1/users/me/'
class GmailError(ValueError):pass
class NeutralGmail:
 def __init__(self,directory,*,mailbox='sipanijai@gmail.com',auth_request=google_json,opener=None,synthetic=False,receipt_sink=None):
  if synthetic is not True:raise GmailError('Synthetic neutral transport must be explicitly enabled.')
  self.directory=Path(directory);self.mailbox=mailbox.casefold();self.auth_request=auth_request;self.opener=opener;self.access_token=None;self.receipt_sink=receipt_sink
 def authenticate(self):
  path=self.directory/'gmail_token.json'
  if path.is_symlink() or self.directory.is_symlink():raise GmailError('Private credentials required.')
  info=path.stat();parent=self.directory.stat()
  if info.st_uid!=os.getuid() or parent.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or stat.S_IMODE(parent.st_mode)!=0o700 or info.st_size>65536:raise GmailError('Owner-only credentials required.')
  record=json.loads(path.read_text())
  if record.get('email')!=self.mailbox or set(record.get('scopes',[]))!=SCOPES or record.get('token_uri')!=TOKEN:raise GmailError('Approved mailbox/scopes do not match.')
  refresh=record.get('refresh_token')
  if not isinstance(refresh,str) or not 1<=len(refresh)<=16384:raise GmailError('Invalid refresh credential.')
  secret=load_client_secret(self.directory/'gmail_client.json',record['client_id'])
  fields={'client_id':record['client_id'],'client_secret':secret,'refresh_token':refresh,'grant_type':'refresh_token'}
  try:token=self.auth_request(TOKEN,fields=fields)
  finally:fields.clear();secret=None;refresh=None;record=None
  if not isinstance(token.get('access_token'),str) or not 1<=len(token['access_token'])<=16384 or str(token.get('token_type','')).casefold()!='bearer':raise GmailError('Access credential unverified.')
  if token.get('scope') is not None and set(token['scope'].split())!=SCOPES:raise GmailError('Refreshed scopes do not match.')
  access=token['access_token'];profile=self.auth_request(PROFILE,access_token=access)
  if str(profile.get('emailAddress','')).casefold()!=self.mailbox:raise GmailError('Authenticated mailbox does not match.')
  self.access_token=access;return {'mailbox':self.mailbox,'scope_source':'initial verified grant plus refresh scope check when supplied','profile_verified':True}
 def _api(self,path,payload=None):
  if not self.access_token:raise GmailError('Authenticate before delivery.')
  known_thread=getattr(self,'receipt',{}).get('thread_id')
  thread_path='threads/'+str(known_thread)+'?format=full' if isinstance(known_thread,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',known_thread) else None
  if path!='messages/send' and path!=thread_path and not re.fullmatch(r'messages/[A-Za-z0-9_-]{1,128}\?format=metadata&metadataHeaders=Message-ID&metadataHeaders=Subject',path):raise GmailError('Unapproved Gmail API operation.')
  if (path=='messages/send')!=(payload is not None):raise GmailError('Unapproved Gmail API method.')
  data=json.dumps(payload).encode() if payload is not None else None
  headers={'Authorization':'Bearer '+self.access_token,'Accept':'application/json'}
  if data:headers['Content-Type']='application/json'
  request=urllib.request.Request(BASE+path,data,headers)
  try:
   with (self.opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())).open(request,timeout=20) as response:raw=response.read(1000001)
   if len(raw)>1000000:raise GmailError('Oversized Gmail response.')
   body=json.loads(raw)
   if not isinstance(body,dict):raise GmailError('Invalid Gmail response.')
   return body
  except Exception:raise GmailError('Gmail outcome unconfirmed; do not automatically retry a send.') from None
 def __call__(self,notification):
  if notification.get('recipient','').casefold()!=self.mailbox:raise GmailError('Only the explicitly approved test recipient is allowed.')
  case=notification.get('case_id','')
  if not re.fullmatch(r'[a-f0-9]{32}',case):raise GmailError('Bound synthetic case required.')
  message=EmailMessage();message['From']=self.mailbox;message['To']=self.mailbox;message['Subject']='Family Health neutral review test '+case[:8]
  message['Message-ID']=email.utils.make_msgid(domain='family-health.local');message['Date']=email.utils.formatdate(localtime=False)
  message.set_content('This is a neutral synthetic integration test. No patient details are included.\n\nPlease reply with: Received - test reply\n\nTest reference: '+case+'\nThis is a test human review, not verified doctor advice.')
  raw=base64.urlsafe_b64encode(message.as_bytes()).decode();result=self._api('messages/send',{'raw':raw})
  if not all(isinstance(result.get(k),str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',result[k]) for k in ('id','threadId')):raise GmailError('Send acceptance lacks correlation evidence; do not retry.')
  self.receipt={'message_id':result['id'],'thread_id':result['threadId'],'rfc_message_id':str(message['Message-ID']),'subject':str(message['Subject']),'accepted_at':time.time(),'mailbox':self.mailbox,'stage':'provider_accepted_inbox_unconfirmed'}
  if self.receipt_sink:self.receipt_sink(dict(self.receipt))
  return {'status':'accepted','recipient':self.mailbox}
 def observe_sent_message(self):
  receipt=self.receipt;body=self._api('messages/'+receipt['message_id']+'?format=metadata&metadataHeaders=Message-ID&metadataHeaders=Subject')
  headers={x.get('name','').casefold():x.get('value') for x in body.get('payload',{}).get('headers',[]) if isinstance(x,dict)}
  if body.get('id')!=receipt['message_id'] or body.get('threadId')!=receipt['thread_id'] or headers.get('subject')!=receipt['subject']:raise GmailError('Message observation correlation failed.')
  canonical=headers.get('message-id')
  if not isinstance(canonical,str) or len(canonical)>998 or not re.fullmatch(r'<[^<>\s]+@[^<>\s]+>',canonical):raise GmailError('Provider canonical Message-ID is unavailable.')
  labels=body.get('labelIds',[])
  if not isinstance(labels,list) or not all(isinstance(x,str) for x in labels):raise GmailError('Invalid mailbox observation.')
  return dict(receipt,provider_rfc_message_id=canonical,submitted_rfc_id_matches=(canonical==receipt['rfc_message_id']),correlation='provider returned id + thread + expected subject',label_ids=labels,stage='observed_in_inbox' if 'INBOX' in labels else 'observed_in_mailbox_inbox_unconfirmed',human_read=False,observed_at=time.time())
 def read_known_thread(self):
  if not getattr(self,'receipt',None):raise GmailError('Known receipt required before thread read.')
  body=self._api('threads/'+self.receipt['thread_id']+'?format=full')
  if body.get('id')!=self.receipt['thread_id'] or not isinstance(body.get('messages'),list) or len(body['messages'])>20:raise GmailError('Thread identity/size mismatch.')
  return body
 def clear(self):self.access_token=None

def owner_confirmed_reply(thread,receipt,*,mailbox,owner_confirmed=False):
 """Narrow self-mail test ingress. Requires explicit owner attestation, not From alone.

 No general remote-sender authentication is claimed. Preserve plaintext verbatim.
 """
 if owner_confirmed is not True:raise GmailError('Owner confirmation required; quarantine reply.')
 return bound_self_reply(thread,receipt,mailbox=mailbox)

def bound_self_reply(thread,receipt,*,mailbox):
 """Validate provider/case binding only; this does not attest human authorship."""
 if thread.get('id')!=receipt['thread_id']:raise GmailError('Wrong thread.')
 candidates=[]
 for message in thread.get('messages',[]):
  if message.get('id')==receipt['message_id']:continue
  headers={}
  for item in message.get('payload',{}).get('headers',[]):
   name=item.get('name','').casefold()
   if name in headers:raise GmailError('Ambiguous duplicate headers.')
   headers[name]=item.get('value','')
  address=lambda key:email.utils.parseaddr(headers.get(key,''))[1].casefold()
  if (message.get('threadId')!=receipt['thread_id'] or 'SENT' not in message.get('labelIds',[]) or
      address('from')!=mailbox or address('to')!=mailbox or
      (headers.get('reply-to') and address('reply-to')!=mailbox) or
      any(k.startswith('resent-') for k in headers) or
      headers.get('in-reply-to','').strip()!=receipt['provider_rfc_message_id'] or
      headers.get('subject','').casefold()!='re: '+receipt['subject'].casefold()):raise GmailError('Reply binding/owner evidence mismatch.')
  def plain(part,depth=0):
   if depth>5:raise GmailError('Oversized MIME tree.')
   if part.get('mimeType')=='text/plain':
    data=part.get('body',{}).get('data','')
    if len(data)>12000:raise GmailError('Oversized reply.')
    return [base64.b64decode(data+'='*((-len(data))%4),altchars=b'-_',validate=True).decode('utf-8')]
   parts=part.get('parts',[])
   if len(parts)>20:raise GmailError('Oversized MIME tree.')
   return [text for p in parts for text in plain(p,depth+1)]
  texts=plain(message.get('payload',{}))
  if len(texts)!=1 or not texts[0] or len(texts[0])>8000:raise GmailError('Single bounded plain-text reply required.')
  candidates.append(dict(event_id='gmail:'+message['id'],text=texts[0],at=int(message['internalDate'])/1000,evidence='Authenticated own-mailbox SENT record + canonical reply binding; human authorship requires explicit confirmation'))
 if len(candidates)!=1:raise GmailError('Exactly one owner-confirmed reply required.')
 return candidates[0]
