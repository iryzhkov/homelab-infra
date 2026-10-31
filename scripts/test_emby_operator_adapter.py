"""Proposed recording loopback HTTP/custody checks. Parent runs in Chaldea only."""
import contextlib
import copy
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import stat
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

import emby_operator_adapter as adapter
from emby_operator_recommendations import Refused, Journal, Operator, digest

S, A, R, G = 'a' * 32, 'b' * 32, 'c' * 32, 'd' * 32


def config():
    # Synthetic placeholders only, never production identities or custody claims.
    return dict(enabled=True, origin='https://synthetic.invalid', server=S, actor=A,
                recipient=R, library='7', library_guid=G, recipe='igor-movies',
                credential_file='/synthetic/credential.json', journal_directory='/synthetic/journal')


def identity_responses():
    return [[{'Id': A}, {'Id': R}],
            {'Id': A, 'ServerId': S, 'Policy': {'IsAdministrator': True, 'IsDisabled': False}},
            {'Id': R, 'ServerId': S, 'Policy': {'IsAdministrator': False, 'IsDisabled': False}},
            [{'ItemId': '7', 'Guid': G, 'CollectionType': 'movies'}],
            {'Items': [{'Id': '7'}], 'TotalRecordCount': 1}]


class RecordingHTTP:
    """Real loopback HTTP, not an Emby process. Never receives real credentials."""
    def __init__(self):
        self.responses = []
        self.calls = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                recorder.calls.append((self.command, self.path, dict(self.headers),
                                       json.loads(raw) if raw else None))
                response = (recorder.responder(recorder.calls[-1]) if hasattr(recorder, 'responder')
                            else recorder.responses.pop(0) if recorder.responses else (500, {}))
                status, body = response if type(response) is tuple else (200, response)
                raw = body if type(body) is bytes else json.dumps(body).encode()
                self.send_response(status)
                if status == 302:
                    self.send_header('Location', '/redirect-must-not-follow')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def open(self, request, timeout):
        # Test-only HTTPS-to-loopback seam, no production origin override exists.
        assert not (request.get_method() == 'POST' and re.search(
            r'/emby/Items/(?!Access(?:[?]|$)|Delete(?:[?]|$))[^/?]+(?:[?]|$)',
            request.full_url)), 'Item metadata POST is forbidden'
        original = request.full_url
        suffix = original.removeprefix('https://synthetic.invalid')
        local = urllib.request.Request('http://127.0.0.1:%s%s' % (
            self.server.server_port, suffix), data=request.data,
            headers=dict(request.header_items()), method=request.method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), adapter.NoRedirect())
        response = opener.open(local, timeout=timeout)
        response.geturl = lambda: original
        return response


def auth_response(admin=True):
    return {'ServerId': S, 'User': {'Id': A, 'Policy': {'IsAdministrator': admin}},
            'AccessToken': 'synthetic-session-token'}


class HTTPTests(unittest.TestCase):
    def authenticated(self, http):
        http.responses += [auth_response()] + identity_responses()
        api = adapter.OperatorAdapter(config())
        api._opener = http
        api.authenticate({'username': 'synthetic-admin', 'password': 'synthetic-password'})
        return api

    def test_auth_binding_and_routes(self):
        with RecordingHTTP() as http:
            api = self.authenticated(http)
            self.assertEqual(http.calls[0][0:2], ('POST', '/emby/Users/AuthenticateByName'))
            self.assertEqual(http.calls[0][3], {'Username': 'synthetic-admin', 'Pw': 'synthetic-password'})
            self.assertFalse(any(k.lower() == 'x-emby-token' for k in http.calls[0][2]))
            self.assertEqual([x[1] for x in http.calls[1:]], [
                '/emby/Users', '/emby/Users/' + A, '/emby/Users/' + R,
                '/emby/Library/VirtualFolders', '/emby/Users/' + R + '/Views'])
            for call in http.calls[1:]:
                self.assertEqual(next(v for k, v in call[2].items() if k.lower() == 'x-emby-token'),
                                 'synthetic-session-token')
            self.assertNotEqual(api.session_binding, api._token)
            api.close()
            self.assertIsNone(api._token)

    def test_denied_nonadmin_global_and_redirect(self):
        for response in [(401, b'synthetic-password'), auth_response(False), (302, {})]:
            with self.subTest(response=response), RecordingHTTP() as http:
                http.responses = [response]
                api = adapter.OperatorAdapter(config())
                api._opener = http
                with self.assertRaises(Refused) as caught:
                    api.authenticate({'username': 'synthetic-admin', 'password': 'synthetic-password'})
                self.assertNotIn('synthetic-password', str(caught.exception))
                self.assertEqual(len(http.calls), 1)
        api = adapter.OperatorAdapter(config())
        with self.assertRaises(Refused):
            api.authenticate({'api_key': 'unsupported'})
        self.assertEqual(api._requests, 0)

    def test_current_policy_and_session_drift(self):
        with RecordingHTTP() as http:
            api = self.authenticated(http)
            responses = identity_responses()
            responses[1]['Policy']['IsAdministrator'] = False
            http.responses += responses
            with self.assertRaisesRegex(Refused, 'Admin policy drift'):
                api.verified_identity()
            api.session_binding = 'changed'
            before = len(http.calls)
            with self.assertRaises(Refused):
                api.verified_identity()
            self.assertEqual(len(http.calls), before)

    def test_server_recipient_library_drift(self):
        for index, change in [(1, {'ServerId': 'e' * 32}), (2, {'Id': 'e' * 32})]:
            with self.subTest(index=index), RecordingHTTP() as http:
                api = self.authenticated(http)
                responses = identity_responses()
                responses[index].update(change)
                http.responses += responses
                with self.assertRaises(Refused):
                    api.verified_identity()
        with RecordingHTTP() as http:
            api = self.authenticated(http)
            responses = identity_responses()
            responses[3][0]['Guid'] = 'e' * 32
            http.responses += responses
            with self.assertRaises(Refused):
                api.verified_identity()

    def test_typed_recipient_query_and_blocked_missing_results(self):
        cases = [({'Items': [{'Id': 'm2', 'Type': 'Movie'}, {'Id': 'm1', 'Type': 'Movie'}],
                   'TotalRecordCount': 2}, True),
                 ({'Items': [{'Id': 'm1', 'Type': 'Movie'}], 'TotalRecordCount': 1}, False),
                 ({'Items': [{'Id': 'm1', 'Type': 'Movie'}], 'TotalRecordCount': 2}, False),
                 ({'Items': [{'Id': 'm1', 'Type': 'Episode'}, {'Id': 'm2', 'Type': 'Movie'}],
                   'TotalRecordCount': 2}, False),
                 ({'Items': [{'Id': 'm1', 'Type': 'Movie'}] * 2, 'TotalRecordCount': 2}, False)]
        for response, success in cases:
            with self.subTest(response=response), RecordingHTTP() as http:
                api = self.authenticated(http)
                http.responses += identity_responses() + [response]
                if success:
                    result = api.query_movies(R, '7', ['m1', 'm2'])
                    self.assertEqual([x['Id'] for x in result], ['m1', 'm2'])
                else:
                    with self.assertRaises(Refused):
                        api.query_movies(R, '7', ['m1', 'm2'])
                from urllib.parse import urlsplit, parse_qs
                query = parse_qs(urlsplit(http.calls[-1][1]).query)
                self.assertEqual(query, {'Recursive': ['true'], 'IncludeItemTypes': ['Movie'],
                    'ParentId': ['7'], 'Ids': ['m1,m2'], 'StartIndex': ['0'], 'Limit': ['26'],
                    'EnableUserData': ['false']})
                self.assertEqual(urlsplit(http.calls[-1][1]).path, '/emby/Users/' + R + '/Items')

    def test_transport_budget_response_cap_and_secret_free_errors(self):
        with RecordingHTTP() as http:
            api = adapter.OperatorAdapter(config())
            api._opener = http
            api._requests = adapter.MAX_REQUESTS
            with self.assertRaises(Refused):
                api._http('POST', '/Users/AuthenticateByName', authenticating=True)
            self.assertEqual(http.calls, [])
        with RecordingHTTP() as http, patch.object(adapter, 'MAX_BYTES', 32):
            http.responses = [b'x' * 33]
            api = adapter.OperatorAdapter(config())
            api._opener = http
            with self.assertRaisesRegex(Refused, 'Response too large'):
                api.authenticate({'username': 'synthetic-admin', 'password': 'synthetic-password'})
            self.assertEqual(len(http.calls), 1)

    def test_no_adoption_or_delete_interface_without_journal(self):
        api = adapter.OperatorAdapter(config())
        for name, args in [('snapshot', (R, 'marker')), ('create_playlist', (A, 'marker')),
                           ('share_read', ('p', R)), ('create_row', (R, {})),
                           ('delete_entries', ('p', A, ['entry'])),
                           ('add_movies', ('p', A, ['movie'])), ('delete_row', (R, 1)),
                           ('delete_owned_playlist', ('p', 'marker', A))]:
            with self.subTest(name=name), self.assertRaises(Refused):
                getattr(api, name)(*args)
        self.assertEqual(api._requests, 0)


