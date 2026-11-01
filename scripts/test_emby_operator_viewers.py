"""Synthetic checks for viewer journal init and fixed-cohort configuration. Parent runs in Chaldea only.

Operator checks drive the real core and adapter over the loopback recording HTTP
model from test_emby_operator_adapter, with synthetic IDs. Helper checks run its
real no-follow, mode, link and rename behavior below a scratch anchor inside the
asserted isolated HOME; only the trusted owner uid and walk anchor are
substituted. Nothing here is runtime evidence or a real recipient restriction.
"""
import contextlib
import copy
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import yaml

import emby_operator_adapter as adapter
import emby_operator_entry as lifecycle
import test_emby_operator_adapter as base
from emby_operator_recommendations import INIT_REQUEST, Journal, Operator, Refused, digest

SCRIPTS = Path(__file__).resolve().parent
HOME = SCRIPTS.parent / '.cache/holonet-operator-tests/home'
IGOR = '545014d1c9d1416b8e0b5a445d6ff9ec'
SERVICE = '54b7d36cef804d3c8478474553ed3a82'
VIEWERS = {'kostya': 'db2f3bfb1f84438ebcde94f228183011',
           'lisa': '313e39bb4d8c4b92b81d4b170c3f1b8e',
           'oleg': 'c06c7d2c3e224d8ba2e466efa5bcb97b',
           'tanya': '22703968ab0b48eebb4faf7c1d16262c',
           'tetiana': 'e05efa68303043b893b0f5c2e200ccd7',
           'tv': 'b8f4f6de49b443f1948cf0118679e863'}
K, L = 'e' * 32, 'f' * 32  # Synthetic additional recipients.


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load('emby_operator_viewers', 'emby-operator-viewers.py')
credential = load('emby_operator_credential_pins', 'emby-operator-credential.py')


def isolated(case):
    case.assertEqual(os.environ['HOME'], str(HOME))
    case.assertEqual(os.environ['TMPDIR'], str(HOME / 'tmp'))
    # Never create test files outside the parent's explicitly isolated HOME.
    scratch = tempfile.TemporaryDirectory(dir=HOME / 'tmp')
    case.addCleanup(scratch.cleanup)
    return Path(scratch.name)


