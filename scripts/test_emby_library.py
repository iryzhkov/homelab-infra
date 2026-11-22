"""Synthetic HoloNet library management v1 worker tests with fake HTTP backends.

No production credentials, live network calls, or writes outside scratch HOME.
"""
import copy
import hashlib
import http.server
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import urllib.parse

import emby_operator_adapter as adapter
import emby_operator_library as lib
import emby_operator_socket as sock
import emby_operator_torrents as tor

FIXTURE = Path(__file__).resolve().parents[1] / 'docs/holonet-library-wire-v1.json'
FIXTURE_SHA256 = '5778e83b79a528c420b8d3e0c4a45017bab3b29b989281802e059d536a6cf639'

RADARR_KEY = '1' * 32
SONARR_KEY = '2' * 32
EMBY_TOKEN = 'e' * 32
TORRENT_HASH_OK = 'f' * 40
TORRENT_HASH_LOW = '1' * 40
TORRENT_HASH_PACK = '2' * 40
TORRENT_HASH_CAT = '3' * 40


def start_server(handler_cls):
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f'http://127.0.0.1:{server.server_port}'


class FakeState:
    def __init__(self):
        self.radarr_recycle = '/mnt/shared/holonet-recycle/radarr'
        self.sonarr_recycle = '/mnt/shared/.recycle/sonarr'
        self.grab_expired = False
        self.image_only_subs = False
        self.deleted_movies = []
        self.deleted_series = []
        self.deleted_episode_files = []
        self.unmonitored_episodes = []
        self.deleted_torrents = []
        self.added_movies = []
        self.added_series = []
        self.grabbed = []
        self.extra_episodes = []
        self.movies = [{
            'id': 18,
            'title': 'Synthetic Movie',
            'year': 2024,
            'tmdbId': 603,
            'imdbId': 'tt0133093',
            'monitored': True,
            'path': '/mnt/shared/media/movies/Synthetic Movie (2024)',
            'sizeOnDisk': 4096,
            'hasFile': True,
            'movieFile': {
                'id': 501,
                'relativePath': 'Synthetic.mkv',
                'path': '/mnt/shared/media/movies/Synthetic Movie (2024)/Synthetic.mkv',
                'size': 4096,
                'dateAdded': '2026-11-01T00:00:00Z',
                'quality': {'quality': {'name': 'WEBDL-1080p'}},
                'mediaInfo': {'audioLanguages': 'eng', 'subtitles': 'eng'},
            },
        }]
        self.series = [
            {
                'id': 26,
                'title': 'Synthetic Anime',
                'year': 2022,
                'tvdbId': 384541,
                'imdbId': 'tt12590266',
                'monitored': True,
                'path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]',
                'seasons': [{'seasonNumber': 1, 'monitored': True, 'statistics': {
                    'totalEpisodeCount': 2, 'episodeFileCount': 2,
                }}],
                'statistics': {'sizeOnDisk': 4096},
            },
            {
                'id': 30,
                'title': 'Synthetic TV',
                'year': 2023,
                'tvdbId': 777777,
                'imdbId': 'tt7777777',
                'monitored': True,
                'path': '/mnt/shared/media/tv shows/Synthetic TV (2023)',
                'seasons': [{'seasonNumber': 1, 'monitored': True, 'statistics': {
                    'totalEpisodeCount': 2, 'episodeFileCount': 2,
                }}],
                'statistics': {'sizeOnDisk': 2000},
            },
        ]
        self.episodes = [
            {
                'id': 101, 'seriesId': 26, 'seasonNumber': 1, 'episodeNumber': 1,
                'title': 'Ep One', 'monitored': True, 'hasFile': True,
                'episodeFile': {
                    'id': 601, 'relativePath': 'Season 01/S01E01.mkv',
                    'path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]/Season 01/S01E01.mkv',
                    'size': 2048, 'dateAdded': '2026-11-01T00:00:00Z',
                    'quality': {'quality': {'name': 'WEBDL-1080p'}},
                    'mediaInfo': {'audioLanguages': 'jpn/eng', 'subtitles': 'eng'},
                },
            },
            {
                'id': 102, 'seriesId': 26, 'seasonNumber': 1, 'episodeNumber': 2,
                'title': 'Ep Two', 'monitored': True, 'hasFile': True,
                'episodeFile': {
                    'id': 602, 'relativePath': 'Season 01/S01E02.mkv',
                    'path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]/Season 01/S01E02.mkv',
                    'size': 2048, 'dateAdded': '2026-11-01T00:00:00Z',
                    'quality': {'quality': {'name': 'WEBDL-1080p'}},
                    'mediaInfo': {'audioLanguages': 'jpn', 'subtitles': 'eng'},
                },
            },
        ]
        self.torrents = {
            TORRENT_HASH_OK: {
                'hash': TORRENT_HASH_OK, 'name': 'Synthetic.Movie.2024',
                'category': 'radarr-movies', 'ratio': 1.5, 'seeding_time': 15 * 86400,
            },
            TORRENT_HASH_LOW: {
                'hash': TORRENT_HASH_LOW, 'name': 'Synthetic.Movie.LowRatio',
                'category': 'radarr-movies', 'ratio': 0.2, 'seeding_time': 3 * 86400,
            },
            TORRENT_HASH_PACK: {
                'hash': TORRENT_HASH_PACK, 'name': 'Synthetic.Anime.S01.Pack',
                'category': 'tv-sonarr', 'ratio': 2.0, 'seeding_time': 20 * 86400,
            },
            TORRENT_HASH_CAT: {
                'hash': TORRENT_HASH_CAT, 'name': 'Synthetic.Other.Cat',
                'category': 'other-cat', 'ratio': 5.0, 'seeding_time': 30 * 86400,
            },
        }
        self.movie_history = [{'downloadId': TORRENT_HASH_OK, 'movieId': 18}]
        self.series_history = [
            {'downloadId': TORRENT_HASH_PACK, 'seriesId': 26, 'episodeId': 101},
            {'downloadId': TORRENT_HASH_PACK, 'seriesId': 26, 'episodeId': 102},
        ]


