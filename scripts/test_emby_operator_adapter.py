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
            if before.get('Subtitle') == 'operator-provisioned' and body.get('Subtitle') == '':
                expected['Subtitle'] = ''
            assert body == expected, 'Only the title and exact old subtitle may change'
            rows[0].update(CustomName=body['CustomName'], Name=body['CustomName'])
            if 'Subtitle' in expected:
                rows[0]['Subtitle'] = expected['Subtitle']
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

    def retain_subtitle(self, subtitle, title='Movies for Igor'):
        # Model a retained, journal-owned row, not manual unjournaled drift.
        self.apply(movies=['m2', 'm1'])
        self.backend.home[-1].update(Subtitle=subtitle, CustomName=title, Name=title)
        with self.journal.locked():
            d = self.journal.load()
            d['display_title'] = title
            d['current']['home'] = adapter.normalize_home(self.backend.home)
            self.journal.save(d)

    def test_display_title_same_title_legacy_subtitle_cleanup_and_replay(self):
        self.retain_subtitle('operator-provisioned')
        before = copy.deepcopy((self.backend.home, self.backend.playlists, self.backend.entries))
        self.assertEqual(self.title(), {'state': 'display-title-set', 'writes': 1})
        expected = copy.deepcopy(before[0])
        expected[-1]['Subtitle'] = ''
        self.assertEqual(self.backend.home, expected)
        self.assertEqual((self.backend.playlists, self.backend.entries), before[1:])
        self.assertEqual(self.backend.writes[-1][3],
                         dict(adapter.normalize_home(before[0])[-1], Subtitle=''))
        offset = len(self.http.calls)
        self.fresh_session()
        self.assertEqual(self.operator.set_display_title('title', 'Movies for Igor'),
                         {'state': 'replayed', 'writes': 0})
        self.assertEqual([c[1] for c in self.http.calls[offset:] if c[0] == 'POST'],
                         ['/emby/Users/AuthenticateByName'])
        self.assertEqual([w[0] for w in self.backend.writes].count('display-title'), 1)

    def test_new_row_blank_subtitle_and_custom_subtitle_preserved(self):
        self.retain_subtitle('My own subtitle')
        row_create = next(w[3] for w in self.backend.writes if w[0] == 'row')
        self.assertEqual(row_create['Subtitle'], '')
        before = copy.deepcopy(self.backend.home)
        self.assertEqual(self.title(title='Weekend movies')['writes'], 1)
        before[-1].update(CustomName='Weekend movies', Name='Weekend movies')
        self.assertEqual(self.backend.home, before)
        self.assertEqual(self.backend.writes[-1][3]['Subtitle'], 'My own subtitle')
        self.fresh_session()
        self.assertEqual(self.title(key='same-custom', title='Weekend movies')['writes'], 0)

    def test_subtitle_cleanup_uncertain_before_never_retries(self):
        self.retain_subtitle('operator-provisioned')
        self.backend.fail_before = 'display-title'
        with self.assertRaises(Refused):
            self.title()
        before = (self.directory / 'journal.json').read_bytes()
        self.backend.fail_before = None
        self.fresh_session()
        self.assertEqual(self.operator.show()['reconcile'], {'state': 'unresolved'})
        self.assertEqual(self.operator.reconcile()['state'], 'unresolved')
        with self.assertRaises(Refused):
            self.operator.set_display_title('title', 'Movies for Igor')
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.assertEqual([w[0] for w in self.backend.writes].count('display-title'), 1)

    def test_subtitle_cleanup_lost_response_exact_readback_and_drift_refusal(self):
        self.retain_subtitle('operator-provisioned')
        self.backend.lose = 'display-title'
        with self.assertRaises(Refused):
            self.title()
        before = (self.directory / 'journal.json').read_bytes()
        self.fresh_session()
        self.assertEqual(self.operator.show()['reconcile'], {'state': 'observed-applied'})
        self.assertEqual(self.operator.reconcile()['state'], 'observed-applied')
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        # The exact before state remains unresolved; no third subtitle is accepted.
        for subtitle in ('manual drift', None, 'Operator-provisioned', 'operator-provisioned '):
            self.backend.home[-1]['Subtitle'] = subtitle
            with self.subTest(subtitle=subtitle):
                with self.assertRaises(Refused):
                    self.operator.show()
                self.assertEqual(self.operator.reconcile()['state'], 'unresolved')
                with self.assertRaises(Refused):
                    self.operator.set_display_title('title', 'Movies for Igor')
                self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.backend.home[-1]['Subtitle'] = ''
        self.backend.lose = None
        self.fresh_session()
        self.assertEqual(self.operator.set_display_title('title', 'Movies for Igor')['writes'], 0)
        self.assertEqual([w[0] for w in self.backend.writes].count('display-title'), 1)

    def test_display_title_subtitle_drift_before_dispatch_refuses(self):
        self.retain_subtitle('operator-provisioned')
        preview = self.operator.display_title_preview('Movies for Igor')
        before = (self.directory / 'journal.json').read_bytes()
        self.backend.home[-1]['Subtitle'] = 'manual drift'
        with self.assertRaises(Refused):
            self.operator.set_display_title('title', 'Movies for Igor', preview['baseline_digest'])
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.assertNotIn('display-title', [w[0] for w in self.backend.writes])

    def test_display_title_adapter_refuses_arbitrary_subtitle_change(self):
        self.retain_subtitle('My own subtitle')
        self.backend.fail_before = 'display-title'
        with self.assertRaises(Refused):
            self.title(title='Weekend movies')
        with self.journal.locked():
            d = self.journal.load()
            d['pending']['args']['Subtitle'] = ''
            self.journal.save(d)
            with self.assertRaises(adapter.NotDispatched):
                self.api.set_row_display_title(R, d['pending']['args'])
        self.assertEqual([w[0] for w in self.backend.writes].count('display-title'), 1)

    def test_historical_pending_subtitle_retained_exactly_without_second_dispatch(self):
        self.retain_subtitle('operator-provisioned', title=self.operator.marker)
        # Preserve a v1 journal without the optional title expectation.
        with self.journal.locked():
            d = self.journal.load()
            del d['display_title']
            self.journal.save(d)
        self.backend.fail_before = 'display-title'
        with self.assertRaises(Refused):
            self.title()
        with self.journal.locked():
            d = self.journal.load()
            d['pending']['args']['Subtitle'] = 'operator-provisioned'
            self.journal.save(d)
        before = (self.directory / 'journal.json').read_bytes()
        self.fresh_session()
        self.assertEqual(self.operator.show()['reconcile'], {'state': 'unresolved'})
        with self.assertRaises(Refused):
            self.operator.set_display_title('title', 'Movies for Igor')
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        # Model the old pending body having reached the server with a lost response.
        self.backend.home[-1].update(CustomName='Movies for Igor', Name='Movies for Igor')
        self.assertEqual(self.operator.show()['reconcile'], {'state': 'observed-applied'})
        self.assertEqual(self.operator.reconcile()['state'], 'observed-applied')
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.backend.home[-1]['Subtitle'] = ''
        with self.assertRaises(Refused):
            self.operator.show()
        with self.assertRaises(Refused):
            self.operator.set_display_title('title', 'Movies for Igor')
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.backend.home[-1]['Subtitle'] = 'operator-provisioned'
        self.backend.fail_before = None
        with patch.object(self.journal, 'save', wraps=self.journal.save) as save:
            self.assertEqual(self.operator.set_display_title('title', 'Movies for Igor')['writes'], 0)
        # No durable gap between clearing pending and completing the request.
        self.assertEqual(save.call_count, 1)
        self.assertIsNone(save.call_args.args[0]['pending'])
        self.assertIsNone(save.call_args.args[0]['run'])
        self.assertIn('title', save.call_args.args[0]['done'])
        self.assertEqual(self.backend.home[-1]['Subtitle'], 'operator-provisioned')
        self.assertEqual([w[0] for w in self.backend.writes].count('display-title'), 1)
        self.fresh_session()
        self.assertEqual(self.operator.set_display_title('title', 'Movies for Igor')['writes'], 0)
        self.assertEqual(self.title(key='cleanup')['writes'], 1)
        self.assertEqual(self.backend.home[-1]['Subtitle'], '')

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
        self.retain_subtitle('operator-provisioned')
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


