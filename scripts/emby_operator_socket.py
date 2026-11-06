#!/usr/bin/env python3
"""Socket-activated one-request root worker for MCP playlist management.

systemd starts one instance per connection (Accept=yes) and passes the
connected AF_UNIX stream socket as fd 3. The worker checks SO_PEERCRED against
the root-owned policy, reads exactly one newline-terminated JSON request,
intersects it with the fixed root ceiling, runs it through the shared operator
entry (or, for the read-only movie-metadata operation, the same adapter without
the Operator), writes one response and exits. It takes no arguments and adds no
listener, spool, subprocess, retry, journal or credential.

The calling MCP service is the trusted JWT authenticator. The worker does not
verify tokens; it authenticates the service by peer UID and GID and enforces
its own issuer, subject, namespace, resource, recipient and operation ceiling.
See docs/emby-operator-mcp.md for the version 1 wire contract.
"""
import copy
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import select
import socket
import struct
import sys
import time

from emby_operator_adapter import (OperatorAdapter, load_config, load_credentials, private_read,
                                   strict_json, catalog_id, printable, ImporterInventoryAdapter,
                                   ImporterPauseAdapter, IMPORTER_BACKENDS)
from emby_operator_custody import (OPERATION as CUSTODY_OPERATION, ACTIONS as CUSTODY_ACTIONS,
                                    validate_block as validate_custody_block,
                                    request as custody_request, attest as custody_attest)
from emby_operator_torrents import (OPERATION as TORRENT_OPERATION, ACTIONS as TORRENT_ACTIONS,
                                    path as torrent_path, inventory as torrent_inventory)
from emby_operator_entry import invoke, operator_session
from emby_operator_recommendations import (DONE_LIMIT, LIST_OPERATIONS, RETIRE_AT,
                                           TYPED_OPERATIONS, Journal, Refused, canonical_id,
                                           catalog_ids, checked_title, digest, movie_ids, opaque,
                                           row_position, series_library_ids, series_map,
                                           typed_items)
from emby_operator_adapter import CREDIT_ROLES, MAX_CATALOG_LIMIT, MAX_CATALOG_OFFSET

VERSION = 1
# Fixed root-owned policy. No argument, environment or request selects a path.
POLICY_PATH = '/etc/holonet-playlists/policy.json'
LISTEN_FD = 3
MAX_REQUEST = 65536    # bytes, including the newline
MAX_RESPONSE = 262144  # bytes, including the newline
MAX_SECONDS = 120      # worker ceiling for one management call
READ_SECONDS = 5       # whole request read, before any caller deadline is known
WRITE_SECONDS = 2      # response write
MAX_RECIPIENTS = 16
MAX_ACTIONS = 32
MAX_EPOCH_SECONDS = 10 ** 11
MAX_EPOCH_MS = 10 ** 14
# The warning means the next new request first retires the active completed
# keys into the replay archive. It needs no operator action.
CAPACITY_WARNING_FREE = 16
if DONE_LIMIT - CAPACITY_WARNING_FREE != RETIRE_AT:
    raise ImportError('Worker capacity warning differs from the core retirement watermark')
BASE_ACTIONS = frozenset({'catalog.read', 'playlist.read', 'home.read'})
# Every core list operation and the actions it needs beyond BASE_ACTIONS. Its
# exact arguments are the core LIST_OPERATIONS table the shared entry uses.
# create-list may grant the recipient read access to the new playlist
# (POST /Items/Access), so it needs object.share even when the recipient is the
# actor and no grant is sent. delete-list deletes the owned playlist.
LIST_ACTIONS = {
    'create-list': frozenset({'playlist.manage', 'home.manage', 'object.share'}),
    'set-members': frozenset({'playlist.manage'}),
    'set-title': frozenset({'home.manage'}),
    'attach-row': frozenset({'home.manage'}),
    'detach-row': frozenset({'home.manage'}),
    'move-row': frozenset({'home.manage'}),
    'delete-list': frozenset({'playlist.manage', 'home.manage', 'object.delete'}),
}
if set(LIST_ACTIONS) != set(LIST_OPERATIONS):
    raise ImportError('Worker list actions differ from the core list operations')
# Read-only Movie metadata for the HoloNet playback tools. Its caller carries
# exactly the fixed history actions and the verified history_users grant;
# every other operation keeps the original caller shape.
IMPORTER_OPERATION = 'janitor-importers-inventory'
IMPORTER_ASSESSMENT = 'janitor-importers-assessment'
IMPORTER_PAUSE = 'janitor-importers-pause'
IMPORTER_OPERATIONS = (IMPORTER_OPERATION, IMPORTER_ASSESSMENT, IMPORTER_PAUSE)
IMPORTER_PAUSE_ACTIONS = ['janitor.importers.read', 'janitor.importers.pause']
IMPORTER_ACTIONS = ['janitor.importers.read']
IMPORTER_CALLER_FIELDS = frozenset({'issuer', 'subject', 'namespace', 'server',
    'target_library', 'actions', 'expires_at', 'policy_revision'})
JANITOR_OPERATION = 'janitor-protections'
JANITOR_ACTIONS = ['janitor.read', 'history.read', 'userdata.read']
JANITOR_CALLER_FIELDS = frozenset({'issuer', 'subject', 'namespace', 'server',
    'target_library', 'actions', 'expires_at', 'history_users', 'policy_revision'})
