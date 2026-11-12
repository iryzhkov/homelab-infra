"""Fixed qBittorrent staged calls and bounded torrent metainfo decoding."""
import hashlib
import os
import re
import secrets
import time
import urllib.parse
from http.cookies import SimpleCookie

from emby_operator_adapter import private_read
from emby_operator_torrents import TorrentAdapter, json_body, project
from emby_operator_acquire import (need, exact, hexid, integer, text, path,
                                   PREFIX, CATEGORY, RESOURCE)


def metainfo(raw):
    """Preserve the exact info dictionary byte span for the v1 hash."""
    need(type(raw) is bytes and 1 <= len(raw) <= 8 * 1024 * 1024)
    pos, count, info_span = 0, 0, None

    def parse(depth=0):
        nonlocal pos, count, info_span
        count += 1
        need(depth <= 8 and count <= 8192 and pos < len(raw))
        start = pos
        token = raw[pos:pos + 1]
        if token == b'i':
            end = raw.find(b'e', pos + 1)
            need(end >= 0 and re.fullmatch(b'0|-?[1-9][0-9]{0,18}', raw[pos + 1:end]))
            pos = end + 1
            return int(raw[start + 1:end])
        if token in (b'd', b'l'):
            pos += 1
            value = {} if token == b'd' else []
            last = None
            while pos < len(raw) and raw[pos:pos + 1] != b'e':
                if token == b'd':
                    key = parse(depth + 1)
                    need(type(key) is bytes and (last is None or last < key))
                    last = key
                    begin = pos
                    item = parse(depth + 1)
                    value[key] = item
                    if depth == 0 and key == b'info':
                        info_span = (begin, pos)
                else:
                    value.append(parse(depth + 1))
            need(pos < len(raw))
            pos += 1
            return value
        colon = raw.find(b':', pos)
        need(colon >= pos and colon - pos <= 9 and
             re.fullmatch(b'0|[1-9][0-9]*', raw[pos:colon]))
        length = int(raw[pos:colon])
        pos = colon + 1
        need(length <= len(raw) - pos)
        value = raw[pos:pos + length]
        pos += length
        return value

    root = parse()
    need(pos == len(raw) and type(root) is dict and info_span is not None)
    info = root.get(b'info')
    need(type(info) is dict and b'meta version' not in info and b'file tree' not in info and
         type(info.get(b'pieces')) is bytes and
         len(info[b'pieces']) > 0 and len(info[b'pieces']) % 20 == 0 and
         integer(info.get(b'piece length'), 1, 1 << 30))
    def component(value):
        need(type(value) is bytes and len(value) <= 255)
        try:
            name = value.decode('utf-8')
        except UnicodeError:
            need(False)
        need(text(name, 255) and name not in ('.', '..') and
             not any(c in name for c in '/\\:'))
        return name
    name = component(info.get(b'name'))
    files = []
    if b'files' in info:
        need(b'length' not in info and type(info[b'files']) is list and
             1 <= len(info[b'files']) <= 256)
        for f in info[b'files']:
            need(type(f) is dict and integer(f.get(b'length'), 0, 1 << 40) and
                 type(f.get(b'path')) is list and 1 <= len(f[b'path']) <= 32 and
                 not any(k in f for k in (b'symlink path', b'attr')))
            files.append(dict(index=len(files), name=name + '/' +
                '/'.join(component(p) for p in f[b'path']), size=f[b'length']))
    else:
        need(integer(info.get(b'length'), 1, 1 << 40))
        files.append(dict(index=0, name=name, size=info[b'length']))
    need(len({f['name'] for f in files}) == len(files) and
         0 < sum(f['size'] for f in files) <= 1 << 40)
    return hashlib.sha1(raw[info_span[0]:info_span[1]]).hexdigest(), name, files, info.get(b'private', 0)