EMPTY_QUERY = {k: [] for k in ('StudioIds', 'TagIds', 'GenreIds', 'CollectionTypes')}


class MultiBackend:
    """Keyed multi-playlist recording DTO model, not live Emby evidence.

    Synthetic IDs only. A separate recipient is the worst-case transport path.
    """
    def __init__(self):
        self.home = [{'Id': 1, 'Unknown': {'retain': [9]}}]
        self.items = {}
        self.visible = ['m%d' % n for n in range(1, 31)]
        self.serial = 0
        self.writes = []
        self.lose = self.fail_before = None
        self.journal = None

    @staticmethod
    def envelope(rows):
        return {'Items': copy.deepcopy(rows), 'TotalRecordCount': len(rows)}

    def respond(self, call):
        from urllib.parse import urlsplit, parse_qs
        method, url, headers, body = call
        u = urlsplit(url)
        path, q = u.path.removeprefix('/emby'), parse_qs(u.query)
        if path == '/Users/AuthenticateByName':
            return auth_response()
        assert next(v for k, v in headers.items() if k.lower() == 'x-emby-token') == 'synthetic-session-token'
        if method == 'GET':
            return self.get(path, q)
        pending = self.journal.load()['pending']
        assert pending is not None, 'Intent precedes every POST'
        op = pending['operation']
        self.writes.append((op, pending.get('list'), path, copy.deepcopy(body)))
        if self.fail_before == op:
            return (500, b'uncertain dispatch')
        self.post(op, path, q, body, pending['args'])
        return (500, b'no response body disclosure') if self.lose == op else (204, b'')

    def get(self, path, q):
        admin = {A: True, R: False}
        parts = path.split('/')
        if path == '/Users':
            return [{'Id': i} for i in admin]
        if path in ('/Users/' + A, '/Users/' + R):
            return {'Id': parts[-1], 'ServerId': S,
                    'Policy': {'IsDisabled': False, 'IsAdministrator': admin[parts[-1]]}}
        if path == '/Library/VirtualFolders':
            return [{'ItemId': '7', 'Guid': G, 'CollectionType': 'movies'}]
        if path == '/Users/' + R + '/Views':
            return self.envelope([{'Id': '7'}])
        if path == '/Users/' + R + '/HomeSections':
            return copy.deepcopy(self.home)
        if path == '/Users/' + R + '/Items':
            assert q['ParentId'] == ['7'] and q['IncludeItemTypes'] == ['Movie']
            return self.envelope([{'Id': i, 'Type': 'Movie'} for i in q['Ids'][0].split(',')
                                  if i in self.visible])
        if path == '/Users/' + A + '/Items':
            assert q['IncludeItemTypes'] == ['Playlist']
            return self.envelope([{'Id': k, 'Type': 'Playlist', 'Name': v['Name']}
                                  for k, v in self.items.items()])
        if path == '/Users/ItemAccess':
            return self.envelope([{'Id': A, 'ServerId': S, 'UserItemShareLevel': 'ManageDelete'},
                                  {'Id': R, 'ServerId': S,
                                   'UserItemShareLevel': self.items[q['ItemId'][0]]['grant']}])
        if path.startswith('/Users/' + A + '/Items/') and parts[-1] in self.items:
            return {'Id': parts[-1], 'Type': 'Playlist', 'Name': self.items[parts[-1]]['Name'],
                    'CanEditItems': True, 'CanManageAccess': True}
        if len(parts) == 4 and parts[1] == 'Playlists' and parts[3] == 'Items' and parts[2] in self.items:
            assert q['UserId'] == [A]
            return self.envelope(self.items[parts[2]]['entries'])
        raise AssertionError('Unmodeled GET')

    def post(self, op, path, q, body, args):
        if op == 'create':
            assert path == '/Playlists' and q['userId'] == [A] and body is None
            self.serial += 1
            self.items['p%d' % self.serial] = {'Name': q['Name'][0], 'entries': [], 'grant': 'None'}
        elif op == 'share':
            assert path == '/Items/Access' and body['UserIds'] == [R] and body['ItemAccess'] == 'Read'
            self.items[body['ItemIds'][0]]['grant'] = 'Read'
        elif op == 'row':
            assert path == '/Users/' + R + '/HomeSections' and body == args
            self.serial += 1
            self.home.append(dict(body, Id='r%d' % self.serial, Name=body['CustomName'],
                                  Monitor=[], CardSizeOffset=0, Query=EMPTY_QUERY))
        elif op == 'display-title':
            assert path == '/Users/' + R + '/HomeSections' and body == args
            row = next(r for r in self.home if r['Id'] == body['Id'])
            row.update(CustomName=body['CustomName'], Name=body['CustomName'], Subtitle=body['Subtitle'])
        elif op == 'add':
            assert path.endswith('/Items') and body == {
                'Ids': ','.join(args), 'userId': A, 'SkipDuplicates': False}
            self.serial += 1
            self.items[path.split('/')[2]]['entries'] = [
                {'Id': m, 'Type': 'Movie', 'PlaylistItemId': 'e%d_%d' % (self.serial, n)}
                for n, m in enumerate(args)]
        elif op == 'clear':
            assert path.endswith('/Items/Delete') and body == {'EntryIds': ','.join(args)}
            self.items[path.split('/')[2]]['entries'] = []
        elif op == 'unrow':
            assert path == '/Users/' + R + '/HomeSections/Delete' and body == {'ids': [args]}
            self.home = [r for r in self.home if r['Id'] != args]
        elif op == 'delete':
            assert path == '/Items/Delete' and body is None
            del self.items[q['Ids'][0]]
        else:
            raise AssertionError('Unmodeled mutation')


