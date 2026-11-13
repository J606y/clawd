"""Bounded read-only API admission provenance; synthetic SQLite only."""
import sys as _sys
from pathlib import Path as _Path
_REPO_ROOT = _Path(__file__).resolve().parents[3]
_TESTS_DIR = _Path(__file__).resolve().parent
if str(_TESTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_TESTS_DIR))
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.append(str(_REPO_ROOT))

from clawd_server import panel_core

import contextlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

class MessageOriginTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'));self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);self.db=self.home/'state.db'
        self.m=panel_core
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            c.executescript('CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,platform_message_id TEXT); CREATE TABLE session_admissions(admission_id TEXT PRIMARY KEY,target_session_id TEXT,principal_id TEXT,intent TEXT,payload_json TEXT);')
            c.executemany('INSERT INTO messages VALUES(?,?,?,?)',[(1,'s','user','a'),(2,'s','user','b'),(3,'s','assistant','a'),(4,'other','user','a'),(5,'s','user','c'),(6,'s','user','d'),(7,'s','user','e')])
            p=json.dumps({'api_turn_v1':{},'text':'synthetic'})
            c.executemany('INSERT INTO session_admissions VALUES(?,?,?,?,?)',[('a','s','api','queue',p),('b','s','api','steer',p),('c','other','api','queue',p),('d','s','telegram','queue',p),('e','s','api','queue','not json')]);c.commit()
    def call(self,session='s',ids='1,2,3,4,5,6,7'):
        with patch.object(self.m,'HERMES_HOME',self.home):return self.m.get_message_origins(session,ids)
    def test_exact_session_role_and_admission_join_without_content_exposure(self):
        before=self.db.read_bytes();self.assertEqual(self.call(),{'items':{'1':'api_queue','2':'api_steer'}});self.assertEqual(self.db.read_bytes(),before)
    def test_query_is_bounded_and_validated(self):
        for session,ids in [('', '1'),('s','0'),('s','1 OR 1=1'),('s','true'),('s',','.join(str(n+1) for n in range(201)))]:
            with self.subTest(ids=ids[:30]),self.assertRaises(self.m.ApiError):self.call(session,ids)
        self.assertEqual(self.call(ids=''),{'items':{}})
        self.assertEqual(self.call(ids='1,1'),{'items':{'1':'api_queue'}})
    def test_old_database_schema_is_supported(self):
        with contextlib.closing(sqlite3.connect(self.db)) as c:c.execute('DROP TABLE session_admissions');c.commit()
        self.assertEqual(self.call(),{'items':{}})
        with contextlib.closing(sqlite3.connect(self.db)) as c:c.execute('DROP TABLE messages');c.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT)');c.commit()
        self.assertEqual(self.call(),{'items':{}})
    def test_missing_database_returns_explicit_unavailable(self):
        self.db.unlink()
        with self.assertRaises(self.m.ApiError) as cm:self.call()
        self.assertEqual(cm.exception.status,503)
    def test_get_route_has_no_write_side_effect(self):
        handler=object.__new__(self.m.Handler)
        with patch.object(self.m,'HERMES_HOME',self.home):
            self.assertEqual(handler._route('GET','/sessions/message-origins',{'session_id':'s','ids':'1'}),{'items':{'1':'api_queue'}})

if __name__=='__main__':unittest.main()
