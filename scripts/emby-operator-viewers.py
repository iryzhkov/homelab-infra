#!/usr/bin/env python3
"""Fixed-cohort protected configuration for the additional Emby viewer recipients.

Only the six pinned viewer recipients on homelab are supported. No argument
selects a path, host, identity, credential, interpreter or command; the optional
recipient name only narrows the fixed cohort. The default action is an offline
check that changes nothing. Mutating actions require --apply:

- provision creates only missing fixed names below the pinned viewers root: the
  root, one 0700 directory per recipient, its empty 0700 journal directory and
  its 0600 config. Existing names are verified, never replaced or repaired.
- enable and disable change only the enable literal of existing configs.

Each config reuses the retained Igor credential pointer. This helper never
opens, stats, copies, hashes, rotates or removes that credential, and never
opens anything inside a journal directory. Output carries metadata only.
"""
import argparse
import fcntl
import json
import os
from pathlib import PurePosixPath
import re
import stat
import sys

PARENT = '/var/lib/holonet-operator'
ROOT = PARENT + '/viewers-20261101'
CONFIG = 'config.json'
CONFIG_STAGE = '.config.json.stage'
JOURNAL = 'journal'
IGOR = '545014d1c9d1416b8e0b5a445d6ff9ec'
# Never managed by this helper: Igor keeps the retained root, and the catalog
# service account keeps its existing reader authority.
EXCLUDED = frozenset({IGOR, '54b7d36cef804d3c8478474553ed3a82'})
COMMON = {
    'origin': 'https://emby.ryzhkov.dev',
    'server': '3c86e6d7ff6c4a3296522cdf03d22bb0',
    'actor': IGOR,
    'library': '3',
    'library_guid': 'c4ba438d95214fc2b7efba5bd9929b28',
    # Pointer only, written into each config as a string.
    'credential_file': '/var/lib/holonet-operator/igor-pr116-retain-20261031-next/credential.json',
}
COHORT = {
    'kostya': 'db2f3bfb1f84438ebcde94f228183011',
    'lisa': '313e39bb4d8c4b92b81d4b170c3f1b8e',
    'oleg': 'c06c7d2c3e224d8ba2e466efa5bcb97b',
    'tanya': '22703968ab0b48eebb4faf7c1d16262c',
    'tetiana': 'e05efa68303043b893b0f5c2e200ccd7',
    'tv': 'b8f4f6de49b443f1948cf0118679e863',
}
FIELDS = ('enabled', 'origin', 'server', 'actor', 'recipient', 'library', 'library_guid',
          'recipe', 'credential_file', 'journal_directory')
ACTIONS = ('check', 'provision', 'enable', 'disable')
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


def check_cohort():
    ids = list(COHORT.values())
    require(all(re.fullmatch('[0-9a-f]{32}', i) for i in ids) and len(set(ids)) == len(ids)
            and not EXCLUDED & set(ids) and
            all(re.fullmatch('[a-z]{1,32}', n) for n in COHORT), 'Invalid fixed cohort')


def pinned(name):
    """Every config field except enabled, from the fixed cohort and root only."""
    return dict(COMMON, recipient=COHORT[name], recipe=name + '-movies',
                journal_directory=ROOT + '/' + name + '/' + JOURNAL)


def config_bytes(name, enabled):
    config = dict(pinned(name), enabled=enabled)
    return (json.dumps({k: config[k] for k in FIELDS}, indent=2) + '\n').encode()


def root_name():
    p, parent = PurePosixPath(ROOT), PurePosixPath(PARENT)
    require(p.parent == parent and re.fullmatch('[a-z0-9-]{1,64}', p.name), 'Unsafe viewer root')
    return p.name