class MultiListTests(unittest.TestCase):
    """Journal version 2 through the real adapter and loopback recording HTTP."""
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
        self.backend = MultiBackend()
        self.backend.journal = self.journal
        self.http = RecordingHTTP()
        self.http.responder = self.backend.respond
        self.http.__enter__()
        self.addCleanup(self.http.__exit__, None, None, None)
        self.fresh_session()

    fresh_session = LifecycleHTTPTests.fresh_session

    def bytes(self):
        return (self.directory / 'journal.json').read_bytes()

    def stored(self):
        return json.loads(self.bytes())

    def intent(self, operation, list_id, **args):
        self.fresh_session()
        return self.operator.list_preview(operation, list_id, **args)['intent_digest']

    def do(self, operation, key, list_id, **args):
        # Preview and mutation are separate invocations, as through the entry.
        intent = self.intent(operation, list_id, **args)
        return self.resume(operation, key, list_id, intent_digest=intent, **args)

    def resume(self, operation, key, list_id, **args):
        self.fresh_session()
        return getattr(self.operator, operation.replace('-', '_'))(key, list_id, **args)

    def v1_apply(self, key, movies):
        self.fresh_session()
        preview = self.operator.preview(movies)
        self.fresh_session()
        return self.operator.apply(key, movies, preview['baseline_digest'])

    def pid(self, list_id):
        marker = self.operator.marker_for(list_id)
        return next(k for k, v in self.backend.items.items() if v['Name'] == marker)

    def row(self, list_id):
        record = self.stored().get('lists', {}).get(list_id)
        return record and record['row']

    def sibling(self, list_id):
        pid = self.pid(list_id)
        return copy.deepcopy((pid, self.backend.items[pid],
                              [r for r in self.backend.home if r['Id'] == self.row(list_id)]))

    def others(self, row):
        return copy.deepcopy([r for r in self.backend.home if r['Id'] != row])

    def test_v1_fixture_migrates_with_identical_show_and_lossless_inverse(self):
        self.v1_apply('first', ['m2', 'm1'])
        self.fresh_session()
        preview = self.operator.display_title_preview('Movies for Igor')
        self.fresh_session()
        self.operator.set_display_title('title', 'Movies for Igor', preview['baseline_digest'])
        self.fresh_session()
        legacy = self.operator.show()
        self.assertEqual(legacy['display_title'], 'Movies for Igor')
        self.assertEqual(legacy['row']['saved']['Subtitle'], '')
        self.assertIsInstance(legacy['row']['saved_id'], str)
        self.assertIsInstance(legacy['playlists'][0]['id'], str)
        before, writes = self.bytes(), copy.deepcopy(self.backend.writes)
        offset = len(self.http.calls)
        self.fresh_session()
        migration = self.operator.migrate_preview()
        self.assertEqual(migration['lists'], ['igor-movies'])
        self.assertEqual(migration['revision'], legacy['revision'])
        self.assertEqual(self.bytes(), before)
        self.fresh_session()
        self.assertEqual(self.operator.migrate('migrate', migration['intent_digest']),
                         {'state': 'migrated', 'writes': 0})
        # Journal-only: authentication is the only POST; no management write exists.
        self.assertEqual([c[1] for c in self.http.calls[offset:] if c[0] == 'POST'],
                         ['/emby/Users/AuthenticateByName'] * 2)
        self.assertEqual(self.backend.writes, writes)
        old, new = json.loads(before), self.stored()
        self.assertEqual(new['version'], 2)
        self.assertNotIn('recipe', new['scope'])
        for field in ('baseline', 'current', 'done'):
            self.assertEqual(new[field], old[field])
        self.assertEqual(new['lists'], {'igor-movies': {'row': old['row'],
                                                        'display_title': 'Movies for Igor'}})
        self.assertEqual(new['migrated_from']['journal_digest'], digest(old))
        migrated = self.bytes()
        self.fresh_session()
        catalog = self.operator.show()
        self.assertEqual(catalog['revision'], legacy['revision'])
        [item] = catalog['lists']
        self.assertEqual((item['list'], item['legacy'], item['marker'], item['display_title']),
                         ('igor-movies', True, legacy['marker'], 'Movies for Igor'))
        self.assertEqual(item['playlist'], legacy['playlists'][0])
        self.assertEqual(item['row'], legacy['row'])
        self.assertEqual(self.operator.reconcile(), {'state': 'clean'})
        self.assertEqual(self.operator.show(list='igor-movies')['lists'], [item])
        self.fresh_session()
        self.assertEqual(self.operator.migrate('migrate', 'replay'),
                         {'state': 'already-migrated', 'writes': 0})
        self.assertEqual(self.bytes(), migrated)
        self.fresh_session()
        inverse = self.operator.downgrade_preview()
        self.fresh_session()
        self.assertEqual(self.operator.downgrade('downgrade', inverse['intent_digest']),
                         {'state': 'downgraded', 'writes': 0})
        self.assertEqual(self.bytes(), before)
        self.fresh_session()
        self.assertEqual(self.operator.show(), legacy)
        self.assertEqual(self.operator.apply('first', ['m2', 'm1']), {'state': 'replayed', 'writes': 0})
        self.assertEqual(self.backend.writes, writes)

    def test_migration_refuses_unfinished_drift_stale_foreign_and_old_digests(self):
        self.backend.lose = 'add'
        with self.assertRaises(Refused):
            self.v1_apply('first', ['m1'])
        self.backend.lose = None
        unfinished = self.bytes()
        self.fresh_session()
        with self.assertRaisesRegex(Refused, 'Unfinished'):
            self.operator.migrate_preview()
        self.assertEqual(self.bytes(), unfinished)
        self.fresh_session()
        self.operator.apply('first', ['m1'])
        self.fresh_session()
        stale = self.operator.migrate_preview()['intent_digest']
        self.v1_apply('second', ['m2'])
        before = self.bytes()
        self.fresh_session()
        current = self.operator.preview(['m1'])['baseline_digest']
        for intent in (stale, current, None, 'x'):
            with self.subTest(intent=intent), self.assertRaises(Refused):
                self.operator.migrate('migrate', intent)
        self.backend.home[0]['Manual'] = True
        self.fresh_session()
        with self.assertRaisesRegex(Refused, 'drift'):
            self.operator.migrate_preview()
        del self.backend.home[0]['Manual']
        foreign = Operator(self.api, self.journal, server=S, actor=A, recipient=R,
                           library='7', recipe='other')
        with self.assertRaisesRegex(Refused, 'Foreign journal'):
            foreign.migrate_preview()
        for call in (lambda: self.operator.list_preview('set-members', 'igor-movies', movies=['m1']),
                     lambda: self.operator.create_list('k', 'other', 'Other', [], 'x'),
                     lambda: self.operator.show(list='igor-movies'),
                     self.operator.downgrade_preview):
            with self.assertRaisesRegex(Refused, 'migration required|version 2 required'):
                call()
        self.assertEqual(self.bytes(), before)
        self.fresh_session()
        migration = self.operator.migrate_preview()
        self.fresh_session()
        self.operator.migrate('migrate', migration['intent_digest'])
        writes, migrated = copy.deepcopy(self.backend.writes), self.bytes()
        # A version 1 digest never authorizes a version 2 alias or other migration.
        for call in (lambda: self.operator.apply('third', ['m1'], current),
                     lambda: self.operator.remove('remove', current),
                     lambda: self.operator.migrate('other-key', migration['intent_digest']),
                     lambda: self.operator.apply('first', ['m1'])):
            self.fresh_session()
            with self.assertRaises(Refused):
                call()
        self.assertEqual(self.backend.writes, writes)
        self.assertEqual(self.bytes(), migrated)

    def test_two_lists_each_operation_preserves_sibling_and_home_order(self):
        self.backend.home.append({'Id': 'manual', 'Name': 'Same', 'CustomName': 'Same',
                                  'SectionType': 'playlist', 'ParentId': 'manual-playlist'})
        baseline = copy.deepcopy(self.backend.home)
        steps = [('set-members', {'movies': ['m3', 'm2']}), ('set-title', {'title': 'Other'}),
                 ('set-title', {'title': 'Same'}), ('detach-row', {}),
                 ('set-members', {'movies': []}), ('attach-row', {'title': 'Same'}),
                 ('set-members', {'movies': ['m1']}), ('delete-list', {})]
        self.assertEqual(self.do('create-list', 'a-create', 'a', title='Same',
                                 movies=['m1', 'm2'])['state'], 'created')
        for target, sibling in (('b', 'a'), ('a', 'b')):
            # Same title and same Movie in both lists; titles are not identity.
            self.do('create-list', 'b-create-' + target, 'b', title='Same', movies=['m2'])
            for n, (operation, args) in enumerate(steps):
                with self.subTest(target=target, operation=operation, n=n):
                    kept, others = self.sibling(sibling), self.others(self.row(target))
                    count = len(self.backend.writes)
                    self.do(operation, '%s-%d' % (target, n), target, **args)
                    self.assertEqual(self.sibling(sibling), kept)
                    self.assertEqual(self.others(self.row(target)), others)
                    self.assertTrue(all(w[1] == target for w in self.backend.writes[count:]))
        self.do('delete-list', 'b-final', 'b')
        self.assertEqual(self.backend.home, baseline)
        self.assertEqual(self.backend.items, {})

    def test_delete_in_any_order_restores_external_baseline(self):
        baseline = copy.deepcopy(self.backend.home)
        for n, order in enumerate(('abc', 'cab', 'bca')):
            for list_id in 'abc':
                self.do('create-list', 'create-%s-%d' % (list_id, n), list_id,
                        title='List ' + list_id, movies=['m1'])
            for list_id in order:
                self.do('delete-list', 'delete-%s-%d' % (list_id, n), list_id)
                owned = {r['row'] for r in self.stored()['lists'].values()}
                self.assertEqual([r for r in self.backend.home if r['Id'] not in owned], baseline)
            stored = self.stored()
            self.assertEqual((stored['lists'], stored['current']), ({}, stored['baseline']))
            self.assertEqual(self.backend.home, baseline)
            self.assertEqual(self.backend.items, {})

    def test_external_home_drift_refuses_every_operation_without_saving(self):
        self.do('create-list', 'create-a', 'a', title='A', movies=['m1'])
        self.do('create-list', 'create-b', 'b', title='B', movies=['m2'])
        intent = self.intent('set-members', 'a', movies=['m3'])
        before, writes = self.bytes(), copy.deepcopy(self.backend.writes)
        home = copy.deepcopy(self.backend.home)
        for mutate in (lambda: self.backend.home.append({'Id': 'manual'}),
                       lambda: self.backend.home[0].update(Unknown='manual'),
                       lambda: self.backend.home.pop(0),
                       lambda: self.backend.home.reverse(),
                       lambda: self.backend.home[-1].update(CustomName='manual')):
            self.backend.home = copy.deepcopy(home)
            mutate()
            self.fresh_session()
            self.assertEqual(self.operator.reconcile(), {'state': 'drift'})
            for call in (self.operator.show,
                         lambda: self.operator.list_preview('delete-list', 'b'),
                         lambda: self.operator.list_preview('create-list', 'c', title='C', movies=[]),
                         lambda: self.operator.set_members('k', 'a', ['m3'], intent)):
                with self.assertRaises(Refused):
                    call()
            self.assertEqual(self.bytes(), before)
            self.assertEqual(self.backend.writes, writes)
        self.backend.home = home
        self.fresh_session()
        self.assertEqual(self.operator.set_members('k', 'a', ['m3'], intent)['state'], 'members-set')

    def test_list_limit_titles_and_membership_bounds_refuse_before_writes(self):
        for list_id, movies in (('a', ['m1']), ('b', ['m1']), ('c', []), ('d', ['m1', 'm2'])):
            self.do('create-list', 'create-' + list_id, list_id, title='Same', movies=movies)
        # The same Movie may appear in different lists.
        self.assertEqual([e['Id'] for e in self.backend.items[self.pid('b')]['entries']], ['m1'])
        writes, before = copy.deepcopy(self.backend.writes), self.bytes()
        self.fresh_session()
        cases = [('create-list', 'e', {'title': 'E', 'movies': []}, 'List limit reached'),
                 ('create-list', 'a', {'title': 'A', 'movies': []}, 'List already exists'),
                 ('set-members', 'a', {'movies': ['m1', 'm1']}, 'Duplicate Movie IDs'),
                 ('set-members', 'a', {'movies': ['m%d' % n for n in range(1, 27)]}, 'Movies'),
                 ('set-members', 'e', {'movies': []}, 'Unknown list'),
                 ('set-title', 'a', {'title': self.operator.marker_for('b')}, 'Marker-shaped title'),
                 ('set-title', 'a', {'title': ''}, 'display title'),
                 ('detach-row', 'zz/..', {}, 'Invalid opaque ID'),
                 ('set-members', 'a', {'movies': ['m1'], 'title': 'x'}, 'Invalid list operation')]
        for operation, list_id, args, message in cases:
            with self.subTest(operation=operation, list_id=list_id), \
                 self.assertRaisesRegex(Refused, message):
                self.operator.list_preview(operation, list_id, **args)
        self.backend.visible.remove('m3')
        with self.assertRaisesRegex(Refused, 'Movie'):
            self.operator.list_preview('set-members', 'a', movies=['m3'])
        self.assertEqual(self.backend.writes, writes)
        self.assertEqual(self.bytes(), before)

    def test_intent_digest_binds_operation_list_and_arguments(self):
        self.do('create-list', 'create-legacy', 'igor-movies', title='Legacy', movies=['m1'])
        self.do('create-list', 'create-b', 'b', title='B', movies=['m1'])
        title = self.intent('set-title', 'igor-movies', title='X')
        self.fresh_session()
        revision = self.operator.show()['revision']
        alias = self.operator.preview(['m2'])['baseline_digest']
        # The legacy alias is exactly the set-title intent for the legacy list.
        self.assertEqual(self.operator.display_title_preview('X')['baseline_digest'], title)
        writes, before = copy.deepcopy(self.backend.writes), self.bytes()
        for call in (lambda: self.operator.set_title('k1', 'b', 'X', title),
                     lambda: self.operator.set_title('k2', 'igor-movies', 'Y', title),
                     lambda: self.operator.set_members('k3', 'igor-movies', ['m1'], title),
                     lambda: self.operator.detach_row('k4', 'igor-movies', title),
                     lambda: self.operator.delete_list('k5', 'igor-movies', title),
                     lambda: self.operator.remove('k6', alias),
                     lambda: self.operator.remove('k7', revision),
                     lambda: self.operator.apply('k8', ['m2'], revision),
                     lambda: self.operator.set_members('k9', 'b', ['m2'], alias),
                     lambda: self.operator.set_members('k10', 'igor-movies', ['m3'], alias)):
            with self.assertRaisesRegex(Refused, 'Fresh preview digest required'):
                call()
        self.assertEqual(self.backend.writes, writes)
        self.assertEqual(self.bytes(), before)
        self.assertEqual(self.operator.apply('k11', ['m2'], alias)['state'], 'members-set')
        self.fresh_session()
        self.assertEqual(self.operator.set_members('k11', 'igor-movies', ['m2'])['state'], 'replayed')
        removal = self.intent('delete-list', 'igor-movies')
        self.fresh_session()
        self.assertEqual(self.operator.remove('k12', removal)['state'], 'deleted')
        self.assertEqual([x['list'] for x in self.operator.show()['lists']], ['b'])

    def test_lost_response_each_step_completes_by_exact_readback_once(self):
        steps = [('create-list', {'title': 'Lost', 'movies': ['m1']}, lost)
                 for lost in ('create', 'share', 'row', 'display-title', 'add')]
        steps += [('set-members', {'movies': ['m2']}, 'clear'), ('detach-row', {}, 'unrow'),
                  ('delete-list', {}, 'delete')]
        self.do('create-list', 'sibling', 'b', title='B', movies=['m1'])
        for n, (operation, args, lost) in enumerate(steps):
            with self.subTest(lost=lost):
                if operation == 'create-list' and n:
                    self.do('delete-list', 'cleanup-%d' % n, 'x')
                kept, count = self.sibling('b'), len(self.backend.writes)
                self.backend.lose = lost
                with self.assertRaises(Refused):
                    self.do(operation, 'lost-%d' % n, 'x', **args)
                self.backend.lose = None
                before = self.bytes()
                self.fresh_session()
                shown = self.operator.show()
                self.assertEqual((shown['reconcile'], shown['pending']),
                                 ({'state': 'observed-applied'}, {'operation': lost, 'list': 'x'}))
                self.assertEqual(self.operator.reconcile(),
                                 {'state': 'observed-applied', 'operation': lost, 'list': 'x'})
                self.assertEqual(self.bytes(), before)
                self.resume(operation, 'lost-%d' % n, 'x', **args)
                self.assertEqual([w[0] for w in self.backend.writes[count:]].count(lost), 1)
                self.assertEqual(self.sibling('b'), kept)
        self.assertNotIn('x', self.stored()['lists'])

    def test_uncertain_unchanged_step_is_never_redispatched(self):
        self.do('create-list', 'create-a', 'a', title='A', movies=['m1'])
        self.backend.fail_before = 'display-title'
        with self.assertRaises(Refused):
            self.do('set-title', 'title', 'a', title='B')
        self.backend.fail_before = None
        before = self.bytes()
        self.fresh_session()
        self.assertEqual(self.operator.show()['reconcile'], {'state': 'unresolved'})
        self.assertEqual(self.operator.reconcile()['state'], 'unresolved')
        for call in (lambda: self.operator.set_title('title', 'a', 'B'),
                     lambda: self.operator.set_members('other', 'a', ['m2'], 'x')):
            with self.assertRaises(Refused):
                call()
        self.assertEqual(self.bytes(), before)
        self.assertEqual([w[0] for w in self.backend.writes].count('display-title'), 2)

    def test_not_dispatched_clears_only_pending_and_resumes_same_request(self):
        original = self.http.open
        def fail(request, *args, **kwargs):
            d = self.journal.load() if self.journal._locked else None
            if d and d['pending'] and d['pending']['operation'] == 'display-title':
                raise OSError('synthetic pre-send GET failure')
            return original(request, *args, **kwargs)
        with patch.object(self.http, 'open', side_effect=fail):
            with self.assertRaises(adapter.NotDispatched):
                self.do('create-list', 'create-a', 'a', title='A', movies=['m1'])
        stored = self.stored()
        self.assertIsNone(stored['pending'])
        self.assertEqual(stored['run']['key'], 'create-a')
        self.assertEqual(stored['lists'], {'a': {'row': self.backend.home[-1]['Id']}})
        self.assertEqual(self.resume('create-list', 'create-a', 'a', title='A',
                                     movies=['m1'])['writes'], 2)
        self.assertEqual([w[0] for w in self.backend.writes],
                         ['create', 'share', 'row', 'display-title', 'add'])

    def test_worst_case_fourth_list_create_fits_unchanged_transport_budget(self):
        for list_id in 'abc':
            self.do('create-list', 'create-' + list_id, list_id, title='Same', movies=['m1'])
        movies = ['m%d' % n for n in range(1, 26)]
        intent = self.intent('create-list', 'd', title='Fourth', movies=movies)
        self.fresh_session()
        self.assertEqual(self.operator.create_list('create-d', 'd', 'Fourth', movies,
                                                   intent)['writes'], 5)
        # One recorded invocation including authentication; not runtime evidence.
        self.assertEqual(adapter.MAX_REQUESTS, 512)
        self.assertLess(self.api._requests, adapter.MAX_REQUESTS)

    def test_unrecorded_markers_refuse_or_stay_ignored_without_adoption(self):
        self.do('create-list', 'create-a', 'a', title='A', movies=['m1'])
        before = self.bytes()
        stray_intent = self.intent('create-list', 'unknown', title='Unknown', movies=[])
        self.backend.items['stray'] = {'Name': self.operator.marker_for('unknown'),
                                       'entries': [], 'grant': 'None'}
        self.fresh_session()
        self.assertEqual([x['list'] for x in self.operator.show()['lists']], ['a'])
        with self.assertRaisesRegex(Refused, 'Existing playlist requires'):
            self.operator.list_preview('create-list', 'unknown', title='Unknown', movies=[])
        self.assertEqual(self.bytes(), before)
        self.fresh_session()
        with self.assertRaisesRegex(Refused, 'Existing playlist requires'):
            self.operator.create_list('stray-create', 'unknown', 'Unknown', [], stray_intent)
        self.assertEqual(self.bytes(), before)
        for name in (self.operator.marker, self.operator.marker_for('a')):
            self.backend.items['extra'] = {'Name': name, 'entries': [], 'grant': 'None'}
            self.fresh_session()
            with self.assertRaisesRegex(Refused, 'Unexplained|Ambiguous'):
                self.operator.show()
            with self.assertRaises(Refused):
                self.operator.list_preview('set-members', 'a', movies=['m2'])
        self.assertEqual(self.bytes(), before)

    def test_completed_key_ceiling_refuses_without_eviction(self):
        self.do('create-list', 'create-a', 'a', title='A', movies=['m1'])
        with self.journal.locked():
            d = self.journal.load()
            # Synthetic completed history fixture filling the unchanged ceiling.
            d['done'].update({'history-%d' % n: {'operation': 'set-members', 'list': 'a',
                                                 'movies': ['m1']} for n in range(63)})
            self.journal.save(d)
        done = self.stored()['done']
        intent = self.intent('set-members', 'a', movies=['m2'])
        self.fresh_session()
        with self.assertRaisesRegex(Refused, 'Journal request limit reached'):
            self.operator.set_members('new', 'a', ['m2'], intent)
        self.assertEqual(self.operator.set_members('history-0', 'a', ['m1']),
                         {'state': 'replayed', 'writes': 0})
        self.assertEqual(self.operator.create_list('create-a', 'a', 'A', ['m1']),
                         {'state': 'replayed', 'writes': 0})
        shown = self.operator.show()
        self.assertEqual((shown['done_count'], shown['done_limit'], shown['keys_exhausted']),
                         (64, 64, True))
        self.assertEqual(self.stored()['done'], done)

    def test_empty_membership_is_retained_and_noops_write_nothing(self):
        self.assertEqual(self.do('create-list', 'create-e', 'e', title='Empty',
                                 movies=[])['writes'], 4)
        pid = self.pid('e')
        self.assertEqual(self.backend.items[pid]['entries'], [])
        self.assertEqual(self.do('set-members', 'fill', 'e', movies=['m1', 'm2'])['writes'], 1)
        self.assertEqual(self.do('set-members', 'same', 'e', movies=['m1', 'm2'])['writes'], 0)
        self.assertEqual(self.do('set-members', 'empty', 'e', movies=[])['writes'], 1)
        self.assertEqual(self.do('set-members', 'empty-again', 'e', movies=[])['writes'], 0)
        self.assertEqual(self.do('set-title', 'same-title', 'e', title='Empty')['writes'], 0)
        self.assertEqual((self.pid('e'), self.backend.items[pid]['entries']), (pid, []))
        self.assertEqual([w[0] for w in self.backend.writes],
                         ['create', 'share', 'row', 'display-title', 'add', 'clear'])

    def test_detached_list_stays_manageable_and_title_needs_row(self):
        self.do('create-list', 'create-a', 'a', title='A', movies=['m1'])
        self.do('detach-row', 'detach', 'a')
        self.assertEqual(self.stored()['lists']['a'], {'row': None})
        self.fresh_session()
        for operation, args in (('set-title', {'title': 'B'}), ('detach-row', {})):
            with self.assertRaises(Refused):
                self.operator.list_preview(operation, 'a', **args)
        self.assertEqual(self.do('set-members', 'members', 'a', movies=['m2'])['writes'], 2)
        self.assertEqual(self.do('attach-row', 'attach', 'a', title='B')['writes'], 2)
        self.assertEqual(self.backend.home[-1]['CustomName'], 'B')
        self.fresh_session()
        with self.assertRaisesRegex(Refused, 'already attached'):
            self.operator.list_preview('attach-row', 'a', title='C')

    def test_fresh_journal_is_v2_and_downgrade_is_lossless_or_refused(self):
        self.do('create-list', 'create-other', 'other', title='Other', movies=['m1'])
        self.assertEqual(self.stored()['version'], 2)
        self.do('create-list', 'create-legacy', 'igor-movies', title='Legacy', movies=['m2'])
        self.do('delete-list', 'delete-other', 'other')
        self.do('detach-row', 'detach-legacy', 'igor-movies')
        self.do('attach-row', 'attach-legacy', 'igor-movies', title='Legacy')
        v2 = self.stored()
        self.fresh_session()
        inverse = self.operator.downgrade_preview()
        self.fresh_session()
        self.assertEqual(self.operator.downgrade('downgrade', inverse['intent_digest'])['state'],
                         'downgraded')
        self.assertEqual(self.stored(), dict(
            version=1, scope=dict(v2['scope'], recipe='igor-movies'), baseline=v2['baseline'],
            current=v2['current'], row=v2['lists']['igor-movies']['row'], display_title='Legacy',
            pending=None, run=None, done=v2['done']))
        self.fresh_session()
        shown = self.operator.show()
        self.assertEqual((shown['display_title'], shown['done_count']), ('Legacy', 5))
        # Version 2 keys stay reserved under version 1; they never replay as v1 work.
        for key, movies in (('create-other', ['m1']), ('create-legacy', ['m2'])):
            with self.assertRaisesRegex(Refused, 'Idempotency key reused'):
                self.operator.apply(key, movies)
        self.assertEqual(self.v1_apply('v1-update', ['m3'])['state'], 'applied')

    def test_downgrade_refuses_extra_foreign_or_detached_lists(self):
        self.do('create-list', 'create-other', 'other', title='Other', movies=['m1'])
        for setup in (lambda: None,
                      lambda: self.do('create-list', 'create-legacy', 'igor-movies',
                                      title='Legacy', movies=['m2']),
                      lambda: (self.do('delete-list', 'delete-other', 'other'),
                               self.do('detach-row', 'detach-legacy', 'igor-movies'))):
            setup()
            before = self.bytes()
            self.fresh_session()
            with self.assertRaisesRegex(Refused, 'Downgrade requires'):
                self.operator.downgrade_preview()
            self.assertEqual(self.bytes(), before)