def make_radarr_handler(state):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, obj):
            raw = b'' if obj is None else json.dumps(obj).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _auth(self):
            return self.headers.get('X-Api-Key') == RADARR_KEY

        def do_GET(self):
            if not self._auth():
                return self._send(401, {'error': 'unauthorized'})
            u = urllib.parse.urlsplit(self.path)
            if u.path == '/api/v3/system/status':
                return self._send(200, {'appName': 'Radarr', 'version': '6.4.4.10685'})
            if u.path == '/api/v3/config/mediamanagement':
                return self._send(200, {'recycleBin': state.radarr_recycle})
            if u.path == '/api/v3/movie':
                return self._send(200, state.movies)
            if u.path == '/api/v3/movie/18':
                return self._send(200, state.movies[0])
            if u.path == '/api/v3/movie/lookup':
                return self._send(200, [{
                    'title': 'Synthetic New Movie', 'year': 2025, 'tmdbId': 888001, 'imdbId': 'tt8880001',
                }])
            if u.path == '/api/v3/release':
                return self._send(200, [{
                    'guid': 'https://indexer.invalid/movie/1', 'indexerId': 2,
                    'title': 'Synthetic.Movie.2024.1080p', 'indexer': 'Synthetic',
                    'size': 4096, 'seeders': 10, 'leechers': 2, 'age': 3,
                    'quality': {'quality': {'name': 'WEBDL-1080p'}},
                    'languages': [{'name': 'English'}], 'customFormatScore': 50,
                    'approved': True, 'rejections': [],
                }])
            if u.path == '/api/v3/queue':
                return self._send(200, {'records': []})
            if u.path == '/api/v3/history/movie':
                return self._send(200, state.movie_history)
            return self._send(404, {'error': 'not found'})

        def do_POST(self):
            if not self._auth():
                return self._send(401, {'error': 'unauthorized'})
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            if self.path == '/api/v3/movie':
                state.added_movies.append(body)
                return self._send(201, {
                    'id': 77, 'title': body['title'],
                    'path': body['rootFolderPath'] + '/' + body['title'],
                })
            if self.path == '/api/v3/release':
                if state.grab_expired:
                    return self._send(404, {'message': 'release expired'})
                state.grabbed.append(body)
                return self._send(200, {'ok': True})
            return self._send(404, {})

        def do_DELETE(self):
            if not self._auth():
                return self._send(401, {'error': 'unauthorized'})
            state.deleted_movies.append(self.path)
            return self._send(200, {})
    return Handler


