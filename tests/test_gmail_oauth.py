import tempfile,unittest,json,stat
from pathlib import Path
from urllib.parse import urlencode,urlsplit,parse_qs
from gmail_oauth import DesktopPKCE,OAuthError,SCOPES,TOKEN,PROFILE,save_refresh,google_json
class GmailOAuthTests(unittest.TestCase):
 def flow(self):return DesktopPKCE('431376045604-test.apps.googleusercontent.com','http://127.0.0.1:12345/oauth/callback','approved@example.com')
 def reply(self,url,**kw):
  if url==TOKEN:return dict(token_type='Bearer',scope=' '.join(SCOPES),access_token='SYNTHETIC_ACCESS',refresh_token='SYNTHETIC_REFRESH',expires_in=3600)
  return {'emailAddress':'approved@example.com'}
 def test_pkce_s256_and_exact_scopes(self):
  f=self.flow();q=parse_qs(urlsplit(f.authorization_url()).query);self.assertEqual(q['code_challenge_method'],['S256']);self.assertEqual(set(q['scope'][0].split()),SCOPES);self.assertNotIn('client_secret',q)
 def test_bad_state_does_not_exchange(self):
  f=self.flow()
  with self.assertRaisesRegex(OAuthError,'state_mismatch'):f.exchange('state=wrong&code=synthetic',lambda *a,**k:self.fail('No network'))
  self.assertFalse(f.used)
 def test_exchange_omits_secret_verifies_profile_and_one_shot(self):
  f=self.flow();calls=[]
  def request(url,**kw):calls.append((url,kw));return self.reply(url,**kw)
  r=f.exchange(urlencode({'state':f.state,'code':'SYNTHETIC_CODE'}),request);self.assertEqual(r['email'],'approved@example.com');self.assertNotIn('client_secret',calls[0][1]['fields']);self.assertEqual(f.verifier,'')
  with self.assertRaises(OAuthError):f.exchange('')
 def test_wrong_mailbox_and_extra_scopes_refused(self):
  for mode in ('mailbox','scope'):
   f=self.flow()
   def request(url,**kw):
    r=self.reply(url,**kw)
    if mode=='mailbox' and url==PROFILE:r['emailAddress']='other@example.com'
    if mode=='scope' and url==TOKEN:r['scope']+=' unexpected'
    return r
   with self.assertRaises(OAuthError):f.exchange(urlencode({'state':f.state,'code':'SYNTHETIC_CODE'}),request)
 def test_missing_refresh_refused(self):
  f=self.flow()
  def request(url,**kw):r=self.reply(url,**kw);r.pop('refresh_token',None);return r
  with self.assertRaisesRegex(OAuthError,'offline_tokens_missing'):f.exchange(urlencode({'state':f.state,'code':'SYNTHETIC_CODE'}),request)
 def test_private_store_no_overwrite_or_symlink(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'private'/'token.json';save_refresh(p,{'refresh_token':'SYNTHETIC_REFRESH'});self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600);self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode),0o700)
   with self.assertRaises(OAuthError):save_refresh(p,{})
   link=Path(d)/'link';link.symlink_to(p)
   with self.assertRaises(OAuthError):save_refresh(link,{})
 def test_endpoint_refused_before_transport(self):
  with self.assertRaises(OAuthError):google_json('https://example.com/token',fields={})
 def test_nonloopback_redirect_refused(self):
  with self.assertRaises(OAuthError):DesktopPKCE('431376045604-test.apps.googleusercontent.com','https://example.com/oauth/callback','approved@example.com')

 def test_idle_preconnection_does_not_block_landing_page(self):
  import socket,threading,urllib.request
  from http.server import BaseHTTPRequestHandler
  from gmail_oauth import LoopbackServer
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    body=b'Consent landing page';self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  server=LoopbackServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();idle=socket.create_connection(server.server_address,timeout=2)
  try:
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:'+str(server.server_port),timeout=2) as response:self.assertEqual(response.read(),b'Consent landing page')
  finally:idle.close();server.shutdown();server.server_close();thread.join(timeout=3)

 def test_concurrent_callback_exchanges_only_one_code(self):
  from concurrent.futures import ThreadPoolExecutor
  import threading
  f=self.flow();query=urlencode({'state':f.state,'code':'SYNTHETIC_CODE'});calls=[];gate=threading.Event()
  def request(url,**kw):
   calls.append(url)
   if url==TOKEN:gate.wait(1)
   return self.reply(url,**kw)
  def attempt():
   try:return f.exchange(query,request)['email']
   except OAuthError:return 'refused'
  with ThreadPoolExecutor(max_workers=2) as pool:
   a=pool.submit(attempt);b=pool.submit(attempt);gate.set();results=[a.result(),b.result()]
  self.assertEqual(sorted(results),['approved@example.com','refused']);self.assertEqual(calls.count(TOKEN),1)

 def test_explicit_secret_sent_only_to_token_endpoint_and_cleared(self):
  f=DesktopPKCE('431376045604-test.apps.googleusercontent.com','http://127.0.0.1:12345/oauth/callback','approved@example.com',client_secret='SYNTHETIC_CLIENT_SECRET');calls=[]
  def request(url,**kw):calls.append((url,kw));return self.reply(url,**kw)
  record=f.exchange(urlencode({'state':f.state,'code':'SYNTHETIC_CODE'}),request)
  self.assertEqual(calls[0][1]['fields']['client_secret'],'SYNTHETIC_CLIENT_SECRET');self.assertNotIn('client_secret',calls[1][1]);self.assertIsNone(f.client_secret);self.assertNotIn('SYNTHETIC_CLIENT_SECRET',json.dumps(record))

 def test_client_file_is_scoped_private_and_rejects_untrusted_endpoints(self):
  from gmail_oauth import load_client_secret,AUTH
  import os
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'client.json';body={'installed':{'client_id':'431376045604-test.apps.googleusercontent.com','client_secret':'SYNTHETIC_CLIENT_SECRET','auth_uri':AUTH,'token_uri':TOKEN}};p.write_text(json.dumps(body));p.chmod(0o600)
   self.assertEqual(load_client_secret(p,body['installed']['client_id']),'SYNTHETIC_CLIENT_SECRET')
   with self.assertRaises(OAuthError):load_client_secret(p,'different-client')
   p.chmod(0o644)
   with self.assertRaises(OAuthError):load_client_secret(p,body['installed']['client_id'])
   p.chmod(0o600);body['installed']['token_uri']='https://example.com/token';p.write_text(json.dumps(body))
   with self.assertRaises(OAuthError):load_client_secret(p,body['installed']['client_id'])
