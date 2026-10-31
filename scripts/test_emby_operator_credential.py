"""Synthetic custody checks for operator credential provisioning. Parent runs in Chaldea only.

Every file lives under a scratch anchor inside the asserted isolated HOME. The
helper's real no-follow, mode, link and rename behavior runs unchanged; only the
trusted owner uid and the walk anchor are substituted. Secrets are synthetic.
"""
import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import re
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

import emby_operator_adapter as adapter

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('emby_operator_credential',
                                              SCRIPTS / 'emby-operator-credential.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

SECRET = 'synthetic-password-0f3a'
ROTATED = 'synthetic-rotated-password-77c1'
PRODUCTION_ROOT = '/var/lib/holonet-operator/igor-pr116-retain-20261031-next'


def secret(password=SECRET, **extra):
    return io.BytesIO(json.dumps(dict({'username': 'igor', 'password': password}, **extra)).encode())


class Unreadable:
    def read(self, *_):
        raise AssertionError('standard input must not be read')


class Fixture(unittest.TestCase):
    def setUp(self):
        home = Path(os.environ['HOME'])
        temporary = Path(os.environ['TMPDIR'])
        self.assertEqual(home, SCRIPTS.parent / '.cache/holonet-operator-tests/home')
        self.assertEqual(temporary, home / 'tmp')
        # Never create test files outside the parent's explicitly isolated HOME.
        scratch = tempfile.TemporaryDirectory(dir=temporary)
        self.addCleanup(scratch.cleanup)
        self.anchor = Path(scratch.name) / 'anchor'
        self.anchor.mkdir(mode=0o700)
        self.root = self.anchor / PurePosixPath(helper.ROOT).relative_to('/')
        for parent in reversed(self.root.parents):
            if parent.is_relative_to(self.anchor) and parent != self.anchor:
                parent.mkdir(mode=0o755, exist_ok=True)
                parent.chmod(0o755)
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        journal = self.root / 'journal'
        journal.mkdir(mode=0o700)
        journal.chmod(0o700)
        self.private(journal / 'journal.json', b'{"version": 2, "synthetic": true}\n')
        self.private(journal / 'lock', b'')
        source = self.root / 'source'
        source.mkdir(mode=0o700)
        for name in ('emby-operator-recommendations.py', 'emby_operator_adapter.py',
                     'emby_operator_recommendations.py'):
            self.private(source / name, b'# synthetic installed source\n')
        self.config_bytes = self.config_text(False)
        self.private(self.root / 'config.json', self.config_bytes)
        for name, value in (('ROOT', str(self.root)), ('ANCHOR', str(self.anchor)),
                            ('OWNER_UID', os.getuid())):
            patcher = patch.object(helper, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def config_text(initial_enabled, **changes):
        config = dict(helper.PINNED, enabled=initial_enabled)
        config = {k: config[k] for k in ('enabled', 'origin', 'server', 'actor', 'recipient',
                                         'library', 'library_guid', 'recipe', 'credential_file',
                                         'journal_directory')}
        config.update(changes)
        return (json.dumps(config, indent=2) + '\n').encode()

    @staticmethod
    def private(path, data):
        path.write_bytes(data)
        path.chmod(0o600)

    def tree(self):
        """Bytes, mode, inode and mtime of everything below the anchor."""
        result = {}
        for path in sorted(self.anchor.rglob('*')):
            s = path.lstat()
            content = (path.read_bytes() if stat.S_ISREG(s.st_mode) else
                       os.readlink(path) if stat.S_ISLNK(s.st_mode) else None)
            # Directory mtimes change with any entry; file mtimes and inodes must not.
            result[str(path.relative_to(self.anchor))] = (
                s.st_mode, s.st_ino, s.st_nlink,
                None if stat.S_ISDIR(s.st_mode) else s.st_mtime_ns, content)
        return result

    def assert_refused_unchanged(self, action='check', **kwargs):
        before = self.tree()
        with self.assertRaises(helper.Refused) as caught:
            helper.run(action, **kwargs)
        self.assertEqual(self.tree(), before)
        for leaked in (SECRET, ROTATED, str(self.root), PRODUCTION_ROOT):
            self.assertNotIn(leaked, str(caught.exception))
        return caught.exception

    def provisioned(self):
        result = helper.run('provision', apply=True, stdin=secret())
        self.assertTrue(result['changed'])
        return result

    def credential(self):
        return self.root / 'credential.json'


class CheckTests(Fixture):
    def test_check_is_metadata_only_and_never_reads_input(self):
        before = self.tree()
        opened = []
        original = os.open

        def recording(path, *args, **kwargs):
            opened.append(os.fspath(path))
            return original(path, *args, **kwargs)
        with patch.object(os, 'open', recording):
            result = helper.run('check', stdin=Unreadable())
        self.assertEqual(result, {'action': 'check', 'applied': False, 'would_change': False,
                                  'config_enabled': False, 'credential': 'absent',
                                  'credential_id': None})
        self.assertEqual(self.tree(), before)
        names = {PurePosixPath(p).name for p in opened}
        self.assertIn('config.json', names)
        self.assertFalse(names & {'credential.json', 'journal', 'journal.json', 'lock', 'source'})

    def test_previews_need_no_secret_and_change_nothing(self):
        before = self.tree()
        result = helper.run('provision', stdin=Unreadable())
        self.assertEqual((result['applied'], result['would_change']), (False, True))
        result = helper.run('retire', expect='absent', stdin=Unreadable())
        self.assertEqual((result['applied'], result['would_change']), (False, False))
        self.assertEqual(self.tree(), before)
        self.assert_refused_unchanged('rotate', expect='absent')

    def test_check_reports_installed_credential_without_reading_it(self):
        self.provisioned()
        opened = []
        original = os.open

        def recording(path, *args, **kwargs):
            opened.append(os.fspath(path))
            return original(path, *args, **kwargs)
        with patch.object(os, 'open', recording):
            result = helper.run('check')
        s = self.credential().lstat()
        self.assertEqual(result['credential_id'], '%d:%d:%d' % (s.st_dev, s.st_ino, s.st_ctime_ns))
        self.assertNotIn('credential.json', {PurePosixPath(p).name for p in opened})
        self.assertNotIn(SECRET, json.dumps(result))


class ProvisionTests(Fixture):
    def test_provision_installs_private_credential_then_enables(self):
        retained = {k: v for k, v in self.tree().items() if '/journal' in k or '/source' in k}
        result = helper.run('provision', apply=True, stdin=secret())
        self.assertEqual((result['applied'], result['changed'], result['config_enabled'],
                          result['credential']), (True, True, True, 'present'))
        self.assertNotIn(SECRET, json.dumps(result))
        s = self.credential().lstat()
        self.assertTrue(stat.S_ISREG(s.st_mode))
        self.assertEqual((stat.S_IMODE(s.st_mode), s.st_nlink, s.st_uid), (0o600, 1, os.getuid()))
        self.assertEqual(json.loads(self.credential().read_bytes()),
                         {'username': 'igor', 'password': SECRET})
        # Only the enable literal changed; every other config byte is retained.
        self.assertEqual((self.root / 'config.json').read_bytes(),
                         self.config_bytes.replace(b'"enabled": false', b'"enabled": true'))
        self.assertEqual(stat.S_IMODE((self.root / 'config.json').stat().st_mode), 0o600)
        self.assertFalse((self.root / '.credential.json.stage').exists())
        self.assertFalse((self.root / '.config.json.stage').exists())
        after = self.tree()
        self.assertEqual({k: after[k] for k in retained}, retained)

    def test_installed_files_satisfy_the_existing_operator_loaders(self):
        self.provisioned()
        original = os.fstat

        def root_owned(fd):
            # Same emulation as test_emby_operator_adapter: ownership only.
            s = original(fd)
            return SimpleNamespace(st_uid=0, st_mode=s.st_mode & ~0o022,
                                   st_nlink=s.st_nlink, st_size=s.st_size)
        with patch.object(adapter.os, 'geteuid', return_value=0), \
             patch.object(adapter.os, 'getuid', return_value=0), \
             patch.object(adapter.os, 'fstat', root_owned):
            config = adapter.load_config(str(self.root / 'config.json'))
            credentials = adapter.load_credentials(str(self.root / 'credential.json'))
        self.assertTrue(config['enabled'])
        self.assertEqual({k: config[k] for k in helper.PINNED}, helper.PINNED)
        self.assertEqual(config['credential_file'], PRODUCTION_ROOT + '/credential.json')
        self.assertEqual(credentials, {'username': 'igor', 'password': SECRET})

    def test_secret_identity_and_shape_refused_before_any_write(self):
        cases = [secret(username='x'), io.BytesIO(b'{"username":"admin","password":"p"}'),
                 io.BytesIO(json.dumps({'username': 'igor', 'password': ''}).encode()),
                 io.BytesIO(json.dumps({'username': 'igor', 'password': 'a\nb'}).encode()),
                 io.BytesIO(json.dumps({'username': 'igor', 'password': 'p' * 1025}).encode()),
                 io.BytesIO(b'{"username":"igor","password":"a","password":"b"}'),
                 io.BytesIO(b'{"username":"igor","password":NaN}'),
                 io.BytesIO(json.dumps({'username': 'igor', 'password': SECRET, 'token': 't'}).encode()),
                 io.BytesIO(b'not json ' + SECRET.encode()), io.BytesIO(b'x' * (17 * 1024)),
                 io.BytesIO(b'["igor", "' + SECRET.encode() + b'"]'), None]
        for case in cases:
            with self.subTest(case=case):
                self.assert_refused_unchanged('provision', apply=True, stdin=case)

    def test_provision_refuses_enabled_or_existing_credential(self):
        self.private(self.root / 'config.json', self.config_text(True))
        self.assert_refused_unchanged('provision', apply=True, stdin=secret())
        self.private(self.root / 'config.json', self.config_bytes)
        self.private(self.credential(), b'{"username":"igor","password":"old"}')
        self.assert_refused_unchanged('provision', apply=True, stdin=secret())

    def test_failure_before_enable_removes_only_this_runs_credential(self):
        before = self.tree()
        with patch.object(helper, 'stage_config', side_effect=OSError('synthetic')):
            with self.assertRaises(helper.Refused):
                helper.run('provision', apply=True, stdin=secret())
        self.assertEqual(self.tree(), before)
        with patch.object(helper.os, 'link', side_effect=OSError('synthetic')):
            with self.assertRaises(helper.Refused):
                helper.run('provision', apply=True, stdin=secret())
        self.assertEqual(self.tree(), before)

    def test_failed_config_commit_leaves_operator_disabled(self):
        before = self.tree()
        original = os.rename

        def failing(src, dst, **kwargs):
            if src == '.config.json.stage':
                raise OSError('synthetic')
            return original(src, dst, **kwargs)
        with patch.object(helper.os, 'rename', failing):
            with self.assertRaises(helper.Refused):
                helper.run('provision', apply=True, stdin=secret())
        self.assertEqual(self.tree(), before)

    def test_concurrent_config_change_refuses_and_rolls_back(self):
        original = helper.write_new
        # Same size, same JSON meaning, different bytes: only a byte comparison notices it.
        replacement = self.config_bytes.replace(b'{\n  "enabled"', b'{ \n "enabled"', 1)
        self.assertNotEqual(replacement, self.config_bytes)
        self.assertEqual(len(replacement), len(self.config_bytes))

        def racing(root_fd, name, data):
            if name == '.config.json.stage':
                (self.root / 'config.json').write_bytes(replacement)
            return original(root_fd, name, data)
        with patch.object(helper, 'write_new', racing):
            with self.assertRaisesRegex(helper.Refused, 'changed'):
                helper.run('provision', apply=True, stdin=secret())
        self.assertFalse(self.credential().exists())
        self.assertFalse((self.root / '.config.json.stage').exists())
        self.assertFalse((self.root / '.credential.json.stage').exists())
        self.assertEqual((self.root / 'config.json').read_bytes(), replacement)

    def test_concurrent_run_refused_by_root_lock(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        message = self.assert_refused_unchanged('provision', apply=True, stdin=secret())
        self.assertIn('Another provisioning run', str(message))


class CustodyTests(Fixture):
    def test_pinned_scope_drift_refuses(self):
        changes = [dict(origin='https://emby.example.invalid'), dict(server='0' * 32),
                   dict(actor='1' * 32), dict(recipient='2' * 32), dict(library='4'),
                   dict(library_guid='3' * 32), dict(recipe='other-recipe'),
                   dict(credential_file='/tmp/credential.json'),
                   dict(journal_directory=PRODUCTION_ROOT + '/other'), dict(extra=True),
                   dict(enabled='false')]
        for change in changes:
            with self.subTest(change=change):
                self.private(self.root / 'config.json', self.config_text(False, **change))
                self.assert_refused_unchanged()
                self.assert_refused_unchanged('provision', apply=True, stdin=secret())
        config = json.loads(self.config_bytes)
        del config['recipe']
        self.private(self.root / 'config.json', json.dumps(config).encode())
        self.assert_refused_unchanged()
        self.private(self.root / 'config.json', b'{"enabled": false, "enabled": false}')
        self.assert_refused_unchanged()

    def test_unsafe_custody_is_refused_not_repaired(self):
        config = self.root / 'config.json'
        credential = self.credential()
        outside = self.anchor / 'outside.json'
        self.private(outside, b'{"username":"igor","password":"elsewhere"}')

        def mode(path, value):
            def apply():
                path.chmod(value)
            def undo():
                path.chmod(0o600 if path.is_file() else 0o700 if path in (
                    self.root, self.root / 'journal') else 0o755)
            return apply, undo

        def link(path, target):
            def apply():
                path.symlink_to(target)
            return apply, path.unlink

        def hardlink(path):
            twin = self.root / 'twin'
            def apply():
                os.link(path, twin)
            return apply, twin.unlink

        def replace_dir_with_symlink(path):
            moved = path.with_name(path.name + '.real')
            def apply():
                path.rename(moved)
                path.symlink_to(moved, target_is_directory=True)
            def undo():
                path.unlink()
                moved.rename(path)
            return apply, undo

        def residue(name):
            path = self.root / name
            def apply():
                self.private(path, b'synthetic residue')
            return apply, path.unlink

        def credential_mode(value):
            def apply():
                self.private(credential, b'{"username":"igor","password":"old"}')
                credential.chmod(value)
            return apply, credential.unlink

        cases = {
            'config 0640': mode(config, 0o640),
            'config symlink': (lambda: (config.rename(self.root / 'config.real'),
                                        config.symlink_to(self.root / 'config.real')),
                               lambda: (config.unlink(), (self.root / 'config.real').rename(config))),
            'config hardlink': hardlink(config),
            'credential symlink': link(credential, outside),
            'credential directory': (lambda: credential.mkdir(mode=0o700), credential.rmdir),
            'credential 0644': credential_mode(0o644),
            'credential 0400': credential_mode(0o400),
            'root 0750': mode(self.root, 0o750),
            'root symlink': replace_dir_with_symlink(self.root),
            'group-writable ancestor': mode(self.root.parent, 0o775),
            'world-writable ancestor': mode(self.root.parent.parent, 0o757),
            'journal 0755': mode(self.root / 'journal', 0o755),
            'journal symlink': replace_dir_with_symlink(self.root / 'journal'),
            'credential stage residue': residue('.credential.json.stage'),
            'config stage residue': residue('.config.json.stage'),
        }
        for name, (apply, undo) in cases.items():
            with self.subTest(case=name):
                apply()
                try:
                    self.assert_refused_unchanged()
                    self.assert_refused_unchanged('provision', apply=True, stdin=secret())
                    self.assert_refused_unchanged('retire', apply=True, expect='absent')
                finally:
                    undo()
                helper.run('check')
        # A hardlinked installed credential is refused for rotation and retirement too.
        self.provisioned()
        os.link(credential, self.root / 'twin')
        self.addCleanup((self.root / 'twin').unlink)
        self.assert_refused_unchanged()
        self.assert_refused_unchanged('retire', apply=True, expect='absent')

    def test_owner_and_operator_identity_required(self):
        other = os.getuid() + 1
        with patch.object(helper, 'OWNER_UID', other), \
             patch.object(helper.os, 'geteuid', return_value=other), \
             patch.object(helper.os, 'getuid', return_value=other):
            self.assertNotIn('Root operator required', str(self.assert_refused_unchanged()))
        with patch.object(helper.os, 'geteuid', return_value=os.getuid() + 1):
            self.assertIn('Root operator required', str(self.assert_refused_unchanged()))
        if os.getuid() == 0:
            self.private(self.credential(), b'{"username":"igor","password":"old"}')
            real_leaf = helper.leaf
            def foreign_owner(fd, name):
                observed = real_leaf(fd, name)
                if name == helper.CREDENTIAL and observed is not None:
                    fields = list(observed)
                    fields[4] = 65534
                    return os.stat_result(fields)
                return observed
            # A Chaldea user namespace may map only UID 0; emulate only this
            # metadata field rather than chown to an unmapped host UID.
            with patch.object(helper, 'leaf', side_effect=foreign_owner):
                self.assert_refused_unchanged()
                self.assert_refused_unchanged('retire', apply=True, expect='absent')

    def test_root_outside_anchor_refused(self):
        with patch.object(helper, 'ROOT', '/elsewhere/root'):
            self.assert_refused_unchanged()


class RotationRetirementTests(Fixture):
    def test_rotation_requires_reviewed_id_and_preserves_enable_state(self):
        self.provisioned()
        reviewed = helper.run('check')['credential_id']
        self.assert_refused_unchanged('rotate', apply=True, expect='1:2:3', stdin=secret(ROTATED))
        self.assert_refused_unchanged('rotate', apply=True, expect='absent', stdin=secret(ROTATED))
        config_before = (self.root / 'config.json').read_bytes()
        result = helper.run('rotate', apply=True, expect=reviewed, stdin=secret(ROTATED))
        self.assertEqual((result['changed'], result['config_enabled']), (True, True))
        self.assertNotEqual(result['credential_id'], reviewed)
        self.assertNotIn(ROTATED, json.dumps(result))
        self.assertEqual(json.loads(self.credential().read_bytes())['password'], ROTATED)
        self.assertEqual(stat.S_IMODE(self.credential().lstat().st_mode), 0o600)
        self.assertEqual(self.credential().lstat().st_nlink, 1)
        self.assertEqual((self.root / 'config.json').read_bytes(), config_before)
        # The superseded id no longer authorizes anything.
        self.assert_refused_unchanged('rotate', apply=True, expect=reviewed, stdin=secret())
        self.assert_refused_unchanged('retire', apply=True, expect=reviewed)

    def test_rotation_keeps_a_disabled_operator_disabled(self):
        self.private(self.credential(), b'{"username":"igor","password":"old"}')
        reviewed = helper.run('check')['credential_id']
        helper.run('rotate', apply=True, expect=reviewed, stdin=secret(ROTATED))
        self.assertEqual((self.root / 'config.json').read_bytes(), self.config_bytes)
        self.assertEqual(json.loads(self.credential().read_bytes())['password'], ROTATED)

    def test_rotation_refuses_wrong_identity_without_touching_credential(self):
        self.provisioned()
        reviewed = helper.run('check')['credential_id']
        self.assert_refused_unchanged('rotate', apply=True, expect=reviewed,
                                      stdin=io.BytesIO(b'{"username":"root","password":"x"}'))

    def test_retirement_disables_first_then_removes_only_the_credential(self):
        self.provisioned()
        retained = {k: v for k, v in self.tree().items() if '/journal' in k or '/source' in k}
        reviewed = helper.run('check')['credential_id']
        self.assert_refused_unchanged('retire', apply=True, expect='absent')
        result = helper.run('retire', apply=True, expect=reviewed, stdin=Unreadable())
        self.assertEqual((result['changed'], result['config_enabled'], result['credential']),
                         (True, False, 'absent'))
        self.assertFalse(self.credential().exists())
        self.assertEqual((self.root / 'config.json').read_bytes(), self.config_bytes)
        after = self.tree()
        self.assertEqual({k: after[k] for k in retained}, retained)
        again = helper.run('retire', apply=True, expect='absent')
        self.assertEqual((again['changed'], again['applied']), (False, True))

    def test_retirement_failure_after_disable_fails_closed(self):
        self.provisioned()
        reviewed = helper.run('check')['credential_id']
        with patch.object(helper.os, 'unlink', side_effect=OSError('synthetic')):
            with self.assertRaises(helper.Refused):
                helper.run('retire', apply=True, expect=reviewed)
        self.assertEqual((self.root / 'config.json').read_bytes(), self.config_bytes)
        self.assertTrue(self.credential().exists())
        # The bounded retry completes from the disabled state.
        reviewed = helper.run('check')['credential_id']
        helper.run('retire', apply=True, expect=reviewed)
        self.assertFalse(self.credential().exists())

    def test_retirement_disables_when_credential_already_absent(self):
        self.private(self.root / 'config.json', self.config_text(True))
        result = helper.run('retire', apply=True, expect='absent')
        self.assertEqual((result['changed'], result['config_enabled']), (True, False))


class CommandLineTests(Fixture):
    def call(self, *argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = helper.main(list(argv), stdin=stdin)
            except SystemExit as exit_:
                code = exit_.code
        return code, out.getvalue(), err.getvalue()

    def test_only_bounded_options_exist(self):
        code, out, _ = self.call()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['action'], 'check')
        for argv in (['--apply'], ['--action', 'check', '--apply'],
                     ['--action', 'provision', '--expect-credential-id', 'absent'],
                     ['--action', 'rotate', '--apply'], ['--action', 'retire'],
                     ['--action', 'rotate', '--expect-credential-id', '../x'],
                     ['--root', '/'], ['--config', '/x'], ['--username', 'x'],
                     ['--credential-file', '/x'], ['--action', 'shell']):
            with self.subTest(argv=argv):
                self.assertEqual(self.call(*argv)[0], 2)

    def test_apply_reads_stdin_and_prints_no_secret(self):
        code, out, err = self.call('--action', 'provision', '--apply', stdin=secret())
        self.assertEqual(code, 0, err)
        self.assertNotIn(SECRET, out + err)
        reviewed = json.loads(out)['credential_id']
        code, out, err = self.call('--action', 'rotate', '--apply', '--expect-credential-id',
                                   reviewed, stdin=io.BytesIO(b'{"username":"igor","password":"' +
                                                              ROTATED.encode() + b'","x":1}'))
        self.assertEqual(code, 2)
        self.assertNotIn(ROTATED, out + err)


class PlaybookContractTests(unittest.TestCase):
    def setUp(self):
        path = SCRIPTS.parent / 'playbooks/emby-operator-credential.yml'
        self.text = path.read_text()
        self.plays = yaml.safe_load(self.text)
        self.play = self.plays[0]
        self.tasks = self.play['tasks']

    def named(self, module):
        return [t for t in self.play['pre_tasks'] + self.tasks if module in t]

    def test_single_pinned_play_with_bounded_modules(self):
        self.assertEqual(len(self.plays), 1)
        self.assertEqual((self.play['hosts'], self.play['become'], self.play['gather_facts']),
                         ('homelab', True, False))
        modules = set()
        for task in self.play['pre_tasks'] + self.tasks:
            modules |= set(task) & {'ansible.builtin.assert', 'ansible.builtin.command',
                                    'community.sops.load_vars', 'ansible.builtin.debug'}
            self.assertEqual(len(set(task) & {
                'ansible.builtin.assert', 'ansible.builtin.command', 'community.sops.load_vars',
                'ansible.builtin.debug'}), 1, task['name'])
        self.assertEqual(modules, {'ansible.builtin.assert', 'ansible.builtin.command',
                                   'community.sops.load_vars', 'ansible.builtin.debug'})
        for forbidden in ('builtin.copy', 'template', 'builtin.file', 'shell', 'builtin.raw',
                          'systemd', 'cron', 'journal.json', 'credential.json', "lookup('pipe",
                          'delegate_to'):
            self.assertNotIn(forbidden, self.text)

    def test_offline_check_precedes_decryption_which_is_gated(self):
        order = [next(k for k in t if '.' in k) for t in self.tasks]
        load = order.index('community.sops.load_vars')
        self.assertEqual(order[0], 'ansible.builtin.command')
        self.assertLess(0, load)
        check = self.tasks[0]
        self.assertEqual((check['check_mode'], check['changed_when']), (False, False))
        self.assertNotIn('stdin', check['ansible.builtin.command'])
        self.assertNotIn('--apply', json.dumps(check))
        loader = self.tasks[load]
        self.assertEqual(loader['community.sops.load_vars'],
                         {'file': '{{ playbook_dir }}/../secrets/emby-operator.sops.yaml',
                          'name': 'emby_operator_secret'})
        self.assertIs(loader['no_log'], True)
        self.assertIn('emby_operator_credential_apply | default(false) | bool and not ansible_check_mode', loader['when'])
        self.assertIn("in ['provision', 'rotate']", ' '.join(loader['when']))
        self.assertNotIn('vars', self.play)

    def test_secret_bearing_tasks_are_no_log_and_secret_only_on_stdin(self):
        for task in self.tasks:
            if 'emby_operator_secret' in json.dumps(task):
                self.assertIs(task.get('no_log'), True, task['name'])
        apply = next(t for t in self.tasks if 'stdin' in t.get('ansible.builtin.command', {}))
        self.assertIs(apply['diff'], False)
        self.assertNotIn('emby_operator_secret', apply['ansible.builtin.command']['argv'])
        self.assertIn("+ ['--apply']", apply['ansible.builtin.command']['argv'])
        argv = apply['ansible.builtin.command']['argv']
        self.assertIn("'/usr/bin/python3', '-I', '-c'", argv)
        self.assertIn("lookup('file', playbook_dir ~ '/../scripts/emby-operator-credential.py')", argv)
        self.assertNotIn('emby_operator_secret', argv)
        report = self.tasks[-1]
        self.assertNotIn('emby_operator_secret', json.dumps(report))
        self.assertNotIn('invocation', json.dumps(report))


    def test_derived_overrides_refuse_before_command_or_decryption(self):
        home = Path(os.environ['HOME'])
        temporary = Path(os.environ['TMPDIR'])
        self.assertEqual(home, SCRIPTS.parent / '.cache/holonet-operator-tests/home')
        self.assertEqual(temporary, home / 'tmp')
        with tempfile.TemporaryDirectory(dir=temporary) as directory:
            root = Path(directory)
            plugins = root / 'collections/ansible_collections/community/sops/plugins'
            (plugins / 'modules').mkdir(parents=True)
            (plugins / 'action').mkdir()
            (plugins / 'modules/load_vars.py').write_text('')
            sentinel = root / 'decryption-attempted'
            (plugins / 'action/load_vars.py').write_text(
                'from ansible.plugins.action import ActionBase\n'
                'from pathlib import Path\n'
                'class ActionModule(ActionBase):\n'
                ' def run(self, tmp=None, task_vars=None):\n'
                f'  Path({str(sentinel)!r}).write_text("unexpected")\n'
                '  return {"failed": True, "msg": "Unexpected decryption"}\n')
            inventory = root / 'inventory'
            inventory.write_text('homelab ansible_connection=local\n')
            environment = dict(os.environ, ANSIBLE_COLLECTIONS_PATH=str(root / 'collections'),
                               ANSIBLE_LOCAL_TEMP=str(root / 'ansible-tmp'),
                               ANSIBLE_NOCOLOR='1')
            cases = [
                ({'emby_operator_credential_helper': 'raise SystemExit(0)'}, False),
                ({'emby_operator_credential_argv': ['/bin/true']}, False),
                ({'emby_operator_credential_applying': True,
                  'emby_operator_credential_action': 'provision'}, False),
                ({'emby_operator_credential_applying': True,
                  'emby_operator_credential_action': 'provision',
                  'emby_operator_credential_apply': True}, True),
                ({'emby_operator_secret': {'username': 'igor', 'password': SECRET}}, False),
            ]
            for extra, check in cases:
                with self.subTest(extra=list(extra), check=check):
                    command = ['ansible-playbook', '-i', str(inventory),
                               str(SCRIPTS.parent / 'playbooks/emby-operator-credential.yml'),
                               '--extra-vars', json.dumps(extra)]
                    if check:
                        command.append('--check')
                    result = subprocess.run(command, env=environment, text=True,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            timeout=45)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Require the pinned host and one explicit bounded action',
                                  result.stdout)
                    self.assertNotIn('TASK [Check the pinned operator root', result.stdout)
                    self.assertNotIn('TASK [Load only the dedicated operator credential',
                                     result.stdout)
                    self.assertFalse(sentinel.exists())

            # Execute the runbook's provision command with --check: its JSON
            # Boolean must pass input validation and reach the secret-free root
            # check, which safely refuses the absent production root in this job.
            doc = (SCRIPTS.parent / 'docs/emby-operator-credential-provisioning.md').read_text()
            examples = re.findall(r'```sh\n(.*?)\n```', doc, re.S)
            provision = next(x for x in examples if 'credential_action=provision' in x)
            command = shlex.split(provision.replace('\\\n', ' '))
            command[1] = str(SCRIPTS.parent / command[1])
            command += ['-i', str(inventory), '--check']
            self.assertFalse(Path(PRODUCTION_ROOT).exists())
            result = subprocess.run(command, env=environment, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('TASK [Check the pinned operator root', result.stdout)
            self.assertNotIn('TASK [Load only the dedicated operator credential', result.stdout)
            self.assertFalse(sentinel.exists())

    def test_helper_has_no_path_identity_or_command_inputs(self):
        source = (SCRIPTS / 'emby-operator-credential.py').read_text()
        self.assertEqual(source.count('add_argument('), 3)
        for forbidden in ('subprocess', 'os.system', 'urllib', 'socket', 'http.client', 'environ',
                          'shutil', 'eval(', 'exec(', 'rmdir', 'listdir', 'scandir'):
            self.assertNotIn(forbidden, source)
        self.assertEqual(helper.ROOT, PRODUCTION_ROOT)


if __name__ == '__main__':
    unittest.main()
