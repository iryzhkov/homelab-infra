"""Synthetic acquisition contract tests. No production credentials or media."""
import copy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import emby_operator_acquire as a
import emby_operator_acquire_torrents as t
import emby_operator_acquire_media as media
import emby_operator_socket as s
from emby_operator_recommendations import Refused

FIXTURE = Path(__file__).resolve().parents[1] / 'docs/emby-janitor-acquire-wire-v1.json'


class SyntheticSonarr:
    _config = {'resource_id': 'synthetic-sonarr', 'expected_version': '4.0.20.3014'}
    def __init__(self, fixture):
        self.data = fixture['inspect']['result']
        self.calls = []
    def selected(self, kind, **query):
        self.calls.append(kind)
        if kind == 'series':
            return [{'id': 42, 'tvdbId': 999, 'title': 'Synthetic series',
                     'path': '/media/anime/Synthetic'}]
        if kind == 'episode':
            return [dict(seriesId=42, seasonNumber=1, id=e['sonarr_episode_id'],
                episodeNumber=e['episode_number'], monitored=e['monitored'],
                hasFile=e['has_file'], episodeFile=dict(
                    relativePath=e['file']['relative_path'], size=e['file']['size'],
                    quality={'quality': {'name': e['file']['quality']}},
                    mediaInfo={'audioLanguages': e['file']['audio_languages'],
                               'subtitles': e['file']['subtitles']}))
                for e in self.data['episodes']]
        return [dict(protocol='torrent', rejected=False, approved=True, fullSeason=True,
            seasonNumber=1, mappedSeriesId=42, title='Synthetic S01 English Dub',
            indexer='Synthetic', size=200, guid='synthetic-release', indexerId=1,
            episodeNumbers=[], languages=[{'name': 'English'}], seeders=4,
            infoHash='1' * 40, downloadUrl='https://indexer.invalid/download?apikey=SECRET')]
    def _get(self, route):
        self.calls.append(route)
        surface = self.data['sonarr_import_surface']
        if route.endswith('/status'):
            return {'version': self._config['expected_version']}
        if route.endswith('/rootfolder'):
            return [{'path': p} for p in surface['root_folders']]
        if route.endswith('/downloadclient'):
            return [dict(name=c['name'], implementation=c['implementation'],
                enable=c['enabled'], protocol=c['protocol'],
                removeCompletedDownloads=c['remove_completed_downloads'],
                fields=[{'name': 'tvCategory', 'privacy': 'normal', 'value': c['category']},
                        {'name': 'password', 'privacy': 'password', 'value': 'SECRET'}])
                for c in surface['download_clients']]
        if route.endswith('/remotepathmapping'):
            return [dict(host=m['host'], remotePath=m['remote_path'], localPath=m['local_path'])
                    for m in surface['remote_path_mappings']]
        return {'listSyncLevel': None}


class SyntheticEmby:
    def _series_libraries(self, libraries):
        return {x: {} for x in libraries}
    def _items(self, recipient, query, cap):
        return [{'Id': '31372', 'Type': 'Series', 'ProviderIds': {'Tvdb': '999'}}]


class AcquisitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The suite has no HOME writes. The job wrapper owns all scratch writes.
        cls.fixture = json.loads(FIXTURE.read_text())
    def setUp(self):
        self.call = copy.deepcopy(self.fixture['inspect_frame'])
        self.block = dict(target_library='3', revision='e' * 40,
            sonarr='/etc/holonet-playlists/sonarr.json',
            torrents='/etc/holonet-playlists/qbit-stage.json',
            series_libraries=['615'], start_enabled=False, media_enabled=False)

    def test_audio_requires_english_audio_association(self):
        for title in ('Dual Audio', 'DUAL Japanese Russian', 'MULTI Audio', 'English',
                      'English Subs', 'English subtitles Dual Audio'):
            self.assertNotEqual(a.audio_claim(title), 'english_audio_claimed', title)
        for title in ('English Dub', 'ENG AUDIO', 'DUB English'):
            self.assertEqual(a.audio_claim(title), 'english_audio_claimed')

    def test_shared_fixture_hash_and_unknown_category_is_not_safe(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            '53f5ada2abc87c57725b9e73bbad7ebe6d71d16297ec135e2039fcf0d5d2954f')
        api = SyntheticSonarr(self.fixture)
        api.data = copy.deepcopy(api.data)
        api.data['sonarr_import_surface']['download_clients'][0]['category'] = None
        observed = a.inspect(self.call, self.block, api, SyntheticEmby())
        self.assertFalse(observed['stage_safety']['category_unused_by_arr'])

    def test_inspect_projects_only_safe_fields(self):
        result = a.inspect(self.call, self.block, SyntheticSonarr(self.fixture), SyntheticEmby())
        self.assertEqual(result, self.fixture['inspect']['result'])
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(result['episodes'][0]['file']['audio_languages'], 'jpn')
        self.assertFalse(result['media_removal_authorized'])

    def test_search_one_inspect_no_url_escape_stable_handle(self):
        self.call['operation'] = a.OPERATIONS[1]
        self.call['request']['limit'] = 25
        api = SyntheticSonarr(self.fixture)
        result, private = a.search(self.call, self.block, api, SyntheticEmby())
        self.assertEqual(len(result['releases']), 1)
        self.assertEqual(api.calls.count('series'), 1)
        self.assertEqual(api.calls.count('release'), 1)
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('downloadUrl', json.dumps(result))
        self.assertIn('SECRET', next(iter(private.values()))['downloadUrl'])
        other, _ = a.search(self.call, self.block, SyntheticSonarr(self.fixture), SyntheticEmby())
        self.assertEqual(result, other)

    def test_wrong_library_and_foreign_series_refuse(self):
        self.call['request']['library_id'] = '1132'
        with self.assertRaises(Refused):
            a.inspect(self.call, self.block, SyntheticSonarr(self.fixture), SyntheticEmby())

    def test_strict_requests(self):
        good = self.call['request']
        self.assertEqual(a.request(a.OPERATIONS[0], good), good)
        for bad in (dict(good, arbitrary_url='https://evil.invalid'),
                    dict(good, season=True), dict(good, season=None),
                    dict(good, library_id='0615')):
            with self.assertRaises(Refused):
                a.request(a.OPERATIONS[0], bad)
        self.assertEqual(a.validate_block(self.block), self.block)
        with self.assertRaises(Refused):
            a.validate_block(dict(self.block, start_enabled=1))

    def test_socket_default_off_and_purpose_separation(self):
        call = s.parse_request(json.dumps(self.call).encode())
        policy = {'caller': {'issuer': call['caller']['issuer'],
            'subject': call['caller']['subject'], 'server': call['caller']['server']},
            'recipients': {call['recipient']: 'unused'}}
        with self.assertRaises(s.WireError):
            s.authorize(call, policy)
        policy['janitor_acquire'] = self.block
        s.authorize(call, policy)
        for actions in ([a.READ, a.START], ['janitor.torrents.read'], [a.READ, a.MEDIA]):
            call['caller']['actions'] = actions
            with self.assertRaises(s.WireError):
                s.authorize(call, policy)

    def test_fixed_operation_time_ceiling(self):
        self.assertEqual(s.remaining_seconds(self.call, 2000000000000), 18)
        self.call['deadline_unix_ms'] = 2000000300000
        self.assertEqual(s.remaining_seconds(self.call, 2000000000000), 20)

    def test_metainfo_hash_exact_and_unsafe_names_refused(self):
        info = b'd6:lengthi3e4:name5:x.mkv12:piece lengthi16384e6:pieces20:' + b'x' * 20 + b'e'
        raw = b'd4:info' + info + b'e'
        digest, name, files, private = t.metainfo(raw)
        self.assertEqual(digest, hashlib.sha1(info).hexdigest())
        self.assertEqual(files, [dict(index=0, name='x.mkv', size=3)])
        self.assertEqual(private, 0)
        for bad in (raw + b'extra', raw.replace(b'x.mkv', b'../xx'),
                    b'd4:info' + info + b'4:info' + info + b'e'):
            with self.assertRaises(Refused):
                t.metainfo(bad)

    def test_status_ownership_requires_path_tag_category_and_hash(self):
        api = object.__new__(t.StageTorrent)
        api.operation_id, api.torrent_hash = 'a' * 32, 'b' * 40
        value = dict(hash=api.torrent_hash, category=a.CATEGORY,
                     save_path=a.PREFIX + '/' + api.operation_id,
                     download_path='', auto_tmm=False,
                     tags='holonet-acquire,' + api.operation_id)
        self.assertTrue(api.owned(value))
        for field, changed in [('hash', 'c' * 40), ('category', 'tv-sonarr'),
                               ('save_path', a.PREFIX + '/another'), ('download_path', '/downloads/incomplete'),
                               ('auto_tmm', True), ('tags', 'holonet-acquire')]:
            self.assertFalse(api.owned(dict(value, **{field: changed})))

    def test_single_real_loopback_multipart_and_readback(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        calls, bodies = [], []
        digest, op = 'f' * 40, 'b' * 32
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def reply(self, body, status=200, cookie=False):
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                if cookie:
                    self.send_header('Set-Cookie', 'QBT_SID_8080=' + 'a' * 32)
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                calls.append(('POST', self.path))
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                if self.path.endswith('/login'):
                    self.reply(b'', status=204, cookie=True)
                elif self.path.endswith('/add'):
                    bodies.append(body)
                    self.reply(json.dumps(dict(success_count=1, failure_count=0,
                        pending_count=0, added_torrent_ids=[digest])).encode())
                else:
                    self.reply(b'', status=204)
            def do_GET(self):
                calls.append(('GET', self.path))
                if self.path.endswith('/version'):
                    self.reply(b'v5.3.0')
                elif self.path.endswith('/categories'):
                    self.reply(json.dumps({a.CATEGORY: dict(name=a.CATEGORY, savePath=a.PREFIX)}).encode())
                elif '?category=' in self.path:
                    self.reply(b'[]')
                else:
                    self.reply(json.dumps([dict(hash=digest, category=a.CATEGORY,
                        save_path=a.PREFIX + '/' + op, download_path='', auto_tmm=False,
                        tags='holonet-acquire,' + op,
                        share_limits_mode='Custom', share_limit_action='Stop',
                        ratio_limit=-1, seeding_time_limit=-1, inactive_seeding_time_limit=-1)]
                        if bodies else []).encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = dict(version=1, resource_id=a.RESOURCE,
            origin='http://127.0.0.1:' + str(server.server_port), expected_version='v5.3.0',
            credential_file='/etc/holonet-playlists/qbit-auth.json',
            categories=[a.CATEGORY], payload_prefixes=[a.PREFIX])
        try:
            with patch.object(t, 'private_read', return_value=b'{"username":"synthetic","password":"synthetic"}'):
                api = t.StageTorrent(config, op, digest, lambda: 20, starting=True)
                api.add(b'synthetic-torrent-bytes', lambda: None)
                self.assertTrue(api.logout())
                self.assertTrue(api.acknowledged)
                self.assertEqual(api.calls, 8)
            self.assertEqual(sum(route.endswith('/add') for _, route in calls), 1)
            self.assertEqual(len(bodies), 1)
            for value in (b'useDownloadPath', b'downloadPath', b'false', b'shareLimitAction', b'Stop', b'shareLimitsMode', b'Custom',
                          b'synthetic-torrent-bytes', a.PREFIX.encode(), b'holonet-stage'):
                self.assertIn(value, bodies[0])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_share_limit_fallback_cannot_qualify_download(self):
        safe = dict(share_limits_mode='Custom', share_limit_action='Stop', ratio_limit=-1,
                    seeding_time_limit=-1, inactive_seeding_time_limit=-1)
        self.assertTrue(t.StageTorrent.safe_limits(safe))
        for field, value in (('share_limits_mode', 'Global'), ('share_limit_action', 'RemoveWithContent'),
                             ('ratio_limit', 1), ('seeding_time_limit', None),
                             ('inactive_seeding_time_limit', False)):
            self.assertFalse(t.StageTorrent.safe_limits(dict(safe, **{field: value})))
        self.assertFalse(t.StageTorrent.safe_limits({}))

    def test_episode_named_sample_never_qualifies_coverage(self):
        from types import SimpleNamespace
        call = copy.deepcopy(self.call)
        observed = self.fixture['inspect']['result']
        call['request'] = dict(target=a.binding(observed),
            expected_episodes=a.episode_set(observed), operation_id='f' * 32)
        q = call['request']
        for name in ('Sample/S01E01.mkv', 'S01E01.sample.mp4', 'Extras/S01E01.mkv'):
            inventory = dict(tags_match_operation=True, client_reports_complete=True,
                save_path=a.PREFIX + '/' + q['operation_id'],
                files=[dict(name=name, size=100)])
            torrent = SimpleNamespace(status=lambda caller: inventory)
            with self.subTest(name=name), patch.object(media, 'inspect', return_value=observed), \
                 patch.object(media, 'open_payload') as opened:
                with self.assertRaises(Refused):
                    media.verify(call, self.block, SyntheticSonarr(self.fixture),
                                 SyntheticEmby(), torrent)
                opened.assert_not_called()

    def test_only_one_add_send(self):
        api = object.__new__(t.StageTorrent)
        api.starting, api.add_may_send = True, False
        api.before_send('POST', '/api/v2/torrents/add')
        with self.assertRaises(Refused):
            api.before_send('POST', '/api/v2/torrents/add')

    def test_lost_add_reply_and_ack_readback_loss_never_become_not_sent(self):
        import time
        q = copy.deepcopy(self.fixture['start_frame'])
        expected = q['request']['expected']
        selected = dict(title=expected['title'], size=expected['size'],
                        infoHash=expected['info_hash'], downloadUrl='https://indexer.invalid/file')
        observation = dict(inspect=copy.deepcopy(self.fixture['inspect']['result']))
        class Api:
            _deadline = time.monotonic() + 30
            _guard = staticmethod(lambda: 20)
        for acknowledged in (False, True):
            class Lost:
                calls = 7
                add_may_send = True
                deadline = time.monotonic() + 20
                def __init__(self, *args, **kwargs):
                    self.acknowledged = acknowledged
                def add(self, raw, callback):
                    raise TimeoutError()
                def logout(self):
                    raise TimeoutError()
            with patch.object(a, 'search', return_value=(observation, {q['request']['handle']: selected})), \
                 patch.object(a, 'download', return_value=b'synthetic'), \
                 patch.object(t, 'metainfo', return_value=(expected['info_hash'], 'release',
                    [dict(index=0, name='release.mkv', size=expected['size'])], 0)), \
                 patch.object(t, 'StageTorrent', Lost):
                result = a.start(q, self.block, Api(), None, {}, [])
            self.assertEqual(result['stage'], 'acknowledged' if acknowledged else 'dispatch_unknown')
            self.assertEqual(result['info_hash'], expected['info_hash'])
            self.assertEqual(result['calls'], 7)


if __name__ == '__main__':
    unittest.main()