class InitTests(unittest.TestCase):
    """Journal init and per-recipient isolation through the real adapter."""
    def setUp(self):
        self.scratch = isolated(self)
        self.backend = base.MultiBackend()
        self.http = base.RecordingHTTP()
        self.http.responder = self.backend.respond
        self.http.__enter__()
        self.addCleanup(self.http.__exit__, None, None, None)
        # The recording model and config read base.R; restored after each test.
        patcher = patch.object(base, 'R', base.R)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.journals, self.homes, self.recipient = {}, {}, None
        self.use(base.R)

    fresh_session = base.LifecycleHTTPTests.fresh_session

    def use(self, recipient):
        """Switch to one recipient's own config, journal directory and Home."""
        if self.recipient is not None:
            self.homes[self.recipient] = self.backend.home
        self.recipient = base.R = recipient
        if recipient not in self.journals:
            directory = self.scratch / recipient
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)
            journal = Journal(directory)
            self.addCleanup(journal.close)
            self.journals[recipient] = (directory, journal)
            self.homes[recipient] = [{'Id': 1, 'Unknown': {'retain': [9]}},
                                     {'Id': 'own-' + recipient[:4], 'CustomName': 'Manual'}]
        self.directory, self.journal = self.journals[recipient]
        self.backend.home = self.homes[recipient]
        self.backend.journal = self.journal
        self.fresh_session()

    def bytes(self):
        return (self.directory / 'journal.json').read_bytes()

    def stored(self):
        return json.loads(self.bytes())

    def absent(self):
        return not (self.directory / 'journal.json').exists()

    def posts(self, offset):
        return [c[1] for c in self.http.calls[offset:]
                if c[0] == 'POST' and c[1] != '/emby/Users/AuthenticateByName']

    def initialize(self, key='init'):
        # Preview and init are separate invocations, as through the entry.
        self.fresh_session()
        preview = self.operator.init_preview()
        self.fresh_session()
        return preview, self.operator.init(key, preview['intent_digest'])

    def do(self, operation, key, list_id, **args):
        self.fresh_session()
        intent = self.operator.list_preview(operation, list_id, **args)['intent_digest']
        self.fresh_session()
        return getattr(self.operator, operation.replace('-', '_'))(
            key, list_id, intent_digest=intent, **args)

    def test_fresh_recipient_initializes_once_with_zero_writes(self):
        baseline = copy.deepcopy(self.backend.home)
        offset = len(self.http.calls)
        self.fresh_session()
        preview = self.operator.init_preview()
        self.assertTrue(self.absent())
        self.assertEqual((preview['operation'], preview['lists']), ('init', []))
        self.fresh_session()
        self.assertEqual(self.operator.init('init', preview['intent_digest']),
                         {'state': 'initialized', 'writes': 0})
        # Admin recipient-context GETs only; authentication is the only POST.
        self.assertEqual(self.posts(offset), [])
        self.assertEqual((self.backend.writes, self.backend.items, self.backend.home),
                         ([], {}, baseline))
        snapshot = {'home': baseline, 'playlists': []}
        scope = {k: base.config()[k] for k in ('server', 'actor', 'recipient', 'library')}
        self.assertEqual(self.stored(), dict(version=2, scope=scope, baseline=snapshot,
                                             current=snapshot, lists={}, pending=None, run=None,
                                             done={'init': INIT_REQUEST}))
        self.assertEqual(preview['revision'], digest(snapshot))
        self.assertEqual(preview['intent_digest'], digest(dict(
            operation='init', scope=scope, journal=None, snapshot=snapshot)))
        initialized = self.bytes()
        self.fresh_session()
        shown = self.operator.show()
        self.assertEqual((shown['version'], shown['lists'], shown['list_count'],
                          shown['reconcile'], shown['done_count'], shown['revision']),
                         (2, [], 0, {'state': 'clean'}, 1, preview['revision']))
        self.assertEqual(self.operator.reconcile(), {'state': 'clean'})
        for intent in (preview['intent_digest'], None):
            self.fresh_session()
            self.assertEqual(self.operator.init('init', intent),
                             {'state': 'already-initialized', 'writes': 0})
        for call in (lambda: self.operator.init_preview(),
                     lambda: self.operator.init('other', preview['intent_digest'])):
            self.fresh_session()
            with self.assertRaisesRegex(Refused, 'Journal already exists'):
                call()
        self.assertEqual(self.bytes(), initialized)
        self.assertEqual(self.posts(offset), [])

    def test_initialized_recipient_manages_an_empty_list_and_inverse_restores_baseline(self):
        baseline = copy.deepcopy(self.backend.home)
        self.initialize()
        self.assertEqual(self.do('create-list', 'create', 'movies', title='Movies', movies=[]),
                         {'state': 'created', 'writes': 4})
        self.assertEqual([w[0] for w in self.backend.writes],
                         ['create', 'share', 'row', 'display-title'])
        [item] = self.backend.items.values()
        self.assertEqual((item['grant'], item['entries']), ('Read', []))
        self.assertEqual(self.backend.home[:len(baseline)], baseline)
        self.fresh_session()
        [shown] = self.operator.show()['lists']
        self.assertEqual((shown['list'], shown['display_title'], shown['playlist']['access'],
                          shown['playlist']['entries'], shown['position']),
                         ('movies', 'Movies', [base.R], [], len(baseline)))
        before = self.bytes()
        self.fresh_session()
        with self.assertRaisesRegex(Refused, 'Idempotency key reused'):
            self.operator.create_list('init', 'other', 'Other', [], None)
        self.assertEqual(self.bytes(), before)
        self.assertEqual(self.do('delete-list', 'delete', 'movies'),
                         {'state': 'deleted', 'writes': 2})
        self.assertEqual((self.backend.home, self.backend.items), (baseline, {}))
        self.fresh_session()
        self.assertEqual(self.operator.init('init', None),
                         {'state': 'already-initialized', 'writes': 0})

    def test_stale_digest_drift_and_unknown_markers_refuse_without_adoption(self):
        self.fresh_session()
        stale = self.operator.init_preview()['intent_digest']
        self.backend.home[0]['Manual'] = True
        self.fresh_session()
        for intent in (stale, None, 'x', digest({})):
            with self.subTest(intent=intent), self.assertRaisesRegex(Refused, 'Fresh preview digest'):
                self.operator.init('init', intent)
        with self.assertRaisesRegex(Refused, 'Invalid opaque ID'):
            self.operator.init('bad key', stale)
        self.assertTrue(self.absent())
        marker = self.operator.marker_for('anything')
        for field in ('CustomName', 'Name'):
            self.backend.home.append({'Id': 'foreign', field: marker})
            self.fresh_session()
            with self.subTest(field=field), self.assertRaisesRegex(Refused, 'Existing managed row'):
                self.operator.init_preview()
            self.backend.home.pop()
        # The legacy marker playlist is never adopted.
        self.backend.items['legacy'] = {'Name': self.operator.marker, 'entries': [], 'grant': 'Read'}
        self.fresh_session()
        with self.assertRaisesRegex(Refused, 'Unexplained existing marker playlist'):
            self.operator.init_preview()
        del self.backend.items['legacy']
        self.assertTrue(self.absent())
        # An unknown list's marker playlist stays unowned and blocks only its own create.
        self.backend.items['unknown'] = {'Name': self.operator.marker_for('unknown'),
                                         'entries': [], 'grant': 'Read'}
        self.initialize()
        self.assertEqual((self.stored()['lists'], self.stored()['current']['playlists']), ({}, []))
        before = self.bytes()
        self.fresh_session()
        with self.assertRaisesRegex(Refused, 'Existing playlist requires explicit operator recovery'):
            self.operator.list_preview('create-list', 'unknown', title='Unknown', movies=[])
        self.assertEqual((self.bytes(), self.backend.writes), (before, []))

    def test_existing_malformed_and_foreign_journals_are_never_reset_or_adopted(self):
        self.fresh_session()
        preview = self.operator.preview(['m1'])
        self.fresh_session()
        self.operator.apply('first', ['m1'], preview['baseline_digest'])
        v1, writes = self.bytes(), copy.deepcopy(self.backend.writes)
        for call in (lambda: self.operator.init_preview(),
                     lambda: self.operator.init('first', None),
                     lambda: self.operator.init('init', None)):
            self.fresh_session()
            with self.assertRaisesRegex(Refused, 'Journal already exists'):
                call()
        self.assertEqual((self.bytes(), self.backend.writes), (v1, writes))
        # A version 2 journal started by create-list is not an initialized journal.
        self.use(K)
        self.do('create-list', 'create', 'movies', title='Movies', movies=['m1'])
        v2, writes = self.bytes(), copy.deepcopy(self.backend.writes)
        for call in (lambda: self.operator.init_preview(),
                     lambda: self.operator.init('create', None),
                     lambda: self.operator.init('init', None)):
            self.fresh_session()
            with self.assertRaisesRegex(Refused, 'Journal already exists'):
                call()
        self.assertEqual((self.bytes(), self.backend.writes), (v2, writes))
        self.use(L)
        foreign = json.loads(v2)  # Scoped to recipient K, read under recipient L.
        snapshot = {'home': copy.deepcopy(self.backend.home), 'playlists': []}
        unfinished = dict(version=2, scope=dict(foreign['scope'], recipient=L), baseline=snapshot,
                          current=snapshot, lists={}, pending=None, done={'init': INIT_REQUEST},
                          run={'key': 'x', 'request': {}})
        path = self.directory / 'journal.json'
        for raw, preview_message, init_message in (
                (b'{', 'Malformed journal', 'Malformed journal'),
                (b'null', 'Malformed journal', 'Malformed journal'),
                (b'[]', 'Malformed journal', 'Malformed journal'),
                (b'false', 'Malformed journal', 'Malformed journal'),
                (json.dumps(foreign).encode(), 'Foreign journal', 'Foreign journal'),
                (json.dumps(dict(foreign, version=3)).encode(), 'Foreign journal', 'Foreign journal'),
                (json.dumps(unfinished).encode(), 'Journal already exists', 'Unfinished operation')):
            path.write_bytes(raw)
            path.chmod(0o600)
            for call, message in ((lambda: self.operator.init_preview(), preview_message),
                                  (lambda: self.operator.init('init', None), init_message)):
                self.fresh_session()
                with self.subTest(message=message), self.assertRaisesRegex(Refused, message):
                    call()
            self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.backend.writes, writes)

    def test_identity_drift_refuses_before_any_journal(self):
        foreign = Operator(self.api, self.journal, server=base.S, actor=base.A, recipient=K,
                           library='7', recipe='kostya-movies')
        with self.assertRaisesRegex(Refused, 'recipient'):
            foreign.init_preview()
        original = self.backend.get

        def disabled(path, query):
            result = original(path, query)
            if path == '/Users/' + base.R:
                result['Policy']['IsDisabled'] = True
            return result
        self.fresh_session()
        with patch.object(self.backend, 'get', disabled), \
                self.assertRaisesRegex(Refused, 'Principal identity/policy drift'):
            self.operator.init_preview()
        self.assertTrue(self.absent())
        self.assertEqual(self.backend.writes, [])

    def test_recipients_are_isolated_and_igor_journal_is_unchanged(self):
        # The actor managing its own Home stands in for the retained Igor journal.
        self.use(base.A)
        self.do('create-list', 'igor-create', 'igor-movies-retain116', title='Movies for Igor',
                movies=['m1', 'm2', 'm3', 'm4', 'm5'])
        igor = (self.bytes(), copy.deepcopy(self.backend.home), copy.deepcopy(self.backend.items))
        self.fresh_session()
        igor_show = self.operator.show()
        for viewer in (K, L):
            self.use(viewer)
            self.assertEqual(self.initialize()[1], {'state': 'initialized', 'writes': 0})
        lisa = (self.bytes(), copy.deepcopy(self.backend.home))
        self.use(K)
        kostya, start = copy.deepcopy(self.backend.home), len(self.backend.writes)
        self.do('create-list', 'create', 'movies', title='Movies', movies=[])
        self.do('set-members', 'members', 'movies', movies=['m6'])
        self.do('move-row', 'move', 'movies', position=0)
        self.assertEqual(self.do('delete-list', 'delete', 'movies')['state'], 'deleted')
        self.assertEqual(self.backend.home, kostya)
        self.assertEqual({w[2] for w in self.backend.writes[start:]
                          if w[0] in ('row', 'display-title', 'move', 'unrow')},
                         {'/Users/%s/HomeSections%s' % (K, s) for s in ('', '/Move', '/Delete')})
        self.use(L)
        self.assertEqual((self.bytes(), self.backend.home), lisa)
        self.assertEqual(self.operator.show()['reconcile'], {'state': 'clean'})
        self.use(base.A)
        self.assertEqual((self.bytes(), self.backend.home, self.backend.items), igor)
        self.assertEqual(self.operator.show(), igor_show)
        with self.assertRaisesRegex(Refused, 'Journal already exists'):
            self.operator.init('init', None)
        self.assertEqual(self.bytes(), igor[0])