def make_sonarr_handler(state):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, obj):
            raw = b'' if obj is None else json.dumps(obj).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _auth(self):
            return self.headers.get('X-Api-Key') == SONARR_KEY

        def do_GET(self):
            if not self._auth():
                return self._send(401, {'error': 'unauthorized'})
            u = urllib.parse.urlsplit(self.path)
            if u.path == '/api/v3/system/status':
                return self._send(200, {'appName': 'Sonarr', 'version': '4.0.20.3014'})
            if u.path == '/api/v3/config/mediamanagement':
                return self._send(200, {'recycleBin': state.sonarr_recycle})
            if u.path == '/api/v3/series':
                return self._send(200, state.series)
            if u.path == '/api/v3/series/26':
                return self._send(200, state.series[0])
            if u.path == '/api/v3/series/lookup':
                return self._send(200, [{
                    'title': 'Synthetic New Anime', 'year': 2025, 'tvdbId': 999001, 'imdbId': 'tt9990001',
                    'seasons': [{'seasonNumber': 1, 'monitored': False}],
                }])
            if u.path == '/api/v3/episode':
                return self._send(200, state.episodes + state.extra_episodes)
            if u.path == '/api/v3/episode/101':
                return self._send(200, state.episodes[0])
            if u.path == '/api/v3/release':
                return self._send(200, [
                    {
                        'guid': 'https://indexer.invalid/release/1', 'indexerId': 1,
                        'title': 'Synthetic Anime S01 1080p', 'indexer': 'Synthetic',
                        'size': 2048, 'seeders': 12, 'leechers': 1, 'age': 5,
                        'quality': {'quality': {'name': 'WEBDL-1080p'}},
                        'languages': [{'name': 'English'}, {'name': 'Japanese'}],
                        'customFormatScore': 100, 'fullSeason': True,
                        'approved': True, 'rejections': [],
                    },
                    {
                        'guid': 'https://indexer.invalid/release/2', 'indexerId': 1,
                        'title': 'Synthetic Anime S01 Bad', 'indexer': 'Synthetic',
                        'size': 1024, 'seeders': 2, 'leechers': 0, 'age': 1,
                        'quality': {'quality': {'name': 'HDTV-720p'}},
                        'languages': [{'name': 'Japanese'}],
                        'customFormatScore': -10, 'fullSeason': True,
                        'approved': False, 'rejections': ['Quality cutoff met'],
                    },
                ])
            if u.path == '/api/v3/queue':
                return self._send(200, {'records': [{
                    'title': 'Synthetic Anime S01 1080p', 'seriesId': 26, 'episodeId': 101,
                    'status': 'downloading', 'trackedDownloadState': 'downloading',
                    'size': 2048, 'sizeleft': 1024, 'timeleft': '00:05:00',
                    'downloadClient': 'qBittorrent', 'statusMessages': [],
                }]})
            if u.path == '/api/v3/history/series':
                return self._send(200, state.series_history)
            return self._send(404, {})

        def do_POST(self):
            if not self._auth():
                return self._send(401, {'error': 'unauthorized'})
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            if self.path == '/api/v3/series':
                state.added_series.append(body)
                return self._send(201, {
                    'id': 99, 'title': body['title'],
                    'path': '/mnt/shared/media/anime/Synthetic New Anime (2025) [tvdb-999001]',
                })
            if self.path == '/api/v3/release':
                if state.grab_expired:
                    return self._send(404, {'message': 'release expired'})
                state.grabbed.append(body)
                return self._send(200, {'ok': True})
            return self._send(404, {})

        def do_PUT(self):
            if not self._auth():
                return self._send(401, {'error': 'unauthorized'})
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            if self.path == '/api/v3/episode/monitor':
                state.unmonitored_episodes.append(body)
                return self._send(202, {})
            return self._send(404, {})

        def do_DELETE(self):
            if not self._auth():
                return self._send(401, {'error': 'unauthorized'})
            if self.path.startswith('/api/v3/episodefile/'):
                state.deleted_episode_files.append(self.path)
            else:
                state.deleted_series.append(self.path)
            return self._send(200, {})
    return Handler


