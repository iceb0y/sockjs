import asyncio
import unittest
from unittest import mock
from datetime import datetime, timedelta

from aiohttp import web
from sockjs import Session, session, protocol


class SessionTestCase(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(None)

    def tearDown(self):
        self.loop.close()

    def make_session(self, name='test',
                     timeout=timedelta(10), handler=None, result=None):
        if handler is None:
            handler = self.make_handler(result)
        return Session(name, handler,
                       timeout=timeout, loop=self.loop, debug=True)

    def make_handler(self, result, coro=True, exc=False):
        if result is None:
            result = []
        output = result

        def handler(msg, s):
            if exc:
                raise ValueError((msg, s))
            output.append((msg, s))

        if coro:
            return asyncio.coroutine(handler)
        else:
            return handler

    @mock.patch('sockjs.session.datetime')
    def test_ctor(self, dt):
        now = dt.now.return_value = datetime.now()

        handler = self.make_handler([])
        session = Session('id', handler, loop=self.loop)

        self.assertEqual(session.id, 'id')
        self.assertEqual(session.expired, False)
        self.assertEqual(session.expires, now + timedelta(seconds=10))

        self.assertEqual(session._hits, 0)
        self.assertEqual(session._heartbeats, 0)
        self.assertEqual(session.state, protocol.STATE_NEW)

        session = Session('id', handler, timeout=timedelta(seconds=15))

        self.assertEqual(session.id, 'id')
        self.assertEqual(session.expired, False)
        self.assertEqual(session.expires, now + timedelta(seconds=15))

    def test_str(self):
        session = self.make_session('test')
        session.state = protocol.STATE_OPEN

        self.assertEqual(str(session), "id='test' connected")

        session._hits = 10
        session._heartbeats = 50
        session.state = protocol.STATE_CLOSING
        self.assertEqual(str(session),
                         "id='test' disconnected hits=10 heartbeats=50")

        session._feed(protocol.FRAME_MESSAGE, 'msg')
        self.assertEqual(
            str(session),
            "id='test' disconnected queue[1] hits=10 heartbeats=50")

        session.state = protocol.STATE_CLOSED
        self.assertEqual(
            str(session),
            "id='test' closed queue[1] hits=10 heartbeats=50")

        session.state = protocol.STATE_OPEN
        session.acquired = True
        self.assertEqual(
            str(session),
            "id='test' connected acquired queue[1] hits=10 heartbeats=50")

    @mock.patch('sockjs.session.datetime')
    def test_tick(self, dt):
        now = dt.now.return_value = datetime.now()
        session = self.make_session('test')

        now = dt.now.return_value = now + timedelta(hours=1)
        session._tick()
        self.assertEqual(session.expires, now + session.timeout)

    @mock.patch('sockjs.session.datetime')
    def test_tick_different_timeoutk(self, dt):
        now = dt.now.return_value = datetime.now()
        session = self.make_session('test', timeout=timedelta(seconds=20))

        now = dt.now.return_value = now + timedelta(hours=1)
        session._tick()
        self.assertEqual(session.expires, now + timedelta(seconds=20))

    @mock.patch('sockjs.session.datetime')
    def test_tick_custom(self, dt):
        now = dt.now.return_value = datetime.now()
        session = self.make_session('test', timeout=timedelta(seconds=20))

        now = dt.now.return_value = now + timedelta(hours=1)
        session._tick(timedelta(seconds=30))
        self.assertEqual(session.expires, now + timedelta(seconds=30))

    def test_heartbeat(self):
        session = self.make_session('test')
        session._tick = mock.Mock()
        self.assertEqual(session._heartbeats, 0)

        session._heartbeat()
        self.assertEqual(session._heartbeats, 1)
        session._heartbeat()
        self.assertEqual(session._heartbeats, 2)
        self.assertEqual(session._tick.call_count, 2)

    def test_heartbeat_transport(self):
        session = self.make_session('test')
        session._heartbeat_transport = True
        session._heartbeat()
        self.assertEqual(
            list(session._queue),
            [(protocol.FRAME_HEARTBEAT, protocol.FRAME_HEARTBEAT)])

    def test_expire(self):
        session = self.make_session('test')
        self.assertFalse(session.expired)

        session.expire()
        self.assertTrue(session.expired)

    def test_send(self):
        session = self.make_session('test')
        session.send('message')
        self.assertEqual(list(session._queue), [])

        session._tick = mock.Mock()
        session.state = protocol.STATE_OPEN
        session.send('message')

        self.assertEqual(
            list(session._queue),
            [(protocol.FRAME_MESSAGE, ['message'])])
        self.assertTrue(session._tick.called)

    def test_send_non_str(self):
        session = self.make_session('test')
        self.assertRaises(AssertionError, session.send, b'str')

    def test_send_frame(self):
        session = self.make_session('test')
        session.send_frame('a["message"]')
        self.assertEqual(list(session._queue), [])

        session._tick = mock.Mock()
        session.state = protocol.STATE_OPEN
        session.send_frame('a["message"]')

        self.assertEqual(
            list(session._queue),
            [(protocol.FRAME_MESSAGE_BLOB, 'a["message"]')])
        self.assertTrue(session._tick.called)

    def test_feed(self):
        session = self.make_session('test')
        session._feed(protocol.FRAME_OPEN, protocol.FRAME_OPEN)
        session._feed(protocol.FRAME_MESSAGE, 'msg')
        session._feed(protocol.FRAME_CLOSE, (3001, 'reason'))

        self.assertEqual(
            list(session._queue),
            [(protocol.FRAME_OPEN, protocol.FRAME_OPEN),
             (protocol.FRAME_MESSAGE, ['msg']),
             (protocol.FRAME_CLOSE, (3001, 'reason'))])

    def test_feed_msg_packing(self):
        session = self.make_session('test')
        session._feed(protocol.FRAME_MESSAGE, 'msg1')
        session._feed(protocol.FRAME_MESSAGE, 'msg2')
        session._feed(protocol.FRAME_CLOSE, (3001, 'reason'))
        session._feed(protocol.FRAME_MESSAGE, 'msg3')

        self.assertEqual(
            list(session._queue),
            [(protocol.FRAME_MESSAGE, ['msg1', 'msg2']),
             (protocol.FRAME_CLOSE, (3001, 'reason')),
             (protocol.FRAME_MESSAGE, ['msg3'])])

    def test_feed_with_waiter(self):
        session = self.make_session('test')
        session._waiter = waiter = asyncio.Future(loop=self.loop)
        session._feed(protocol.FRAME_MESSAGE, 'msg')

        self.assertEqual(
            list(session._queue),
            [(protocol.FRAME_MESSAGE, ['msg'])])
        self.assertIsNone(session._waiter)
        self.assertTrue(waiter.done())

    def test_wait(self):
        s = self.make_session('test')
        s.state = protocol.STATE_OPEN

        def send():
            yield from asyncio.sleep(0.001, loop=self.loop)
            s._feed(protocol.FRAME_MESSAGE, 'msg1')

        asyncio.async(send(), loop=self.loop)
        frame, payload = self.loop.run_until_complete(s._wait())
        self.assertEqual(frame, protocol.FRAME_MESSAGE)
        self.assertEqual(payload, 'a["msg1"]')

    def test_wait_closed(self):
        s = self.make_session('test')
        s.state = protocol.STATE_CLOSED
        self.assertRaises(
            session.SessionIsClosed,
            self.loop.run_until_complete, s._wait())

    def test_wait_message(self):
        s = self.make_session('test')
        s.state = protocol.STATE_OPEN
        s._feed(protocol.FRAME_MESSAGE, 'msg1')
        frame, payload = self.loop.run_until_complete(s._wait())
        self.assertEqual(frame, protocol.FRAME_MESSAGE)
        self.assertEqual(payload, 'a["msg1"]')

    def test_wait_close(self):
        s = self.make_session('test')
        s.state = protocol.STATE_OPEN
        s._feed(protocol.FRAME_CLOSE, (3000, 'Go away!'))
        frame, payload = self.loop.run_until_complete(s._wait())
        self.assertEqual(frame, protocol.FRAME_CLOSE)
        self.assertEqual(payload, 'c[3000,"Go away!"]')

    def test_wait_message_unpack(self):
        s = self.make_session('test')
        s.state = protocol.STATE_OPEN
        s._feed(protocol.FRAME_MESSAGE, 'msg1')
        frame, payload = self.loop.run_until_complete(s._wait(pack=False))
        self.assertEqual(frame, protocol.FRAME_MESSAGE)
        self.assertEqual(payload, ['msg1'])

    def test_wait_close_unpack(self):
        s = self.make_session('test')
        s.state = protocol.STATE_OPEN
        s._feed(protocol.FRAME_CLOSE, (3000, 'Go away!'))
        frame, payload = self.loop.run_until_complete(s._wait(pack=False))
        self.assertEqual(frame, protocol.FRAME_CLOSE)
        self.assertEqual(payload, (3000, 'Go away!'))

    def test_close(self):
        session = self.make_session('test')
        session.state = protocol.STATE_OPEN
        session.close()
        self.assertEqual(session.state, protocol.STATE_CLOSING)
        self.assertEqual(
            list(session._queue),
            [(protocol.FRAME_CLOSE, (3000, 'Go away!'))])

    def test_close_idempotent(self):
        session = self.make_session('test')
        session.state = protocol.STATE_CLOSED
        session.close()
        self.assertEqual(session.state, protocol.STATE_CLOSED)
        self.assertEqual(list(session._queue), [])

    def test_acquire_new_session(self):
        manager = object()
        messages = []

        session = self.make_session(result=messages)
        self.assertEqual(session.state, protocol.STATE_NEW)

        self.loop.run_until_complete(session._acquire(manager))
        self.assertEqual(session.state, protocol.STATE_OPEN)
        self.assertIs(session.manager, manager)
        self.assertTrue(session._heartbeat_transport)
        self.assertEqual(
            list(session._queue),
            [(protocol.FRAME_OPEN, protocol.FRAME_OPEN)])
        self.assertEqual(messages, [(protocol.OpenMessage, session)])

    def test_acquire_exception_in_handler(self):

        @asyncio.coroutine
        def handler(msg, s):
            raise ValueError

        session = self.make_session(handler=handler)
        self.assertEqual(session.state, protocol.STATE_NEW)

        self.loop.run_until_complete(session._acquire(object()))
        self.assertEqual(session.state, protocol.STATE_CLOSING)
        self.assertTrue(session._heartbeat_transport)
        self.assertTrue(session.interrupted)
        self.assertEqual(
            list(session._queue),
            [(protocol.FRAME_OPEN, protocol.FRAME_OPEN),
             (protocol.FRAME_CLOSE, (3000, 'Internal error'))])

    def test_remote_close(self):
        messages = []
        session = self.make_session(result=messages)

        self.loop.run_until_complete(session._remote_close())
        self.assertFalse(session.interrupted)
        self.assertEqual(session.state, protocol.STATE_CLOSING)
        self.assertEqual(
            messages,
            [(protocol.SockjsMessage(
                tp=protocol.MSG_CLOSE, data=None), session)])

    def test_remote_close_idempotent(self):
        messages = []
        session = self.make_session(result=messages)
        session.state = protocol.STATE_CLOSED

        self.loop.run_until_complete(session._remote_close())
        self.assertEqual(session.state, protocol.STATE_CLOSED)
        self.assertEqual(messages, [])

    def test_remote_close_with_exc(self):
        messages = []
        session = self.make_session(result=messages)

        exc = ValueError()
        self.loop.run_until_complete(session._remote_close(exc=exc))
        self.assertTrue(session.interrupted)
        self.assertEqual(session.state, protocol.STATE_CLOSING)
        self.assertEqual(
            messages,
            [(protocol.SockjsMessage(tp=protocol.MSG_CLOSE, data=exc),
              session)])

    def test_remote_close_exc_in_handler(self):
        handler = self.make_handler([], exc=True)
        session = self.make_session(handler=handler)

        self.loop.run_until_complete(session._remote_close())
        self.assertFalse(session.interrupted)
        self.assertEqual(session.state, protocol.STATE_CLOSING)

    def test_remote_closed(self):
        messages = []
        session = self.make_session(result=messages)

        self.loop.run_until_complete(session._remote_closed())
        self.assertTrue(session.expired)
        self.assertEqual(session.state, protocol.STATE_CLOSED)
        self.assertEqual(
            messages, [(protocol.ClosedMessage, session)])

    def test_remote_closed_idempotent(self):
        messages = []
        session = self.make_session(result=messages)
        session.state = protocol.STATE_CLOSED

        self.loop.run_until_complete(session._remote_closed())
        self.assertEqual(session.state, protocol.STATE_CLOSED)
        self.assertEqual(messages, [])

    def test_remote_closed_with_waiter(self):
        messages = []
        session = self.make_session(result=messages)
        session._waiter = waiter = asyncio.Future(loop=self.loop)

        self.loop.run_until_complete(session._remote_closed())
        self.assertTrue(waiter.done())
        self.assertTrue(session.expired)
        self.assertIsNone(session._waiter)
        self.assertEqual(session.state, protocol.STATE_CLOSED)
        self.assertEqual(
            messages, [(protocol.ClosedMessage, session)])

    def test_remote_closed_exc_in_handler(self):
        handler = self.make_handler([], exc=True)
        session = self.make_session(handler=handler)

        self.loop.run_until_complete(session._remote_closed())
        self.assertTrue(session.expired)
        self.assertEqual(session.state, protocol.STATE_CLOSED)

    def test_remote_message(self):
        messages = []
        session = self.make_session(result=messages)

        self.loop.run_until_complete(session._remote_message('msg'))
        self.assertEqual(
            messages,
            [(protocol.SockjsMessage(tp=protocol.MSG_MESSAGE, data='msg'),
              session)])

    def test_remote_message_exc(self):
        messages = []
        handler = self.make_handler(messages, exc=True)
        session = self.make_session(handler=handler)

        self.loop.run_until_complete(session._remote_message('msg'))
        self.assertEqual(messages, [])

    def test_remote_messages(self):
        messages = []
        session = self.make_session(result=messages)

        self.loop.run_until_complete(
            session._remote_messages(('msg1', 'msg2')))
        self.assertEqual(
            messages,
            [(protocol.SockjsMessage(tp=protocol.MSG_MESSAGE, data='msg1'),
              session),
             (protocol.SockjsMessage(tp=protocol.MSG_MESSAGE, data='msg2'),
              session)])

    def test_remote_messages_exc(self):
        messages = []
        handler = self.make_handler(messages, exc=True)
        session = self.make_session(handler=handler)

        self.loop.run_until_complete(
            session._remote_messages(('msg1', 'msg2')))
        self.assertEqual(messages, [])


class _GcThreadTestCase:  # (TestCase):

    def setUp(self):
        #  super(GcThreadTestCase, self).setUp()

        self.gc_executed = False

        def gc(s):
            self.gc_executed = True

        from pyramid_sockjs.session import SessionManager

        self.gc_origin = SessionManager._gc
        SessionManager._gc = gc

    def tearDown(self):
        from pyramid_sockjs.session import SessionManager
        SessionManager._gc = self.gc_origin

        #  super(GcThreadTestCase, self).tearDown()

    def test_gc_thread(self):
        from pyramid_sockjs.session import SessionManager

        sm = SessionManager('sm', self.registry, gc_cycle=0.1)
        sm.start()
        sm.stop()
        #  self.assertTrue(self.gc_executed)


class SessionManagerTestCase(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(None)

        self.app = web.Application(loop=self.loop)

    def tearDown(self):
        self.loop.close()

    def make_session(self, name, handler=None, timeout=timedelta(10)):
        if handler is None:
            handler = self.make_handler([])
        return Session(name, handler,
                       timeout=timeout, loop=self.loop, debug=True)

    def make_handler(self, result, coro=True):
        output = result

        def handler(msg, s):
            output.append(output)

        if coro:
            return asyncio.coroutine(handler)
        else:
            return handler

    def make_manager(self, handler=None):
        if handler is None:
            handler = self.make_handler([])
        s = self.make_session('test', handler=handler)
        return s, session.SessionManager(
            'sm', self.app, handler, loop=self.loop, debug=True)

    def test_fresh(self):
        s, sm = self.make_manager()
        sm._add(s)
        self.assertIn('test', sm)

    def _test_gc_removed(self):
        Session, sm = self.make_one()

        sm._add(Session('id'))
        del sm['id']

        self.assertEqual(len(sm.pool), 1)
        sm._gc()

        self.assertEqual(len(sm.pool), 0)

    def _test_gc_expire(self):
        from pyramid_sockjs import STATE_CLOSED
        Session, sm = self.make_one()

        session = Session('id')
        session.open()

        sm._add(session)

        self.now = session.expires + timedelta(seconds=10)

        sm._gc()
        self.assertNotIn('id', sm)
        self.assertTrue(session.expired)
        self.assertEqual(session.state, STATE_CLOSED)

    def _test_gc_expire_acquired(self):
        from pyramid_sockjs import STATE_CLOSED
        Session, sm = self.make_one()

        session = Session('id')
        session.open()

        sm._add(session)
        sm.acquired['id'] = session

        self.now = session.expires + timedelta(seconds=10)

        sm._gc()
        self.assertNotIn('id', sm)
        self.assertNotIn('id', sm.acquired)
        self.assertTrue(session.expired)
        self.assertEqual(session.state, STATE_CLOSED)

    def _test_gc_one_expire(self):
        Session, sm = self.make_one()

        session1 = Session('id1')
        session1.open()

        session2 = Session('id2')
        session2.open()

        sm._add(session1)
        sm._add(session2)

        self.now = session1.expires + timedelta(seconds=10)

        session2.tick()

        sm._gc()
        self.assertNotIn('id1', sm)
        self.assertIn('id2', sm)

    def test_add(self):
        s, sm = self.make_manager()

        sm._add(s)
        self.assertIn('test', sm)
        self.assertIs(sm['test'], s)
        self.assertIs(s.manager, sm)

    def test_add_expired(self):
        s, sm = self.make_manager()
        s.expire()

        self.assertRaises(ValueError, sm._add, s)

    def test_get(self):
        s, sm = self.make_manager()
        self.assertRaises(KeyError, sm.get, 'test')

        sm._add(s)
        self.assertIs(sm.get('test'), s)

    def test_get_unknown_with_default(self):
        s, sm = self.make_manager()
        default = object()

        item = sm.get('id', default=default)
        self.assertIs(item, default)

    def test_get_with_create(self):
        _, sm = self.make_manager()

        s = sm.get('test', True)
        self.assertIn(s.id, sm)
        self.assertIsInstance(s, Session)

    def test_acquire(self):
        s1, sm = self.make_manager()
        sm._add(s1)
        s1._acquire = mock.Mock()
        s1._acquire.return_value = asyncio.Future(loop=self.loop)
        s1._acquire.return_value.set_result(1)

        s2 = self.loop.run_until_complete(sm.acquire(s1))

        self.assertIs(s1, s2)
        self.assertIn(s1.id, sm.acquired)
        self.assertTrue(sm.acquired[s1.id])
        self.assertTrue(sm.is_acquired(s1))
        self.assertTrue(s1._acquire.called)

    def test_acquire_unknown(self):
        s, sm = self.make_manager()
        self.assertRaises(
            KeyError, self.loop.run_until_complete, sm.acquire(s))

    def test_acquire_locked(self):
        s, sm = self.make_manager()
        sm._add(s)
        self.loop.run_until_complete(sm.acquire(s))

        self.assertRaises(
            session.SessionIsAcquired,
            self.loop.run_until_complete, sm.acquire(s))

    def test_release(self):
        _, sm = self.make_manager()
        s = sm.get('test', True)
        s._release = mock.Mock()

        self.loop.run_until_complete(sm.acquire(s))
        self.loop.run_until_complete(sm.release(s))

        self.assertNotIn('test', sm.acquired)
        self.assertFalse(sm.is_acquired(s))
        self.assertTrue(s._release.called)

    def test_active_sessions(self):
        _, sm = self.make_manager()

        s1 = sm.get('test1', True)
        s2 = sm.get('test2', True)
        s2.expire()

        active = list(sm.active_sessions())
        self.assertEqual(len(active), 1)
        self.assertIn(s1, active)

    def test_broadcast(self):
        _, sm = self.make_manager()

        s1 = sm.get('test1', True)
        s1.state = protocol.STATE_OPEN
        s2 = sm.get('test2', True)
        s2.state = protocol.STATE_OPEN
        sm.broadcast('msg')

        self.assertEqual(
            list(s1._queue),
            [(protocol.FRAME_MESSAGE_BLOB, 'a["msg"]')])
        self.assertEqual(
            list(s2._queue),
            [(protocol.FRAME_MESSAGE_BLOB, 'a["msg"]')])

    def test_clear(self):
        _, sm = self.make_manager()

        s1 = sm.get('s1', True)
        s1.state = protocol.STATE_OPEN
        s2 = sm.get('s2', True)
        s2.state = protocol.STATE_OPEN

        self.loop.run_until_complete(sm.clear())

        self.assertFalse(bool(sm))
        self.assertTrue(s1.expired)
        self.assertTrue(s2.expired)
        self.assertEqual(s1.state, protocol.STATE_CLOSED)
        self.assertEqual(s2.state, protocol.STATE_CLOSED)