class InitEntryTests(unittest.TestCase):
    def test_entry_routes_init_requests_with_exact_fields(self):
        entry = base.EntryTests().entry()
        good = [('init-preview', {}), ('init', {'key': 'k', 'intent_digest': 'd'})]
        bad = [('init-preview', {'key': 'k'}), ('init', {'key': 'k'}),
               ('init', {'key': 'k', 'baseline_digest': 'd'}),
               ('init', {'key': 'k', 'intent_digest': 'd', 'recipient': base.R})]
        for operation, request in good + bad:
            with self.subTest(operation=operation, request=request):
                api, operator = MagicMock(), MagicMock()
                method = getattr(operator, operation.replace('-', '_'))
                method.return_value = {'state': 'synthetic-success'}
                with patch.object(entry, 'load_config', return_value=base.config()), \
                     patch.object(entry, 'private_read', return_value=json.dumps(request)), \
                     patch.object(lifecycle, 'load_credentials', return_value={
                         'username': 'synthetic', 'password': 'synthetic'}) as credentials, \
                     patch.object(lifecycle, 'Journal', return_value=MagicMock()) as journal, \
                     patch.object(lifecycle, 'OperatorAdapter', return_value=api), \
                     patch.object(lifecycle, 'Operator', return_value=operator), \
                     contextlib.redirect_stdout(io.StringIO()), \
                     contextlib.redirect_stderr(io.StringIO()) as errors:
                    code = entry.main(['--config', '/synthetic/config', operation,
                                       '--request-file', '/synthetic/request'])
                if (operation, request) in good:
                    self.assertEqual(code, 0)
                    journal.assert_called_once_with('/synthetic/journal', root_safe=True)
                    method.assert_called_once_with(**request)
                else:
                    self.assertEqual(code, 2)
                    self.assertIn('Invalid operator request fields', errors.getvalue())
                    credentials.assert_not_called()
                    method.assert_not_called()


