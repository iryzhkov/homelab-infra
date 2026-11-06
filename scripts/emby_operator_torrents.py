"""Single-torrent qBittorrent v5.3.0 read projection. Default-off socket operation.

No filesystem attestation or media authority. Credentials and this call's cookie
live only in this worker; remote session disposal is not guaranteed on cancellation.
"""
import copy
import http.client
from http.cookies import SimpleCookie
import ipaddress
import math
import queue
import re
import ssl
import threading
import time
import urllib.parse

from emby_operator_adapter import private_read, strict_json, importer_response_bytes
from emby_operator_recommendations import Refused

OPERATION = 'janitor-torrents-inventory'
ACTIONS = ['janitor.torrents.read']
FAILURE = 'Torrent inventory unavailable'


def need(condition):
    if not condition:
        raise Refused(FAILURE)


def text(value, limit):
    return (isinstance(value, str) and 1 <= len(value.encode('utf-8')) <= limit and
            all(c.isprintable() for c in value))


def path(value, absolute=True):
    need(text(value, 4096) and '\\' not in value and value.startswith('/') == absolute)
    parts = value[1:].split('/') if absolute else value.split('/')
    need(all(p not in ('', '.', '..') for p in parts))
    return value


def within(value, prefix):
    return value == prefix or value.startswith(prefix + '/')


def descriptor(value):
    need(type(value) is dict and set(value) == {
        'version', 'resource_id', 'origin', 'expected_version', 'credential_file',
        'categories', 'payload_prefixes'})
    need(type(value['version']) is int and value['version'] == 1 and
         value['expected_version'] == 'v5.3.0' and text(value['resource_id'], 128))
    path(value['credential_file'])
    need(text(value['origin'], 1024))
    origin = urllib.parse.urlsplit(value['origin'])
    try:
        loopback = origin.hostname == 'localhost'
        if not loopback:
            try:
                loopback = ipaddress.ip_address(origin.hostname or '').is_loopback
            except ValueError:
                loopback = False
        need(origin.scheme in ('https', 'http') and origin.hostname and
             origin.username is None and origin.password is None and
             origin.path in ('', '/') and not origin.query and not origin.fragment and
             '?' not in value['origin'] and '#' not in value['origin'] and
             (origin.port is None or 1 <= origin.port <= 65535) and
             (origin.scheme == 'https' or loopback))
    except ValueError:
        raise Refused(FAILURE) from None
    categories, prefixes = value['categories'], value['payload_prefixes']
    need(type(categories) is list and 1 <= len(categories) <= 16 and
         all(text(c, 64) for c in categories) and len(set(categories)) == len(categories))
    need(type(prefixes) is list and 1 <= len(prefixes) <= 8)
    for p in prefixes:
        path(p)
    need(all(not within(a, b) for i, a in enumerate(prefixes)
             for j, b in enumerate(prefixes) if i != j))
    return copy.deepcopy(value)


def json_body(raw):
    # Bound nesting before parsing, including ignored upstream fields.
    depth, quoted, escaped = 0, False, False
    for c in raw.decode('utf-8'):
        if quoted:
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c in '[{':
            depth += 1
            need(depth <= 32)
        elif c in ']}':
            depth -= 1
    return strict_json(raw)


def integer(value):
    return type(value) is int and 0 <= value <= 2 ** 53 - 1