class CustodyTests(unittest.TestCase):
    def setUp(self):
        home = Path(os.environ['HOME'])
        temporary = Path(os.environ['TMPDIR'])
        self.assertEqual(home, Path(__file__).resolve().parents[1] / '.cache/holonet-operator-tests/home')
        self.assertEqual(temporary, home / 'tmp')
        # Never create test files outside the parent's explicitly isolated HOME.
        self.directory = tempfile.TemporaryDirectory(dir=temporary)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root.chmod(0o700)
        self.file = self.root / 'secret.json'
        self.file.write_text('{"username":"synthetic","password":"synthetic-secret"}')
        self.file.chmod(0o600)

    @contextlib.contextmanager
    def synthetic_root_ancestors(self):
        # Emulate root ownership only; actual filesystem modes/symlink checks stay real.
        # Test job need not run root or chown its HOME. Never used by production.
        original = os.fstat
        def fstat(fd):
            s = original(fd)
            return SimpleNamespace(st_uid=0, st_mode=s.st_mode & ~0o022,
                                   st_nlink=s.st_nlink, st_size=s.st_size)
        with patch.object(adapter.os, 'fstat', fstat):
            yield

    def test_no_follow_file_ancestor_and_modes(self):
        with self.synthetic_root_ancestors():
            self.assertIn(b'synthetic-secret', adapter.private_read(str(self.file)))
            self.file.chmod(0o640)
            with self.assertRaises(Refused):
                adapter.private_read(str(self.file))
            self.file.chmod(0o600)
            link = self.root / 'link'
            link.symlink_to(self.file)
            with self.assertRaises(Refused):
                adapter.private_read(str(link))
            directory_link = self.root / 'dirlink'
            directory_link.symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(Refused):
                adapter.private_read(str(directory_link / self.file.name))
            os.link(self.file, self.root / 'hardlink')
            with self.assertRaises(Refused):
                adapter.private_read(str(self.file))

    def test_real_unsafe_ancestor_refused_without_secret_in_errors(self):
        self.root.chmod(0o777)
        with self.assertRaises(Refused) as caught:
            adapter.private_read(str(self.file))
        self.assertNotIn('synthetic-secret', str(caught.exception))
        self.assertNotIn(str(self.file), str(caught.exception))

    def test_nonroot_and_unrecognized_credentials(self):
        with patch.object(adapter.os, 'geteuid', return_value=1000):
            with self.assertRaisesRegex(Refused, 'Root operator required'):
                adapter.load_credentials(str(self.file))
        with patch.object(adapter.os, 'geteuid', return_value=0), \
             patch.object(adapter.os, 'getuid', return_value=0), \
             patch.object(adapter, 'private_read', return_value=b'{"api_key":"synthetic"}'):
            with self.assertRaises(Refused):
                adapter.load_credentials(str(self.file))

    def test_normalization_unknown_fields_order_and_id_types(self):
        rows = [{'Id': 1, 'Unknown': {'nested': [3, 2]}, 'ParentItem': {}},
                {'Id': '1', 'UserData': {}, 'Query': {'opaque': True}}]
        result = adapter.normalize_home(rows)
        self.assertEqual(result, [{'Id': 1, 'Unknown': {'nested': [3, 2]}},
                                  {'Id': '1', 'Query': {'opaque': True}}])
        result[0]['Unknown']['nested'].append(4)
        self.assertEqual(rows[0]['Unknown']['nested'], [3, 2])
        with self.assertRaises(Refused):
            adapter.normalize_home([{'Id': 1}, {'Id': 1}])
        with self.assertRaises(Refused):
            adapter.normalize_home({'Items': [], 'TotalRecordCount': 1})

    def test_only_exact_empty_query_expansion_is_normalized(self):
        empty = {k: [] for k in ('StudioIds', 'TagIds', 'GenreIds', 'CollectionTypes')}
        self.assertEqual(adapter.normalize_home([{'Id': 'r', 'Query': empty}]),
                         [{'Id': 'r', 'Query': {}}])
        variants = [dict(empty, TagIds=['restricted']), dict(empty, Unknown=[]),
                    {'StudioIds': []}, None, {'StudioIds': False}]
        for query in variants:
            self.assertEqual(adapter.normalize_home([{'Id': 'r', 'Query': query}]),
                             [{'Id': 'r', 'Query': query}])

    def test_origin_default_off_and_invalid_request_before_secret_network(self):
        c = config()
        c['enabled'] = False
        with self.assertRaisesRegex(Refused, 'disabled'):
            adapter.OperatorAdapter(c)
        for origin in ('http://synthetic.invalid', 'https://x/emby', 'https://x?x=1',
                       'https://user:password@x', 'https://x/#frag'):
            c = config()
            c['origin'] = origin
            with self.assertRaises(Refused):
                adapter.validate_config(c)
        path = Path(__file__).with_name('emby-operator-recommendations.py')
        spec = importlib.util.spec_from_file_location('operator_entry', path)
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        with patch.object(entry, 'load_config', return_value=config()), \
             patch.object(entry, 'load_credentials') as credentials, \
             patch.object(adapter.urllib.request, 'build_opener') as network, \
             contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(entry.main(['--config', '/synthetic/config', 'preview']), 2)
            credentials.assert_not_called()
            network.assert_not_called()
            self.assertIn('Invalid operator request fields', errors.getvalue())



