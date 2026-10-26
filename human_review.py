"""Optional human-review outbox and authenticated replies; no ambient mail access.

A trusted ingress must attest the authenticated reviewer account, not just From/DKIM.
HMACReplyAdapter is a server-to-server hook and local test transport, not an email login.
"""
import hashlib
import hmac
import json
import math
import re
import secrets
import time


class ReviewError(ValueError):pass


class HMACReplyAdapter:
    def __init__(self,secret,clock=time.time):
        if not isinstance(secret,bytes) or len(secret)<32:raise ReviewError('A separately authorized ingress secret is required.')
        self.secret,self.clock=secret,clock

    def verify(self,body,signature):
        if not isinstance(body,bytes) or len(body)>16000 or not isinstance(signature,str):raise ReviewError('Invalid ingress.')
        expected=hmac.new(self.secret,body,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):raise ReviewError('Unauthenticated ingress.')
        try:event=json.loads(body)
        except (ValueError,TypeError):raise ReviewError('Invalid ingress.') from None
        required={'event_id','case_id','household','member_id','episode_id','challenge','principal','recipient','from','reply_to','forwarded','sequence','at','text'}
        if not isinstance(event,dict) or not required<=set(event)<=required|{'issued_at'}:raise ReviewError('Invalid reply fields.')
        if type(event['at']) not in (int,float) or not math.isfinite(event['at']) or event['at']>self.clock()+300:raise ReviewError('Invalid authored time.')
        issued=event.get('issued_at',event['at'])
        if type(issued) not in (int,float) or not abs(self.clock()-issued)<=300:raise ReviewError('Expired ingress.')
        for key in required-{'forwarded','sequence','at'}:
            if not isinstance(event[key],str) or not event[key] or len(event[key])>(8000 if key=='text' else 256):raise ReviewError('Invalid reply fields.')
        if type(event['sequence']) is not int or event['sequence']<1 or event['forwarded'] is not False:raise ReviewError('Forwarded or invalid reply.')
        # Plain text only: preserve human wording, never execute HTML or tool instructions.
        validation_text=re.sub(r"<[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+>", '', event['text'])
        if re.search(r'<\s*/?\s*[A-Za-z!]|[\x00-\x08\x0b\x0c\x0e-\x1f]',validation_text):raise ReviewError('Plain text reply required.')
        if event['from'].casefold()!=event['principal'].casefold() or event['reply_to'].casefold()!=event['principal'].casefold():raise ReviewError('Identity headers conflict.')
        return event