class StageTorrent(TorrentAdapter):
    def __init__(self, config, operation_id, torrent_hash, guard, *, starting=False):
        super().__init__(config, request_guard=guard)
        need(config['resource_id'] == RESOURCE and config['categories'] == [CATEGORY] and
             config['payload_prefixes'] == [PREFIX] and hexid(operation_id, 32) and
             hexid(torrent_hash, 40))
        self.operation_id, self.torrent_hash = operation_id, torrent_hash
        self.starting = starting
        self.deadline = time.monotonic() + min(30 if starting else 10, guard())
        self.add_may_send = False
        self.boundary = None

    def call_limit(self):
        return 8 if self.starting else 5

    def allowed_exchanges(self):
        allowed = super().allowed_exchanges()
        if self.starting:
            allowed |= {('GET', '/api/v2/torrents/categories'),
                        ('GET', '/api/v2/torrents/info?category=' + CATEGORY),
                        ('POST', '/api/v2/torrents/add')}
        return allowed

    def before_send(self, method, route):
        if method == 'POST' and route == '/api/v2/torrents/add':
            need(self.starting and not self.add_may_send)
            self.add_may_send = True

    def content_type(self):
        return ('multipart/form-data; boundary=' + self.boundary if self.boundary
                else super().content_type())

    def login(self):
        credentials = json_body(private_read(self.config['credential_file'], limit=16384))
        try:
            exact(credentials, 'username password')
            need(all(isinstance(v, str) and 0 < len(v) <= 4096 and
                     not any(c in v for c in '\r\n\x00') for v in credentials.values()))
            status, headers, raw = self.exchange('POST', '/api/v2/auth/login',
                urllib.parse.urlencode(credentials).encode('ascii'))
        finally:
            credentials.clear()
        cookies = headers.get_all('Set-Cookie', [])
        need(status == 204 and raw == b'' and len(cookies) == 1 and len(cookies[0]) <= 4096)
        cookie = cookies[0].split(';', 1)[0].strip()
        need(re.fullmatch(r'QBT_SID_[0-9]{1,5}=[A-Za-z0-9+/]{32}', cookie))
        parsed = SimpleCookie()
        parsed.load(cookies[0])
        need(len(parsed) == 1 and next(iter(parsed.values())).value == cookie.split('=', 1)[1])
        self.cookie = cookie
        status, _, raw = self.exchange('GET', '/api/v2/app/version')
        need(status == 200 and raw == b'v5.3.0')

    def logout(self):
        if self.cookie is None or self.io_stalled:
            return False
        try:
            status, _, raw = self.exchange('POST', '/api/v2/auth/logout', cleanup=True)
            return status in (200, 204) and raw == b''
        finally:
            self.cookie = None

    def get(self, route):
        status, _, raw = self.exchange('GET', route)
        need(status == 200)
        return json_body(raw)

    def owned(self, info):
        return (type(info) is dict and info.get('hash') == self.torrent_hash and
                info.get('category') == CATEGORY and
                info.get('save_path') == PREFIX + '/' + self.operation_id and
                info.get('download_path') in ('', PREFIX + '/' + self.operation_id) and
                info.get('auto_tmm') is False and
                self.operation_id in [x.strip() for x in info.get('tags', '').split(',')])

    @staticmethod
    def safe_limits(info):
        return (type(info) is dict and info.get('share_limits_mode') == 'Custom' and
                info.get('share_limit_action') == 'Stop' and
                all(type(info.get(k)) in (int, float) and info[k] == -1 for k in
                    ('ratio_limit', 'seeding_time_limit', 'inactive_seeding_time_limit')))

    def status(self, caller):
        self.login()
        try:
            info = self.get('/api/v2/torrents/info?hashes=' + self.torrent_hash)
            need(type(info) is list and len(info) == 1 and self.safe_limits(info[0]))
            files = self.get('/api/v2/torrents/files?hash=' + self.torrent_hash)
            result = project(info[0], files, self.config, self.torrent_hash, caller)
            result['tags_match_operation'] = self.owned(info[0])
        finally:
            cleaned = self.logout()
        need(cleaned)
        return result

    def add(self, raw, check_before_add):
        self.login()
        need(self.get('/api/v2/torrents/info?hashes=' + self.torrent_hash) == [])
        categories = self.get('/api/v2/torrents/categories')
        need(type(categories) is dict and len(categories) <= 64 and
             type(categories.get(CATEGORY)) is dict and
             categories[CATEGORY].get('savePath') == PREFIX)
        for name, c in categories.items():
            need(type(c) is dict)
            p = c.get('savePath')
            if p and name != CATEGORY:
                path(p)
                need(not (PREFIX == p or PREFIX.startswith(p.rstrip('/') + '/')))
        current = self.get('/api/v2/torrents/info?category=' + CATEGORY)
        need(type(current) is list and len(current) <= 256)
        destination = PREFIX + '/' + self.operation_id
        need(not any(type(r) is not dict or r.get('save_path') == destination or
                     r.get('content_path') == destination for r in current))
        check_before_add()
        self.boundary = 'holonet' + secrets.token_hex(16)
        fields = dict(savepath=destination, downloadPath=destination, useDownloadPath='false',
            category=CATEGORY,
            tags='holonet-acquire,' + self.operation_id, stopped='false',
            autoTMM='false', contentLayout='Original', ratioLimit='-1',
            seedingTimeLimit='-1', inactiveSeedingTimeLimit='-1',
            shareLimitsMode='Custom', shareLimitAction='Stop')
        body = bytearray()
        for k, v in fields.items():
            body.extend(('--' + self.boundary + '\r\nContent-Disposition: form-data; name="' +
                         k + '"\r\n\r\n' + v + '\r\n').encode('ascii'))
        body.extend(('--' + self.boundary +
            '\r\nContent-Disposition: form-data; name="torrents"; filename="release.torrent"'
            '\r\nContent-Type: application/x-bittorrent\r\n\r\n').encode('ascii'))
        body.extend(raw)
        body.extend(('\r\n--' + self.boundary + '--\r\n').encode('ascii'))
        try:
            status, _, data = self.exchange('POST', '/api/v2/torrents/add', bytes(body))
        finally:
            self.boundary = None
        need(status == 200)
        ack = json_body(data)
        need(type(ack) is dict and type(ack.get('success_count')) is int and
             ack['success_count'] == 1 and ack.get('failure_count') == 0 and
             ack.get('pending_count') == 0 and ack.get('added_torrent_ids') == [self.torrent_hash])
        # ACK is retained even if the next read or logout fails.
        self.acknowledged = True
        info = self.get('/api/v2/torrents/info?hashes=' + self.torrent_hash)
        need(type(info) is list and len(info) == 1 and self.owned(info[0]) and self.safe_limits(info[0]))
        return info[0]