class CohortPinTests(unittest.TestCase):
    def test_fixed_cohort_excludes_igor_and_service_and_reuses_only_the_pointer(self):
        self.assertEqual(helper.COHORT, VIEWERS)
        self.assertEqual(helper.EXCLUDED, {IGOR, SERVICE})
        self.assertEqual(helper.PARENT, '/var/lib/holonet-operator')
        self.assertEqual(helper.ROOT, '/var/lib/holonet-operator/viewers-20261101')
        self.assertEqual((helper.OWNER_UID, helper.ANCHOR), (0, '/'))
        for key in ('origin', 'server', 'actor', 'library', 'library_guid', 'credential_file'):
            self.assertEqual(helper.COMMON[key], credential.PINNED[key])
        self.assertEqual(set(helper.FIELDS), set(credential.CONFIG_KEYS))
        helper.check_cohort()
        for name, rid in VIEWERS.items():
            config = dict(helper.pinned(name), enabled=True)
            self.assertEqual(adapter.validate_config(config), config)
            self.assertEqual((config['recipient'], config['recipe'], config['journal_directory']),
                             (rid, name + '-movies', helper.ROOT + '/' + name + '/journal'))
            self.assertNotEqual(config['journal_directory'], credential.PINNED['journal_directory'])
        for cohort in (dict(VIEWERS, igor=IGOR), dict(VIEWERS, service=SERVICE),
                       dict(VIEWERS, copy=VIEWERS['tv']), dict(VIEWERS, tv='B' * 32)):
            with self.subTest(cohort=cohort), patch.object(helper, 'COHORT', cohort), \
                    self.assertRaisesRegex(helper.Refused, 'Invalid fixed cohort'):
                helper.run('check')


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.anchor = isolated(self) / 'anchor'
        self.anchor.mkdir(mode=0o700)
        self.parent = self.anchor / PurePosixPath(helper.PARENT).relative_to('/')
        for directory in (*reversed(self.parent.parents), self.parent):
            if directory.is_relative_to(self.anchor) and directory != self.anchor:
                directory.mkdir(mode=0o755, exist_ok=True)
                directory.chmod(0o755)
        # Synthetic retained Igor root; its credential must stay untouched.
        self.igor = self.anchor / PurePosixPath(credential.ROOT).relative_to('/')
        self.igor.mkdir(mode=0o700)
        (self.igor / 'journal').mkdir(mode=0o700)
        self.private(self.igor / 'journal' / 'journal.json', b'{"synthetic": "igor"}')
        self.private(self.igor / 'credential.json', b'{"username":"igor","password":"synthetic"}')
        self.private(self.igor / 'config.json', b'{"synthetic": true}\n')
        self.root = self.anchor / PurePosixPath(helper.ROOT).relative_to('/')
        for name, value in (('PARENT', str(self.parent)), ('ROOT', str(self.root)),
                            ('ANCHOR', str(self.anchor)), ('OWNER_UID', os.getuid())):
            patcher = patch.object(helper, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def private(path, data):
        path.write_bytes(data)
        path.chmod(0o600)

    def tree(self, top=None):
        """Bytes, mode, inode and file mtime of everything below top."""
        top = top or self.anchor
        result = {}
        for path in sorted(top.rglob('*')):
            s = path.lstat()
            content = (path.read_bytes() if stat.S_ISREG(s.st_mode) else
                       os.readlink(path) if stat.S_ISLNK(s.st_mode) else None)
            result[str(path.relative_to(top))] = (
                s.st_mode, s.st_ino, s.st_nlink,
                None if stat.S_ISDIR(s.st_mode) else s.st_mtime_ns, content)
        return result

    def config(self, name):
        return self.root / name / 'config.json'

    def test_check_and_previews_change_nothing(self):
        before = self.tree()
        absent = dict(directory='absent', journal='absent', config='absent')
        self.assertEqual(helper.run('check'), dict(
            root='absent', recipients={n: absent for n in VIEWERS}, action='check',
            applied=False, would_change=False))
        self.assertTrue(helper.run('provision')['would_change'])
        for action in ('enable', 'disable'):
            with self.assertRaisesRegex(helper.Refused, 'not provisioned'):
                helper.run(action)
        with self.assertRaisesRegex(helper.Refused, 'Check never applies'):
            helper.run('check', apply=True)
        self.assertEqual(self.tree(), before)

    def test_provision_creates_exactly_the_fixed_layout_once(self):
        igor = self.tree(self.igor)
        result = helper.run('provision', apply=True)
        self.assertTrue(result['changed'])
        self.assertEqual(result['recipients'], {n: dict(directory='present', journal='present',
                                                        config='enabled') for n in VIEWERS})
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')),
                         sorted(x for n in VIEWERS for x in (n, n + '/journal', n + '/config.json')))
        for name, rid in VIEWERS.items():
            for directory in (self.root / name, self.root / name / 'journal'):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(list((self.root / name / 'journal').iterdir()), [])
            s = self.config(name).lstat()
            self.assertEqual((stat.S_IMODE(s.st_mode), s.st_nlink), (0o600, 1))
            self.assertEqual(self.config(name).read_bytes(), helper.config_bytes(name, True))
            self.assertEqual(json.loads(self.config(name).read_bytes()), dict(
                enabled=True, origin='https://emby.ryzhkov.dev',
                server='3c86e6d7ff6c4a3296522cdf03d22bb0', actor=IGOR, recipient=rid,
                library='3', library_guid='c4ba438d95214fc2b7efba5bd9929b28',
                recipe=name + '-movies', credential_file=credential.PINNED['credential_file'],
                journal_directory=str(self.root / name / 'journal')))
        self.assertEqual(self.tree(self.igor), igor)
        once = self.tree()
        again = helper.run('provision', apply=True)
        self.assertFalse(again['changed'])
        self.assertEqual(self.tree(), once)

    def test_installed_layout_satisfies_the_existing_operator_loaders(self):
        helper.run('provision', apply=True)
        original = os.fstat

        def fstat(fd):
            # Emulate root ownership only; real modes, links and no-follow stay real.
            s = original(fd)
            return SimpleNamespace(st_uid=0, st_mode=s.st_mode & ~0o022, st_nlink=s.st_nlink,
                                   st_size=s.st_size)
        for name in VIEWERS:
            with patch.object(os, 'fstat', fstat), patch.object(os, 'getuid', return_value=0):
                config = adapter.validate_config(adapter.strict_json(
                    adapter.private_read(str(self.config(name)))))
                journal = Journal(config['journal_directory'], root_safe=True)
                try:
                    with journal.locked():
                        self.assertIsNone(journal.load())
                finally:
                    journal.close()
            self.assertEqual(config['recipient'], VIEWERS[name])

    def test_existing_journals_and_disabled_configs_are_preserved(self):
        self.root.mkdir(mode=0o700)
        kostya = self.root / 'kostya'
        (kostya / 'journal').mkdir(mode=0o700, parents=True)
        kostya.chmod(0o700)
        self.private(kostya / 'journal' / 'journal.json', b'{"version": 2, "synthetic": true}')
        self.private(kostya / 'journal' / 'lock', b'')
        lisa = self.root / 'lisa'
        (lisa / 'journal').mkdir(mode=0o700, parents=True)
        lisa.chmod(0o700)
        self.private(self.config('lisa'), helper.config_bytes('lisa', False))
        journal, disabled = self.tree(kostya / 'journal'), self.tree(lisa)
        result = helper.run('provision', apply=True)
        self.assertEqual(self.tree(kostya / 'journal'), journal)
        self.assertEqual(self.tree(lisa), disabled)
        self.assertEqual(result['recipients']['lisa']['config'], 'disabled')
        self.assertEqual(self.config('kostya').read_bytes(), helper.config_bytes('kostya', True))

    def test_drift_and_unsafe_custody_refuse_unchanged(self):
        def k(*parts):
            return self.root.joinpath('kostya', *parts)

        def rewrite(**changes):
            self.private(k('config.json'), json.dumps(
                dict(json.loads(k('config.json').read_bytes()), **changes)).encode())
        other = self.anchor / 'other.json'
        cases = {
            'foreign recipient': lambda: rewrite(recipient=VIEWERS['lisa']),
            'excluded service': lambda: rewrite(recipient=SERVICE),
            'igor recipient': lambda: rewrite(recipient=IGOR),
            'credential pointer': lambda: rewrite(credential_file='/var/lib/holonet-operator/x/c.json'),
            'journal pointer': lambda: rewrite(journal_directory=str(self.root / 'lisa' / 'journal')),
            'extra field': lambda: rewrite(extra=True),
            'string flag': lambda: rewrite(enabled='true'),
            'malformed': lambda: self.private(k('config.json'), b'{'),
            'config mode': lambda: k('config.json').chmod(0o644),
            'config link': lambda: os.link(k('config.json'), self.root / 'alias'),
            'config symlink': lambda: (k('config.json').unlink(), self.private(other, b'{}'),
                                       k('config.json').symlink_to(other)),
            'journal symlink': lambda: (k('journal').rmdir(),
                                        k('journal').symlink_to(self.root / 'lisa' / 'journal')),
            'journal mode': lambda: k('journal').chmod(0o755),
            'member mode': lambda: k().chmod(0o750),
            'root mode': lambda: self.root.chmod(0o755),
            'stage residue': lambda: self.private(k('.config.json.stage'), b'{}'),
            'parent writable': lambda: self.parent.chmod(0o777),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self.parent.chmod(0o755)
                if self.root.exists():
                    shutil.rmtree(self.root)
                if other.exists():
                    other.unlink()
                helper.run('provision', apply=True)
                mutate()
                before = self.tree()
                for action, apply in (('check', False), ('provision', True), ('disable', True)):
                    with self.assertRaises(helper.Refused):
                        helper.run(action, apply=apply)
                self.assertEqual(self.tree(), before)
        self.parent.chmod(0o755)

    def test_enable_and_disable_change_only_the_selected_flags(self):
        helper.run('provision', apply=True)
        before = self.tree()
        result = helper.run('disable', apply=True, recipient='tv')
        self.assertTrue(result['changed'])
        self.assertEqual({n: r['config'] for n, r in result['recipients'].items()},
                         {n: 'disabled' if n == 'tv' else 'enabled' for n in VIEWERS})
        after = self.tree()
        self.assertEqual({p for p in set(before) | set(after) if before.get(p) != after.get(p)},
                         {str(self.config('tv').relative_to(self.anchor))})
        self.assertEqual(self.config('tv').read_bytes(), helper.config_bytes('tv', False))
        self.assertFalse(helper.run('disable', apply=True, recipient='tv')['changed'])
        helper.run('enable', apply=True, recipient='tv')
        self.assertEqual(self.config('tv').read_bytes(), helper.config_bytes('tv', True))
        helper.run('disable', apply=True)
        self.assertFalse(helper.run('provision', apply=True)['changed'])
        for name in VIEWERS:
            self.assertEqual(self.config(name).read_bytes(), helper.config_bytes(name, False))

    def test_credential_and_journal_contents_are_never_touched(self):
        names = []

        def recording(function):
            def wrapper(path, *args, **kwargs):
                names.append(os.fspath(path))
                return function(path, *args, **kwargs)
            return wrapper
        igor = self.tree(self.igor)
        with contextlib.ExitStack() as stack:
            for target in ('open', 'stat', 'lstat', 'listdir', 'scandir', 'unlink', 'rename',
                           'replace', 'link', 'mkdir', 'rmdir', 'chmod'):
                stack.enter_context(patch.object(helper.os, target, recording(getattr(os, target))))
            helper.run('check')
            helper.run('provision', apply=True)
            helper.run('disable', apply=True)
            helper.run('enable', apply=True, recipient='oleg')
        self.assertEqual(self.tree(self.igor), igor)
        relative = [n for n in names if not n.startswith('/')]
        self.assertEqual([n for n in relative if 'credential' in n or 'igor-pr116' in n or
                          n in ('journal.json', 'lock')], [])
        self.assertEqual({n for n in names if n.startswith('/')}, {str(self.anchor)})

    def test_concurrent_run_refused_by_parent_lock(self):
        fd = os.open(self.parent, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = self.tree()
        with self.assertRaisesRegex(helper.Refused, 'Another viewer provisioning run'):
            helper.run('provision', apply=True)
        self.assertEqual(self.tree(), before)
        self.assertTrue(helper.run('provision')['would_change'])

    def test_owner_identity_required(self):
        before = self.tree()
        with patch.object(helper, 'OWNER_UID', os.getuid() + 1), \
                self.assertRaisesRegex(helper.Refused, 'Root operator required'):
            helper.run('provision', apply=True)
        self.assertEqual(self.tree(), before)

    def test_cli_options_are_bounded_and_output_is_metadata(self):
        for argv in (['--root', '/x'], ['--config', '/x'], ['--credential', '/x'], ['--host', 'x'],
                     ['--recipient', 'igor'], ['--recipient', 'holonet-catalog-movies'],
                     ['--action', 'retire'], ['--action', 'rotate']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                helper.main(argv)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(helper.main(['--action', 'provision', '--apply', '--recipient', 'tv']), 0)
        result = json.loads(output.getvalue())
        self.assertEqual((result['applied'], result['changed'], result['recipients']['tv']['config'],
                          result['recipients']['lisa']['config']), (True, True, 'enabled', 'absent'))
        self.assertNotIn('credential', output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(helper.main(['--action', 'enable', '--apply', '--recipient', 'lisa']), 2)
        self.assertIn('not provisioned', errors.getvalue())


class PlaybookTests(unittest.TestCase):
    def test_single_pinned_secret_free_play_checks_before_apply(self):
        text = (SCRIPTS.parent / 'playbooks/emby-operator-viewers.yml').read_text()
        [play] = yaml.safe_load(text)
        self.assertEqual((play['hosts'], play['become'], play['gather_facts']), ('homelab', True, False))
        tasks = play['pre_tasks'] + play['tasks']
        modules = [next(k for k in t if k.startswith('ansible.')) for t in tasks]
        self.assertEqual(modules, ['ansible.builtin.assert', 'ansible.builtin.command',
                                   'ansible.builtin.debug', 'ansible.builtin.command',
                                   'ansible.builtin.assert'])
        self.assertNotIn('sops', text)
        self.assertNotIn('no_log', text)
        check, apply = (t['ansible.builtin.command'] for t in tasks if 'ansible.builtin.command' in t)
        self.assertNotIn('--apply', check['argv'])
        self.assertIn("+ ['--apply']", apply['argv'])
        self.assertFalse(any('stdin' in t['ansible.builtin.command'] for t in tasks
                             if 'ansible.builtin.command' in t))
        self.assertIn('emby_operator_viewers_apply | default(false) | bool', tasks[3]['when'])
        self.assertIn('emby-operator-viewers.py', check['argv'])


if __name__ == '__main__':
    unittest.main()
