"""Synthetic clock and SQLite delivery sink only; never real notifications."""
import json,tempfile,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from reminders import Reminders,ReminderError,utc_due,command
from runtime import Runtime
from health_agent import CONFIG_PATH

class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'synthetic.db';self.now=utc_due('2026-10-24','00:00','UTC');self.r=Reminders(self.path,lambda:self.now);self.addCleanup(self.r.close)
    def create(self):return self.r.create('qa','Fictional medicine reminder','2026-10-25','06:00','Asia/Kolkata')
    def test_six_am_timezone_date_and_persistence(self):
        x=self.create();self.assertEqual(x['due'],utc_due('2026-10-25','00:30','UTC'));self.r.close();self.r=Reminders(self.path,lambda:self.now);self.assertEqual(self.r.get('qa',x['id'])['local_time'],'06:00')
    def test_delivery_receipt_and_duplicate_suppression(self):
        x=self.create();self.assertEqual(self.r.tick(),0);self.now=x['due'];self.assertEqual(self.r.tick(),1);self.assertEqual(self.r.tick(),0);self.assertEqual(len(self.r.receipts('qa')),1);self.assertEqual(self.r.receipts('qa')[0]['text'],'Fictional medicine reminder')
    def test_delivery_catches_up_after_stopped_process(self):
        x=self.create();self.r.close();self.now=x['due']+3600;self.r=Reminders(self.path,lambda:self.now);self.assertEqual(self.r.tick(),1);self.assertGreater(self.r.receipts('qa')[0]['received'],x['due'])
    def test_edit_invalidates_old_due_time(self):
        x=self.create();y=self.r.edit('qa',x['id'],'2026-10-25','07:00','Asia/Kolkata');self.now=x['due'];self.assertEqual(self.r.tick(),0);self.now=y['due'];self.assertEqual(self.r.tick(),1);self.assertEqual(self.r.receipts('qa')[0]['version'],2)
    def test_cancel_never_delivers(self):
        x=self.create();self.r.cancel('qa',x['id']);self.now=x['due'];self.assertEqual(self.r.tick(),0);self.assertEqual(self.r.receipts('qa'),[])
    def test_duplicate_create_has_same_id(self):self.assertEqual(self.create()['id'],self.create()['id'])
    def test_wrong_household_cannot_read_edit_cancel(self):
        x=self.create()
        for action in [lambda:self.r.get('other',x['id']),lambda:self.r.edit('other',x['id'],'2026-10-25','07:00','UTC'),lambda:self.r.cancel('other',x['id'])]:
            with self.assertRaises(ReminderError):action()
        self.assertEqual(self.r.list('other'),[])
    def test_competing_workers_deliver_once(self):
        x=self.create();self.now=x['due']
        def worker():
            sink=Reminders(self.path,lambda:self.now)
            try:return sink.tick()
            finally:sink.close()
        with ThreadPoolExecutor(max_workers=2) as pool:counts=list(pool.map(lambda _:worker(),range(2)))
        self.assertEqual(sum(counts),1);self.assertEqual(len(self.r.receipts('qa')),1)
    def test_dst_gap_and_fold_are_rejected(self):
        for day,clock in [('2026-03-08','02:30'),('2026-11-01','01:30')]:
            with self.assertRaises(ReminderError):utc_due(day,clock,'America/New_York')
    def test_invalid_zone_date_or_past_time_is_rejected(self):
        for day,clock,zone in [('2026-02-31','06:00','UTC'),('2026-10-25','25:00','UTC'),('2026-10-25','06:00','Fake/Zone'),('2020-01-01','06:00','UTC')]:
            with self.assertRaises(ReminderError):self.r.create('qa','Fictional',day,clock,zone)
    def test_ambiguous_natural_request_saves_nothing(self):
        out=command(self.r,'qa','Set a reminder for 6 a.m. to take my medicine.',self.now,True);self.assertIn('timezone',out);self.assertEqual(self.r.list('qa'),[])
    def test_runtime_local_commands_work_without_provider(self):
        cfg=json.loads(CONFIG_PATH.read_text());cfg['reminders']['enabled']=True;r=Runtime(self.path,None,config=cfg,clock=lambda:self.now)
        try:
            a=r.chat('qa','Remind me at 06:00 on 2026-10-25 in Asia/Kolkata to take fictional medicine.');self.assertEqual(a['route'],'reminder');self.assertIn('SQLite sink',a['message']);self.assertEqual(len(self.r.list('qa')),1);rid=self.r.list('qa')[0]['id'];self.assertIn('Cancelled',r.chat('qa','Cancel reminder '+rid)['message']);self.assertEqual(self.r.get('qa',rid)['status'],'cancelled')
        finally:r.close()
    def test_disabled_reminders_make_no_claim_of_delivery(self):
        out=command(None,'qa','Set a reminder for 6 a.m.',self.now,False);self.assertIn('disabled',out);self.assertIn('no reminder was saved',out)