METADATA_OPERATION = 'movie-metadata'
HISTORY_ACTIONS = ['catalog.read', 'playlist.read', 'home.read', 'history.read']
# Read-only Series facts for the HoloNet Series tools. Caller: the history
# caller plus a nonempty series_libraries; management callers may carry an
# optional series_libraries; movie-metadata keeps its exact original caller.
SERIES_OPERATIONS = ('series-seeds', 'recipient-series', 'series-catalog')
SERIES_REQUEST = {'series-seeds': {'episode_ids'}, 'recipient-series': {'ids'}}
CATALOG_REQUIRED = frozenset({'library_id', 'limit'})
CATALOG_OPTIONAL = frozenset({'offset', 'person_id', 'person_role'})
WORKER_OPERATIONS = ('show', 'reconcile', 'preview', 'apply', METADATA_OPERATION,
                     *SERIES_OPERATIONS, JANITOR_OPERATION, *IMPORTER_OPERATIONS, CUSTODY_OPERATION, TORRENT_OPERATION)
REQUEST_FIELDS = frozenset({'version', 'caller', 'recipient', 'deadline_unix_ms',
                            'operation', 'request'})
PINNED_CALLER = ('issuer', 'subject', 'namespace', 'server', 'library', 'catalog_user')
CALLER_FIELDS = frozenset(PINNED_CALLER + ('actions', 'expires_at'))
HISTORY_CALLER_FIELDS = CALLER_FIELDS | {'history_users'}
SERIES_CALLER_FIELDS = HISTORY_CALLER_FIELDS | {'series_libraries'}
POLICY_FIELDS = frozenset({'version', 'enabled', 'peer', 'caller', 'recipients'})
# Optional opt-ins: without history_users the policy permits no history read;
# without series_libraries it permits no Series read or typed list state.
POLICY_OPTIONAL = frozenset({'history_users', 'series_libraries', 'janitor', 'janitor_importers', 'janitor_importers_pause', 'janitor_custody', 'janitor_torrents'})
ERROR_CODES = frozenset({'refused', 'busy', 'unfinished', 'deadline', 'unavailable'})
HEX32 = re.compile('[0-9a-f]{32}')
HEX64 = re.compile('[0-9a-f]{64}')
ACTION = re.compile(r'[A-Za-z0-9._:-]{1,64}')
# Core refusals are constant text; anything else is replaced, never echoed.
SAFE_TEXT = re.compile(r"[A-Za-z0-9 ;:,.()/'-]{1,160}")
UNFINISHED = frozenset({'Another request is unfinished', 'Unfinished operation'})
FAILED = 'Operator failed; protected manual inspection required'
STOPPED = {'deadline': 'Stopped at caller deadline; use show before resubmitting the same request',
           'cancelled': 'Caller cancelled; use show before resubmitting the same request'}


class WireError(Exception):
    """One typed response error with fixed safe text."""
    def __init__(self, code, message, operation=None, list_id=None):
        super().__init__(message)
        assert code in ERROR_CODES and SAFE_TEXT.fullmatch(message)
        self.code, self.message = code, message
        self.operation, self.list_id = operation, list_id


def check(condition, code, message):
    if not condition:
        raise WireError(code, message)


def bounded_text(value, limit):
    return (isinstance(value, str) and 1 <= len(value) <= limit and
            all(ord(c) >= 32 and ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF
                for c in value))


def bounded_int(value, limit):
    return type(value) is int and 0 <= value <= limit


def hex_users(value):
    """1..16 unique lowercase 32-hex user IDs."""
    return (type(value) is list and 1 <= len(value) <= MAX_RECIPIENTS and
            all(isinstance(u, str) and HEX32.fullmatch(u) for u in value) and
            len(set(value)) == len(value))