class ListEntryTests(unittest.TestCase):
    def test_entry_routes_list_and_migration_requests_with_exact_fields(self):
        from unittest.mock import MagicMock
        entry = EntryTests().entry()
        good = [('list-preview', {'operation': 'set-title', 'list': 'a', 'title': 'T'}),
                ('list-preview', {'operation': 'detach-row', 'list': 'a'}),
                ('create-list', {'key': 'k', 'list': 'a', 'title': 'T', 'movies': [],
                                 'intent_digest': 'd'}),
                ('set-members', {'key': 'k', 'list': 'a', 'movies': ['m1'], 'intent_digest': 'd'}),
                ('set-title', {'key': 'k', 'list': 'a', 'title': 'T', 'intent_digest': 'd'}),
                ('detach-row', {'key': 'k', 'list': 'a', 'intent_digest': 'd'}),
                ('attach-row', {'key': 'k', 'list': 'a', 'title': 'T', 'intent_digest': 'd'}),
                ('delete-list', {'key': 'k', 'list': 'a', 'intent_digest': 'd'}),
                ('show', {'list': 'a'}), ('migrate-preview', {}), ('downgrade-preview', {}),
                ('migrate', {'key': 'k', 'intent_digest': 'd'}),
                ('downgrade', {'key': 'k', 'intent_digest': 'd'})]
        bad = [('list-preview', {'operation': 'set-title', 'list': 'a'}),
               ('list-preview', {'operation': ['set-title'], 'list': 'a', 'title': 'T'}),
               ('list-preview', {'operation': 'apply', 'list': 'a', 'movies': ['m1']}),
               ('create-list', {'key': 'k', 'list': 'a', 'title': 'T', 'movies': [],
                                'baseline_digest': 'd'}),
               ('set-members', {'key': 'k', 'list': 'a', 'movies': ['m1']}),
               ('show', {'list': 'a', 'key': 'k'}),
               ('migrate', {'key': 'k', 'baseline_digest': 'd'})]
        for operation, request in good + bad:
            with self.subTest(operation=operation, request=request):
                api, operator = MagicMock(), MagicMock()
                method = getattr(operator, operation.replace('-', '_'))
                method.return_value = {'state': 'synthetic-success'}
                with patch.object(entry, 'load_config', return_value=config()), \
                     patch.object(entry, 'private_read', return_value=json.dumps(request)), \
                     patch.object(entry, 'load_credentials', return_value={
                         'username': 'synthetic', 'password': 'synthetic'}) as credentials, \
                     patch.object(entry, 'Journal', return_value=MagicMock()), \
                     patch.object(entry, 'OperatorAdapter', return_value=api), \
                     patch.object(entry, 'Operator', return_value=operator), \
                     contextlib.redirect_stdout(io.StringIO()), \
                     contextlib.redirect_stderr(io.StringIO()) as errors:
                    code = entry.main(['--config', '/synthetic/config', operation,
                                       '--request-file', '/synthetic/request'])
                if (operation, request) in good:
                    self.assertEqual(code, 0)
                    method.assert_called_once_with(**request)
                else:
                    self.assertEqual(code, 2)
                    self.assertIn('Invalid operator request fields', errors.getvalue())
                    credentials.assert_not_called()
                    method.assert_not_called()


if __name__ == '__main__':
    unittest.main()