def make_qbit_handler(state):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, raw, cookie=False):
            self.send_response(status)
            self.send_header('Content-Length', str(len(raw)))
            if cookie:
                self.send_header('Set-Cookie', 'QBT_SID_8080=' + 'a' * 32)
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode('ascii')
            if self.path == '/api/v2/auth/login':
                return self._send(204, b'', cookie=True)
            if self.path == '/api/v2/auth/logout':
                return self._send(204, b'')
            if self.path == '/api/v2/torrents/delete':
                params = urllib.parse.parse_qs(body)
                state.deleted_torrents.append(params)
                return self._send(200, b'')
            return self._send(404, b'')

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            if u.path == '/api/v2/app/version':
                return self._send(200, b'v5.3.0')
            if u.path == '/api/v2/torrents/info':
                hashes = urllib.parse.parse_qs(u.query).get('hashes', [''])[0].split('|')
                rows = [state.torrents[h] for h in hashes if h in state.torrents]
                return self._send(200, json.dumps(rows).encode('utf-8'))
            return self._send(404, b'')
    return Handler


def make_emby_handler(state):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, raw, ct='application/json'):
            self.send_response(status)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path == '/emby/Users/AuthenticateByName':
                return self._send(200, json.dumps({
                    'AccessToken': EMBY_TOKEN,
                    'ServerId': 'a' * 32,
                    'User': {'Id': 'b' * 32, 'Name': 'operator', 'Policy': {'IsAdministrator': True}},
                }).encode('utf-8'))
            return self._send(404, b'{}')

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            if u.path == '/emby/Users':
                return self._send(200, json.dumps([{'Id': 'b' * 32}, {'Id': 'c' * 32}]).encode('utf-8'))
            if u.path == '/emby/Users/' + 'b' * 32:
                return self._send(200, json.dumps({
                    'Id': 'b' * 32, 'ServerId': 'a' * 32,
                    'Policy': {'IsDisabled': False, 'IsAdministrator': True},
                }).encode('utf-8'))
            if u.path == '/emby/Users/' + 'c' * 32:
                return self._send(200, json.dumps({
                    'Id': 'c' * 32, 'ServerId': 'a' * 32,
                    'Policy': {'IsDisabled': False, 'IsAdministrator': False},
                }).encode('utf-8'))
            if u.path == '/emby/Library/VirtualFolders':
                return self._send(200, json.dumps([
                    {'ItemId': '3', 'Guid': '9' * 32, 'Name': 'Movies', 'CollectionType': 'movies'},
                ]).encode('utf-8'))
            if u.path == '/emby/Users/' + 'c' * 32 + '/Views':
                return self._send(200, json.dumps({
                    'TotalRecordCount': 1, 'Items': [{'Id': '3', 'Name': 'Movies'}],
                }).encode('utf-8'))
            if u.path.endswith('/Items'):
                qs = urllib.parse.parse_qs(u.query)
                item_types = qs.get('IncludeItemTypes', [''])[0]
                if item_types == 'Series':
                    return self._send(200, json.dumps({
                        'TotalRecordCount': 1,
                        'Items': [{
                            'Id': '55000', 'Type': 'Series', 'Name': 'Synthetic Anime',
                            'Path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]',
                            'ProviderIds': {'Tvdb': '384541'},
                        }],
                    }).encode('utf-8'))
                if item_types == 'Season':
                    return self._send(200, json.dumps({
                        'TotalRecordCount': 1,
                        'Items': [{
                            'Id': '55010', 'Type': 'Season', 'Name': 'Season 1',
                            'IndexNumber': 1,
                        }],
                    }).encode('utf-8'))
                if item_types == 'Episode':
                    return self._send(200, json.dumps({
                        'TotalRecordCount': 2,
                        'Items': [
                            {
                                'Id': '55011', 'Type': 'Episode', 'Name': 'Ep One',
                                'ParentIndexNumber': 1, 'IndexNumber': 1,
                                'Path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]/Season 01/S01E01.mkv',
                            },
                            {
                                'Id': '55012', 'Type': 'Episode', 'Name': 'Ep Two',
                                'ParentIndexNumber': 1, 'IndexNumber': 2,
                                'Path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]/Season 01/S01E02.mkv',
                            },
                        ],
                    }).encode('utf-8'))
                streams = (
                    [{'Type': 'Subtitle', 'Index': 3, 'Language': 'eng', 'Codec': 'pgssub',
                      'DisplayTitle': 'English (PGSSUB)', 'IsForced': False, 'IsExternal': False,
                      'IsTextSubtitleStream': False}]
                    if state.image_only_subs else
                    [{'Type': 'Subtitle', 'Index': 2, 'Language': 'eng', 'Codec': 'subrip',
                      'DisplayTitle': 'English (SUBRIP)', 'IsForced': False, 'IsExternal': False,
                      'IsTextSubtitleStream': True}]
                )
                return self._send(200, json.dumps({
                    'TotalRecordCount': 1,
                    'Items': [{
                        'Id': '44801', 'Type': 'Movie', 'Name': 'Synthetic Movie',
                        'Path': '/mnt/shared/media/movies/Synthetic Movie (2024)/Synthetic.mkv',
                        'ProviderIds': {'Tmdb': '603'},
                        'MediaSources': [{'Id': 'mediasource_44801', 'MediaStreams': streams}],
                    }],
                }).encode('utf-8'))
            if u.path == '/emby/Videos/44801/mediasource_44801/Subtitles/2/Stream.srt':
                srt = (
                    '1\n00:00:01,000 --> 00:00:03,000\n<i>Synthetic</i> line one.\n\n'
                    '2\n00:00:05,500 --> 00:00:07,000\n{\\an8}Synthetic line two.\n\n'
                    '3\n00:05:00,000 --> 00:05:02,000\nOutside window.\n'
                ).encode('utf-8')
                return self._send(200, srt, ct='text/plain; charset=utf-8')
            return self._send(404, b'{}')
    return Handler


class LibraryWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text())

    def setUp(self):
        self.state = FakeState()
        self.radarr_srv, self.radarr_th, self.radarr_url = start_server(make_radarr_handler(self.state))
        self.sonarr_srv, self.sonarr_th, self.sonarr_url = start_server(make_sonarr_handler(self.state))
        self.qbit_srv, self.qbit_th, self.qbit_url = start_server(make_qbit_handler(self.state))
        self.emby_srv, self.emby_th, self.emby_url = start_server(make_emby_handler(self.state))
        self.policy = {
            'version': 1,
            'enabled': True,
            'peer': {'uid': 940, 'gid': 940},
            'caller': {
                'issuer': 'https://issuer.invalid/application/o/synthetic/',
                'subject': 'synthetic-writer-subject',
                'namespace': 'managed-recommendations',
                'server': 'a' * 32,
                'library': '3',
                'catalog_user': 'd' * 32,
            },
            'recipients': {'c' * 32: '/var/lib/holonet-operator/synthetic/config.json'},
            'library': {
                'enabled': True,
                'target_library': '3',
                'revision': 'e' * 40,
                'libraries': {
                    'movies': {
                        'library_id': '3', 'kind': 'movie', 'arr': 'radarr',
                        'descriptor': '/etc/holonet-playlists/radarr.json',
                        'root_folder_path': '/mnt/shared/media/movies',
                        'quality_profile_id': 11,
                    },
                    'tv': {
                        'library_id': '1132', 'kind': 'series', 'arr': 'sonarr',
                        'descriptor': '/etc/holonet-playlists/sonarr.json',
                        'root_folder_path': '/mnt/shared/media/tv shows',
                        'quality_profile_id': 6,
                    },
                    'anime': {
                        'library_id': '615', 'kind': 'series', 'arr': 'sonarr',
                        'descriptor': '/etc/holonet-playlists/sonarr.json',
                        'root_folder_path': '/mnt/shared/media/anime',
                        'quality_profile_id': 6,
                    },
                },
                'torrents': '/etc/holonet-playlists/qbit.json',
                'allowed_categories': ['radarr-movies', 'tv-sonarr'],
                'removal_thresholds': {'min_ratio': 1.0, 'min_seeding_days': 14},
                'limits': {
                    'lookup_max_rows': 25,
                    'releases_max_rows': 50,
                    'subtitles_default_chars': 20000,
                    'subtitles_max_chars': 60000,
                    'remove_max_files': 500,
                },
            },
        }
        self.emby_config = {
            'enabled': True,
            'origin': self.emby_url,
            'server': 'a' * 32,
            'actor': 'b' * 32,
            'recipient': 'c' * 32,
            'library': '3',
            'library_guid': '9' * 32,
            'recipe': 'synthetic',
            'credential_file': '/etc/holonet-playlists/emby.json',
            'journal_directory': '/var/lib/holonet-operator/synthetic/journal',
        }
        self.descriptors = {
            '/etc/holonet-playlists/radarr.json': {
                'version': 1, 'backend': 'radarr', 'resource_id': 'homelab-radarr',
                'origin': self.radarr_url, 'expected_version': '6.4.4.10685',
                'app_config_file': '/mnt/docker-data/swarm/radarr-movie/config/config.xml',
                'app_config_owner_uid': 1002, 'app_config_read_gid': 988,
            },
            '/etc/holonet-playlists/sonarr.json': {
                'version': 1, 'backend': 'sonarr', 'resource_id': 'homelab-sonarr-tv',
                'origin': self.sonarr_url, 'expected_version': '4.0.20.3014',
                'app_config_file': '/mnt/docker-data/swarm/sonarr-tv/config/config.xml',
                'app_config_owner_uid': 1002, 'app_config_read_gid': 988,
            },
            '/etc/holonet-playlists/qbit.json': {
                'version': 1, 'resource_id': 'homelab-qbit-movies',
                'origin': self.qbit_url, 'expected_version': 'v5.3.0',
                'credential_file': '/etc/holonet-playlists/qbit-auth.json',
                'categories': ['radarr-movies', 'tv-sonarr'],
                'payload_prefixes': ['/mnt/shared/downloads/complete/movies'],
            },
            '/etc/holonet-playlists/qbit-auth.json': {'username': 'synthetic', 'password': 'synthetic'},
        }

    def tearDown(self):
        for srv, th in (
            (self.radarr_srv, self.radarr_th),
            (self.sonarr_srv, self.sonarr_th),
            (self.qbit_srv, self.qbit_th),
            (self.emby_srv, self.emby_th),
        ):
            srv.shutdown()
            srv.server_close()
            th.join()

    def _run(self, frame):
        def fake_private_read(path, limit=16384):
            return json.dumps(self.descriptors[path]).encode('utf-8')

        def fake_importer_key(cfg):
            return RADARR_KEY if cfg['backend'] == 'radarr' else SONARR_KEY

        parsed = sock.parse_request(json.dumps(frame).encode('utf-8'))
        sock.authorize(parsed, self.policy)
        with patch.object(lib, 'private_read', side_effect=fake_private_read), \
             patch.object(tor, 'private_read', side_effect=fake_private_read), \
             patch.object(lib, 'importer_key', side_effect=fake_importer_key), \
             patch.object(lib, 'load_credentials', return_value={'username': 'op', 'password': 'pw'}), \
             patch.object(adapter, 'validate_config', side_effect=lambda c: copy.deepcopy(c)):
            return lib.execute(self.policy, parsed, lambda: 60.0, self.emby_config)

    def test_wire_fixture_hash_and_all_frames_parse(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), FIXTURE_SHA256)
        sock.validate_policy(self.policy)
        for key, value in self.fixture.items():
            if key.endswith('_frame'):
                parsed = sock.parse_request(json.dumps(value).encode('utf-8'))
                sock.authorize(parsed, self.policy)

    def test_lookup_detail_releases_queue_and_subtitles(self):
        self.assertEqual(
            self._run(self.fixture['library_lookup_frame']),
            self.fixture['library_lookup']['result'],
        )
        self.assertEqual(
            self._run(self.fixture['library_detail_frame']),
            self.fixture['library_detail']['result'],
        )
        releases_res = self._run(self.fixture['library_releases_frame'])
        self.assertEqual(releases_res['releases'][0], self.fixture['library_releases']['result']['releases'][0])
        self.assertEqual(len(releases_res['releases']), 2)
        self.assertEqual(
            self._run(self.fixture['library_queue_frame']),
            self.fixture['library_queue']['result'],
        )
        self.assertEqual(
            self._run(self.fixture['library_subtitles_frame']),
            self.fixture['library_subtitles']['result'],
        )

    def test_subtitles_image_subtitle_reported_without_decoding(self):
        self.state.image_only_subs = True
        res = self._run(self.fixture['library_subtitles_frame'])
        self.assertEqual(res['status'], 'image_subtitle')
        self.assertEqual(res['lines'], [])
        self.assertEqual(res['streams'][0]['codec'], 'pgssub')

    def test_add_preview_apply_plan_changed_and_already_in_library(self):
        preview = self._run(self.fixture['library_add_preview_frame'])
        self.assertEqual(preview, self.fixture['library_add_preview']['result'])
        applied = self._run(self.fixture['library_add_apply_frame'])
        self.assertEqual(applied, self.fixture['library_add_apply']['result'])
        self.assertEqual(len(self.state.added_series), 1)
        self.assertEqual(self.state.added_series[0]['qualityProfileId'], 6)

        bad_apply = copy.deepcopy(self.fixture['library_add_apply_frame'])
        bad_apply['request']['plan_sha256'] = '0' * 64
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(bad_apply)
        self.assertEqual(ctx.exception.reason, 'plan_changed')

        dup = copy.deepcopy(self.fixture['library_add_preview_frame'])
        dup['request']['tvdb_id'] = 384541
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(dup)
        self.assertEqual(ctx.exception.reason, 'already_in_library')

    def test_grab_preview_apply_rejected_and_expired(self):
        preview = self._run(self.fixture['library_grab_preview_frame'])
        self.assertEqual(preview, self.fixture['library_grab_preview']['result'])
        applied = self._run(self.fixture['library_grab_apply_frame'])
        self.assertEqual(applied, self.fixture['library_grab_apply']['result'])
        self.assertEqual(self.state.grabbed, [{'guid': 'https://indexer.invalid/release/1', 'indexerId': 1}])

        # Expired release refuses release_expired.
        self.state.grab_expired = True
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(self.fixture['library_grab_apply_frame'])
        self.assertEqual(ctx.exception.reason, 'release_expired')
        self.state.grab_expired = False

        # Rejected release requires allow_rejected true.
        rej = copy.deepcopy(self.fixture['library_grab_apply_frame'])
        rej['request']['approved'] = False
        rej['request']['rejections'] = ['Quality cutoff met']
        rej['request']['allow_rejected'] = False
        rej_preview = copy.deepcopy(rej)
        rej_preview['request']['mode'] = 'preview'
        del rej_preview['request']['operation_id']
        del rej_preview['request']['plan_sha256']
        rej['request']['plan_sha256'] = self._run(rej_preview)['plan_sha256']
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(rej)
        self.assertEqual(ctx.exception.reason, 'rejected_release')

    def test_remove_movie_title_preview_apply_and_recycle_bin_refusal(self):
        preview = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(preview, self.fixture['library_remove_preview']['result'])
        applied = self._run(self.fixture['library_remove_apply_frame'])
        self.assertEqual(applied, self.fixture['library_remove_apply']['result'])
        self.assertEqual(self.state.deleted_movies, ['/api/v3/movie/18?deleteFiles=true&addImportExclusion=false'])
        self.assertEqual(self.state.deleted_torrents, [{'hashes': [TORRENT_HASH_OK], 'deleteFiles': ['true']}])

        # Recycle bin unset refuses.
        self.state.radarr_recycle = ''
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(ctx.exception.reason, 'recycle_bin_unset')

    def test_remove_seeding_threshold_and_season_pack_partially_kept(self):
        # Low ratio / low seeding time -> torrent kept.
        self.state.movie_history = [
            {'downloadId': TORRENT_HASH_LOW, 'movieId': 18},
            {'downloadId': TORRENT_HASH_CAT, 'movieId': 18},
        ]
        res = self._run(self.fixture['library_remove_preview_frame'])
        by_hash = {t['hash']: t for t in res['plan']['torrents']}
        self.assertEqual(by_hash[TORRENT_HASH_LOW]['action'], 'keep')
        self.assertEqual(by_hash[TORRENT_HASH_LOW]['reason'], 'seeding_threshold_not_met')
        self.assertEqual(by_hash[TORRENT_HASH_CAT]['action'], 'keep')
        self.assertEqual(by_hash[TORRENT_HASH_CAT]['reason'], 'category_not_allowed')

        # Deleting only episode 101 from a 2-episode season pack keeps the torrent.
        ep_frame = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        ep_frame['request'] = {
            'mode': 'preview', 'library': 'anime', 'arr_id': 26,
            'target': {'episode_arr_ids': [101]}, 'remove_torrents': True,
            'keep_emby_ids': [],
        }
        ep_preview = self._run(ep_frame)
        self.assertFalse(ep_preview['plan']['delete_title'])
        self.assertEqual(len(ep_preview['plan']['files']), 1)
        self.assertEqual(ep_preview['plan']['torrents'][0]['action'], 'keep')
        self.assertEqual(ep_preview['plan']['torrents'][0]['reason'], 'partially_kept')

        # Applying episode removal unmonitors first, then deletes episode file, keeps pack torrent.
        ep_apply = copy.deepcopy(ep_frame)
        ep_apply['request'].update(
            mode='apply', operation_id='b' * 32, plan_sha256=ep_preview['plan_sha256'],
        )
        ep_res = self._run(ep_apply)
        self.assertEqual(self.state.unmonitored_episodes, [{'episodeIds': [101], 'monitored': False}])
        self.assertEqual(self.state.deleted_episode_files, ['/api/v3/episodefile/601'])
        self.assertEqual(self.state.deleted_torrents, [])
        self.assertEqual(ep_res['kept_torrent_hashes'], [TORRENT_HASH_PACK])

    def test_remove_keep_listed_movie_and_series(self):
        # Unrelated keep_emby_ids does not change plan_sha256.
        base_preview = self._run(self.fixture['library_remove_preview_frame'])
        other_keep = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        other_keep['request']['keep_emby_ids'] = ['99999']
        other_preview = self._run(other_keep)
        self.assertEqual(other_preview['plan_sha256'], base_preview['plan_sha256'])

        # Movie Emby ID 44801 in keep_emby_ids refuses preview and apply.
        kept_movie_prev = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        kept_movie_prev['request']['keep_emby_ids'] = ['44801']
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(kept_movie_prev)
        self.assertEqual(ctx.exception.reason, 'keep_listed')

        kept_movie_apply = copy.deepcopy(self.fixture['library_remove_apply_frame'])
        kept_movie_apply['request']['keep_emby_ids'] = ['44801']
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(kept_movie_apply)
        self.assertEqual(ctx.exception.reason, 'keep_listed')
        self.assertEqual(self.state.deleted_movies, [])
        self.assertEqual(self.state.deleted_torrents, [])

        # Series keep_listed checks series ID (55000), affected season ID (55010), and affected episode ID (55011).
        for target, keep_ids in (
            ('title', ['55000']),
            ({'season': 1}, ['55000']),
            ({'season': 1}, ['55010']),
            ({'episode_arr_ids': [101]}, ['55000']),
            ({'episode_arr_ids': [101]}, ['55010']),
            ({'episode_arr_ids': [101]}, ['55011']),
        ):
            frame = copy.deepcopy(self.fixture['library_remove_preview_frame'])
            frame['request'] = {
                'mode': 'preview', 'library': 'anime', 'arr_id': 26,
                'target': target, 'remove_torrents': False,
                'keep_emby_ids': keep_ids,
            }
            with self.assertRaises(lib.LibraryRefusal) as ctx:
                self._run(frame)
            self.assertEqual(ctx.exception.reason, 'keep_listed')

        # Keeping only episode 102 (55012) allows removing episode 101.
        ok_ep = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        ok_ep['request'] = {
            'mode': 'preview', 'library': 'anime', 'arr_id': 26,
            'target': {'episode_arr_ids': [101]}, 'remove_torrents': False,
            'keep_emby_ids': ['55012'],
        }
        res = self._run(ok_ep)
        self.assertEqual(len(res['plan']['files']), 1)

    def test_authorization_disabled_policy_and_missing_action(self):
        read_call = sock.parse_request(json.dumps(self.fixture['library_lookup_frame']).encode('utf-8'))
        manage_call = sock.parse_request(json.dumps(self.fixture['library_add_preview_frame']).encode('utf-8'))

        # Disabled policy refuses both read and manage.
        disabled_policy = copy.deepcopy(self.policy)
        disabled_policy['library']['enabled'] = False
        for call in (read_call, manage_call):
            with self.assertRaises(sock.WireError) as ctx:
                sock.authorize(call, disabled_policy)
            self.assertEqual(ctx.exception.code, 'refused')

        # Missing library.manage refuses manage op.
        read_only_manage = copy.deepcopy(manage_call)
        read_only_manage['caller']['actions'] = ['library.read']
        with self.assertRaises(sock.WireError) as ctx:
            sock.authorize(read_only_manage, self.policy)
        self.assertEqual(ctx.exception.message, 'Missing required library action')

        # Missing library.read refuses read op.
        manage_only_read = copy.deepcopy(read_call)
        manage_only_read['caller']['actions'] = ['library.manage']
        with self.assertRaises(sock.WireError) as ctx:
            sock.authorize(manage_only_read, self.policy)
        self.assertEqual(ctx.exception.message, 'Missing required library action')


if __name__ == '__main__':
    unittest.main()