def validate_policy(value):
    """Root ceiling: one peer, one exact caller and the fixed recipient config map.

    Optional history_users opts those configured recipients into the read-only
    movie-metadata operation; a policy without it permits no history read.
    """
    def ok(condition):
        if not condition:
            raise Refused('Invalid management policy')
    ok(type(value) is dict and POLICY_FIELDS <= set(value) <= POLICY_FIELDS | POLICY_OPTIONAL and
       value['version'] == 1 and
       type(value['version']) is int and type(value['enabled']) is bool)
    peer = value['peer']
    ok(type(peer) is dict and set(peer) == {'uid', 'gid'} and
       bounded_int(peer['uid'], 2 ** 31 - 1) and bounded_int(peer['gid'], 2 ** 31 - 1))
    caller = value['caller']
    ok(type(caller) is dict and set(caller) == set(PINNED_CALLER) and
       all(bounded_text(caller[k], 512) for k in PINNED_CALLER) and
       HEX32.fullmatch(caller['server']) and HEX32.fullmatch(caller['catalog_user']))
    try:
        opaque(caller['library'])
    except Refused:
        ok(False)
    recipients = value['recipients']
    ok(type(recipients) is dict and 1 <= len(recipients) <= MAX_RECIPIENTS)
    for recipient, path in recipients.items():
        ok(HEX32.fullmatch(recipient) and isinstance(path, str) and len(path) <= 4096 and
           path.startswith('/') and '..' not in PurePosixPath(path).parts and
           len(PurePosixPath(path).parts) > 2)
    ok(len(set(recipients.values())) == len(recipients))
    if 'history_users' in value:
        ok(hex_users(value['history_users']) and set(value['history_users']) <= set(recipients))
    if 'series_libraries' in value:
        # The dedicated Series ceiling, separate from the Movie catalog library.
        try:
            series = series_map(value['series_libraries'])
        except Refused:
            ok(False)
        ok(caller['library'] not in series)
    if 'janitor' in value:
        janitor = value['janitor']
        ok(type(janitor) is dict and set(janitor) ==
           {'protected_users', 'target_library', 'revision'})
        ok(hex_users(janitor['protected_users']) and
           set(janitor['protected_users']) == set(recipients) ==
           set(value.get('history_users', [])) and
           catalog_id(janitor['target_library']) and
           janitor['target_library'] == caller['library'] and
           isinstance(janitor['revision'], str) and
           re.fullmatch('[0-9a-f]{40,64}', janitor['revision']))
    if 'janitor_torrents' in value:
        block = value['janitor_torrents']
        ok(type(block) is dict and set(block) == {'target_library', 'revision', 'config'})
        ok(catalog_id(block['target_library']) and block['target_library'] == caller['library'] and
           isinstance(block['revision'], str) and re.fullmatch('[0-9a-f]{40,64}', block['revision']))
        try:
            torrent_path(block['config'])
        except (Refused, TypeError, ValueError, UnicodeError):
            ok(False)
    if 'janitor_importers' in value:
        block = value['janitor_importers']
        ok(type(block) is dict and set(block) == {'target_library', 'revision', 'backends'})
        ok(catalog_id(block['target_library']) and block['target_library'] == caller['library'] and
           isinstance(block['revision'], str) and re.fullmatch('[0-9a-f]{40,64}', block['revision']))
        backends = block['backends']
        ok(type(backends) is dict and 1 <= len(backends) <= 2 and
           set(backends) <= IMPORTER_BACKENDS)
        for path in backends.values():
            ok(isinstance(path, str) and 1 <= len(path) <= 4096 and path.startswith('/') and
               str(PurePosixPath(path)) == path and '..' not in PurePosixPath(path).parts and
               len(PurePosixPath(path).parts) > 2)
    if 'janitor_custody' in value:
        ok('janitor_importers' in value)
        try:
            validate_custody_block(value['janitor_custody'], value['janitor_importers'])
        except (Refused, ValueError, TypeError, UnicodeError):
            ok(False)
    if 'janitor_importers_pause' in value:
        pause = value['janitor_importers_pause']
        ok('janitor_importers' in value and type(pause) is dict and set(pause) == {'backends'})
        backends = pause['backends']
        ok(type(backends) is dict and 1 <= len(backends) <= 2 and
           set(backends) <= set(value['janitor_importers']['backends']))
        for ids in backends.values():
            ok(type(ids) is list and 1 <= len(ids) <= 32 and
               all(type(i) is int and 1 <= i <= 2 ** 31 - 1 for i in ids) and
               len(set(ids)) == len(ids))
    return copy.deepcopy(value)


def load_policy():
    if not (os.geteuid() == 0 and os.getuid() == 0):
        raise Refused('Root worker required')
    return validate_policy(strict_json(private_read(POLICY_PATH)))


def internal_key(caller, recipient, logical):
    """Exact v1 derivation. Operation and list are deliberately excluded."""
    material = [caller['issuer'], caller['subject'], caller['namespace'], caller['server'],
                caller['library'], recipient, logical]
    canonical = json.dumps(material, separators=(',', ':'), ensure_ascii=False)
    return 'mcp-' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def peer_credentials(conn):
    pid, uid, gid = struct.unpack('3i', conn.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
    return pid, uid, gid


def peer_state(conn):
    """'open', 'closed' (EOF, half-close, hangup) or 'data' (bytes after the request)."""
    poller = select.poll()
    poller.register(conn.fileno(), select.POLLIN | select.POLLPRI | select.POLLRDHUP)
    events = poller.poll(0)
    if not events:
        return 'open'
    if events[0][1] & (select.POLLHUP | select.POLLERR | select.POLLNVAL | select.POLLRDHUP):
        return 'closed'
    try:
        return 'data' if conn.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) else 'closed'
    except BlockingIOError:
        return 'open'
    except OSError:
        return 'closed'


def read_frame(conn, deadline):
    """Exactly one newline-terminated request of at most MAX_REQUEST bytes."""
    buffer = b''
    while b'\n' not in buffer:
        remaining = deadline - time.monotonic()
        check(remaining > 0, 'deadline', 'Request not received in time')
        conn.settimeout(remaining)
        try:
            chunk = conn.recv(min(65536, MAX_REQUEST + 1 - len(buffer)))
        except socket.timeout:
            raise WireError('deadline', 'Request not received in time') from None
        except OSError:
            raise WireError('refused', 'Incomplete request frame') from None
        check(chunk, 'refused', 'Incomplete request frame')
        buffer += chunk
        check(len(buffer.split(b'\n', 1)[0]) < MAX_REQUEST, 'refused', 'Request too large')
    frame, rest = buffer.split(b'\n', 1)
    check(rest == b'', 'refused', 'Trailing data after request')
    return frame