def progress(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def project(info, files, config, torrent_hash, caller):
    need(type(info) is dict and info.get('hash') == torrent_hash and
         info.get('category') in config['categories'])
    need(text(info.get('state'), 64) and progress(info.get('progress')) and
         integer(info.get('amount_left')) and integer(info.get('total_size')) and
         info['amount_left'] <= info['total_size'])
    for key in ('save_path', 'content_path'):
        path(info.get(key))
        need(any(within(info[key], p) for p in config['payload_prefixes']))
    need(type(files) is list and 1 <= len(files) <= 256)
    projected = []
    for f in files:
        need(type(f) is dict and integer(f.get('index')) and integer(f.get('size')) and
             progress(f.get('progress')) and type(f.get('priority')) is int and
             f['priority'] in (0, 1, 6, 7))
        path(f.get('name'), False)
        projected.append({k: f[k] for k in ('index', 'name', 'size', 'progress', 'priority')})
    projected.sort(key=lambda f: f['index'])
    need([f['index'] for f in projected] == list(range(len(projected))) and
         len({f['name'] for f in projected}) == len(projected) and
         sum(f['size'] for f in projected) <= info['total_size'])
    result = {k: info[k] for k in ('category', 'state', 'progress', 'amount_left',
                                  'total_size', 'save_path', 'content_path')}
    result.update(qualification='qbit_torrent_inventory_v1', resource_id=config['resource_id'],
        server=caller['server'], library=caller['target_library'],
        policy_revision=caller['policy_revision'], torrent_hash=torrent_hash, files=projected,
        client_reports_complete=(info['state'] in ('uploading', 'stalledUP', 'forcedUP',
            'stoppedUP', 'queuedUP') and info['progress'] == 1 and info['amount_left'] == 0 and
            all(f['progress'] == 1 for f in projected)),
        paths_qualified=False, seed_topology='unqualified', media_removal_authorized=False)
    return result


class TorrentAdapter:
    def __init__(self, config, *, request_guard):
        self.config = descriptor(config)
        self.guard = request_guard
        self.deadline = time.monotonic() + min(10, request_guard())
        self.cookie = None
        self.calls = 0
        self.origin = urllib.parse.urlsplit(self.config['origin'])
        self.context = ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt')

    def remaining(self, cleanup=False):
        remaining = min(self.guard(), self.deadline - time.monotonic() - (0 if cleanup else 2))
        need(remaining > 0)
        return remaining

    def exchange(self, method, route, body=None, cleanup=False):
        # Internal fixed call sites only; even accidental callers cannot enumerate.
        allowed = {('POST', '/api/v2/auth/login'), ('POST', '/api/v2/auth/logout'),
                   ('GET', '/api/v2/app/version')}
        allowed.update({('GET', '/api/v2/torrents/info?hashes=' + self.torrent_hash),
                        ('GET', '/api/v2/torrents/files?hash=' + self.torrent_hash)})
        need((method, route) in allowed and self.calls < 5)
        remaining = self.remaining(cleanup)
        self.calls += 1
        headers = {'Accept': 'application/json', 'Connection': 'close'}
        if self.cookie is not None:
            headers['Cookie'] = self.cookie
        if body is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        completed = queue.Queue(maxsize=1)
        def io():
            conn = None
            try:
                if self.origin.scheme == 'https':
                    conn = http.client.HTTPSConnection(self.origin.hostname, self.origin.port,
                        timeout=remaining, context=self.context)
                else:
                    conn = http.client.HTTPConnection(self.origin.hostname, self.origin.port,
                        timeout=remaining)
                conn.connect()
                # A slow connect must not send after authority or the original budget expires.
                conn.sock.settimeout(self.remaining(cleanup))
                conn.request(method, route, body=body, headers=headers)
                response = conn.getresponse()
                raw = importer_response_bytes(response)
                completed.put((response.status, response.headers, raw))
            except Exception:
                completed.put(None)
            finally:
                if conn is not None:
                    conn.close()
        threading.Thread(target=io, daemon=True).start()
        while True:
            wait = min(.05, self.remaining(cleanup))
            try:
                result = completed.get(timeout=wait)
                break
            except queue.Empty:
                pass
        self.remaining(cleanup)
        need(result is not None)
        return result

    def inventory(self, torrent_hash, caller):
        need(isinstance(torrent_hash, str) and re.fullmatch('[0-9a-f]{40}', torrent_hash))
        self.torrent_hash = torrent_hash
        result, cleaned = None, False
        credentials = None
        try:
            credentials = json_body(private_read(self.config['credential_file'], limit=16384))
            need(type(credentials) is dict and set(credentials) == {'username', 'password'})
            for key, limit in (('username', 256), ('password', 4096)):
                value = credentials[key]
                need(isinstance(value, str) and 1 <= len(value.encode('utf-8')) <= limit and
                     not any(c in value for c in ('\x00', '\r', '\n')))
            need(min(self.guard(), self.deadline - time.monotonic()) >= 3)
            status, headers, raw = self.exchange('POST', '/api/v2/auth/login',
                urllib.parse.urlencode(credentials).encode('ascii'))
            credentials.clear()
            cookies = headers.get_all('Set-Cookie', [])
            need(status == 204 and raw == b'' and len(cookies) == 1 and
                 len(cookies[0].encode('utf-8')) <= 4096)
            cookie = cookies[0].split(';', 1)[0].strip()
            need(re.fullmatch(r'QBT_SID_[0-9]{1,5}=[A-Za-z0-9+/]{32}', cookie) is not None)
            parsed = SimpleCookie()
            parsed.load(cookies[0])
            need(len(parsed) == 1 and next(iter(parsed)) == cookie.split('=', 1)[0] and
                 next(iter(parsed.values())).value == cookie.split('=', 1)[1])
            self.cookie = cookie
            status, _, raw = self.exchange('GET', '/api/v2/app/version')
            need(status == 200 and len(raw) <= 64 and raw == b'v5.3.0')
            status, _, raw = self.exchange('GET', '/api/v2/torrents/info?hashes=' + torrent_hash)
            need(status == 200)
            info = json_body(raw)
            need(type(info) is list and len(info) == 1 and type(info[0]) is dict and
                 info[0].get('hash') == torrent_hash and
                 info[0].get('category') in self.config['categories'])
            status, _, raw = self.exchange('GET', '/api/v2/torrents/files?hash=' + torrent_hash)
            need(status == 200)
            result = project(info[0], json_body(raw), self.config, torrent_hash, caller)
        finally:
            if credentials is not None:
                credentials.clear()
            if self.cookie is not None:
                try:
                    status, _, raw = self.exchange('POST', '/api/v2/auth/logout', cleanup=True)
                    cleaned = status in (200, 204) and raw == b''
                except Exception:
                    cleaned = False
                finally:
                    self.cookie = None
        need(cleaned and result is not None)
        self.remaining(cleanup=True)
        return result


def inventory(policy, call, guard):
    guard()
    config = json_body(private_read(policy['janitor_torrents']['config']))
    return TorrentAdapter(config, request_guard=guard).inventory(
        call['request']['torrent_hash'], call['caller'])
