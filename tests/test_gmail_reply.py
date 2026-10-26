import base64,copy,hashlib,hmac,json,unittest
from gmail_transport import owner_confirmed_reply,GmailError
from human_review import HMACReplyAdapter,ReviewError
class ReplyTests(unittest.TestCase):
 def setUp(self):
  self.receipt=dict(thread_id='t1',message_id='original',provider_rfc_message_id='<original@example.test>',subject='Synthetic test')
  h={'From':'sipanijai@gmail.com','To':'sipanijai@gmail.com','Subject':'Re: Synthetic test','In-Reply-To':'<original@example.test>'}
  self.thread={'id':'t1','messages':[{'id':'reply','threadId':'t1','labelIds':['SENT'],'internalDate':'1000000','payload':{'headers':[dict(name=k,value=v) for k,v in h.items()],'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(b'Received - test reply').decode()}}}]}
 def parse(self,**kw):return owner_confirmed_reply(self.thread,self.receipt,mailbox='sipanijai@gmail.com',owner_confirmed=True,**kw)
 def test_owner_attestation_required(self):
  with self.assertRaises(GmailError):owner_confirmed_reply(self.thread,self.receipt,mailbox='sipanijai@gmail.com')
 def test_bound_plaintext_preserved(self):self.assertEqual(self.parse()['text'],'Received - test reply')
 def test_untrusted_binding_and_ambiguous_replies_rejected(self):
  for field,value in [('threadId','other'),('labelIds',['INBOX'])]:
   with self.subTest(field=field):
    original=copy.deepcopy(self.thread);self.thread['messages'][0][field]=value
    with self.assertRaises(GmailError):self.parse()
    self.thread=original
  self.thread['messages']*=2
  with self.assertRaises(GmailError):self.parse()
 def test_old_authored_time_fresh_ingress_and_plain_email_quote(self):
  secret=b'a'*32;adapter=HMACReplyAdapter(secret,clock=lambda:2000)
  e=dict(event_id='e',case_id='c',household='h',member_id='m',episode_id='p',challenge='x',principal='a@example.test',recipient='a@example.test',**{'from':'a@example.test'},reply_to='a@example.test',forwarded=False,sequence=1,at=1000,issued_at=2000,text='Received\n<a@example.test> wrote:')
  def verify():
   body=json.dumps(e).encode();return adapter.verify(body,hmac.new(secret,body,hashlib.sha256).hexdigest())
  self.assertEqual(verify()['at'],1000);self.assertEqual(verify()['text'],e['text'])
  e['issued_at']=1000
  with self.assertRaises(ReviewError):verify()
  e.update(issued_at=2000,text='<script>alert(1)</script>')
  with self.assertRaises(ReviewError):verify()