def parse_request(frame):
    """Exact v1 shape and typed values only; no policy, file or backend access."""
    try:
        text = frame.decode('utf-8')
    except UnicodeDecodeError:
        raise WireError('refused', 'Invalid UTF-8 request') from None
    check(text and text == text.strip(' \t\r'), 'refused', 'Invalid request frame')
    try:
        value = strict_json(text)
    except Refused as error:
        raise WireError('refused', str(error)) from None
    check(type(value) is dict and set(value) == REQUEST_FIELDS, 'refused', 'Invalid request fields')
    check(type(value['version']) is int and value['version'] == VERSION,
          'refused', 'Unsupported version')
    caller = value['caller']
    # Only the read operations carry history_users; Series reads also carry a
    # nonempty series_libraries, management may; movie-metadata never does.
    metadata = value['operation'] == METADATA_OPERATION
    series_read = value['operation'] in SERIES_OPERATIONS
    janitor = value['operation'] == JANITOR_OPERATION
    importer = value['operation'] in (*IMPORTER_OPERATIONS, CUSTODY_OPERATION, TORRENT_OPERATION)
    allowed = ((IMPORTER_CALLER_FIELDS,) if importer else (JANITOR_CALLER_FIELDS,) if janitor else
               (HISTORY_CALLER_FIELDS,) if metadata else (SERIES_CALLER_FIELDS,) if series_read
               else (CALLER_FIELDS, CALLER_FIELDS | {'series_libraries'}))
    pins = ('issuer', 'subject', 'namespace', 'server', 'target_library', 'policy_revision') if (janitor or importer) else PINNED_CALLER
    check(type(caller) is dict and set(caller) in allowed and
          all(bounded_text(caller[k], 512) for k in pins) and
          bounded_int(caller['expires_at'], MAX_EPOCH_SECONDS) and
          type(caller['actions']) is list and len(caller['actions']) <= MAX_ACTIONS and
          all(isinstance(a, str) and ACTION.fullmatch(a) for a in caller['actions']) and
          len(set(caller['actions'])) == len(caller['actions']), 'refused', 'Invalid caller')
    check(not (metadata or series_read or janitor) or hex_users(caller['history_users']),
          'refused', 'Invalid caller')
    if 'series_libraries' in caller:
        try:
            series_library_ids(caller['series_libraries'])
        except Refused:
            raise WireError('refused', 'Invalid caller') from None
    check(isinstance(value['recipient'], str) and HEX32.fullmatch(value['recipient']),
          'refused', 'Invalid recipient')
    check(bounded_int(value['deadline_unix_ms'], MAX_EPOCH_MS), 'refused', 'Invalid deadline')
    operation = value['operation']
    check(isinstance(operation, str) and operation in WORKER_OPERATIONS,
          'refused', 'Unsupported operation')
    value['request'] = typed_request(operation, value['request'])
    return value


def typed_request(operation, request):
    check(type(request) is dict, 'refused', 'Invalid request fields')
    fields = set(request)
    if operation == TORRENT_OPERATION:
        check(fields == {'torrent_hash'} and isinstance(request['torrent_hash'], str) and
              re.fullmatch('[0-9a-f]{40}', request['torrent_hash']),
              'refused', 'Invalid torrent request')
        return copy.deepcopy(request)
    if operation == CUSTODY_OPERATION:
        try:
            return copy.deepcopy(custody_request(request))
        except (Refused, ValueError, TypeError, UnicodeError):
            raise WireError('refused', 'Invalid custody request') from None
    if operation == IMPORTER_PAUSE:
        check(fields == {'backend', 'list_id', 'resource_id', 'operation_id', 'expected'} and
              isinstance(request['backend'], str) and request['backend'] in IMPORTER_BACKENDS and
              type(request['list_id']) is int and 1 <= request['list_id'] <= 2 ** 31 - 1 and
              isinstance(request['operation_id'], str) and HEX32.fullmatch(request['operation_id']),
              'refused', 'Invalid importer pause request')
        try:
            opaque(request['resource_id'])
        except Refused:
            raise WireError('refused', 'Invalid importer resource') from None
        row = request['expected']
        check(type(row) is dict and set(row) ==
              {'id', 'name', 'implementation', 'enabled', 'automatic_add', 'search_on_add'} and
              type(row['id']) is int and row['id'] == request['list_id'] and
              printable(row['name'], 256) and printable(row['implementation'], 64) and
              row['automatic_add'] is True and type(row['search_on_add']) is bool and
              (type(row['enabled']) is bool if request['backend'] == 'radarr'
               else row['enabled'] is None), 'refused', 'Invalid importer expected state')
        return copy.deepcopy(request)
    if operation in IMPORTER_OPERATIONS:
        check(fields == {'backend'} and isinstance(request['backend'], str) and
              request['backend'] in IMPORTER_BACKENDS, 'refused', 'Invalid importer backend')
        return copy.deepcopy(request)
    if operation == JANITOR_OPERATION:
        check(fields == {'target'} and catalog_id(request['target']),
              'refused', 'Invalid janitor target')
        return copy.deepcopy(request)
    if operation in SERIES_OPERATIONS:
        return series_request(operation, request)
    if operation == METADATA_OPERATION:
        check(fields == {'ids'}, 'refused', 'Invalid request fields')
        try:
            movie_ids(request['ids'], 1)
        except Refused as error:
            raise WireError('refused', safe_message(error)) from None
        return copy.deepcopy(request)
    if operation == 'show':
        check(fields in (set(), {'list'}), 'refused', 'Invalid request fields')
    elif operation == 'reconcile':
        check(not fields, 'refused', 'Invalid request fields')
    else:
        target = request.get('operation')
        check(isinstance(target, str) and target in LIST_ACTIONS,
              'refused', 'Unsupported list operation')
        arguments = LIST_OPERATIONS[target]
        if 'items' in fields and target in TYPED_OPERATIONS:
            arguments = TYPED_OPERATIONS[target]
        expected = {'operation', 'list', *arguments}
        if operation == 'apply':
            expected |= {'key', 'intent_digest'}
        check(fields == expected, 'refused', 'Invalid request fields')
    try:
        if 'list' in request:
            opaque(request['list'])
        if 'movies' in request:
            movie_ids(request['movies'], 0)
        if 'items' in request:
            typed_items(request['items'], 1 if request.get('operation') == 'create-list' else 0)
        if 'title' in request:
            checked_title(request['title'])
            check(bounded_text(request['title'], 256), 'refused', 'Invalid journal display title')
        if 'position' in request:
            row_position(request['position'])
        if 'key' in request:
            opaque(request['key'])
    except Refused as error:
        raise WireError('refused', safe_message(error)) from None
    if 'intent_digest' in request:
        check(isinstance(request['intent_digest'], str) and
              HEX64.fullmatch(request['intent_digest']), 'refused', 'Invalid intent digest')
    return copy.deepcopy(request)


