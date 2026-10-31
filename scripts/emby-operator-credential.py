#!/usr/bin/env python3
"""Fixed-root provisioning of the protected Emby operator credential.

Only the retained Igor operator root on homelab is supported. No argument
selects a path, host, identity, interpreter or command. The default action is
an offline check: it never reads standard input, never opens the credential or
the journal and never changes a file. Mutating actions require --apply, read the
credential JSON from standard input and touch only fixed names directly inside
the pinned root. Output carries metadata only, never credential contents.
"""
import argparse
import fcntl
import json
import os
from pathlib import PurePosixPath
import re
import stat
import sys

ROOT = '/var/lib/holonet-operator/igor-pr116-retain-20261031-next'
CONFIG = 'config.json'
CREDENTIAL = 'credential.json'
JOURNAL = 'journal'
CREDENTIAL_STAGE = '.credential.json.stage'
CONFIG_STAGE = '.config.json.stage'
USERNAME = 'igor'
PINNED = {
    'origin': 'https://emby.ryzhkov.dev',
    'server': '3c86e6d7ff6c4a3296522cdf03d22bb0',
    'actor': '545014d1c9d1416b8e0b5a445d6ff9ec',
    'recipient': '545014d1c9d1416b8e0b5a445d6ff9ec',
    'library': '3',
    'library_guid': 'c4ba438d95214fc2b7efba5bd9929b28',
    'recipe': 'igor-movies-retain116',
    'credential_file': ROOT + '/' + CREDENTIAL,
    'journal_directory': ROOT + '/' + JOURNAL,
}
CONFIG_KEYS = frozenset({'enabled', *PINNED})
ACTIONS = ('check', 'provision', 'rotate', 'retire')
EXPECT = re.compile(r'absent|[0-9]+:[0-9]+:[0-9]+')
MAX_BYTES = 16 * 1024
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# Production trusts only root and walks every ancestor from '/'. The synthetic
# tests substitute their own uid and a scratch anchor; no CLI option does.
OWNER_UID = 0
ANCHOR = '/'


class Refused(Exception):
    """Expected refusal. Messages never contain paths or credential data."""


def require(condition, message):
    if not condition:
        raise Refused(message)


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result

    def constant(_):
        raise Refused('Nonfinite JSON')
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise Refused('Invalid JSON') from None