class SyntheticBackend:
    """Recording DTO model for transport integration, not live Emby evidence."""
    def __init__(self):
        self.home = [{'Id': 1, 'Unknown': {'retain': [9]}}]
        self.playlists = []
        self.entries = []
        self.grant = 'None'
        self.visible = ['m1', 'm2']
        self.serial = 0
        self.writes = []
        self.lose = None
        self.fail_before = None
        self.bad_item = {}
        self.discovery_extra = 0
        self.admin = True
        self.journal = None
        self.other_grant = 'ManageDelete'

    def envelope(self, rows):
        return {'Items': copy.deepcopy(rows), 'TotalRecordCount': len(rows)}

    def respond(self, call):
        from urllib.parse import urlsplit, parse_qs
        method, url, headers, body = call
        u = urlsplit(url)
        path, q = u.path.removeprefix('/emby'), parse_qs(u.query)
        if path == '/Users/AuthenticateByName':
            return auth_response(self.admin)
        # Every subsequent request stays on this fresh authenticated session.
        assert next(v for k, v in headers.items() if k.lower() == 'x-emby-token') == 'synthetic-session-token'
        if method == 'GET':
            if path == '/Users':
                return [{'Id': i} for i in dict.fromkeys((A, R))]
            if path in ('/Users/' + A, '/Users/' + R):
                return {'Id': path.split('/')[-1], 'ServerId': S,
                        'Policy': {'IsDisabled': False, 'IsAdministrator': self.admin if path.endswith(A) else False}}
            if path == '/Library/VirtualFolders':
                return [{'ItemId': '7', 'Guid': G, 'CollectionType': 'movies'}]
            if path == '/Users/' + R + '/Views':
                return self.envelope([{'Id': '7'}])
            if path == '/Users/' + R + '/HomeSections':
                return copy.deepcopy(self.home)
            if path == '/Users/' + R + '/Items' and q.get('IncludeItemTypes') == ['Movie']:
                assert q['ParentId'] == ['7'] and q['IncludeItemTypes'] == ['Movie']
                return self.envelope([{'Id': i, 'Type': 'Movie'} for i in q['Ids'][0].split(',') if i in self.visible])
            if path == '/Users/' + A + '/Items':
                assert q['IncludeItemTypes'] == ['Playlist']
                dto = self.envelope(self.playlists)
                dto['TotalRecordCount'] += self.discovery_extra
                return dto
            if path == '/Users/' + A + '/Items/p1':
                if not self.playlists:
                    return (404, {})
                capabilities = dict(CanEditItems=True, CanDelete=True) if A == R else dict(CanManageAccess=True, CanEditItems=True)
                return dict(self.playlists[0], **capabilities) | self.bad_item
            if path == '/Users/ItemAccess':
                assert q == {'ItemId': ['p1']}
                return self.envelope([{'Id': A, 'ServerId': S, 'UserItemShareLevel': 'ManageDelete'},
                    *([] if A == R else [{'Id': R, 'ServerId': S, 'UserItemShareLevel': self.grant}]),
                    {'Id': 'e' * 32, 'ServerId': S, 'UserItemShareLevel': self.other_grant}])
            if path == '/Playlists/p1/Items':
                assert q['UserId'] == [A]
                return self.envelope(self.entries)
            raise AssertionError('Unmodeled GET')
        assert method == 'POST'
        pending = self.journal.load()['pending']
        assert pending is not None, 'Intent precedes every POST'
        op = pending['operation']
        self.writes.append((op, path, q, copy.deepcopy(body)))
        if self.fail_before == op:
            return (500, b'uncertain dispatch')
        if op == 'create':
            assert path == '/Playlists' and q == {'Name': [q['Name'][0]], 'userId': [A]} and body is None
            self.playlists = [{'Id': 'p1', 'Type': 'Playlist', 'Name': q['Name'][0]}]
        elif op == 'share':
            assert path == '/Items/Access' and body == {'UserIds': [R], 'ItemIds': ['p1'], 'ItemAccess': 'Read'}
            self.grant = 'Read'
        elif op == 'row':
            assert path == '/Users/' + R + '/HomeSections' and body == pending['args']
            self.home.append(dict(body, Id='2', Name=body['CustomName'],
                                  Monitor=[], CardSizeOffset=0,
                                  Query={k: [] for k in ('StudioIds', 'TagIds', 'GenreIds', 'CollectionTypes')}))
        elif op == 'display-title':
            assert path == '/Users/' + R + '/HomeSections' and body == pending['args']
            rows = [r for r in self.home if r['Id'] == body['Id']]
            assert len(rows) == 1 and rows[0]['ParentId'] == body['ParentId'] == 'p1'
            before = adapter.normalize_home([rows[0]])[0]
            expected = dict(before, CustomName=body['CustomName'])
            assert body == expected, 'Only CustomName may change in the request'
            rows[0].update(CustomName=body['CustomName'], Name=body['CustomName'])
        elif op == 'add':
            assert path == '/Playlists/p1/Items' and body == {
                'Ids': ','.join(pending['args']), 'userId': A, 'SkipDuplicates': False}
            self.serial += 1
            self.entries = [{'Id': m, 'Type': 'Movie', 'PlaylistItemId': 'e%s_%s' % (self.serial, n)}
                            for n, m in enumerate(pending['args'])]
        elif op == 'clear':
            assert path == '/Playlists/p1/Items/Delete' and body == {'EntryIds': ','.join(pending['args'])}
            self.entries = []
        elif op == 'unrow':
            assert path == '/Users/' + R + '/HomeSections/Delete' and body == {'ids': ['2']}
            self.home.pop()
        elif op == 'delete':
            assert path == '/Items/Delete' and q == {'Ids': ['p1']} and body is None
            self.playlists = []
            self.entries = []
            self.grant = 'None'  # A newly created resource starts without a grant.
        else:
            raise AssertionError('Unmodeled mutation')
        if self.lose == op:
            return (500, b'no response body disclosure')
        # Empty successful responses are legitimate mutation acknowledgments.
        return (204, b'')