def series_request(operation, request):
    """Exact Series read request: canonical IDs, bounded page, optional exact credit."""
    fields = set(request)
    if operation in SERIES_REQUEST:
        check(fields == SERIES_REQUEST[operation], 'refused', 'Invalid request fields')
        (name,) = SERIES_REQUEST[operation]
        try:
            catalog_ids(request[name], 'Episode' if name == 'episode_ids' else 'Series')
        except Refused as error:
            raise WireError('refused', safe_message(error)) from None
        return copy.deepcopy(request)
    check(CATALOG_REQUIRED <= fields <= CATALOG_REQUIRED | CATALOG_OPTIONAL and
          ('person_id' in fields) == ('person_role' in fields), 'refused', 'Invalid request fields')
    try:
        canonical_id(request['library_id'])
    except Refused as error:
        raise WireError('refused', safe_message(error)) from None
    check(type(request['limit']) is int and 1 <= request['limit'] <= MAX_CATALOG_LIMIT,
          'refused', 'Invalid limit')
    check('offset' not in request or bounded_int(request['offset'], MAX_CATALOG_OFFSET),
          'refused', 'Invalid offset')
    if 'person_id' in request:
        check(isinstance(request['person_id'], str) and
              re.fullmatch('[1-9][0-9]{0,18}', request['person_id']) is not None and
              int(request['person_id']) < 2 ** 63 and request['person_role'] in CREDIT_ROLES,
              'refused', 'Invalid person filter')
    return copy.deepcopy(request)


def authorize(call, policy):
    """Intersect the verified caller with the root ceiling on every request."""
    caller = call['caller']
    if call['operation'] == TORRENT_OPERATION:
        ceiling = policy.get('janitor_torrents')
        check(ceiling is not None, 'refused', 'Torrent read not enabled by policy')
        check(all(caller[k] == policy['caller'][k] for k in ('issuer', 'subject', 'server')) and
              caller['namespace'] == 'managed-janitor' and
              caller['target_library'] == ceiling['target_library'] and
              caller['policy_revision'] == ceiling['revision'] and
              caller['actions'] == TORRENT_ACTIONS and call['recipient'] in policy['recipients'],
              'refused', 'Caller outside torrent policy')
        return
    if call['operation'] == CUSTODY_OPERATION:
        ceiling = policy.get('janitor_importers')
        custody = policy.get('janitor_custody', {}).get('backends', {})
        check(ceiling is not None and call['request']['backend'] in custody,
              'refused', 'Custody read not enabled by policy')
        check(all(caller[k] == policy['caller'][k] for k in ('issuer', 'subject', 'server')) and
              caller['namespace'] == 'managed-janitor' and
              caller['target_library'] == ceiling['target_library'] and
              caller['policy_revision'] == ceiling['revision'] and
              caller['actions'] == CUSTODY_ACTIONS and call['recipient'] in policy['recipients'],
              'refused', 'Caller outside custody policy')
        return
    if call['operation'] in IMPORTER_OPERATIONS:
        ceiling = policy.get('janitor_importers')
        check(ceiling is not None and call['request']['backend'] in ceiling['backends'],
              'refused', 'Importer read not enabled by policy')
        pause = call['operation'] == IMPORTER_PAUSE
        if pause:
            allowed = policy.get('janitor_importers_pause', {}).get('backends', {})
            check(call['request']['list_id'] in allowed.get(call['request']['backend'], []),
                  'refused', 'Importer pause target not enabled by policy')
        check(all(caller[k] == policy['caller'][k] for k in ('issuer', 'subject', 'server')) and
              caller['namespace'] == 'managed-janitor' and
              caller['target_library'] == ceiling['target_library'] and
              caller['policy_revision'] == ceiling['revision'] and
              caller['actions'] == (IMPORTER_PAUSE_ACTIONS if pause else IMPORTER_ACTIONS) and call['recipient'] in policy['recipients'],
              'refused', 'Caller outside importer policy')
        return
    if call['operation'] == JANITOR_OPERATION:
        ceiling = policy.get('janitor')
        check(ceiling is not None, 'refused', 'Janitor read not enabled by policy')
        check(all(caller[k] == policy['caller'][k] for k in ('issuer', 'subject', 'server')) and
              caller['namespace'] == 'managed-janitor' and
              caller['target_library'] == ceiling['target_library'] and
              caller['policy_revision'] == ceiling['revision'],
              'refused', 'Caller outside janitor policy')
        check(caller['actions'] == JANITOR_ACTIONS and
              set(caller['history_users']) == set(ceiling['protected_users']) and
              call['recipient'] in ceiling['protected_users'],
              'refused', 'Recipient outside janitor policy')
        return
    check(all(caller[k] == policy['caller'][k] for k in PINNED_CALLER),
          'refused', 'Caller outside management policy')
    check(call['recipient'] in policy['recipients'],
          'refused', 'Recipient outside management policy')
    if 'series_libraries' in caller:
        # Signed grants intersected by the service, intersected again with the
        # current root Series ceiling. The Movie library never implies one.
        check('series_libraries' in policy, 'refused', 'Series libraries not enabled by policy')
        check(set(caller['series_libraries']) <= set(policy['series_libraries']),
              'refused', 'Series libraries outside management policy')
    if call['operation'] in SERIES_OPERATIONS:
        check('history_users' in policy, 'refused', 'History read not enabled by policy')
        check(caller['actions'] == HISTORY_ACTIONS,
              'refused', 'History read needs exactly the fixed actions')
        check(set(caller['history_users']) <= set(policy['recipients']),
              'refused', 'History users outside management policy')
        check(call['recipient'] in caller['history_users'] and
              call['recipient'] in policy['history_users'],
              'refused', 'Recipient outside history policy')
        check(call['operation'] != 'series-catalog' or
              call['request']['library_id'] in caller['series_libraries'],
              'refused', 'Library outside Series grant')
        return
    if call['operation'] == METADATA_OPERATION:
        # Current policy must opt in; the recipient field alone proves nothing.
        check('history_users' in policy, 'refused', 'History read not enabled by policy')
        check(caller['actions'] == HISTORY_ACTIONS,
              'refused', 'History read needs exactly the fixed actions')
        check(set(caller['history_users']) <= set(policy['recipients']),
              'refused', 'History users outside management policy')
        check(call['recipient'] in caller['history_users'] and
              call['recipient'] in policy['history_users'],
              'refused', 'Recipient outside history policy')
        return
    required = set(BASE_ACTIONS)
    if call['operation'] in ('preview', 'apply'):
        required |= LIST_ACTIONS[call['request']['operation']]
    check(required <= set(caller['actions']), 'refused', 'Missing required action')
    items = call['request'].get('items')
    if items is not None:
        check('series_libraries' in caller, 'refused', 'Series libraries required')
        check(all(i['library_id'] in caller['series_libraries'] for i in items),
              'refused', 'Item library outside Series grant')