def open_root(anchor, root):
    """Hold the operator root, opening each component without following links."""
    a, p = PurePosixPath(anchor), PurePosixPath(root)
    require(a.is_absolute() and p.is_absolute() and '..' not in a.parts + p.parts,
            'Unsafe operator root')
    try:
        relative = p.relative_to(a)
    except ValueError:
        raise Refused('Unsafe operator root') from None
    require(relative.parts, 'Unsafe operator root')
    fd = os.open(str(a), DIRECTORY)
    try:
        for part in (None, *relative.parts):
            if part is not None:
                nxt = os.open(part, DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = nxt
            s = os.fstat(fd)
            require(s.st_uid in (0, OWNER_UID) and not s.st_mode & 0o022,
                    'Unsafe operator root ancestor')
        s = os.fstat(fd)
        require(s.st_uid == OWNER_UID and stat.S_IMODE(s.st_mode) == 0o700,
                'Operator root must be owner-only mode 0700')
        held, fd = fd, None
        return held
    finally:
        if fd is not None:
            os.close(fd)


def leaf(root_fd, name):
    try:
        return os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def safe_file(s):
    return (s is not None and stat.S_ISREG(s.st_mode) and s.st_uid == OWNER_UID and
            stat.S_IMODE(s.st_mode) == 0o600 and s.st_nlink == 1 and s.st_size <= MAX_BYTES)


def identity(s):
    """Metadata guard token. It never depends on file contents."""
    return '%d:%d:%d' % (s.st_dev, s.st_ino, s.st_ctime_ns)


def same_inode(a, b):
    return a is not None and b is not None and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def read_config(root_fd):
    fd = os.open(CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                 dir_fd=root_fd)
    try:
        s = os.fstat(fd)
        require(safe_file(s), 'Unsafe operator configuration file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
    finally:
        os.close(fd)
    require(len(raw) <= MAX_BYTES, 'Operator configuration too large')
    config = strict_json(raw)
    require(type(config) is dict and set(config) == CONFIG_KEYS,
            'Unexpected operator configuration fields')
    require(type(config['enabled']) is bool, 'Invalid operator enable flag')
    for key, value in PINNED.items():
        require(type(config[key]) is str and config[key] == value,
                'Operator configuration differs from the pinned scope')
    return {'config': config, 'raw': raw, 'stat': s}


def survey(root_fd):
    """Metadata-only state. Reads the configuration, never credential or journal bytes."""
    state = read_config(root_fd)
    journal = leaf(root_fd, JOURNAL)
    require(journal is not None and stat.S_ISDIR(journal.st_mode) and
            journal.st_uid == OWNER_UID and stat.S_IMODE(journal.st_mode) == 0o700,
            'Journal directory missing or unsafe')
    credential = leaf(root_fd, CREDENTIAL)
    require(credential is None or safe_file(credential),
            'Unsafe credential file; refusing to adopt or repair it')
    for name in (CREDENTIAL_STAGE, CONFIG_STAGE):
        require(leaf(root_fd, name) is None,
                'Staging residue present; protected manual inspection required')
    state['credential'] = credential
    return state


def summary(state):
    credential = state['credential']
    return {'config_enabled': state['config']['enabled'],
            'credential': 'absent' if credential is None else 'present',
            'credential_id': None if credential is None else identity(credential)}


def preconditions(action, state, expect):
    credential = state['credential']
    if action == 'provision':
        require(not state['config']['enabled'], 'Provisioning requires the operator disabled')
        require(credential is None, 'Provisioning requires an absent credential; use rotate')
    elif action == 'rotate':
        require(credential is not None, 'Rotation requires an installed credential')
        require(expect == identity(credential), 'Credential differs from the reviewed check')
    elif action == 'retire':
        observed = 'absent' if credential is None else identity(credential)
        require(expect == observed, 'Credential differs from the reviewed check')


def would_change(action, state):
    if action == 'retire':
        return state['config']['enabled'] or state['credential'] is not None
    return action in ('provision', 'rotate')


def read_secret(stream):
    require(stream is not None, 'Credential input required')
    raw = stream.read(MAX_BYTES + 1)
    require(type(raw) is bytes and len(raw) <= MAX_BYTES, 'Invalid credential input')
    secret = strict_json(raw)
    require(type(secret) is dict and set(secret) == {'username', 'password'},
            'Credential input must hold only username and password')
    for key in ('username', 'password'):
        value = secret[key]
        require(isinstance(value, str) and 1 <= len(value) <= 1024 and
                not any(ord(x) < 32 for x in value), 'Invalid credential input')
    require(secret['username'] == USERNAME, 'Credential input is not the pinned operator identity')
    payload = json.dumps({'username': secret['username'], 'password': secret['password']},
                         separators=(',', ':')).encode()
    secret.clear()
    return payload


def remove_exact(root_fd, name, created):
    """Remove a fixed name only while it is still the inode this run created."""
    if same_inode(leaf(root_fd, name), created):
        os.unlink(name, dir_fd=root_fd)


def write_new(root_fd, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=root_fd)
    created = None
    try:
        created = os.fstat(fd)
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
        written = os.fstat(fd)
        os.close(fd)
        fd = None
        return written
    except BaseException:
        if fd is not None:
            os.close(fd)
        if created is not None:
            remove_exact(root_fd, name, created)
        raise


def stage_config(root_fd, state, enabled):
    """Stage the configuration with only the enable flag's literal changed."""
    raw, count = re.subn(rb'("enabled"\s*:\s*)(?:true|false)',
                         lambda m: m.group(1) + (b'true' if enabled else b'false'), state['raw'])
    expected = dict(state['config'], enabled=enabled)
    require(count == 1 and strict_json(raw) == expected,
            'Configuration enable flag cannot be changed in place')
    return write_new(root_fd, CONFIG_STAGE, raw)


def commit_config(root_fd, state, staged):
    """Replace the configuration only while it is the exact file and bytes surveyed."""
    try:
        current = read_config(root_fd)
        require(same_inode(current['stat'], state['stat']) and current['raw'] == state['raw'],
                'Configuration changed during provisioning')
        os.rename(CONFIG_STAGE, CONFIG, src_dir_fd=root_fd, dst_dir_fd=root_fd)
    except BaseException:
        remove_exact(root_fd, CONFIG_STAGE, staged)
        raise
    os.fsync(root_fd)


def provision(root_fd, state, payload):
    staged = write_new(root_fd, CREDENTIAL_STAGE, payload)
    installed = False
    try:
        os.link(CREDENTIAL_STAGE, CREDENTIAL, src_dir_fd=root_fd, dst_dir_fd=root_fd,
                follow_symlinks=False)
        installed = True
        os.unlink(CREDENTIAL_STAGE, dir_fd=root_fd)
        os.fsync(root_fd)
        final = leaf(root_fd, CREDENTIAL)
        require(same_inode(final, staged) and safe_file(final), 'Credential staging verification failed')
        config_staged = stage_config(root_fd, state, True)
    except BaseException:
        # Fail closed: the operator stays disabled and only this run's inode is removed.
        remove_exact(root_fd, CREDENTIAL_STAGE, staged)
        if installed:
            remove_exact(root_fd, CREDENTIAL, staged)
        os.fsync(root_fd)
        raise
    try:
        commit_config(root_fd, state, config_staged)
    except Refused:
        remove_exact(root_fd, CREDENTIAL, staged)
        os.fsync(root_fd)
        raise
    except OSError:
        if leaf(root_fd, CONFIG_STAGE) is None and not same_inode(leaf(root_fd, CONFIG), config_staged):
            remove_exact(root_fd, CREDENTIAL, staged)
            os.fsync(root_fd)
        raise


def rotate(root_fd, state, payload):
    staged = write_new(root_fd, CREDENTIAL_STAGE, payload)
    try:
        current = leaf(root_fd, CREDENTIAL)
        require(safe_file(current) and identity(current) == identity(state['credential']),
                'Credential changed during rotation')
        os.rename(CREDENTIAL_STAGE, CREDENTIAL, src_dir_fd=root_fd, dst_dir_fd=root_fd)
    except BaseException:
        remove_exact(root_fd, CREDENTIAL_STAGE, staged)
        raise
    os.fsync(root_fd)
    require(same_inode(leaf(root_fd, CREDENTIAL), staged), 'Credential rotation verification failed')


def retire(root_fd, state):
    # Disable first so any later failure leaves the operator refusing to run.
    if state['config']['enabled']:
        commit_config(root_fd, state, stage_config(root_fd, state, False))
    if state['credential'] is not None:
        current = leaf(root_fd, CREDENTIAL)
        require(safe_file(current) and identity(current) == identity(state['credential']),
                'Credential changed during retirement')
        os.unlink(CREDENTIAL, dir_fd=root_fd)
        os.fsync(root_fd)


def run(action, *, apply=False, expect=None, stdin=None):
    require(action in ACTIONS, 'Unknown action')
    require(not (apply and action == 'check'), 'Check never applies')
    require((expect is not None) == (action in ('rotate', 'retire')),
            'Rotation and retirement need exactly the reviewed credential id')
    require(expect is None or EXPECT.fullmatch(expect), 'Invalid credential id')
    require(os.geteuid() == OWNER_UID and os.getuid() == OWNER_UID, 'Root operator required')
    umask = os.umask(0o077)
    root_fd = None
    try:
        root_fd = open_root(ANCHOR, ROOT)
        if apply:
            try:
                fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('Another provisioning run holds the operator root') from None
        state = survey(root_fd)
        preconditions(action, state, expect)
        change = would_change(action, state)
        if not apply:
            return dict(summary(state), action=action, applied=False, would_change=change)
        if action in ('provision', 'rotate'):
            payload = read_secret(stdin)
            (provision if action == 'provision' else rotate)(root_fd, state, payload)
        elif change:
            retire(root_fd, state)
        return dict(summary(survey(root_fd)), action=action, applied=True, changed=change)
    except OSError:
        raise Refused('Operator root unavailable, unsafe or changed') from None
    finally:
        if root_fd is not None:
            os.close(root_fd)
        os.umask(umask)


def main(argv=None, stdin=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=ACTIONS, default='check')
    parser.add_argument('--apply', action='store_true',
                        help='Perform the action; credential JSON is read from standard input')
    parser.add_argument('--expect-credential-id',
                        help='Credential id reported by a reviewed check, or "absent"')
    args = parser.parse_args(argv)
    try:
        needs_secret = args.apply and args.action in ('provision', 'rotate')
        result = run(args.action, apply=args.apply, expect=args.expect_credential_id,
                     stdin=(stdin or sys.stdin.buffer) if needs_secret else None)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Refused as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        print('Provisioning failed; protected manual inspection required', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