class LifecycleHTTPTests(unittest.TestCase):
    def setUp(self):
        home = Path(__file__).resolve().parents[1] / '.cache/holonet-operator-tests/home'
        self.assertEqual(os.environ['HOME'], str(home))
        self.assertEqual(os.environ['TMPDIR'], str(home / 'tmp'))
        self.temp = tempfile.TemporaryDirectory(dir=home / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.directory.chmod(0o700)
        self.journal = Journal(self.directory)
        self.addCleanup(self.journal.close)
        self.backend = SyntheticBackend()
        self.backend.journal = self.journal
        self.http = RecordingHTTP()
        self.http.responder = self.backend.respond
        self.http.__enter__()
        self.addCleanup(self.http.__exit__, None, None, None)
        self.fresh_session()

    def fresh_session(self):
        self.api = adapter.OperatorAdapter(config(), self.journal)
        self.api._opener = self.http
        self.api.authenticate({'username': 'synthetic', 'password': 'synthetic'})
        self.operator = Operator(self.api, self.journal, **{
            k: config()[k] for k in ('server', 'actor', 'recipient', 'library', 'recipe')})

    def apply(self, key='first', movies=None):
        movies = movies or ['m1']
        p = self.operator.preview(movies)
        return self.operator.apply(key, movies, p['baseline_digest'])

    def test_show_retained_v1_real_adapter_held_lock_final_readback(self):
        self.apply(movies=['m2', 'm1'])
        before = (self.directory / 'journal.json').read_bytes()
        self.assertNotIn('display_title', json.loads(before))
        self.fresh_session()
        offset = len(self.http.calls)
        result = self.operator.show()
        self.assertEqual([e['movie'] for e in result['playlists'][0]['entries']], ['m2', 'm1'])
        self.assertEqual(result['playlists'][0]['id'], 'p1')
        self.assertEqual(result['row']['saved_id'], self.backend.home[-1]['Id'])
        self.assertEqual(result['row']['saved']['ParentId'], 'p1')
        self.assertEqual(result['display_title'], self.operator.marker)
        self.assertEqual(result['reconcile'], {'state': 'clean'})
        with self.journal.locked():
            final = self.api.snapshot(R, self.operator.marker)
        self.assertEqual(result['revision'], digest(final))
        self.assertEqual(result['playlists'], final['playlists'])
        self.assertTrue(all(c[0] == 'GET' for c in self.http.calls[offset:]))
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        with self.assertRaisesRegex(Refused, 'Held core journal lock required'):
            self.api.snapshot(R, self.operator.marker)

    def test_show_entry_uses_real_adapter_and_same_journal(self):
        self.apply()
        before = (self.directory / 'journal.json').read_bytes()
        entry = EntryTests().entry()
        made = []
        def make_api(c, journal):
            self.assertIs(journal, self.journal)
            api = adapter.OperatorAdapter(c, journal)
            api._opener = self.http
            made.append(api)
            return api
        offset = len(self.http.calls)
        with patch.object(entry, 'load_config', return_value=config()), \
             patch.object(entry, 'load_credentials', return_value={'username': 'synthetic', 'password': 'synthetic'}), \
             patch.object(entry, 'Journal', return_value=self.journal), \
             patch.object(entry, 'OperatorAdapter', side_effect=make_api), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(entry.main(['--config', '/synthetic/config', 'show']), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['playlists'][0]['id'], 'p1')
        # Authentication POST is required; the show itself sends GETs only.
        self.assertEqual([c[1] for c in self.http.calls[offset:] if c[0] == 'POST'],
                         ['/emby/Users/AuthenticateByName'])
        self.assertIsNone(made[0]._token)
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)

    def test_saved_id_lookup_separates_protected_title_from_marker(self):
        self.apply()
        title = 'Igor Movies'
        # Protected fixture for a completed row-only title update.
        with self.journal.locked():
            d = self.journal.load()
            d['display_title'] = title
            d['current']['home'][-1].update(CustomName=title, Name=title)
            self.journal.save(d)
        self.backend.home[-1].update(CustomName=title, Name=title)
        before = (self.directory / 'journal.json').read_bytes()
        self.fresh_session()
        result = self.operator.show()
        self.assertEqual(result['display_title'], title)
        self.assertEqual(result['marker'], self.operator.marker)
        self.assertEqual(result['playlists'][0]['marker'], self.operator.marker)
        self.assertEqual(self.operator.reconcile(), {'state': 'clean'})
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.fresh_session()
        self.assertEqual(self.apply('update-title-fixture', ['m2'])['writes'], 2)
        preview = self.operator.preview(['m1'])
        self.assertEqual(self.operator.remove('remove-title-fixture', preview['baseline_digest'])['writes'], 2)
        self.assertEqual(self.backend.playlists, [])
        self.assertEqual(self.backend.home, [{'Id': 1, 'Unknown': {'retain': [9]}}])

    def test_show_refuses_name_saved_id_row_membership_and_foreign_marker_drift(self):
        self.apply()
        original = copy.deepcopy((self.backend.playlists, self.backend.home, self.backend.entries))
        before = (self.directory / 'journal.json').read_bytes()
        for mutate in (lambda: self.backend.playlists[0].update(Name='manual'),
                       lambda: self.backend.playlists[0].update(Id='foreign'),
                       lambda: self.backend.playlists.clear(),
                       lambda: self.backend.home[-1].update(CustomName='manual'),
                       lambda: self.backend.home[-1].update(ParentId='foreign'),
                       lambda: self.backend.entries[0].update(PlaylistItemId='manual'),
                       lambda: self.backend.playlists.append(dict(Id='foreign', Type='Playlist', Name=self.operator.marker))):
            self.backend.playlists, self.backend.home, self.backend.entries = copy.deepcopy(original)
            mutate()
            self.fresh_session()
            with self.assertRaises(Refused):
                self.operator.show()
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.assertEqual(len(self.backend.writes), 4)

    def test_show_bounds_exact_dtos_capabilities_and_complete_results(self):
        self.apply()
        before = (self.directory / 'journal.json').read_bytes()
        for bad in ({'Id': 'foreign'}, {'Type': 'Movie'}, {'Name': 'manual'},
                    {'CanEditItems': False}, {'CanManageAccess': False}):
            self.backend.bad_item = bad
            self.fresh_session()
            with self.assertRaises(Refused):
                self.operator.show()
        self.backend.bad_item = {}
        self.backend.discovery_extra = 1
        self.fresh_session()
        with self.assertRaises(Refused):
            self.operator.show()
        self.backend.discovery_extra = 0
        entries = copy.deepcopy(self.backend.entries)
        self.backend.entries = [dict(Id='m1', Type='Movie', PlaylistItemId='e' + str(i))
                                for i in range(26)]
        self.fresh_session()
        with self.assertRaises(Refused):
            self.operator.show()
        self.backend.entries = entries
        self.backend.home = [dict(Id=i) for i in range(101)]
        self.fresh_session()
        with self.assertRaises(Refused):
            self.operator.show()
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.assertEqual(len(self.backend.writes), 4)

    def test_show_pending_create_and_delete_preserve_v1_journal(self):
        self.backend.lose = 'create'
        with self.assertRaises(Refused):
            self.apply()
        before = (self.directory / 'journal.json').read_bytes()
        self.fresh_session()
        self.assertEqual(self.operator.show()['reconcile'], {'state': 'observed-applied'})
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.backend.lose = None
        self.operator.apply('first', ['m1'])
        self.backend.lose = 'delete'
        self.fresh_session()
        preview = self.operator.preview(['m1'])
        with self.assertRaises(Refused):
            self.operator.remove('remove', preview['baseline_digest'])
        before = (self.directory / 'journal.json').read_bytes()
        self.fresh_session()
        result = self.operator.show()
        self.assertEqual(result['playlists'], [])
        self.assertEqual(result['pending'], {'operation': 'delete'})
        self.assertEqual(result['reconcile'], {'state': 'observed-applied'})
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)

    def test_full_lifecycle_routes_grant_and_exact_inverse(self):
        before = copy.deepcopy(self.backend.home)
        self.assertEqual(self.apply()['writes'], 4)
        self.assertEqual(self.backend.grant, 'Read')
        self.assertEqual(self.backend.other_grant, 'ManageDelete')
        row = copy.deepcopy(self.backend.home[-1])
        self.fresh_session()
        self.assertEqual(self.apply('update', ['m2', 'm1'])['writes'], 2)
        self.assertEqual(self.backend.home[-1], row)
        self.assertEqual([r['Id'] for r in self.backend.entries], ['m2', 'm1'])
        self.fresh_session()
        preview = self.operator.preview(['m1'])
        self.assertEqual(self.operator.remove('remove', preview['baseline_digest'])['writes'], 2)
        self.assertEqual(self.backend.home, before)
        self.assertEqual(self.backend.playlists, [])
        self.assertEqual([w[0] for w in self.backend.writes],
                         ['create', 'share', 'row', 'add', 'clear', 'add', 'unrow', 'delete'])
        self.assertEqual(self.operator.reconcile(), {'state': 'clean'})

    def test_admin_own_home_preserves_manage_delete_without_share(self):
        with patch(__name__ + '.R', A):
            self.fresh_session()
            self.assertEqual(self.apply()['writes'], 3)
            self.assertNotIn('share', [w[0] for w in self.backend.writes])
            self.assertEqual(self.backend.grant, 'None')
            self.assertFalse(any('/Users/ItemAccess?' in call[1] for call in self.http.calls))
            p = self.operator.preview(['m1'])
            self.assertEqual(self.operator.remove('self-remove', p['baseline_digest'])['writes'], 2)
            self.assertEqual(self.backend.playlists, [])
            self.assertEqual(self.backend.home, [{'Id': 1, 'Unknown': {'retain': [9]}}])

    def test_admin_own_home_requires_explicit_edit_delete_capabilities(self):
        with patch(__name__ + '.R', A):
            self.fresh_session()
            self.apply()
            before = len(self.backend.writes)
            for key in ('CanDelete', 'CanEditItems'):
                for value in (False, None, 1, 'true'):
                    self.backend.bad_item = {key: value}
                    with self.assertRaises(Refused):
                        self.operator.preview(['m1'])
                    self.assertEqual(len(self.backend.writes), before)

    def test_read_grant_normalization_is_not_an_acl_privacy_claim(self):
        self.apply()
        with self.journal.locked():
            s = self.api.snapshot(R, self.operator.marker)
            self.assertEqual(s['playlists'][0]['access'], [R])
            self.assertEqual(s['playlists'][0]['owner'], A)
            self.backend.other_grant = 'Read'
            self.assertEqual(self.api.snapshot(R, self.operator.marker), s)
            self.backend.grant = 'ManageDelete'
            with self.assertRaises(Refused):
                self.api.snapshot(R, self.operator.marker)

    def test_unexplained_marker_and_ambiguous_discovery_refuse(self):
        self.backend.playlists = [{'Id': 'p1', 'Name': self.operator.marker, 'Type': 'Playlist'}]
        with self.assertRaisesRegex(Refused, 'Unexplained'):
            self.operator.preview(['m1'])
        self.backend.playlists.append({'Id': 'p2', 'Name': self.operator.marker, 'Type': 'Playlist'})
        with self.assertRaises(Refused):
            self.operator.preview(['m1'])
        self.assertEqual(self.backend.writes, [])

    def test_wrong_exact_id_type_marker_capability_and_truncation_refuse(self):
        self.apply()
        for bad in [{'Id': 'p2'}, {'Type': 'Movie'}, {'Name': 'manual'},
                    {'CanEditItems': False}, {'CanManageAccess': False}]:
            self.backend.bad_item = bad
            with self.subTest(bad=bad), self.assertRaises(Refused):
                self.operator.reconcile()
        self.backend.bad_item = {}
        self.backend.discovery_extra = 1
        with self.assertRaises(Refused):
            self.operator.reconcile()
        self.assertEqual(len(self.backend.writes), 4)

    def test_rename_delete_membership_and_home_drift_never_overwritten(self):
        self.apply()
        self.backend.playlists[0]['Name'] = 'manual'
        with self.assertRaises(Refused):
            self.operator.reconcile()
        self.backend.playlists[0]['Name'] = self.operator.marker
        saved = self.backend.playlists
        self.backend.playlists = []
        with self.assertRaises(Refused):
            self.operator.reconcile()
        self.backend.playlists = saved
        self.backend.entries[0]['PlaylistItemId'] = 'manual'
        self.assertEqual(self.operator.reconcile(), {'state': 'drift'})
        self.backend.home[0]['Manual'] = True
        with self.assertRaises(Refused):
            self.operator.remove('remove', 'anything')
        self.assertEqual(len(self.backend.writes), 4)

    def test_restricted_movie_absent_before_any_write(self):
        self.backend.visible = ['m1']
        with self.assertRaises(Refused):
            self.apply(movies=['m1', 'm2'])
        self.assertEqual(self.backend.writes, [])
        self.assertFalse((self.directory / 'journal.json').exists())

    def test_lost_create_response_reconciles_once_in_new_authenticated_session(self):
        self.backend.lose = 'create'
        with self.assertRaises(Refused):
            self.apply()
        before = (self.directory / 'journal.json').read_bytes()
        self.fresh_session()
        self.assertEqual(self.operator.reconcile(), {'state': 'observed-applied', 'operation': 'create'})
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.backend.lose = None
        self.operator.apply('first', ['m1'])
        self.assertEqual([w[0] for w in self.backend.writes].count('create'), 1)

    def test_presend_get_failure_can_resume_without_duplicate_share(self):
        original = self.http.open
        def fail_share_preflight(request, *args, **kwargs):
            pending = self.journal.load().get('pending') if self.journal.load() else None
            if pending and pending['operation'] == 'share' and request.get_method() == 'GET':
                raise OSError('synthetic GET failure before mutation')
            return original(request, *args, **kwargs)
        with patch.object(self.http, 'open', side_effect=fail_share_preflight):
            with self.assertRaises(adapter.NotDispatched):
                self.apply()
        with self.journal.locked():
            self.assertIsNone(self.journal.load()['pending'])
        self.assertEqual([w[0] for w in self.backend.writes], ['create'])
        self.fresh_session()
        self.operator.apply('first', ['m1'])
        self.assertEqual([w[0] for w in self.backend.writes].count('share'), 1)
        self.assertEqual([w[0] for w in self.backend.writes].count('create'), 1)

    def test_presend_post_budget_refusal_can_resume(self):
        original = self.api._http
        def exhaust_before_post(method, path, *args, **kwargs):
            if method == 'POST' and path == '/Playlists':
                self.api._requests = adapter.MAX_REQUESTS
            return original(method, path, *args, **kwargs)
        with patch.object(self.api, '_http', side_effect=exhaust_before_post):
            with self.assertRaises(adapter.NotDispatched):
                self.apply()
        with self.journal.locked():
            self.assertIsNone(self.journal.load()['pending'])
        self.assertEqual(self.backend.writes, [])
        self.fresh_session()
        self.operator.apply('first', ['m1'])
        self.assertEqual([w[0] for w in self.backend.writes].count('create'), 1)

    def test_uncertain_unchanged_create_is_never_retried(self):
        self.backend.fail_before = 'create'
        with self.assertRaises(Refused):
            self.apply()
        self.fresh_session()
        self.assertEqual(self.operator.reconcile(), {'state': 'unresolved', 'operation': 'create'})
        self.backend.fail_before = None
        with self.assertRaises(Refused):
            self.operator.apply('first', ['m1'])
        self.assertEqual([w[0] for w in self.backend.writes], ['create'])

    def test_direct_mutations_cannot_adopt_foreign_ids_or_change_intent(self):
        self.apply()
        with self.journal.locked():
            for method, args in [('delete_owned_playlist', ('other', self.operator.marker, A)),
                                 ('add_movies', ('p1', A, ['m2'])),
                                 ('delete_row', (R, '2')), ('share_read', ('p1', R))]:
                with self.subTest(method=method), self.assertRaises(Refused):
                    getattr(self.api, method)(*args)
        self.assertEqual(len(self.backend.writes), 4)

    def test_admin_session_revoked_before_update(self):
        self.apply()
        self.backend.admin = False
        with self.assertRaises(Refused):
            self.apply('second', ['m2'])
        self.assertEqual(len(self.backend.writes), 4)

    def title(self, key='title', title='Movies for Igor'):
        preview = self.operator.display_title_preview(title)
        return self.operator.set_display_title(key, title, preview['baseline_digest'])

    def test_display_title_requires_retained_owned_row(self):
        with self.assertRaises(Refused):
            self.operator.display_title_preview('Movies for Igor')
        self.assertFalse((self.directory / 'journal.json').exists())
        self.assertEqual(self.backend.writes, [])

    def test_display_title_one_write_exact_footprint_and_zero_write_replay(self):
        self.apply(movies=['m2', 'm1'])
        before = copy.deepcopy((self.backend.home, self.backend.playlists, self.backend.entries))
        self.backend.bad_item = {'RunTimeTicks': 0, 'Overview': 'preserved',
                                 'ProviderIds': {'Synthetic': 'retained'}, 'LockData': True}
        metadata = copy.deepcopy(self.backend.bad_item)
        self.assertEqual(self.title(), {'state': 'display-title-set', 'writes': 1})
        after_home = copy.deepcopy(before[0])
        after_home[-1].update(CustomName='Movies for Igor', Name='Movies for Igor')
        self.assertEqual(self.backend.home, after_home)
        self.assertEqual((self.backend.playlists, self.backend.entries), before[1:])
        self.assertEqual(self.backend.bad_item, metadata)
        op, path, query, body = self.backend.writes[-1]
        self.assertEqual((op, path, query), ('display-title', '/Users/' + R + '/HomeSections', {}))
        self.assertEqual(body, dict(adapter.normalize_home(before[0])[-1], CustomName='Movies for Igor'))
        offset = len(self.http.calls)
        self.fresh_session()
        self.assertEqual(self.operator.set_display_title('title', 'Movies for Igor'),
                         {'state': 'replayed', 'writes': 0})
        self.assertEqual([c[1] for c in self.http.calls[offset:] if c[0] == 'POST'],
                         ['/emby/Users/AuthenticateByName'])
        result = self.operator.show()
        self.assertEqual(result['marker'], self.operator.marker)
        self.assertEqual(result['playlist_name'], self.operator.marker)
        self.assertEqual(result['playlist_expected_name'], self.operator.marker)
        self.assertEqual(result['display_title'], 'Movies for Igor')
        self.assertEqual(result['row']['observed'][0]['CustomName'], 'Movies for Igor')
        with self.assertRaises(Refused):
            self.operator.set_display_title('title', 'Changed')

    def test_display_title_preview_binds_intent_and_is_readonly(self):
        self.apply()
        before = (self.directory / 'journal.json').read_bytes()
        one = self.operator.display_title_preview('One')
        two = self.operator.display_title_preview('Two')
        self.assertNotEqual(one['baseline_digest'], two['baseline_digest'])
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        with self.assertRaises(Refused):
            self.operator.set_display_title('title', 'Two', one['baseline_digest'])
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        for bad in ('', 'x' * 257, 'bad\n', None, 9):
            with self.subTest(title=bad), self.assertRaises(Refused):
                self.operator.display_title_preview(bad)

    def test_display_title_pending_before_readonly_no_retry_and_key_guard(self):
        self.apply()
        self.backend.fail_before = 'display-title'
        with self.assertRaises(Refused):
            self.title()
        before = (self.directory / 'journal.json').read_bytes()
        self.fresh_session()
        self.assertEqual(self.operator.show()['reconcile'], {'state': 'unresolved'})
        self.assertEqual(self.operator.reconcile(), {'state': 'unresolved', 'operation': 'display-title'})
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.backend.fail_before = None
        for key, title in [('other', 'Movies for Igor'), ('title', 'Different')]:
            with self.assertRaises(Refused):
                self.operator.set_display_title(key, title)
        with self.assertRaises(Refused):
            self.operator.remove('remove')
        with self.assertRaises(Refused):
            self.operator.set_display_title('title', 'Movies for Igor')
        self.assertEqual([w[0] for w in self.backend.writes].count('display-title'), 1)
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)

    def test_display_title_lost_response_after_readonly_then_completion(self):
        self.apply()
        self.backend.lose = 'display-title'
        with self.assertRaises(Refused):
            self.title()
        before = (self.directory / 'journal.json').read_bytes()
        self.fresh_session()
        self.assertEqual(self.operator.show()['reconcile'], {'state': 'observed-applied'})
        self.assertEqual(self.operator.reconcile(), {'state': 'observed-applied', 'operation': 'display-title'})
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.backend.lose = None
        self.assertEqual(self.operator.set_display_title('title', 'Movies for Igor')['writes'], 0)
        self.assertEqual(self.operator.show()['display_title'], 'Movies for Igor')
        self.assertEqual([w[0] for w in self.backend.writes].count('display-title'), 1)

    def test_display_title_pending_accepts_only_exact_before_or_after(self):
        self.apply()
        self.backend.lose = 'display-title'
        with self.assertRaises(Refused):
            self.title()
        saved = copy.deepcopy((self.backend.home, self.backend.entries))
        for mutate in (lambda: self.backend.home[-1].update(Name='wrong'),
                       lambda: self.backend.home[-1].update(CustomName='wrong'),
                       lambda: self.backend.home[-1].update(ParentId='foreign'),
                       lambda: self.backend.home[-1].update(Id='foreign'),
                       lambda: self.backend.home.reverse(),
                       lambda: self.backend.entries[0].update(PlaylistItemId='other')):
            self.backend.home, self.backend.entries = copy.deepcopy(saved)
            mutate()
            with self.assertRaises(Refused):
                self.operator.show()
            with self.assertRaises(Refused):
                self.operator.set_display_title('title', 'Movies for Igor')
        self.assertEqual([w[0] for w in self.backend.writes].count('display-title'), 1)

    def test_display_title_presend_failure_retains_run_then_resume(self):
        self.apply()
        original = self.http.open
        def fail(request, *args, **kwargs):
            d = self.journal.load()
            if d and d['pending'] and d['pending']['operation'] == 'display-title':
                raise OSError('pre-send GET failure')
            return original(request, *args, **kwargs)
        with patch.object(self.http, 'open', side_effect=fail):
            with self.assertRaises(adapter.NotDispatched):
                self.title()
        with self.journal.locked():
            d = self.journal.load()
            self.assertIsNone(d['pending'])
            self.assertEqual(d['run']['key'], 'title')
        self.assertNotIn('display-title', [w[0] for w in self.backend.writes])
        self.fresh_session()
        self.assertEqual(self.operator.set_display_title('title', 'Movies for Igor')['writes'], 1)

    def test_display_title_manual_drift_and_unknown_fixed_fields_refuse(self):
        self.apply()
        p = self.operator.display_title_preview('Movies for Igor')
        saved = copy.deepcopy((self.backend.home, self.backend.playlists, self.backend.entries))
        for mutate in (lambda: self.backend.home[-1].update(CustomName='manual'),
                       lambda: self.backend.home[-1].update(ParentId='foreign'),
                       lambda: self.backend.home[-1].update(Id='foreign'),
                       lambda: self.backend.playlists[0].update(Name='manual'),
                       lambda: self.backend.entries[0].update(PlaylistItemId='foreign')):
            self.backend.home, self.backend.playlists, self.backend.entries = copy.deepcopy(saved)
            mutate()
            with self.assertRaises(Refused):
                self.operator.set_display_title('title', 'Movies for Igor', p['baseline_digest'])
        self.backend.home, self.backend.playlists, self.backend.entries = copy.deepcopy(saved)
        # Even a retained unknown stable field refuses rather than being dropped.
        self.backend.home[-1]['UnknownFutureField'] = {'preserve': True}
        with self.journal.locked():
            d = self.journal.load()
            d['current']['home'] = adapter.normalize_home(self.backend.home)
            self.journal.save(d)
        with self.assertRaisesRegex(Refused, 'Unsupported fixed row fields'):
            self.operator.display_title_preview('Movies for Igor')
        self.assertNotIn('display-title', [w[0] for w in self.backend.writes])

    def test_display_title_foreign_recipient_and_body_refused_presend(self):
        self.apply()
        self.backend.fail_before = 'display-title'
        with self.assertRaises(Refused):
            self.title()
        with self.journal.locked():
            body = self.journal.load()['pending']['args']
            for recipient, change in [(A, {}), (R, {'Id': 'foreign'}),
                                      (R, {'ParentId': 'foreign'}), (R, {'Name': 'wrong'}),
                                      (R, {'Unknown': 1})]:
                with self.assertRaises(adapter.NotDispatched):
                    self.api.set_row_display_title(recipient, dict(body, **change))
        self.assertEqual([w[0] for w in self.backend.writes].count('display-title'), 1)

    def test_display_title_remove_recreate_resets_expectations(self):
        self.apply()
        self.fresh_session()
        self.title()
        self.fresh_session()
        self.assertEqual(self.apply('update', ['m2'])['writes'], 2)
        self.assertEqual(self.operator.show()['display_title'], 'Movies for Igor')
        self.fresh_session()
        p = self.operator.preview(['m1'])
        self.operator.remove('remove', p['baseline_digest'])
        self.assertEqual(self.operator.show()['display_title'], self.operator.marker)
        self.fresh_session()
        self.assertEqual(self.apply('recreate')['writes'], 4)
        result = self.operator.show()
        self.assertEqual(result['playlist_name'], self.operator.marker)
        self.assertEqual(result['row']['saved']['CustomName'], self.operator.marker)

    def test_display_title_collision_does_not_strand_owned_removal(self):
        self.backend.home.append({'Id': 'manual', 'Name': 'Movies for Igor',
                                  'CustomName': 'Movies for Igor',
                                  'SectionType': 'playlist', 'ParentId': 'manual-playlist'})
        baseline = copy.deepcopy(self.backend.home)
        self.apply()
        self.fresh_session()
        self.assertEqual(self.title()['writes'], 1)
        self.fresh_session()
        p = self.operator.preview(['m1'])
        self.assertEqual(self.operator.remove('remove-collision', p['baseline_digest'])['writes'], 2)
        self.assertEqual(self.backend.home, baseline)
        self.assertEqual(self.backend.playlists, [])
        self.assertEqual([w[0] for w in self.backend.writes][-2:], ['unrow', 'delete'])
        with self.journal.locked():
            d = self.journal.load()
            self.assertIsNone(d['pending'])
            self.assertIsNone(d['run'])
            self.assertEqual(d['current'], d['baseline'])

    def test_display_title_admin_own_home_no_new_share(self):
        with patch(__name__ + '.R', A):
            self.fresh_session()
            self.apply()
            self.assertEqual(self.title()['writes'], 1)
            self.assertNotIn('share', [w[0] for w in self.backend.writes])

    def test_display_title_complete_entry_holds_same_lock_through_final_snapshot(self):
        self.apply()
        preview = self.operator.display_title_preview('Movies for Igor')
        request = dict(key='title', title='Movies for Igor', baseline_digest=preview['baseline_digest'])
        entry = EntryTests().entry()
        original = adapter.OperatorAdapter.snapshot
        seen = []
        def snapshot(api, recipient, marker):
            self.assertIs(api._journal, self.journal)
            self.assertTrue(self.journal._locked)
            result = original(api, recipient, marker)
            seen.append(copy.deepcopy(result))
            return result
        def make_api(c, journal):
            self.assertIs(journal, self.journal)
            api = adapter.OperatorAdapter(c, journal)
            api._opener = self.http
            return api
        # Entry closes the actual adapter; keep the shared journal for assertions.
        with patch.object(entry, 'load_config', return_value=config()), \
             patch.object(entry, 'private_read', return_value=json.dumps(request)), \
             patch.object(entry, 'load_credentials', return_value={'username': 'synthetic', 'password': 'synthetic'}), \
             patch.object(entry, 'Journal', return_value=self.journal), \
             patch.object(entry, 'OperatorAdapter', side_effect=make_api), \
             patch.object(self.journal, 'close'), \
             patch.object(adapter.OperatorAdapter, 'snapshot', snapshot), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(entry.main(['--config', '/synthetic/config', 'set-display-title',
                                        '--request-file', '/synthetic/request']), 0)
        self.assertEqual(json.loads(output.getvalue()), {'state': 'display-title-set', 'writes': 1})
        self.assertEqual(seen[-1]['home'][-1]['CustomName'], 'Movies for Igor')
        with self.journal.locked():
            self.assertEqual(self.journal.load()['current'], seen[-1])

    def test_item_metadata_post_is_outside_management_transport(self):
        # Recorder refuses the metadata route even if a future caller tries it.
        request = urllib.request.Request('https://synthetic.invalid/emby/Items/p1',
                                         data=b'{}', method='POST')
        with self.assertRaisesRegex(AssertionError, 'metadata POST'):
            self.http.open(request, timeout=1)


