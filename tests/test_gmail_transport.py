import base64,io,json,tempfile,unittest
from pathlib import Path
from email import message_from_bytes
from gmail_oauth import SCOPES,TOKEN,AUTH,PROFILE
from gmail_transport import NeutralGmail,GmailError
class GmailTransportTests(unittest.TestCase):
 def setup_client(self,d,opener=None):
  p=Path(d);p.chmod(0o700);cid='431376045604-test.apps.googleusercontent.com'
  for name,body in [('gmail_token.json',{'client_id':cid,'email':'sipanijai@gmail.com','refresh_token':'SYNTHETIC_REFRESH','scopes':sorted(SCOPES),'token_uri':TOKEN}),('gmail_client.json',{'installed':{'client_id':cid,'client_secret':'SYNTHETIC_SECRET','token_uri':TOKEN,'auth_uri':AUTH}})]:f=p/name;f.write_text(json.dumps(body));f.chmod(0o600)
  def auth(url,**kw):
   if url==TOKEN:return {'access_token':'SYNTHETIC_ACCESS','token_type':'Bearer','scope':' '.join(SCOPES)}
   return {'emailAddress':'sipanijai@gmail.com'}
  return NeutralGmail(p,auth_request=auth,opener=opener,synthetic=True)
 def test_send_is_neutral_self_only_and_receipt_not_inbox_proof(self):
  class Transport:
   def open(self,req,timeout):
    self.request=req;return io.BytesIO(json.dumps({'id':'message1','threadId':'thread1'}).encode())
  with tempfile.TemporaryDirectory() as d:
   t=Transport();c=self.setup_client(d,t);c.authenticate();self.assertEqual(c({'recipient':'sipanijai@gmail.com','case_id':'a'*32,'text':'PRIVATE_HEALTH_CANARY'}),{'status':'accepted','recipient':'sipanijai@gmail.com'})
   raw=base64.urlsafe_b64decode(json.loads(t.request.data)['raw']);self.assertNotIn(b'PRIVATE_HEALTH_CANARY',raw);m=message_from_bytes(raw);self.assertEqual(m['To'],'sipanijai@gmail.com');self.assertIsNone(m['Cc']);self.assertEqual(c.receipt['stage'],'provider_accepted_inbox_unconfirmed');c.clear();self.assertIsNone(c.access_token)
 def test_other_recipient_and_unapproved_endpoint_refused(self):
  with tempfile.TemporaryDirectory() as d:
   c=self.setup_client(d)
   with self.assertRaises(GmailError):c({'recipient':'someone@example.com','case_id':'a'*32})
   with self.assertRaises(GmailError):c._api('messages')
 def test_wrong_profile_refused(self):
  with tempfile.TemporaryDirectory() as d:
   c=self.setup_client(d);original=c.auth_request
   c.auth_request=lambda url,**kw: {'emailAddress':'other@example.com'} if url==PROFILE else original(url,**kw)
   with self.assertRaises(GmailError):c.authenticate()
 def test_scopes_cannot_broaden(self):
  with tempfile.TemporaryDirectory() as d:
   c=self.setup_client(d);c.auth_request=lambda url,**kw:{'access_token':'SYNTHETIC_ACCESS','token_type':'Bearer','scope':' '.join(SCOPES)+' unexpected'}
   with self.assertRaises(GmailError):c.authenticate()

 def test_provider_canonical_rfc_id_change_is_not_false_delivery_failure(self):
  class Transport:
   def open(self,req,timeout):return io.BytesIO(json.dumps({'id':'message1','threadId':'thread1','labelIds':['INBOX','SENT'],'payload':{'headers':[{'name':'Message-ID','value':'<provider-canonical@example.test>'},{'name':'Subject','value':'Expected synthetic subject'}]}}).encode())
  with tempfile.TemporaryDirectory() as d:
   c=self.setup_client(d,Transport());c.authenticate();c.receipt={'message_id':'message1','thread_id':'thread1','rfc_message_id':'<submitted@example.test>','subject':'Expected synthetic subject'}
   r=c.observe_sent_message();self.assertEqual(r['stage'],'observed_in_inbox');self.assertFalse(r['submitted_rfc_id_matches']);self.assertEqual(r['provider_rfc_message_id'],'<provider-canonical@example.test>');self.assertFalse(r['human_read'])

 def test_wrong_subject_or_message_still_refused(self):
  class Transport:
   def open(self,req,timeout):return io.BytesIO(json.dumps({'id':'other','threadId':'thread1','labelIds':['INBOX'],'payload':{'headers':[{'name':'Message-ID','value':'<canonical@example.test>'},{'name':'Subject','value':'Wrong subject'}]}}).encode())
  with tempfile.TemporaryDirectory() as d:
   c=self.setup_client(d,Transport());c.authenticate();c.receipt={'message_id':'message1','thread_id':'thread1','rfc_message_id':'<submitted@example.test>','subject':'Expected synthetic subject'}
   with self.assertRaises(GmailError):c.observe_sent_message()