def open_parent():
    """Hold the operator parent, opening each component without following links."""
    a, p = PurePosixPath(ANCHOR), PurePosixPath(PARENT)
    require(a.is_absolute() and p.is_absolute() and '..' not in a.parts + p.parts,
            'Unsafe operator parent')
    try:
        relative = p.relative_to(a)
    except ValueError:
        raise Refused('Unsafe operator parent') from None
    require(relative.parts, 'Unsafe operator parent')
    fd = os.open(str(a), DIRECTORY)
    try:
        for part in (None, *relative.parts):
            if part is not None:
                nxt = os.open(part, DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = nxt
            s = os.fstat(fd)
            require(s.st_uid in (0, OWNER_UID) and not s.st_mode & 0o022,
                    'Unsafe operator parent ancestor')
        held, fd = fd, None
        return held
    finally:
        if fd is not None:
            os.close(fd)


def leaf(dir_fd, name):
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def safe_dir(s):
    return (s is not None and stat.S_ISDIR(s.st_mode) and s.st_uid == OWNER_UID and
            stat.S_IMODE(s.st_mode) == 0o700)


def safe_file(s):
    return (s is not None and stat.S_ISREG(s.st_mode) and s.st_uid == OWNER_UID and
            stat.S_IMODE(s.st_mode) == 0o600 and s.st_nlink == 1 and s.st_size <= MAX_BYTES)


def same_inode(a, b):
    return a is not None and b is not None and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def open_dir(dir_fd, name, expected):
    """Open a surveyed directory, requiring the same safe inode."""
    fd = os.open(name, DIRECTORY, dir_fd=dir_fd)
    s = os.fstat(fd)
    if not (same_inode(s, expected) and safe_dir(s)):
        os.close(fd)
        raise Refused('Directory changed or unsafe')
    return fd


def ensure_dir(dir_fd, name, expected):
    """Create a missing owner-only directory, or open the surveyed one."""
    if expected is not None:
        return open_dir(dir_fd, name, expected)
    os.mkdir(name, 0o700, dir_fd=dir_fd)
    os.fsync(dir_fd)
    fd = os.open(name, DIRECTORY, dir_fd=dir_fd)
    if not safe_dir(os.fstat(fd)):
        os.close(fd)
        raise Refused('Created directory is unsafe; protected manual inspection required')
    return fd


def read_config(dir_fd, name):
    fd = os.open(CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                 dir_fd=dir_fd)
    try:
        s = os.fstat(fd)
        require(safe_file(s), 'Unsafe viewer configuration file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
    finally:
        os.close(fd)
    require(len(raw) <= MAX_BYTES, 'Viewer configuration too large')
    config = strict_json(raw)
    require(type(config) is dict and set(config) == set(FIELDS),
            'Unexpected viewer configuration fields')
    require(type(config['enabled']) is bool, 'Invalid viewer enable flag')
    for key, value in pinned(name).items():
        require(type(config[key]) is str and config[key] == value,
                'Viewer configuration differs from the pinned scope')
    return {'config': config, 'raw': raw, 'stat': s}


def member_state(root_fd, name):
    s = leaf(root_fd, name)
    if s is None:
        return {'directory': None, 'journal': None, 'config': None}
    require(safe_dir(s), 'Unsafe recipient directory')
    fd = open_dir(root_fd, name, s)
    try:
        # Metadata only; nothing inside the journal directory is opened.
        journal = leaf(fd, JOURNAL)
        require(journal is None or safe_dir(journal),
                'Unsafe journal directory; refusing to adopt or repair it')
        require(leaf(fd, CONFIG_STAGE) is None,
                'Staging residue present; protected manual inspection required')
        config = None if leaf(fd, CONFIG) is None else read_config(fd, name)
    finally:
        os.close(fd)
    return {'directory': s, 'journal': journal, 'config': config}


def survey(parent_fd):
    members = {n: {'directory': None, 'journal': None, 'config': None} for n in COHORT}
    root = leaf(parent_fd, root_name())
    if root is None:
        return {'root': None, 'members': members}
    require(safe_dir(root), 'Unsafe viewer root')
    root_fd = open_dir(parent_fd, root_name(), root)
    try:
        for name in COHORT:
            members[name] = member_state(root_fd, name)
    finally:
        os.close(root_fd)
    return {'root': root, 'members': members}


def summary(state):
    def present(s):
        return 'absent' if s is None else 'present'

    def enabled(c):
        return 'absent' if c is None else 'enabled' if c['config']['enabled'] else 'disabled'
    return {'root': present(state['root']),
            'recipients': {n: {'directory': present(m['directory']),
                               'journal': present(m['journal']),
                               'config': enabled(m['config'])}
                           for n, m in sorted(state['members'].items())}}


def preconditions(action, state, names):
    if action in ('enable', 'disable'):
        for name in names:
            m = state['members'][name]
            require(all(m[k] is not None for k in ('directory', 'journal', 'config')),
                    'Recipient not provisioned; use provision')


def would_change(action, state, names):
    if action == 'check':
        return False
    if action == 'provision':
        return state['root'] is None or any(
            v is None for name in names for v in state['members'][name].values())
    target = action == 'enable'
    return any(state['members'][n]['config']['config']['enabled'] != target for n in names)


def remove_exact(dir_fd, name, created):
    """Remove a fixed name only while it is still the inode this run created."""
    if same_inode(leaf(dir_fd, name), created):
        os.unlink(name, dir_fd=dir_fd)


def write_new(dir_fd, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=dir_fd)
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
            remove_exact(dir_fd, name, created)
        raise


def install_config(member_fd, name):
    """Link a fully written config into place; an existing name is never replaced."""
    data = config_bytes(name, True)
    staged = write_new(member_fd, CONFIG_STAGE, data)
    try:
        os.link(CONFIG_STAGE, CONFIG, src_dir_fd=member_fd, dst_dir_fd=member_fd,
                follow_symlinks=False)
    finally:
        remove_exact(member_fd, CONFIG_STAGE, staged)
        os.fsync(member_fd)
    final = read_config(member_fd, name)
    require(same_inode(final['stat'], staged) and final['raw'] == data,
            'Configuration staging verification failed')


def provision(parent_fd, state, names):
    root_fd = ensure_dir(parent_fd, root_name(), state['root'])
    try:
        for name in names:
            m = state['members'][name]
            member_fd = ensure_dir(root_fd, name, m['directory'])
            try:
                os.close(ensure_dir(member_fd, JOURNAL, m['journal']))
                if m['config'] is None:
                    install_config(member_fd, name)
            finally:
                os.close(member_fd)
    finally:
        os.close(root_fd)


def stage_config(member_fd, current, enabled):
    """Stage the config with only the enable flag's literal changed."""
    raw, count = re.subn(rb'("enabled"\s*:\s*)(?:true|false)',
                         lambda m: m.group(1) + (b'true' if enabled else b'false'), current['raw'])
    require(count == 1 and strict_json(raw) == dict(current['config'], enabled=enabled),
            'Configuration enable flag cannot be changed in place')
    return write_new(member_fd, CONFIG_STAGE, raw)


def commit_config(member_fd, name, surveyed, staged):
    """Replace the config only while it is the exact file and bytes surveyed."""
    try:
        current = read_config(member_fd, name)
        require(same_inode(current['stat'], surveyed['stat']) and current['raw'] == surveyed['raw'],
                'Configuration changed during update')
        os.rename(CONFIG_STAGE, CONFIG, src_dir_fd=member_fd, dst_dir_fd=member_fd)
    except BaseException:
        remove_exact(member_fd, CONFIG_STAGE, staged)
        raise
    os.fsync(member_fd)


def toggle(parent_fd, state, names, enabled):
    root_fd = open_dir(parent_fd, root_name(), state['root'])
    try:
        for name in names:
            m = state['members'][name]
            if m['config']['config']['enabled'] == enabled:
                continue
            member_fd = open_dir(root_fd, name, m['directory'])
            try:
                commit_config(member_fd, name, m['config'],
                              stage_config(member_fd, m['config'], enabled))
            finally:
                os.close(member_fd)
    finally:
        os.close(root_fd)


def run(action, *, apply=False, recipient=None):
    require(action in ACTIONS, 'Unknown action')
    require(not (apply and action == 'check'), 'Check never applies')
    require(recipient is None or recipient in COHORT, 'Unknown recipient')
    check_cohort()
    require(os.geteuid() == OWNER_UID and os.getuid() == OWNER_UID, 'Root operator required')
    names = [recipient] if recipient is not None else sorted(COHORT)
    umask = os.umask(0o077)
    parent_fd = None
    try:
        parent_fd = open_parent()
        if apply:
            try:
                fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('Another viewer provisioning run holds the operator parent') from None
        state = survey(parent_fd)
        preconditions(action, state, names)
        change = would_change(action, state, names)
        if not apply:
            return dict(summary(state), action=action, applied=False, would_change=change)
        if change and action == 'provision':
            provision(parent_fd, state, names)
        elif change:
            toggle(parent_fd, state, names, action == 'enable')
        return dict(summary(survey(parent_fd)), action=action, applied=True, changed=change)
    except OSError:
        raise Refused('Viewer root unavailable, unsafe or changed') from None
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
        os.umask(umask)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=ACTIONS, default='check')
    parser.add_argument('--apply', action='store_true', help='Perform the action')
    parser.add_argument('--recipient', choices=sorted(COHORT),
                        help='Limit the action to one fixed cohort member')
    args = parser.parse_args(argv)
    try:
        result = run(args.action, apply=args.apply, recipient=args.recipient)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Refused as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        print('Viewer provisioning failed; protected manual inspection required', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