class JournalCustodyTests(unittest.TestCase):
    setUp = CustodyTests.setUp

    def test_held_directory_survives_ancestor_rename_without_redirecting_writes(self):
        directory = self.root / 'journal'
        directory.mkdir(mode=0o700)
        journal = Journal(directory)
        self.addCleanup(journal.close)
        moved = self.root / 'moved'
        directory.rename(moved)
        directory.mkdir(mode=0o700)
        with journal.locked():
            journal.save({'held': True})
            self.assertEqual(journal.load(), {'held': True})
        self.assertFalse((directory / 'journal.json').exists())
        self.assertEqual(json.loads((moved / 'journal.json').read_text()), {'held': True})

    def test_journal_directory_and_leaf_symlinks_refused(self):
        directory = self.root / 'journal'
        directory.mkdir(mode=0o700)
        link = self.root / 'alias'
        link.symlink_to(directory, target_is_directory=True)
        with self.assertRaises(OSError):
            Journal(link)
        journal = Journal(directory)
        self.addCleanup(journal.close)
        (directory / 'journal.json').symlink_to(self.file)
        with journal.locked(), self.assertRaises(OSError):
            journal.load()

    def test_root_safe_journal_refuses_untrusted_chain(self):
        directory = self.root / 'journal'
        directory.mkdir(mode=0o700)
        # Mode violation is real even if the job happens to be root.
        self.root.chmod(0o777)
        with self.assertRaises(Refused):
            Journal(directory, root_safe=True)




