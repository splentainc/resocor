"""Local SQLite conversation/query journal with linked, content-hashed receipts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from .tracing import timestamp


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def source_manifest():
    root = Path(__file__).resolve().parents[1]
    paths = [p for folder in ('engine', 'nlg', 'chatter', 'dmn') for p in (root / folder).glob('*.py')]
    paths += list((root / 'engine/web_assets').glob('*'))
    paths += [p for folder in ('engine/data', 'engine/knowledge', 'dmn/examples')
              for p in (root / folder).glob('*') if p.suffix in ('.db','.json')]
    paths += [root / 'chatter/generic.db']
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


class Journal:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, title TEXT NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS queries (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
                at TEXT NOT NULL, kind TEXT NOT NULL, request TEXT NOT NULL,
                status TEXT NOT NULL, response TEXT);
            CREATE INDEX IF NOT EXISTS query_conversation ON queries(conversation_id, at);
            CREATE TABLE IF NOT EXISTS source_manifests (id TEXT PRIMARY KEY, manifest TEXT NOT NULL);
        ''')
        manifest = source_manifest()
        self.source_id = digest(manifest)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO source_manifests VALUES (?,?)', (self.source_id,canonical(manifest)))
            # An accepted request interrupted by shutdown remains a visible log.
            interrupted = self.db.execute("SELECT id,conversation_id FROM queries WHERE status='running' AND kind='query'").fetchall()
            for row in interrupted:
                state = {'context':None, 'idle':0, 'last_turn_id':row['id']}
                self.db.execute('UPDATE conversations SET state=? WHERE id=?', (canonical(state),row['conversation_id']))
            self.db.execute("UPDATE queries SET status='interrupted' WHERE status='running'")

    def close(self):
        self.db.close()

    def create(self):
        identity = str(uuid4())
        state = {'context':None, 'idle':0, 'last_turn_id':None}
        with self.lock, self.db:
            self.db.execute('INSERT INTO conversations VALUES (?,?,?,?)', (identity,timestamp(),'New conversation',canonical(state)))
        return self.conversation(identity)

    def conversations(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('''SELECT c.id,c.created_at,c.title,
                (SELECT count(*) FROM queries q WHERE q.conversation_id=c.id AND q.kind!='render') AS turn_count
                FROM conversations c ORDER BY c.created_at DESC''')]

    def conversation(self, identity):
        with self.lock:
            row = self.db.execute('SELECT * FROM conversations WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise ValueError('Conversation not found.')
            turns = [self._turn(r) for r in self.db.execute('SELECT * FROM queries WHERE conversation_id=? ORDER BY rowid', (identity,))]
            return {**dict(row), 'state':json.loads(row['state']), 'turns':turns,
                    'integrity_ok':self.verify(turns)}

    @staticmethod
    def _turn(row):
        if row['response']:
            return json.loads(row['response'])
        request = json.loads(row['request'])
        return {'id':row['id'], 'conversation_id':row['conversation_id'], 'query':request['query'],
                'kind':row['kind'], 'detail':request.get('detail','crisp'),
                'status':row['status'], 'result':{'outcome':'error', 'answer':None,
                'reason':'This accepted request was interrupted before a complete trace was saved.'},
                'trace':{'started_at':row['at'], 'ended_at':None, 'total_us':None, 'steps':[], 'rules':[]},
                'segments':[], 'facts':[], 'annotation':{}}

    @staticmethod
    def verify(turns):
        previous = None
        for turn in turns:
            if turn.get('status') in ('interrupted','running'):
                continue
            checked = deepcopy(turn)
            if not isinstance(checked.get('audit'),dict) or 'record_hash' not in checked['audit'] or 'previous_hash' not in checked['audit']:
                return False
            expected = checked['audit'].pop('record_hash')
            if checked['audit']['previous_hash'] != previous or digest(checked) != expected:
                return False
            previous = expected
        return True

    def get(self, identity):
        with self.lock:
            row = self.db.execute('SELECT * FROM queries WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise ValueError('Query not found.')
            return self._turn(row)

    def existing(self, identity, request):
        with self.lock:
            row = self.db.execute('SELECT * FROM queries WHERE id=?', (identity,)).fetchone()
            if row is None:
                return None
            if canonical(request) != row['request']:
                raise ValueError('Request ID already belongs to a different query.')
            return self._turn(row)

    def start(self, identity, conversation_id, kind, request):
        with self.lock, self.db:
            self.db.execute('INSERT INTO queries VALUES (?,?,?,?,?,?,NULL)',
                            (identity,conversation_id,timestamp(),kind,canonical(request),'running'))

    def finish(self, turn, state):
        turn = json.loads(canonical(turn))
        identity = turn['conversation_id']
        with self.lock, self.db:
            rows = self.db.execute('SELECT response FROM queries WHERE conversation_id=? AND response IS NOT NULL ORDER BY rowid DESC LIMIT 1', (identity,)).fetchone()
            previous = json.loads(rows['response'])['audit']['record_hash'] if rows else None
            turn['audit'] = {'previous_hash':previous, 'source_manifest':self.source_id,
                             'persisted_at':timestamp(), 'schema_version':1}
            if turn['kind'] == 'import':
                turn['audit'].update(source_manifest=None, importer_manifest=self.source_id,
                                     origin='legacy browser export; original runtime hashes unavailable')
            turn['audit']['record_hash'] = digest(turn)
            self.db.execute('UPDATE queries SET status=?,response=? WHERE id=?', ('completed',canonical(turn),turn['id']))
            self.db.execute("UPDATE conversations SET state=?,title=CASE WHEN title='New conversation' THEN ? ELSE title END WHERE id=?",
                            (canonical(state),turn['query'][:80],identity))
        return turn

    def import_legacy(self, export):
        """Preserve old tab-only records without claiming current runtime proof."""
        session = self.create()
        state = session['state']
        for original in export['turns']:
            turn = deepcopy(original)
            turn.update(id=str(uuid4()), conversation_id=session['id'], kind='import',
                        legacy_turn_id=original['id'], parent_turn_id=state['last_turn_id'],
                        resolved_query=original['query'], state_before=deepcopy(state),
                        context={'status':'imported', 'original_query':original['query'],
                                 'resolved_query':original['query']})
            state = {**state, 'last_turn_id':turn['id']}
            turn['state_after'] = deepcopy(state)
            self.start(turn['id'],session['id'],'import',{'query':turn['query'],'legacy_turn_id':original['id']})
            self.finish(turn,state)
        return self.conversation(session['id'])

    def manifest(self, identity):
        with self.lock:
            row = self.db.execute('SELECT manifest FROM source_manifests WHERE id=?',(identity,)).fetchone()
            if row is None:
                raise ValueError('Source manifest not found.')
            return json.loads(row[0])

    def export(self, identity):
        session = self.conversation(identity)
        manifests = {value for t in session['turns'] for key,value in t.get('audit',{}).items()
                     if key in ('source_manifest','importer_manifest') and value}
        return {'exported_at':timestamp(), 'conversation_id':identity,
                'integrity_ok':session['integrity_ok'], 'turns':session['turns'],
                'source_manifests':{key:self.manifest(key) for key in sorted(manifests)}}

    def logs(self, offset=0):
        with self.lock:
            rows = self.db.execute('SELECT * FROM queries ORDER BY rowid DESC LIMIT 100 OFFSET ?', (offset,)).fetchall()
            entries = []
            for row in rows:
                turn = self._turn(row)
                entries.append({k:turn.get(k) for k in ('id','conversation_id','query','resolved_query','detail','kind','parent_turn_id')}
                               | {'at':row['at'], 'status':row['status'], 'outcome':turn['result']['outcome'],
                                  'total_us':turn['trace']['total_us'], 'record_hash':turn.get('audit',{}).get('record_hash')})
            total = self.db.execute('SELECT count(*) FROM queries').fetchone()[0]
            return {'entries':entries, 'total':total, 'next_offset':offset+100 if offset+100 < total else None}