def remaining_seconds(call, now_ms):
    """min(token expiry, caller deadline, worker ceiling) from now; never renewed."""
    end = min(call['caller']['expires_at'] * 1000, call['deadline_unix_ms'],
              now_ms + (10 if call.get('operation') == TORRENT_OPERATION else MAX_SECONDS) * 1000)
    check(end > now_ms, 'deadline', 'Deadline or token expiry already reached')
    return (end - now_ms) / 1000


class Guard:
    """Adapter request guard: peer disconnect and deadline before every send."""
    def __init__(self, conn, deadline):
        self.conn, self.deadline, self.tripped = conn, deadline, None

    def expired(self):
        return time.monotonic() >= self.deadline

    def __call__(self):
        if peer_state(self.conn) != 'open':
            self.tripped = 'cancelled'
            raise Refused('Caller cancelled')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.tripped = 'deadline'
            raise Refused('Caller deadline exceeded')
        return remaining


def selected_config(policy, recipient):
    """Load only the root map's config and require it to match that map entry."""
    try:
        config = load_config(policy['recipients'][recipient])
    except Refused:
        raise WireError('unavailable', 'Recipient configuration unavailable') from None
    check(config['recipient'] == recipient and config['server'] == policy['caller']['server']
          and config['library'] == policy['caller']['library'],
          'unavailable', 'Recipient configuration does not match policy')
    check(config['enabled'] is True, 'unavailable', 'Recipient management disabled')
    return config


def series_grant(policy, config, caller):
    """Current Series grant: caller IDs whose root policy and recipient config agree.

    None for a Movie-only caller. Any granted library missing from, or
    different in, the recipient config refuses before credentials or network.
    """
    granted = caller.get('series_libraries')
    if granted is None:
        return None
    configured = config.get('series_libraries') or {}
    check(all(configured.get(x) == policy['series_libraries'][x] for x in granted),
          'unavailable', 'Recipient configuration does not match policy')
    return {x: copy.deepcopy(configured[x]) for x in granted}


def safe_message(error):
    text = str(error)
    return text if SAFE_TEXT.fullmatch(text) else FAILED


def safe_run(run):
    """Unfinished request view: operation, list and typed arguments, never its key."""
    request = run.get('request') if type(run) is dict else None
    if type(request) is not dict or not isinstance(request.get('operation'), str):
        return {'operation': None, 'list': None, 'args': {}}
    return {'operation': request['operation'], 'list': request.get('list'),
            'args': {k: copy.deepcopy(v) for k, v in request.items()
                     if k not in ('operation', 'list')}}


