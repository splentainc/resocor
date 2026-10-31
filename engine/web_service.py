"""Loaded engine + persistent, isolated dialogue and query audit boundaries."""
from copy import deepcopy
import os
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

from nlg import LEVELS
from .answer import Engine
from .conversation import advance, resolve
from .journal import Journal
from .tracing import Trace, span
from .web_policy import RevenueReview

STAGES = ('segments','query','facts','execution','validation','dmn','nlg')


def identity(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('Invalid conversation/request ID.')
    return value


class Workbench:
    def __init__(self, journal_path=None):
        self.engine = Engine()
        self.policy = RevenueReview()
        self.lock = Lock()
        path = journal_path if journal_path is not None else os.environ.get('RESOCOR_JOURNAL',
            str(Path(__file__).with_name('runtime') / 'workbench.sqlite3'))
        self.journal = Journal(path)

    @staticmethod
    def validate(body):
        if not isinstance(body, dict):
            raise ValueError('Request must be a JSON object.')
        question = body.get('query')
        if not isinstance(question, str) or not question.strip() or len(question) > 4096:
            raise ValueError('Enter a question containing 1 to 4096 characters.')
        detail, policy = body.get('detail','crisp'), body.get('policy','none')
        if detail not in LEVELS or policy not in ('none','revenue_review'):
            raise ValueError('Unknown response detail or policy.')
        return question, detail, policy

    def ask(self, body):
        question, detail, policy = self.validate(body)
        with self.lock:
            cid = identity(body['conversation_id']) if body.get('conversation_id') else self.journal.create()['id']
            conversation = self.journal.conversation(cid)
            request = {'query':question,'detail':detail,'policy':policy,'conversation_id':cid}
            return self.run(request, conversation, body.get('request_id'))

    def render(self, body):
        if not isinstance(body, dict):
            raise ValueError('Request must be a JSON object.')
        with self.lock:
            source = self.journal.get(identity(body.get('turn_id')))
            if source.get('kind') == 'render':
                source = self.journal.get(source['render_of'])
            request = {'query':source['query'], 'detail':body.get('detail','crisp'),
                       'policy':source.get('policy','none'), 'conversation_id':source['conversation_id'],
                       'render_of':source['id']}
            self.validate(request)
            conversation = self.journal.conversation(source['conversation_id'])
            return self.run(request, conversation, body.get('request_id'), source=source)

    def run(self, request, conversation, request_id, *, source=None):
        turn_id = identity(request_id) if request_id else str(uuid4())
        existing = self.journal.existing(turn_id, request)
        if existing:
            return existing
        kind = 'render' if source else 'query'
        self.journal.start(turn_id, conversation['id'], kind, request)
        trace = Trace()
        before = conversation['state']
        resolution = {'status':'standalone', 'original_query':request['query'], 'resolved_query':request['query']}
        receipt = None
        try:
            if source:
                resolution = deepcopy(source['context'])
                trace.skipped('context','Rendering a stored receipt; conversation context is unchanged.')
                receipt = deepcopy(source['result'].get('execution'))
                trace.receipt = receipt
                if source['result'].get('route') == 'chatter':
                    result = {**deepcopy(source['result']), 'detail':request['detail']}
                elif receipt:
                    result = self.engine.render_result(receipt, detail=request['detail'],
                        decision=self.policy if request['policy'] == 'revenue_review' else None, trace=trace)
                else:
                    result = deepcopy(source['result'])
            else:
                with span(trace,'context') as step:
                    resolution = resolve(request['query'], before)
                    step['resolution'] = resolution['status']
                if resolution['status'] == 'clarify':
                    result = {'outcome':'clarify', 'answer':None, 'reason':resolution['reason'],
                              'reason_code':'conversation_clarification', 'evidence':[], 'warnings':[]}
                else:
                    result = self.engine.answer(resolution['resolved_query'], detail=request['detail'],
                        decision=self.policy if request['policy'] == 'revenue_review' else None, trace=trace)
        except Exception:
            # The start record survives even an unexpected failure. Persist a
            # safe error receipt; never silently return an unlogged query.
            result = {'outcome':'error', 'answer':None, 'reason':'The engine could not complete this request.',
                      'reason_code':'engine_error', 'evidence':[], 'warnings':[]}
        receipt = result.get('execution') or trace.receipt or receipt or {}
        annotation = receipt.get('annotation') or (source or {}).get('annotation') or {}
        recorded = {s['stage'] for s in trace.steps}
        for stage in STAGES:
            if stage not in recorded:
                reason = ('No DMN policy selected.' if stage == 'dmn' and request['policy'] == 'none'
                          else 'Stored receipt reused; no new parsing or retrieval.' if source and stage in ('segments','query','facts','execution')
                          else 'Reviewed chatter response; no factual execution or NLG.' if result.get('route') == 'chatter'
                          else result.get('reason') or result.get('reason_code') or 'An earlier stage did not complete.')
                trace.skipped(stage,reason)
        turn = {'id':turn_id, 'conversation_id':conversation['id'], 'parent_turn_id':before.get('last_turn_id'),
                'kind':kind, 'query':request['query'], 'resolved_query':resolution['resolved_query'],
                'context':resolution, 'detail':request['detail'], 'policy':request['policy'],
                'result':result, 'trace':trace.finish(), 'segments':annotation.get('segments',[]),
                'tree':annotation.get('tree'), 'plan':receipt.get('plan'),
                'facts':receipt.get('evidence',[]), 'annotation':annotation,
                'dmn_model':self.policy.model.describe() if request['policy'] == 'revenue_review' else None,
                'state_before':before}
        if source:
            turn['render_of'] = source['id']
        after = before if source else advance(before,turn)
        turn['state_after'] = after
        return self.journal.finish(turn, after)