class HumanReview:
    def __init__(self,db,*,reviewer='sipanijai@gmail.com',inbox='review@local.invalid',clock=time.time):
        self.db,self.reviewer,self.inbox,self.clock=db,reviewer.casefold(),inbox.casefold(),clock
        db.executescript('''
        CREATE TABLE IF NOT EXISTS human_cases(
          id TEXT PRIMARY KEY,household TEXT NOT NULL,member_id TEXT NOT NULL,episode_id TEXT NOT NULL,
          reviewer TEXT NOT NULL,inbox TEXT NOT NULL,challenge TEXT NOT NULL,status TEXT NOT NULL,
          created REAL NOT NULL,notified REAL,revoked INTEGER NOT NULL DEFAULT 0,
          UNIQUE(household,episode_id));
        CREATE TABLE IF NOT EXISTS human_replies(
          event_id TEXT PRIMARY KEY,case_id TEXT NOT NULL,text TEXT NOT NULL,at REAL NOT NULL,
          received REAL NOT NULL,sequence INTEGER NOT NULL,UNIQUE(case_id,sequence));
        ''');db.commit()

    def create(self,household,member_id,episode_id):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT state FROM households WHERE id=?',(household,)).fetchone()
            state=json.loads(row[0]) if row else {};episode=state.get('episodes',{}).get(episode_id,{})
            if episode.get('member_id')!=member_id or episode.get('status')!='doctor':raise ReviewError('A bound non-emergency review episode is required.')
            self.db.execute('INSERT OR IGNORE INTO human_cases(id,household,member_id,episode_id,reviewer,inbox,challenge,status,created) VALUES (?,?,?,?,?,?,?,"queued",?)',
                            (secrets.token_hex(16),household,member_id,episode_id,self.reviewer,self.inbox,secrets.token_urlsafe(32),self.clock()))
            row=self.db.execute('SELECT id,status FROM human_cases WHERE household=? AND episode_id=?',(household,episode_id)).fetchone()
        return dict(id=row[0],status=row[1],label='Test human reviewer; not a verified doctor',delivery='No email delivery is confirmed.')

    def notification(self,case_id):
        row=self.db.execute('SELECT reviewer,challenge,status,revoked FROM human_cases WHERE id=?',(case_id,)).fetchone()
        if not row or row[3] or row[2]!='queued':raise ReviewError('Case is not queued.')
        # Trusted transport receives an opaque reference; never include the household,
        # member, episode, symptoms or a bearer link that grants record access.
        return dict(recipient=row[0],subject='A test review is available',text='An optional synthetic review is available. Use the separately authenticated review service.',case_id=case_id)

    def dispatch(self,case_id,transport):
        # Claim before calling transport; uncertain/crashed submission is never blindly retried.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            message=self.notification(case_id)
            bound=self.db.execute('SELECT household,member_id,episode_id FROM human_cases WHERE id=?',(case_id,)).fetchone()
            state_row=self.db.execute('SELECT state FROM households WHERE id=?',(bound[0],)).fetchone()
            state=json.loads(state_row[0]) if state_row else {}
            episode=state.get('episodes',{}).get(bound[2],{})
            if (episode.get('member_id')!=bound[1] or episode.get('status')!='doctor'
                or state.get('members',{}).get(bound[1],{}).get('safety_hold') or state.get('unassigned_emergency')):
                raise ReviewError('Case is stale or urgent; notification not submitted.')
            self.db.execute('UPDATE human_cases SET status="notification_pending" WHERE id=?',(case_id,))
        try:
            receipt=transport(message)
            if receipt!={'status':'accepted','recipient':message['recipient']}:raise ReviewError('Transport acceptance unconfirmed.')
        except Exception:
            with self.db:self.db.execute('UPDATE human_cases SET status="notification_unknown" WHERE id=? AND status="notification_pending"',(case_id,))
            raise ReviewError('Notification outcome unknown; reconcile before retry.') from None
        with self.db:self.db.execute("UPDATE human_cases SET status='notified',notified=? WHERE id=? AND status='notification_pending' AND revoked=0",(self.clock(),case_id))
        # notified means transport acceptance only, never inbox receipt or review.

    def revoke(self,household,case_id):
        with self.db:self.db.execute("UPDATE human_cases SET revoked=1,status='revoked' WHERE household=? AND id=?",(household,case_id))

    def accept(self,body,signature,adapter):
        event=adapter.verify(body,signature)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT household,member_id,episode_id,reviewer,inbox,challenge,status,revoked,created FROM human_cases WHERE id=?',(event['case_id'],)).fetchone()
            if not row or row[:3]!=(event['household'],event['member_id'],event['episode_id']):raise ReviewError('Reply is not authorized for this case.')
            if (row[3]!=event['principal'].casefold() or row[4]!=event['recipient'].casefold() or row[7]
                or row[6] not in ('notified','answered') or not hmac.compare_digest(row[5],event['challenge']) or event['at']<row[8]):raise ReviewError('Reply is not authorized for this case.')
            state=json.loads(self.db.execute('SELECT state FROM households WHERE id=?',(row[0],)).fetchone()[0])
            episode=state['episodes'].get(row[2],{});member=state['members'].get(row[1],{})
            if (episode.get('status')!='doctor' or episode.get('member_id')!=row[1] or member.get('safety_hold') or state.get('unassigned_emergency')):raise ReviewError('Case is stale or urgent; reply not accepted.')
            old=self.db.execute('SELECT case_id,text,sequence,at FROM human_replies WHERE event_id=?',(event['event_id'],)).fetchone()
            if old:
                if old!=(event['case_id'],event['text'],event['sequence'],event['at']):raise ReviewError('Conflicting replay.')
                return 'duplicate'
            latest=self.db.execute('SELECT coalesce(max(sequence),0),coalesce(max(at),0) FROM human_replies WHERE case_id=?',(event['case_id'],)).fetchone()
            if event['sequence']!=latest[0]+1 or event['at']<latest[1]:raise ReviewError('Reply sequence is stale or has a gap.')
            self.db.execute('INSERT INTO human_replies VALUES (?,?,?,?,?,?)',(event['event_id'],event['case_id'],event['text'],event['at'],self.clock(),event['sequence']))
            self.db.execute('UPDATE human_cases SET status="answered" WHERE id=?',(event['case_id'],))
        return 'accepted'

    def view(self,household,allowed=None):
        result=[]
        for row in self.db.execute('SELECT id,member_id,episode_id,status,created,notified,reviewer FROM human_cases WHERE household=? ORDER BY created,id',(household,)):
            if allowed is not None and row[1] not in allowed:continue
            replies=[dict(event_id=r[0],text=r[1],authored_at=r[2],received_at=r[3],sequence=r[4]) for r in self.db.execute('SELECT event_id,text,at,received,sequence FROM human_replies WHERE case_id=? ORDER BY sequence',(row[0],))]
            result.append(dict(id=row[0],member_id=row[1],episode_id=row[2],status=row[3],created_at=row[4],notified_at=row[5],reviewer=row[6],label='Test human reviewer; not a verified doctor',replies=replies))
        return result