def unfinished(journal):
    """Typed unfinished error naming the unfinished request, read under the lock."""
    try:
        with journal.locked():
            d = journal.load()
    except (BlockingIOError, Refused):
        return WireError('unfinished', 'Another request is unfinished')
    view = safe_run(d.get('run')) if type(d) is dict else safe_run(None)
    operation, list_id = view['operation'], view['list']
    if not (isinstance(operation, str) and SAFE_TEXT.fullmatch(operation)):
        operation = None
    if not (isinstance(list_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', list_id)):
        list_id = None
    return WireError('unfinished', 'Another request is unfinished', operation, list_id)


def show(operator, request):
    result = invoke(operator, 'show', request)
    check(result.get('version') == 2, 'refused', 'Journal migration required')
    shown = copy.deepcopy(result)
    if result['run'] is not None:
        # Same journal, read-only, proven identical to the shown state by digest.
        with operator.journal.locked():
            d = operator.journal.load()
        check(d is not None and digest(d) == result['journal_digest'],
              'busy', 'Recipient journal changed; nothing was changed')
        shown['run'] = safe_run(d['run'])
    if type(shown.get('migrated_from')) is dict:
        shown['migrated_from'].pop('key', None)
    shown['capacity_warning'] = result['done_count'] >= DONE_LIMIT - CAPACITY_WARNING_FREE
    return shown


def dispatch(operator, call):
    operation, request = call['operation'], call['request']
    if operation == 'show':
        return show(operator, request)
    if operation == 'reconcile':
        return invoke(operator, 'reconcile', {})
    # Every validated argument of the list operation, and nothing else.
    target = request['operation']
    args = {k: v for k, v in request.items() if k not in ('operation', 'list', 'key',
                                                          'intent_digest')}
    if operation == 'preview':
        return invoke(operator, 'list-preview', dict(args, operation=target, list=request['list']))
    key = internal_key(call['caller'], call['recipient'], request['key'])
    return invoke(operator, target, dict(args, key=key, list=request['list'],
                                         intent_digest=request['intent_digest']))


def movie_metadata(config, call, guard, series=None):
    """Read-only metadata under the recipient's nonblocking journal lock.

    Uses the same Journal, adapter, credential and guard as management, but no
    Operator: no journal load or write, reconcile, init, adoption or preview.
    The Series reads take the same path with the current Series grant.
    """
    journal = Journal(config['journal_directory'], root_safe=True)
    api = None
    try:
        with journal.locked():
            api = OperatorAdapter(config, journal, request_guard=guard)
            credentials = load_credentials(config['credential_file'])
            try:
                api.authenticate(credentials)
            finally:
                credentials.clear()
            return read(api, config, call, series)
    finally:
        if api is not None:
            api.close()
        journal.close()


def read(api, config, call, series):
    recipient, request = call['recipient'], call['request']
    if call['operation'] == JANITOR_OPERATION:
        return {'qualification': 'emby_positive_protections_v1', 'server': config['server'],
                'library': config['library'], 'recipient': recipient, 'target': request['target'],
                'items': api.janitor_protections(recipient, config['library'], request['target'])}
    if call['operation'] == METADATA_OPERATION:
        return {'movies': api.movie_metadata(recipient, config['library'], request['ids'])}
    libraries = sorted(series)
    if call['operation'] == 'series-seeds':
        return {'series': api.series_seeds(recipient, request['episode_ids'], libraries)}
    if call['operation'] == 'recipient-series':
        return {'series': api.recipient_series(recipient, request['ids'], libraries)}
    person = ((request['person_id'], request['person_role'])
              if 'person_id' in request else None)
    return api.series_catalog(recipient, request['library_id'], request['limit'],
                              request.get('offset', 0), person, libraries)


def execute(config, call, guard, series=None):
    try:
        if call['operation'] in (METADATA_OPERATION, *SERIES_OPERATIONS, JANITOR_OPERATION):
            return movie_metadata(config, call, guard, series)
        with operator_session(config, request_guard=guard,
                              **({'series': series} if series is not None else {})) as operator:
            try:
                return dispatch(operator, call)
            except Refused as error:
                if str(error) in UNFINISHED and not guard.tripped:
                    raise unfinished(operator.journal) from None
                raise
    except WireError:
        raise
    except BlockingIOError:
        # Only the nonblocking journal lock raises this; nothing was changed.
        raise WireError('busy', 'Recipient journal busy; nothing was changed') from None
    except Refused as error:
        if guard.tripped or guard.expired():
            raise WireError('deadline', STOPPED[guard.tripped or 'deadline']) from None
        raise WireError('refused', safe_message(error)) from None
    except Exception:
        if guard.tripped or guard.expired():
            raise WireError('deadline', STOPPED[guard.tripped or 'deadline']) from None
        raise WireError('unavailable', FAILED) from None


def importer_inventory(policy, call, guard):
    """No Emby config, history opt-in, journal or account credentials."""
    try:
        guard()
        backend = call['request']['backend']
        config = strict_json(private_read(policy['janitor_importers']['backends'][backend]))
        check(type(config) is dict and config.get('backend') == backend,
              'unavailable', 'Importer configuration mismatch')
        adapter = ImporterInventoryAdapter(config, request_guard=guard)
        assessment = call['operation'] == IMPORTER_ASSESSMENT
        observed = adapter.assessment() if assessment else {'lists': adapter.inventory()}
        result = {'qualification': 'arr_importer_inventory_v2' if assessment else 'arr_importer_inventory_v1', 'backend': backend,
                'resource_id': config['resource_id'], 'server': call['caller']['server'],
                'library': call['caller']['target_library'],
                'policy_revision': call['caller']['policy_revision'], 'lists': observed['lists']}
        if assessment:
            result['cleanup_level'] = observed['cleanup_level']
        return result
    except WireError:
        raise
    except Exception:
        if guard.tripped or guard.expired():
            raise WireError('deadline', STOPPED[guard.tripped or 'deadline']) from None
        raise WireError('unavailable', 'Importer inventory unavailable') from None


def importer_pause(policy, call, guard):
    """Stateless fixed write; the client owns write-ahead and no-retry policy."""
    request, caller = call['request'], call['caller']
    result = {'qualification': 'arr_importer_pause_v1', 'backend': request['backend'],
              'resource_id': request['resource_id'], 'server': caller['server'],
              'library': caller['target_library'], 'policy_revision': caller['policy_revision'],
              'operation_id': request['operation_id'], 'stage': 'not_sent',
              'reason': 'config_unavailable', 'cleanup_level': None,
              'before': None, 'ack': None, 'after': None, 'calls': 0}
    api = None
    try:
        guard()
        config = strict_json(private_read(
            policy['janitor_importers']['backends'][request['backend']]))
        if (type(config) is not dict or config.get('backend') != request['backend'] or
                config.get('resource_id') != request['resource_id']):
            result['reason'] = 'identity_mismatch'
            return result
        api = ImporterPauseAdapter(config, request_guard=guard)
        result.update(api.pause(request['list_id'], request['expected'], caller['expires_at']))
    except Exception:
        if api is not None and api._put_started:
            result['stage'], result['reason'] = 'dispatch_unknown', 'put_uncertain'
        elif guard.tripped or guard.expired():
            result['reason'] = 'deadline'
    return result


def custody_failure_log(error, guard):
    """One fixed diagnostic on stderr; never serialize exception text or facts."""
    reason = 'unexpected'
    if guard.tripped or guard.expired():
        reason = 'deadline'
    elif isinstance(error, Refused):
        reason = {
            'Importer deadline reached': 'backend_deadline',
            'Importer inventory unavailable': 'backend_unavailable',
            'Custody observation changed': 'observation_changed',
            'Custody roots changed': 'roots_changed',
            'Custody file size changed': 'file_size_changed',
            'Custody content changed': 'content_changed',
            'Custody retention unavailable': 'retention_unavailable',
            'Custody recycle mismatch': 'recycle_mismatch',
            'Custody identity mismatch': 'backend_identity_mismatch',
        }.get(str(error), 'refused')
    elif isinstance(error, OSError):
        reason = 'io_unavailable'
    try:
        print('holonet custody failure reason=' + reason, file=sys.stderr, flush=True)
    except Exception:
        pass  # Diagnostics must not change the original fail-closed response.


def handle(conn):
    """Validate one connection's request and run it; returns the result object."""
    started = time.monotonic()
    try:
        policy = load_policy()
    except Refused:
        raise WireError('unavailable', 'Management unavailable') from None
    check(policy['enabled'], 'unavailable', 'Management disabled')
    _, uid, gid = peer_credentials(conn)
    check((uid, gid) == (policy['peer']['uid'], policy['peer']['gid']),
          'refused', 'Peer not authorized')
    call = parse_request(read_frame(conn, started + READ_SECONDS))
    authorize(call, policy)
    guard = Guard(conn, time.monotonic() + remaining_seconds(call, int(time.time() * 1000)))
    check(peer_state(conn) == 'open', 'refused', 'Trailing data or early close')
    if call['operation'] == TORRENT_OPERATION:
        try:
            return torrent_inventory(policy, call, guard)
        except Exception:
            raise WireError('unavailable', 'Torrent inventory unavailable') from None
    if call['operation'] == CUSTODY_OPERATION:
        try:
            return custody_attest(policy, call, guard)
        except Exception as error:
            custody_failure_log(error, guard)
            if guard.tripped or guard.expired():
                raise WireError('deadline', STOPPED[guard.tripped or 'deadline']) from None
            raise WireError('unavailable', 'Custody observation unavailable') from None
    if call['operation'] == IMPORTER_PAUSE:
        return importer_pause(policy, call, guard)
    if call['operation'] in IMPORTER_OPERATIONS:
        return importer_inventory(policy, call, guard)
    config = selected_config(policy, call['recipient'])
    series = series_grant(policy, config, call['caller'])
    if series is None:
        return execute(config, call, guard)
    return execute(config, call, guard, series)


def envelope(result=None, error=None):
    if error is None:
        return {'version': VERSION, 'ok': True, 'result': result}
    body = {'code': error.code, 'message': error.message}
    if error.operation is not None:
        body['operation'] = error.operation
    if error.list_id is not None:
        body['list'] = error.list_id
    return {'version': VERSION, 'ok': False, 'error': body}


def encode(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii') + b'\n'


def serve(conn):
    """One request, one response. Returns the response envelope that was sent."""
    try:
        result = handle(conn)
        check(type(result) is dict, 'unavailable', FAILED)
        response = envelope(result)
    except WireError as error:
        response = envelope(error=error)
    except Exception:
        response = envelope(error=WireError('unavailable', FAILED))
    try:
        raw = encode(response)
    except (TypeError, ValueError):
        response = envelope(error=WireError('unavailable', FAILED))
        raw = encode(response)
    if len(raw) > MAX_RESPONSE:
        response = envelope(error=WireError('unavailable', 'Response too large'))
        raw = encode(response)
    try:
        conn.settimeout(WRITE_SECONDS)
        conn.sendall(raw)
        conn.shutdown(socket.SHUT_WR)
    except OSError:
        pass  # A lost response is an unknown outcome for the caller, never a retry.
    return response


def activated_socket(fd=LISTEN_FD):
    """The one connected AF_UNIX stream socket passed by systemd Accept=yes."""
    if not (os.environ.get('LISTEN_PID') == str(os.getpid()) and
            os.environ.get('LISTEN_FDS') == '1'):
        raise Refused('Socket activation required')
    for name in ('LISTEN_PID', 'LISTEN_FDS', 'LISTEN_FDNAMES'):
        os.environ.pop(name, None)
    conn = socket.socket(fileno=fd)
    os.set_inheritable(conn.fileno(), False)
    if conn.family != socket.AF_UNIX or conn.type != socket.SOCK_STREAM:
        conn.close()
        raise Refused('Socket activation required')
    return conn


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print('No arguments accepted', file=sys.stderr)
        return 2
    try:
        conn = activated_socket()
    except (Refused, OSError):
        print('Socket activation required', file=sys.stderr)
        return 2
    try:
        serve(conn)
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