class EntryTests(unittest.TestCase):
    def entry(self):
        path = Path(__file__).with_name('emby-operator-recommendations.py')
        spec = importlib.util.spec_from_file_location('operator_entry_success', path)
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        return entry

    def test_entry_routes_each_request_through_one_root_safe_journal_and_fresh_auth(self):
        from unittest.mock import MagicMock
        cases = [('preview', {'movies': ['m1']}),
                 ('apply', {'key': 'one', 'movies': ['m1'], 'baseline_digest': 'digest'}),
                 ('remove', {'key': 'two', 'baseline_digest': 'digest'}), ('reconcile', {}), ('show', {}),
                 ('display-title-preview', {'title': 'Movies for Igor'}),
                 ('set-display-title', {'key': 'title', 'title': 'Movies for Igor',
                                        'baseline_digest': 'digest'})]
        for operation, request in cases:
            with self.subTest(operation=operation):
                entry = self.entry()
                credentials = {'username': 'synthetic', 'password': 'synthetic'}
                journal, api, operator = MagicMock(), MagicMock(), MagicMock()
                getattr(operator, operation.replace('-', '_')).return_value = {'state': 'synthetic-success'}
                with patch.object(entry, 'load_config', return_value=config()), \
                     patch.object(entry, 'private_read', return_value=json.dumps(request)), \
                     patch.object(entry, 'load_credentials', return_value=credentials), \
                     patch.object(entry, 'Journal', return_value=journal) as make_journal, \
                     patch.object(entry, 'OperatorAdapter', return_value=api) as make_api, \
                     patch.object(entry, 'Operator', return_value=operator) as make_operator, \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(entry.main(['--config', '/synthetic/config', operation,
                                                 '--request-file', '/synthetic/request']), 0)
                make_journal.assert_called_once_with('/synthetic/journal', root_safe=True)
                make_api.assert_called_once_with(config(), journal)
                self.assertIs(make_operator.call_args.args[1], journal)
                api.authenticate.assert_called_once()
                self.assertEqual(credentials, {})
                getattr(operator, operation.replace('-', '_')).assert_called_once_with(**request)
                api.close.assert_called_once()
                journal.close.assert_called_once()
                self.assertEqual(json.loads(output.getvalue()), {'state': 'synthetic-success'})


if __name__ == '__main__':
    unittest.main()
