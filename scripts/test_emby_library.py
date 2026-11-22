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
FIXTURE_SHA256 = 'b9d25c6a0d7a29eec40e272dfb16ad4aa9aba995cdd26c6582720097f7218b09'

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
        self.subtitles_override = None
        self.subtitle_srt_calls = []
        self.item_get_calls = []
        self.deleted_movies = []
        self.deleted_series = []
        self.deleted_episode_files = []
        self.unmonitored_episodes = []
        self.deleted_torrents = []
        self.added_movies = []
        self.added_series = []
        self.grabbed = []
        self.extra_episodes = []
        self.full_list_calls = []
        self.release_calls = []
        self.queue_total_records = None
        self.radarr_history_by_dl = None
        self.sonarr_history_by_dl = None
        self.history_truncated = False
        self.history_missing_total = False
        self.history_page_size = 200
        self.sonarr_history_fail = False
        self.fail_episode_file_delete = False
        self.fail_qbit_delete = False
        self.fail_movie_delete = False
        self.emby_movies_override = None
        self.emby_series_override = None
        self.emby_episodes_override = None
        self.emby_episodes_total_override = None
        self.emby_episodes_page_size = None
        self.episode_files_by_id = {}
        self.movie_files_by_id = {}
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
        self.movie_history = [{'downloadId': TORRENT_HASH_OK, 'movieId': 18, 'eventType': 'downloadFolderImported'}]
        self.series_history = [
            {'downloadId': TORRENT_HASH_PACK, 'seriesId': 26, 'episodeId': 101, 'eventType': 'downloadFolderImported'},
            {'downloadId': TORRENT_HASH_PACK, 'seriesId': 26, 'episodeId': 102, 'eventType': 'downloadFolderImported'},
        ]
        self.radarr_exclusions = []
        self.radarr_importlists = []
        self.radarr_recommendations = []
        self.sonarr_exclusions = []
        self.sonarr_importlists = []


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
                state.full_list_calls.append(('radarr', self.path))
                return self._send(200, state.movies)
            if u.path.startswith('/api/v3/movie/') and u.path != '/api/v3/movie/lookup':
                try:
                    mid = int(u.path.rsplit('/', 1)[-1])
                except ValueError:
                    return self._send(404, {'error': 'not found'})
                for m in state.movies:
                    if m['id'] == mid:
                        state.item_get_calls.append(('radarr', mid))
                        return self._send(200, m)
                return self._send(404, {'error': 'not found'})
            if u.path == '/api/v3/movie/lookup':
                term = urllib.parse.parse_qs(u.query).get('term', [''])[0]
                def strip_movie_stats(m):
                    c = copy.deepcopy(m)
                    c.pop('sizeOnDisk', None)
                    c.pop('statistics', None)
                    return c
                if term == 'tmdb:603':
                    return self._send(200, [strip_movie_stats(state.movies[0])])
                rows = [
                    strip_movie_stats(m) for m in state.movies
                    if term.lower() in m['title'].lower() or term == f"tmdb:{m['tmdbId']}"
                ]
                rows.append({
                    'title': 'Synthetic New Movie', 'year': 2025, 'tmdbId': 888001, 'imdbId': 'tt8880001',
                })
                return self._send(200, rows)
            if u.path == '/api/v3/release':
                state.release_calls.append(('radarr', self.path))
                return self._send(200, [{
                    'guid': 'https://indexer.invalid/movie/1', 'indexerId': 2,
                    'title': 'Synthetic.Movie.2024.1080p', 'indexer': 'Synthetic',
                    'size': 4096, 'seeders': 10, 'leechers': 2, 'age': 3,
                    'quality': {'quality': {'name': 'WEBDL-1080p'}},
                    'languages': [{'name': 'English'}], 'customFormatScore': 50,
                    'approved': True, 'rejections': [],
                }])
            if u.path == '/api/v3/queue':
                total = state.queue_total_records if state.queue_total_records is not None else 0
                return self._send(200, {'records': [], 'totalRecords': total})
            if u.path.startswith('/api/v3/moviefile/'):
                try:
                    fid = int(u.path.rsplit('/', 1)[-1])
                except ValueError:
                    return self._send(404, {})
                mf = state.movie_files_by_id.get(fid)
                if mf is not None:
                    return self._send(200, mf)
                return self._send(404, {'error': 'not found'})
            if u.path == '/api/v3/history/movie':
                return self._send(200, state.movie_history)
            if u.path == '/api/v3/exclusions':
                return self._send(200, state.radarr_exclusions)
            if u.path == '/api/v3/importlist':
                return self._send(200, state.radarr_importlists)
            if u.path == '/api/v3/importlist/movie':
                return self._send(200, state.radarr_recommendations)
            if u.path == '/api/v3/history':
                qs = urllib.parse.parse_qs(u.query)
                dl = qs.get('downloadId', [''])[0].lower()
                page = int(qs.get('page', ['1'])[0])
                req_ps = int(qs.get('pageSize', ['200'])[0])
                ps = min(req_ps, state.history_page_size)
                if state.radarr_history_by_dl is not None:
                    recs = state.radarr_history_by_dl.get(dl, [])
                else:
                    recs = [r for r in state.movie_history if str(r.get('downloadId', '')).lower() == dl]
                page_recs = recs[(page - 1) * ps : page * ps]
                payload = {'page': page, 'pageSize': ps, 'records': page_recs}
                if not state.history_missing_total:
                    payload['totalRecords'] = len(recs) + 5 if state.history_truncated else len(recs)
                return self._send(200, payload)
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
            if state.fail_movie_delete:
                return self._send(500, {'error': 'boom'})
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
                state.full_list_calls.append(('sonarr', self.path))
                return self._send(200, state.series)
            if u.path.startswith('/api/v3/series/') and u.path != '/api/v3/series/lookup':
                try:
                    sid = int(u.path.rsplit('/', 1)[-1])
                except ValueError:
                    return self._send(404, {})
                for s in state.series:
                    if s['id'] == sid:
                        state.item_get_calls.append(('sonarr', sid))
                        return self._send(200, s)
                return self._send(404, {})
            if u.path == '/api/v3/series/lookup':
                term = urllib.parse.parse_qs(u.query).get('term', [''])[0]
                def strip_series_stats(s):
                    c = copy.deepcopy(s)
                    c.pop('statistics', None)
                    for season in c.get('seasons') or []:
                        season.pop('statistics', None)
                    return c
                rows = [
                    strip_series_stats(s) for s in state.series
                    if term.lower() in s['title'].lower() or term == f"tvdb:{s['tvdbId']}"
                ]
                rows.append({
                    'title': 'Synthetic New Anime', 'year': 2025, 'tvdbId': 999001, 'imdbId': 'tt9990001',
                    'seasons': [{'seasonNumber': 1, 'monitored': False}],
                })
                return self._send(200, rows)
            if u.path == '/api/v3/episode':
                return self._send(200, state.episodes + state.extra_episodes)
            if u.path.startswith('/api/v3/episode/'):
                try:
                    eid = int(u.path.rsplit('/', 1)[-1])
                except ValueError:
                    return self._send(404, {})
                for ep in state.episodes + state.extra_episodes:
                    if ep['id'] == eid:
                        return self._send(200, ep)
                return self._send(404, {})
            if u.path == '/api/v3/release':
                state.release_calls.append(('sonarr', self.path))
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
                recs = [{
                    'title': 'Synthetic Anime S01 1080p', 'seriesId': 26, 'episodeId': 101,
                    'series': state.series[0],
                    'status': 'downloading', 'trackedDownloadState': 'downloading',
                    'size': 2048, 'sizeleft': 1024, 'timeleft': '00:05:00',
                    'downloadClient': 'qBittorrent', 'statusMessages': [],
                }]
                total = state.queue_total_records if state.queue_total_records is not None else len(recs)
                return self._send(200, {'records': recs, 'totalRecords': total})
            if u.path.startswith('/api/v3/episodefile/'):
                try:
                    fid = int(u.path.rsplit('/', 1)[-1])
                except ValueError:
                    return self._send(404, {})
                ef = state.episode_files_by_id.get(fid)
                if ef is not None:
                    return self._send(200, ef)
                return self._send(404, {'error': 'not found'})
            if u.path == '/api/v3/history/series':
                return self._send(200, state.series_history)
            if u.path == '/api/v3/importlistexclusion':
                return self._send(200, state.sonarr_exclusions)
            if u.path == '/api/v3/importlist':
                return self._send(200, state.sonarr_importlists)
            if u.path == '/api/v3/history':
                if state.sonarr_history_fail:
                    return self._send(500, {'error': 'sonarr history down'})
                qs = urllib.parse.parse_qs(u.query)
                dl = qs.get('downloadId', [''])[0].lower()
                page = int(qs.get('page', ['1'])[0])
                req_ps = int(qs.get('pageSize', ['200'])[0])
                ps = min(req_ps, state.history_page_size)
                if state.sonarr_history_by_dl is not None:
                    recs = state.sonarr_history_by_dl.get(dl, [])
                else:
                    recs = [r for r in state.series_history if str(r.get('downloadId', '')).lower() == dl]
                page_recs = recs[(page - 1) * ps : page * ps]
                payload = {'page': page, 'pageSize': ps, 'records': page_recs}
                if not state.history_missing_total:
                    payload['totalRecords'] = len(recs) + 5 if state.history_truncated else len(recs)
                return self._send(200, payload)
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
                if state.fail_episode_file_delete:
                    return self._send(500, {'error': 'delete failed'})
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
                if state.fail_qbit_delete:
                    return self._send(500, b'err')
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
                    rows = state.emby_series_override if state.emby_series_override is not None else [{
                        'Id': '55000', 'Type': 'Series', 'Name': 'Synthetic Anime',
                        'Path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]',
                        'ProviderIds': {'Tvdb': '384541'},
                    }]
                    return self._send(200, json.dumps({
                        'TotalRecordCount': len(rows),
                        'Items': rows,
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
                    rows = state.emby_episodes_override if state.emby_episodes_override is not None else [
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
                    ]
                    start = int(qs.get('StartIndex', ['0'])[0])
                    limit = int(qs.get('Limit', ['500'])[0])
                    if state.emby_episodes_page_size is not None:
                        limit = min(limit, state.emby_episodes_page_size)
                    page_rows = rows[start : start + limit]
                    total = (
                        state.emby_episodes_total_override
                        if state.emby_episodes_total_override is not None
                        else len(rows)
                    )
                    return self._send(200, json.dumps({
                        'TotalRecordCount': total,
                        'Items': page_rows,
                    }).encode('utf-8'))
                if state.emby_movies_override is not None:
                    prov_filter = qs.get('AnyProviderIdEquals', [None])[0]
                    rows = state.emby_movies_override(prov_filter) if callable(state.emby_movies_override) else state.emby_movies_override
                    return self._send(200, json.dumps({
                        'TotalRecordCount': len(rows),
                        'Items': rows,
                    }).encode('utf-8'))
                if state.subtitles_override is not None:
                    streams = state.subtitles_override
                else:
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
            if u.path.startswith('/emby/Videos/44801/mediasource_44801/Subtitles/') and u.path.endswith('/Stream.srt'):
                idx = int(u.path.split('/')[-2])
                state.subtitle_srt_calls.append(idx)
                if idx == 2:
                    srt = (
                        '1\n00:00:01,000 --> 00:00:03,000\n<i>Synthetic</i> line one.\n\n'
                        '2\n00:00:05,500 --> 00:00:07,000\n{\\an8}Synthetic line two.\n\n'
                        '3\n00:05:00,000 --> 00:05:02,000\nOutside window.\n'
                    ).encode('utf-8')
                else:
                    srt = (
                        f'1\n00:00:02,000 --> 00:00:04,000\nExplicit stream {idx} line.\n\n'
                    ).encode('utf-8')
                return self._send(200, srt, ct='text/plain; charset=utf-8')
            return self._send(404, b'{}')
    return Handler


class LibraryWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text())
        # Keep the shared wire fixture intact; authorization uses M3 host uids.
        for key, frame in cls.fixture.items():
            if key.endswith('_frame'):
                frame['caller']['subject'] = '1' * 64

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
                'subjects': ['1' * 64, '2' * 64],
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

    def test_library_subjects_are_independent_of_writer(self):
        frame = copy.deepcopy(self.fixture['library_lookup_frame'])
        for subject in self.policy['library']['subjects']:
            frame['caller']['subject'] = subject
            sock.authorize(sock.parse_request(json.dumps(frame).encode()), self.policy)
        for subject in (self.policy['caller']['subject'], '3' * 64):
            frame['caller']['subject'] = subject
            with self.assertRaises(sock.WireError):
                sock.authorize(sock.parse_request(json.dumps(frame).encode()), self.policy)
        for subjects in ([], ['A' * 64], ['1' * 63], ['1' * 64] * 2,
                         [str(i) * 64 for i in range(5)]):
            policy = copy.deepcopy(self.policy)
            policy['library']['subjects'] = subjects
            with self.assertRaises(sock.Refused):
                sock.validate_policy(policy)

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
            {'downloadId': TORRENT_HASH_LOW, 'movieId': 18, 'eventType': 'downloadFolderImported'},
            {'downloadId': TORRENT_HASH_CAT, 'movieId': 18, 'eventType': 'downloadFolderImported'},
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

    def test_r1_1_multi_episode_file_expands_unmonitor_and_keep_check(self):
        # Episode 103 shares episodeFile id 601 with episode 101.
        self.state.extra_episodes = [{
            'id': 103, 'seriesId': 26, 'seasonNumber': 1, 'episodeNumber': 3,
            'title': 'Ep Three (shared file)', 'monitored': True, 'hasFile': True,
            'episodeFileId': 601,
            'episodeFile': copy.deepcopy(self.state.episodes[0]['episodeFile']),
        }]
        self.state.emby_episodes_override = [
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
            {
                'Id': '55013', 'Type': 'Episode', 'Name': 'Ep Three',
                'ParentIndexNumber': 1, 'IndexNumber': 3,
                'Path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]/Season 01/S01E01.mkv',
            },
        ]
        # Keep check must catch sibling episode 103 (Emby ID 55013) even though only 101 was requested.
        kept = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        kept['request'] = {
            'mode': 'preview', 'library': 'anime', 'arr_id': 26,
            'target': {'episode_arr_ids': [101]}, 'remove_torrents': False,
            'keep_emby_ids': ['55013'],
        }
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(kept)
        self.assertEqual(ctx.exception.reason, 'keep_listed')

        # Preview expands affected_episode_arr_ids to [101, 103] and apply unmonitors both.
        prev_frame = copy.deepcopy(kept)
        prev_frame['request']['keep_emby_ids'] = []
        preview = self._run(prev_frame)
        self.assertEqual(preview['plan']['affected_episode_arr_ids'], [101, 103])
        apply_frame = copy.deepcopy(prev_frame)
        apply_frame['request'].update(mode='apply', operation_id='a' * 32, plan_sha256=preview['plan_sha256'])
        res = self._run(apply_frame)
        self.assertEqual(self.state.unmonitored_episodes, [{'episodeIds': [101, 103], 'monitored': False}])
        self.assertEqual(res['unmonitored_episode_count'], 2)

    def test_r1_2_cross_arr_torrent_exclusivity(self):
        # 1. Torrent shared by two movies -> kept with shared_torrent.
        self.state.radarr_history_by_dl = {
            TORRENT_HASH_OK: [
                {'downloadId': TORRENT_HASH_OK, 'movieId': 18, 'eventType': 'downloadFolderImported'},
                {'downloadId': TORRENT_HASH_OK, 'movieId': 19, 'eventType': 'downloadFolderImported'},
            ],
        }
        res = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(res['plan']['torrents'][0]['action'], 'keep')
        self.assertEqual(res['plan']['torrents'][0]['reason'], 'shared_torrent')

        # 2. Torrent shared by two series -> kept with shared_torrent even when deleting whole series 26.
        self.state.radarr_history_by_dl = {}
        self.state.sonarr_history_by_dl = {
            TORRENT_HASH_PACK: [
                {'downloadId': TORRENT_HASH_PACK, 'seriesId': 26, 'episodeId': 101, 'eventType': 'downloadFolderImported'},
                {'downloadId': TORRENT_HASH_PACK, 'seriesId': 30, 'episodeId': 201, 'eventType': 'downloadFolderImported'},
            ],
        }
        s_frame = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        s_frame['request'] = {
            'mode': 'preview', 'library': 'anime', 'arr_id': 26,
            'target': 'title', 'remove_torrents': True, 'keep_emby_ids': [],
        }
        s_res = self._run(s_frame)
        self.assertEqual(s_res['plan']['torrents'][0]['action'], 'keep')
        self.assertEqual(s_res['plan']['torrents'][0]['reason'], 'shared_torrent')

        # 3. Truncated history -> kept with ownership_incomplete.
        self.state.radarr_history_by_dl = None
        self.state.sonarr_history_by_dl = None
        self.state.history_truncated = True
        trunc_res = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(trunc_res['plan']['torrents'][0]['action'], 'keep')
        self.assertEqual(trunc_res['plan']['torrents'][0]['reason'], 'ownership_incomplete')
        self.state.history_truncated = False

        # 4. Sonarr history query failure while removing a movie -> kept with ownership_unverified.
        self.state.sonarr_history_fail = True
        fail_res = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(fail_res['plan']['torrents'][0]['action'], 'keep')
        self.assertEqual(fail_res['plan']['torrents'][0]['reason'], 'ownership_unverified')
        self.state.sonarr_history_fail = False

    def test_r1_3_and_p1_4_emby_unresolved_fail_closed_and_movie_path_mapping(self):
        # Unresolvable movie with non-empty keep_emby_ids refuses emby_unresolved.
        self.state.emby_movies_override = []
        kept_movie = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        kept_movie['request']['keep_emby_ids'] = ['99999']
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(kept_movie)
        self.assertEqual(ctx.exception.reason, 'emby_unresolved')

        # Unresolvable episode with file refuses emby_unresolved.
        self.state.emby_movies_override = None
        self.state.emby_episodes_override = []
        kept_ep = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        kept_ep['request'] = {
            'mode': 'preview', 'library': 'anime', 'arr_id': 26,
            'target': {'episode_arr_ids': [101]}, 'remove_torrents': False,
            'keep_emby_ids': ['99999'],
        }
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(kept_ep)
        self.assertEqual(ctx.exception.reason, 'emby_unresolved')
        self.state.emby_episodes_override = None

        # P1-4: Radarr path under /movies while Emby path is /mnt/shared/media/movies.
        # 1) Single Movie provider-id match accepted in detail and keep check.
        self.policy['library']['libraries']['movies']['root_folder_path'] = '/movies'
        self.state.movies[0]['path'] = '/movies/Synthetic Movie (2024)'
        self.state.movies[0]['movieFile']['path'] = '/movies/Synthetic Movie (2024)/Synthetic.mkv'
        detail = self._run(self.fixture['library_detail_frame'])
        self.assertEqual(detail['file']['emby_item_id'], '44801')

        # 2) Provider-id query returns empty -> falls back to path mapping via root_folder_path.
        self.state.emby_movies_override = lambda prov: [] if prov else [{
            'Id': '44801', 'Type': 'Movie', 'Name': 'Synthetic Movie',
            'Path': '/mnt/shared/media/movies/Synthetic Movie (2024)/Synthetic.mkv',
            'ProviderIds': {},
        }]
        kept_mapped = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        kept_mapped['request']['keep_emby_ids'] = ['44801']
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(kept_mapped)
        self.assertEqual(ctx.exception.reason, 'keep_listed')

    def test_r1_4_post_dispatch_partial_and_uncertain_errors(self):
        # Partial failure: unmonitor succeeds, then episode file delete fails -> code='partial' with completed list.
        ep_frame = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        ep_frame['request'] = {
            'mode': 'preview', 'library': 'anime', 'arr_id': 26,
            'target': {'episode_arr_ids': [101]}, 'remove_torrents': False,
            'keep_emby_ids': [],
        }
        preview = self._run(ep_frame)
        ep_apply = copy.deepcopy(ep_frame)
        ep_apply['request'].update(mode='apply', operation_id='c' * 32, plan_sha256=preview['plan_sha256'])
        self.state.fail_episode_file_delete = True
        with self.assertRaises(lib.LibraryPostDispatchError) as ctx:
            self._run(ep_apply)
        self.assertEqual(ctx.exception.code, 'partial')
        self.assertEqual(ctx.exception.completed, ['episodes_unmonitored:1'])
        env = sock.envelope(error=sock.WireError(ctx.exception.code, ctx.exception.message, completed=ctx.exception.completed))
        self.assertEqual(env['error']['code'], 'partial')
        self.assertEqual(env['error']['completed'], ['episodes_unmonitored:1'])
        self.state.fail_episode_file_delete = False

        # Partial failure: movie delete succeeds, then qBit delete fails -> code='partial', completed=['title_deleted'].
        self.state.fail_qbit_delete = True
        with self.assertRaises(lib.LibraryPostDispatchError) as ctx:
            self._run(self.fixture['library_remove_apply_frame'])
        self.assertEqual(ctx.exception.code, 'partial')
        self.assertEqual(ctx.exception.completed, ['title_deleted'])
        self.state.fail_qbit_delete = False

        # Uncertain failure: movie delete itself fails -> code='uncertain', completed=[].
        self.state.fail_movie_delete = True
        with self.assertRaises(lib.LibraryPostDispatchError) as ctx:
            self._run(self.fixture['library_remove_apply_frame'])
        self.assertEqual(ctx.exception.code, 'uncertain')
        self.assertEqual(ctx.exception.completed, [])
        self.state.fail_movie_delete = False

    def test_r1_7_releases_by_episode_verifies_series_id(self):
        self.state.extra_episodes = [{
            'id': 201, 'seriesId': 30, 'seasonNumber': 1, 'episodeNumber': 1,
            'title': 'Other Series Ep', 'monitored': True, 'hasFile': False,
        }]
        bad_rel = copy.deepcopy(self.fixture['library_releases_frame'])
        bad_rel['request'] = {'library': 'anime', 'arr_id': 26, 'episode_arr_id': 201}
        self.state.release_calls.clear()
        with self.assertRaises(lib.Refused):
            self._run(bad_rel)
        self.assertEqual(self.state.release_calls, [])

    def test_p1_7_and_p1_8_movie_min_availability_and_no_full_list_fetches(self):
        # P1-7: movie add includes minimum_availability in plan and minimumAvailability in payload.
        add_prev = copy.deepcopy(self.fixture['library_add_preview_frame'])
        add_prev['request'] = {
            'mode': 'preview', 'library': 'movies', 'tmdb_id': 888001,
            'monitor': 'all', 'search_now': False,
        }
        self.state.full_list_calls.clear()
        prev = self._run(add_prev)
        self.assertEqual(prev['plan']['minimum_availability'], 'released')
        add_apply = copy.deepcopy(add_prev)
        add_apply['request'].update(mode='apply', operation_id='d' * 32, plan_sha256=prev['plan_sha256'])
        self._run(add_apply)
        self.assertEqual(self.state.added_movies[0]['minimumAvailability'], 'released')
        self.assertEqual(self.state.full_list_calls, [])

        # P1-8: lookup with term, lookup with arr_id, and queue avoid full list calls; queue reports truncated.
        self._run(self.fixture['library_lookup_frame'])
        by_id = copy.deepcopy(self.fixture['library_lookup_frame'])
        by_id['request'] = {'library': 'movies', 'arr_id': 18}
        by_id_res = self._run(by_id)
        self.assertEqual(len(by_id_res['items']), 1)
        self.assertEqual(by_id_res['items'][0]['arr_id'], 18)

        self.state.queue_total_records = 150
        q_res = self._run(self.fixture['library_queue_frame'])
        self.assertTrue(q_res['truncated'])
        self.assertEqual(self.state.full_list_calls, [])

    def test_r2_r1_3_whole_series_title_removal_checks_all_episodes(self):
        title_frame = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        title_frame['request'] = {
            'mode': 'preview', 'library': 'anime', 'arr_id': 26,
            'target': 'title', 'remove_torrents': False,
            'keep_emby_ids': ['55012'],
        }
        # 1. Protected episode present in Emby listing -> refuses keep_listed.
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(title_frame)
        self.assertEqual(ctx.exception.reason, 'keep_listed')

        # 2. Protected episode omitted from Emby episode listing -> refuses emby_unresolved.
        self.state.emby_episodes_override = [
            {
                'Id': '55011', 'Type': 'Episode', 'Name': 'Ep One',
                'ParentIndexNumber': 1, 'IndexNumber': 1,
                'Path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]/Season 01/S01E01.mkv',
            },
        ]
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(title_frame)
        self.assertEqual(ctx.exception.reason, 'emby_unresolved')

        # 3. Incomplete Emby paging (TotalRecordCount > returned items) -> refuses emby_unresolved.
        self.state.emby_episodes_override = None
        self.state.emby_episodes_total_override = 5
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(title_frame)
        self.assertEqual(ctx.exception.reason, 'emby_unresolved')
        self.state.emby_episodes_total_override = None

        # 4. Duplicate Emby episode match -> refuses emby_unresolved.
        self.state.emby_episodes_override = [
            {
                'Id': '55011', 'Type': 'Episode', 'Name': 'Ep One',
                'ParentIndexNumber': 1, 'IndexNumber': 1,
                'Path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]/Season 01/S01E01.mkv',
            },
            {
                'Id': '55099', 'Type': 'Episode', 'Name': 'Ep One Dup',
                'ParentIndexNumber': 1, 'IndexNumber': 1,
                'Path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]/Season 01/S01E01.mkv',
            },
            {
                'Id': '55012', 'Type': 'Episode', 'Name': 'Ep Two',
                'ParentIndexNumber': 1, 'IndexNumber': 2,
                'Path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]/Season 01/S01E02.mkv',
            },
        ]
        title_frame['request']['keep_emby_ids'] = ['99999']
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(title_frame)
        self.assertEqual(ctx.exception.reason, 'emby_unresolved')
        self.state.emby_episodes_override = None

    def test_r2_r1_2_torrent_exclusivity_completeness_and_positive_ownership(self):
        # 1. Missing totalRecords -> kept with ownership_incomplete.
        self.state.history_missing_total = True
        res = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(res['plan']['torrents'][0]['action'], 'keep')
        self.assertEqual(res['plan']['torrents'][0]['reason'], 'ownership_incomplete')
        self.state.history_missing_total = False

        # 2. Empty histories in both Arrs -> kept with ownership_unverified.
        self.state.radarr_history_by_dl = {}
        self.state.sonarr_history_by_dl = {}
        res = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(res['plan']['torrents'][0]['action'], 'keep')
        self.assertEqual(res['plan']['torrents'][0]['reason'], 'ownership_unverified')

        # 3. Grabbed-only history (no import record) -> kept with ownership_unverified.
        self.state.radarr_history_by_dl = {
            TORRENT_HASH_OK: [
                {'id': 1, 'downloadId': TORRENT_HASH_OK, 'movieId': 18, 'eventType': 'grabbed'},
            ],
        }
        res = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(res['plan']['torrents'][0]['action'], 'keep')
        self.assertEqual(res['plan']['torrents'][0]['reason'], 'ownership_unverified')

        # 4. Multi-page history: 3 pages of 1 record each; exclusive import -> removed.
        self.state.history_page_size = 1
        self.state.radarr_history_by_dl = {
            TORRENT_HASH_OK: [
                {'id': 1, 'downloadId': TORRENT_HASH_OK, 'movieId': 18, 'eventType': 'grabbed'},
                {'id': 2, 'downloadId': TORRENT_HASH_OK, 'movieId': 18, 'eventType': 'downloadFolderImported'},
                {'id': 3, 'downloadId': TORRENT_HASH_OK, 'movieId': 18, 'eventType': 'movieFileDeleted'},
            ],
        }
        res = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(res['plan']['torrents'][0]['action'], 'remove')
        self.assertEqual(res['plan']['torrents'][0]['reason'], 'meets_threshold')

        # 5. Multi-page history where a later page references another movie -> shared_torrent.
        self.state.radarr_history_by_dl[TORRENT_HASH_OK].append(
            {'id': 4, 'downloadId': TORRENT_HASH_OK, 'movieId': 19, 'eventType': 'downloadFolderImported'},
        )
        res = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(res['plan']['torrents'][0]['action'], 'keep')
        self.assertEqual(res['plan']['torrents'][0]['reason'], 'shared_torrent')
        self.state.history_page_size = 200
        self.state.radarr_history_by_dl = None
        self.state.sonarr_history_by_dl = None

    def test_r2_1_episodefile_fallback_metadata_and_file_limit(self):
        # Episode 104 has episodeFileId=605 without embedded episodeFile metadata.
        self.state.extra_episodes = [{
            'id': 104, 'seriesId': 26, 'seasonNumber': 1, 'episodeNumber': 4,
            'title': 'Ep Four', 'monitored': True, 'hasFile': True,
            'episodeFileId': 605,
        }]
        ep_frame = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        ep_frame['request'] = {
            'mode': 'preview', 'library': 'anime', 'arr_id': 26,
            'target': {'episode_arr_ids': [104]}, 'remove_torrents': False,
            'keep_emby_ids': [],
        }

        # 1. Missing episodeFile metadata (404 from /api/v3/episodefile/605) -> refuses file_metadata_missing.
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(ep_frame)
        self.assertEqual(ctx.exception.reason, 'file_metadata_missing')

        # 2. Resolved via GET /api/v3/episodefile/605 -> included in plan.files and total_bytes.
        self.state.episode_files_by_id[605] = {
            'id': 605,
            'relativePath': 'Season 01/S01E04.mkv',
            'path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]/Season 01/S01E04.mkv',
            'size': 3072,
        }
        prev = self._run(ep_frame)
        self.assertEqual(prev['plan']['files'], [{
            'id': 605,
            'path': '/mnt/shared/media/anime/Synthetic Anime (2022) [tvdb-384541]/Season 01/S01E04.mkv',
            'size': 3072,
        }])
        self.assertEqual(prev['plan']['total_bytes'], 3072)

        # 3. Resolved fallback file counts toward remove_max_files limit (ep 101 + fallback ep 104 = 2 > 1).
        limit_frame = copy.deepcopy(ep_frame)
        limit_frame['request']['target'] = {'episode_arr_ids': [101, 104]}
        orig_limit = self.policy['library']['limits']['remove_max_files']
        self.policy['library']['limits']['remove_max_files'] = 1
        try:
            with self.assertRaises(lib.LibraryRefusal) as ctx:
                self._run(limit_frame)
            self.assertEqual(ctx.exception.reason, 'file_limit_exceeded')
        finally:
            self.policy['library']['limits']['remove_max_files'] = orig_limit

        # 4. Movie with hasFile=True and missing movieFile metadata -> refuses file_metadata_missing.
        self.state.movies[0]['movieFile'] = None
        self.state.movies[0]['movieFileId'] = 505
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(ctx.exception.reason, 'file_metadata_missing')

        # 5. Movie with resolved /api/v3/moviefile/505 -> included in plan.files.
        self.state.movie_files_by_id[505] = {
            'id': 505,
            'relativePath': 'Synthetic.mkv',
            'path': '/mnt/shared/media/movies/Synthetic Movie (2024)/Synthetic.mkv',
            'size': 4096,
        }
        m_prev = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(len(m_prev['plan']['files']), 1)
        self.assertEqual(m_prev['plan']['files'][0]['id'], 505)

    def test_lookup_stats_unknown_when_detail_vanishes(self):
        # A 404 (None) or mismatched detail must not leave zeros that look valid.
        for detail in (None, {'id': 999}):
            series = {'arr_id': 26, 'size_on_disk': 0,
                      'seasons': [{'season': 1, 'monitored': True, 'episode_count': 0, 'episode_file_count': 0}]}
            lib._fill_lookup_stats(series, detail, 'series')
            self.assertIsNone(series['size_on_disk'])
            self.assertIsNone(series['seasons'][0]['episode_count'])
            self.assertIsNone(series['seasons'][0]['episode_file_count'])
            movie = {'arr_id': 216, 'size_on_disk': 0}
            lib._fill_lookup_stats(movie, detail, 'movie')
            self.assertIsNone(movie['size_on_disk'])

    def test_lookup_enriches_in_library_stats_for_movies_and_series(self):
        # Movie lookup: statistics.sizeOnDisk (Radarr) for in-library row; 0 for not-in-library row.
        self.state.movies[0]['sizeOnDisk'] = 0
        self.state.movies[0]['statistics'] = {'sizeOnDisk': 76_000_000_000}
        self.state.item_get_calls.clear()
        self.state.full_list_calls.clear()
        movie_frame = copy.deepcopy(self.fixture['library_lookup_frame'])
        movie_frame['request'] = {'library': 'movies', 'term': 'Synthetic'}
        movie_res = self._run(movie_frame)
        self.assertEqual(len(movie_res['items']), 2)
        in_lib_m, new_m = movie_res['items']
        self.assertTrue(in_lib_m['in_library'])
        self.assertEqual(in_lib_m['arr_id'], 18)
        self.assertEqual(in_lib_m['size_on_disk'], 76_000_000_000)
        self.assertFalse(new_m['in_library'])
        self.assertIsNone(new_m['arr_id'])
        self.assertEqual(new_m['size_on_disk'], 0)
        self.assertEqual(self.state.item_get_calls, [('radarr', 18)])
        self.assertEqual(self.state.full_list_calls, [])

        # Series lookup: statistics.sizeOnDisk and per-season totalEpisodeCount/episodeFileCount.
        self.state.series[0]['statistics'] = {'sizeOnDisk': 12_345_678}
        self.state.series[0]['seasons'] = [
            {'seasonNumber': 1, 'monitored': True, 'statistics': {'totalEpisodeCount': 10, 'episodeFileCount': 10}},
            {'seasonNumber': 2, 'monitored': True, 'statistics': {'totalEpisodeCount': 10, 'episodeFileCount': 10}},
        ]
        self.state.item_get_calls.clear()
        series_res = self._run(self.fixture['library_lookup_frame'])
        self.assertEqual(len(series_res['items']), 2)
        in_lib_s, new_s = series_res['items']
        self.assertTrue(in_lib_s['in_library'])
        self.assertEqual(in_lib_s['arr_id'], 26)
        self.assertEqual(in_lib_s['size_on_disk'], 12_345_678)
        self.assertEqual(in_lib_s['seasons'], [
            {'season': 1, 'monitored': True, 'episode_count': 10, 'episode_file_count': 10},
            {'season': 2, 'monitored': True, 'episode_count': 10, 'episode_file_count': 10},
        ])
        self.assertFalse(new_s['in_library'])
        self.assertIsNone(new_s['arr_id'])
        self.assertEqual(new_s['size_on_disk'], 0)
        self.assertEqual(new_s['seasons'], [
            {'season': 1, 'monitored': False, 'episode_count': 0, 'episode_file_count': 0},
        ])
        self.assertEqual(self.state.item_get_calls, [('sonarr', 26)])
        self.assertEqual(self.state.full_list_calls, [])

        # Bounded by lookup_max_rows: second in-library row beyond cap does not trigger a GET.
        self.state.series.append({
            'id': 27,
            'title': 'Synthetic Anime Two',
            'year': 2024,
            'tvdbId': 384542,
            'imdbId': 'tt12590267',
            'monitored': True,
            'path': '/mnt/shared/media/anime/Synthetic Anime Two (2024)',
            'seasons': [{'seasonNumber': 1, 'monitored': True, 'statistics': {'totalEpisodeCount': 5, 'episodeFileCount': 5}}],
            'statistics': {'sizeOnDisk': 9999},
        })
        orig_cap = self.policy['library']['limits']['lookup_max_rows']
        self.policy['library']['limits']['lookup_max_rows'] = 1
        self.state.item_get_calls.clear()
        try:
            capped = self._run(self.fixture['library_lookup_frame'])
        finally:
            self.policy['library']['limits']['lookup_max_rows'] = orig_cap
        self.assertTrue(capped['truncated'])
        self.assertEqual(len(capped['items']), 1)
        self.assertEqual(self.state.item_get_calls, [('sonarr', 26)])
        self.assertEqual(self.state.full_list_calls, [])

    def test_subtitles_english_selection_and_explicit_stream_index(self):
        # 1. English text present alongside Arabic text -> picks English text and extracts.
        self.state.subtitles_override = [
            {'Type': 'Subtitle', 'Index': 4, 'Language': 'ara', 'Codec': 'subrip',
             'DisplayTitle': 'Arabic (SUBRIP)', 'IsForced': False, 'IsExternal': False,
             'IsTextSubtitleStream': True},
            {'Type': 'Subtitle', 'Index': 2, 'Language': 'eng', 'Codec': 'subrip',
             'DisplayTitle': 'English (SUBRIP)', 'IsForced': False, 'IsExternal': False,
             'IsTextSubtitleStream': True},
        ]
        self.state.subtitle_srt_calls.clear()
        res_ok = self._run(self.fixture['library_subtitles_frame'])
        self.assertEqual(res_ok['status'], 'ok')
        self.assertEqual(res_ok['selected_stream']['index'], 2)
        self.assertEqual(len(res_ok['streams']), 2)
        self.assertEqual(res_ok['lines'], ['[00:00:01] Synthetic line one.', '[00:00:05] Synthetic line two.'])
        self.assertEqual(self.state.subtitle_srt_calls, [2])

        # 2. Only English image (PGS) + Arabic text, no stream_index -> image_subtitle, no extraction.
        self.state.subtitles_override = [
            {'Type': 'Subtitle', 'Index': 4, 'Language': 'ara', 'Codec': 'subrip',
             'DisplayTitle': 'Arabic (SUBRIP)', 'IsForced': False, 'IsExternal': False,
             'IsTextSubtitleStream': True},
            {'Type': 'Subtitle', 'Index': 3, 'Language': 'eng', 'Codec': 'pgssub',
             'DisplayTitle': 'English (PGSSUB)', 'IsForced': False, 'IsExternal': False,
             'IsTextSubtitleStream': False},
        ]
        self.state.subtitle_srt_calls.clear()
        res_img = self._run(self.fixture['library_subtitles_frame'])
        self.assertEqual(res_img['status'], 'image_subtitle')
        self.assertEqual(res_img['selected_stream']['index'], 3)
        self.assertEqual(res_img['lines'], [])
        self.assertEqual([s['index'] for s in res_img['streams']], [4, 3])
        self.assertEqual(self.state.subtitle_srt_calls, [])

        # 3. No English at all (Arabic text + French PGS), no stream_index -> no_english_text, no extraction.
        self.state.subtitles_override = [
            {'Type': 'Subtitle', 'Index': 4, 'Language': 'ara', 'Codec': 'subrip',
             'DisplayTitle': 'Arabic (SUBRIP)', 'IsForced': False, 'IsExternal': False,
             'IsTextSubtitleStream': True},
            {'Type': 'Subtitle', 'Index': 5, 'Language': 'fre', 'Codec': 'pgssub',
             'DisplayTitle': 'French (PGSSUB)', 'IsForced': False, 'IsExternal': False,
             'IsTextSubtitleStream': False},
        ]
        self.state.subtitle_srt_calls.clear()
        res_no_eng = self._run(self.fixture['library_subtitles_frame'])
        self.assertEqual(res_no_eng['status'], 'no_english_text')
        self.assertIsNone(res_no_eng['selected_stream'])
        self.assertEqual(res_no_eng['lines'], [])
        self.assertEqual([s['index'] for s in res_no_eng['streams']], [4, 5])
        self.assertEqual(self.state.subtitle_srt_calls, [])

        # 4. Explicit stream_index still extracts non-English text stream.
        explicit_frame = copy.deepcopy(self.fixture['library_subtitles_frame'])
        explicit_frame['request']['stream_index'] = 4
        self.state.subtitle_srt_calls.clear()
        res_explicit = self._run(explicit_frame)
        self.assertEqual(res_explicit['status'], 'ok')
        self.assertEqual(res_explicit['selected_stream']['index'], 4)
        self.assertEqual(res_explicit['lines'], ['[00:00:02] Explicit stream 4 line.'])
        self.assertEqual(self.state.subtitle_srt_calls, [4])

    def _make_recs_state_dir(self, name):
        base = Path(os.environ['TMPDIR']) / f'recs-{name}-{os.getpid()}'
        if base.exists():
            import shutil
            shutil.rmtree(base)
        base.mkdir(parents=True, mode=0o700)
        os.chmod(base, 0o700)
        return str(base)

    def _recs_policy(self, state_dir, *, daily_ceiling=100, cache_ttl=21600, min_refresh=3600):
        pol = copy.deepcopy(self.policy)
        pol['library']['recommendations'] = {
            'mdblist_key_file': '/etc/holonet/mdblist.key',
            'state_dir': state_dir,
            'lists': [
                {'slug': 'top-movies', 'kind': 'movie'},
                {'slug': 'top-anime-movies', 'kind': 'anime'},
                {'slug': 'top-tv', 'kind': 'series'},
                {'slug': 'top-anime', 'kind': 'anime'},
            ],
            'daily_ceiling': daily_ceiling,
            'cache_ttl_seconds': cache_ttl,
            'min_refresh_age_seconds': min_refresh,
        }
        return pol

    def _fake_mdblist(self, *, status_429_at=None, remaining_start=999):
        calls = []
        user_lists = [
            {'id': 101, 'name': 'Top Movies', 'slug': 'top-movies', 'mediatype': 'movie',
             'items': 2, 'last_updated_at': '2026-11-22T00:00:00Z'},
            {'id': 102, 'name': 'Top Anime Movies', 'slug': 'top-anime-movies', 'mediatype': 'movie',
             'items': 1, 'last_updated_at': '2026-11-22T00:00:00Z'},
            {'id': 103, 'name': 'Top TV', 'slug': 'top-tv', 'mediatype': 'show',
             'items': 1, 'last_updated_at': '2026-11-22T00:00:00Z'},
            {'id': 104, 'name': 'Top Anime', 'slug': 'top-anime', 'mediatype': 'show',
             'items': 2, 'last_updated_at': '2026-11-22T00:00:00Z'},
        ]
        long_desc = 'A' * 350
        list_payloads = {
            101: {
                'movies': [
                    {
                        'id': 555001, 'mediatype': 'movie', 'imdb_id': 'tt5550001',
                        'ids': {'mdblist': 'm1', 'imdb': 'tt5550001', 'tmdb': 555001, 'tvdb': None},
                        'title': 'Synthetic Recommended Movie', 'language': 'en', 'country': 'us',
                        'release_year': 2025, 'status': 'released', 'runtime': 118,
                        'description': long_desc, 'genre': ['Sci-Fi', 'Thriller'],
                        'ratings': [
                            {'source': 'mdblist', 'score': 88},
                            {'source': 'imdb', 'value': 8.2, 'votes': 125000},
                            {'source': 'tmdb', 'value': 8.0},
                        ],
                    },
                    {
                        # Already in Radarr (tmdbId 603 from FakeState.movies)
                        'id': 603, 'mediatype': 'movie', 'imdb_id': 'tt0133093',
                        'ids': {'mdblist': 'm2', 'imdb': 'tt0133093', 'tmdb': 603, 'tvdb': None},
                        'title': 'Synthetic Movie', 'language': 'en', 'country': 'us',
                        'release_year': 2024, 'status': 'released', 'runtime': 136,
                        'description': 'Already in Radarr.', 'genre': ['Action'],
                        'ratings': [{'source': 'mdblist', 'score': 92}, {'source': 'imdb', 'value': 8.7, 'votes': 900000}],
                    },
                ],
                'shows': [],
                'pagination': {'page': 1},
            },
            102: {
                'movies': [
                    {
                        'id': 555003, 'mediatype': 'movie', 'imdb_id': 'tt5550003',
                        'ids': {'mdblist': 'm3', 'imdb': 'tt5550003', 'tmdb': 555003, 'tvdb': None},
                        'title': 'Synthetic Anime Film', 'language': 'ja', 'country': 'jp',
                        'release_year': 2025, 'status': 'released', 'runtime': 105,
                        'description': 'Anime film in Radarr movies library.',
                        'genre': [{'title': 'Animation'}, {'title': 'Fantasy'}],
                        'ratings': [{'source': 'mdblist', 'score': 86}, {'source': 'imdb', 'value': 8.1, 'votes': 30000}],
                    },
                ],
                'shows': [],
            },
            103: {
                'movies': [],
                'shows': [
                    {
                        'id': 555004, 'mediatype': 'show', 'imdb_id': 'tt5550004', 'tvdb_id': 888004,
                        'ids': {'mdblist': 's1', 'imdb': 'tt5550004', 'tmdb': 555004, 'tvdb': 888004},
                        'title': 'Synthetic Drama', 'language': 'en', 'country': 'gb',
                        'release_year': 2025, 'status': 'continuing', 'runtime': 50,
                        'description': 'Excluded TV drama.', 'genre': ['Drama'],
                        'ratings': [{'source': 'mdblist', 'score': 80}, {'source': 'imdb', 'value': 7.9, 'votes': 50000}],
                    },
                ],
            },
            104: {
                'movies': [],
                'shows': [
                    {
                        'id': 555002, 'mediatype': 'show', 'imdb_id': 'tt5550002', 'tvdb_id': 888002,
                        'ids': {'mdblist': 's2', 'imdb': 'tt5550002', 'tmdb': 555002, 'tvdb': 888002},
                        'title': 'Synthetic Recommended Anime', 'language': 'ja', 'country': 'jp',
                        'release_year': 2025, 'status': 'continuing', 'runtime': 24,
                        'description': 'Top anime series.', 'genre': ['Animation', 'Action'],
                        'ratings': [{'source': 'mdblist', 'score': 85}, {'source': 'imdb', 'value': 8.4, 'votes': 42000}],
                    },
                    {
                        # Already in Sonarr (tvdbId 384541)
                        'id': 555099, 'mediatype': 'show', 'imdb_id': 'tt12590266', 'tvdb_id': 384541,
                        'ids': {'mdblist': 's3', 'imdb': 'tt12590266', 'tmdb': 555099, 'tvdb': 384541},
                        'title': 'Synthetic Anime', 'language': 'ja', 'country': 'jp',
                        'release_year': 2022, 'status': 'continuing', 'runtime': 24,
                        'description': 'Already in Sonarr.', 'genre': ['Animation'],
                        'ratings': [{'source': 'mdblist', 'score': 90}, {'source': 'imdb', 'value': 8.5, 'votes': 60000}],
                    },
                ],
            },
        }

        class FakeClient:
            def close(self):
                pass

            def get_json(self, route, params=None):
                calls.append((route, dict(params or {})))
                rem = remaining_start - len(calls)
                if status_429_at is not None and len(calls) >= status_429_at:
                    return 429, None, 0
                if route == '/lists/user':
                    return 200, copy.deepcopy(user_lists), rem
                if route.startswith('/lists/') and route.endswith('/items'):
                    lid = int(route.split('/')[2])
                    return 200, copy.deepcopy(list_payloads[lid]), rem
                raise AssertionError(f'unexpected mdblist route: {route}')

        return FakeClient(), calls

    def test_recommendations_validation_and_disabled(self):
        # Request validation.
        self.assertEqual(lib.request('library-recommendations', {}), {})
        self.assertEqual(
            lib.request('library-recommendations', {
                'kind': 'anime', 'source': 'mdblist', 'limit': 10,
                'include_in_library': True, 'min_score': 75, 'refresh': True,
            }),
            {
                'kind': 'anime', 'source': 'mdblist', 'limit': 10,
                'include_in_library': True, 'min_score': 75, 'refresh': True,
            },
        )
        for bad in (
            {'kind': 'music'},
            {'source': 'trakt'},
            {'limit': 0},
            {'limit': 51},
            {'limit': True},
            {'include_in_library': 'yes'},
            {'min_score': -1},
            {'min_score': 101},
            {'min_score': 75.5},
            {'refresh': 1},
            {'unknown': 1},
        ):
            with self.subTest(bad=bad), self.assertRaises(lib.Refused):
                lib.request('library-recommendations', bad)

        # Disabled when policy omits library.recommendations.
        frame = copy.deepcopy(self.fixture['library_recommendations_frame'])
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(frame)
        self.assertEqual(ctx.exception.reason, 'recommendations_disabled')

    def test_recommendations_normalization_radarr_membership_and_warnings(self):
        state_dir = self._make_recs_state_dir('norm')
        pol = self._recs_policy(state_dir)
        self.state.radarr_exclusions = [{'tmdbId': 555003}]
        self.state.sonarr_exclusions = [{'tvdbId': 888004}]
        self.state.radarr_importlists = [
            {'id': 1, 'name': 'Radarr Auto', 'enabled': True, 'enableAuto': True},
            {'id': 2, 'name': 'Radarr Off', 'enabled': False, 'enableAuto': True},
        ]
        self.state.sonarr_importlists = [
            {'id': 1, 'name': 'Sonarr Auto', 'enableAutomaticAdd': True},
            {'id': 2, 'name': 'Sonarr Off', 'enableAutomaticAdd': False},
        ]
        self.state.radarr_recommendations = [
            # Merges with mdblist Top Movies item 555001
            {
                'title': 'Synthetic Recommended Movie', 'year': 2025, 'tmdbId': 555001, 'imdbId': 'tt5550001',
                'originalLanguage': {'id': 1, 'name': 'English'}, 'genres': ['Sci-Fi'],
                'overview': 'From Radarr.', 'status': 'released', 'runtime': 118,
                'isRecommendation': True, 'isExisting': False, 'isExcluded': False,
            },
            # Radarr-only Japanese Animation -> kind='anime', add={'library': 'movies', 'tmdb_id': 777001}
            {
                'title': 'Radarr Anime Film', 'year': 2025, 'tmdbId': 777001, 'imdbId': 'tt7770001',
                'originalLanguage': {'id': 8, 'name': 'Japanese'}, 'genres': ['Animation', 'Drama'],
                'overview': 'Japanese animated film.', 'status': 'released', 'runtime': 99,
                'ratings': {'imdb': {'value': 7.8, 'votes': 12000}, 'tmdb': {'value': 7.9}},
                'isRecommendation': True, 'isExisting': False, 'isExcluded': False,
            },
            # Non-recommendation trending item must be ignored
            {
                'title': 'Not A Rec', 'year': 2025, 'tmdbId': 777999,
                'isRecommendation': False, 'isExisting': False, 'isExcluded': False,
            },
        ]
        fake_client, mdb_calls = self._fake_mdblist()

        def run_with(req, *, now_ts=1795262400.0):
            frame = copy.deepcopy(self.fixture['library_recommendations_frame'])
            frame['request'] = req
            with patch.object(lib.time, 'time', return_value=now_ts), \
                 patch.object(lib, 'MdblistClient', return_value=fake_client), \
                 patch.object(lib, 'read_mdblist_key', return_value='synthetic-key-12345678'):
                orig = self.policy
                self.policy = pol
                try:
                    return self._run(frame)
                finally:
                    self.policy = orig

        # 1. Cold fetch: 1 (/lists/user) + 4 lists = 5 calls.
        res = run_with({}, now_ts=1795262400.0)
        self.assertEqual(len(mdb_calls), 5)
        self.assertEqual(res['quota'], {'tool_calls_today': 5, 'daily_ceiling': 100, 'mdblist_remaining': 994})
        self.assertFalse(res['cache']['stale'])
        self.assertEqual(res['cache']['age_seconds'], 0)
        self.assertEqual(len(res['lists']), 4)

        # In-library items (603 and 384541) filtered out by default; trending item ignored.
        titles = [i['title'] for i in res['items']]
        self.assertEqual(titles, [
            'Synthetic Recommended Movie',  # mdblist 88
            'Synthetic Anime Film',         # mdblist 86
            'Synthetic Recommended Anime',  # mdblist 85
            'Synthetic Drama',              # mdblist 80
            'Radarr Anime Film',            # mdblist None
        ])
        by_title = {i['title']: i for i in res['items']}
        # Description truncated to <= 300 chars.
        self.assertLessEqual(len(by_title['Synthetic Recommended Movie']['description']), 300)
        self.assertTrue(by_title['Synthetic Recommended Movie']['description'].endswith('...'))
        # Merged sources & add payload.
        self.assertEqual(by_title['Synthetic Recommended Movie']['sources'], ['Top Movies', 'radarr-recommendation'])
        self.assertEqual(by_title['Synthetic Recommended Movie']['add'], {'library': 'movies', 'tmdb_id': 555001})
        # Anime movie -> kind='anime' but add.library='movies' with tmdb_id, and Radarr exclusion marked.
        self.assertEqual(by_title['Synthetic Anime Film']['kind'], 'anime')
        self.assertEqual(by_title['Synthetic Anime Film']['add'], {'library': 'movies', 'tmdb_id': 555003})
        self.assertTrue(by_title['Synthetic Anime Film']['excluded'])
        # Anime series -> kind='anime', add.library='anime' with tvdb_id.
        self.assertEqual(by_title['Synthetic Recommended Anime']['add'], {'library': 'anime', 'tvdb_id': 888002})
        # TV series -> kind='series', add.library='tv' with tvdb_id, Sonarr exclusion marked.
        self.assertEqual(by_title['Synthetic Drama']['add'], {'library': 'tv', 'tvdb_id': 888004})
        self.assertTrue(by_title['Synthetic Drama']['excluded'])
        # Radarr anime detection.
        self.assertEqual(by_title['Radarr Anime Film']['kind'], 'anime')
        self.assertEqual(by_title['Radarr Anime Film']['add'], {'library': 'movies', 'tmdb_id': 777001})
        # Auto-add warnings for both Radarr and Sonarr (only enabled auto lists).
        self.assertEqual(len(res['warnings']), 2)
        self.assertTrue(any('Radarr Auto' in w for w in res['warnings']))
        self.assertTrue(any('Sonarr Auto' in w for w in res['warnings']))

        # 2. include_in_library=True shows 603 and 384541 from cache with 0 extra mdblist calls.
        res_all = run_with({'include_in_library': True}, now_ts=1795262500.0)
        self.assertEqual(len(mdb_calls), 5)
        self.assertEqual(res_all['cache']['age_seconds'], 100)
        in_lib_titles = {i['title'] for i in res_all['items'] if i['in_library']}
        self.assertEqual(in_lib_titles, {'Synthetic Movie', 'Synthetic Anime'})

        # 3. refresh=True under 1 hour does NOT bypass cache.
        run_with({'refresh': True}, now_ts=1795262400.0 + 1800)
        self.assertEqual(len(mdb_calls), 5)

        # 4. refresh=True after 1 hour DOES refresh cache (+5 calls = 10).
        res_ref = run_with({'refresh': True}, now_ts=1795262400.0 + 3601)
        self.assertEqual(len(mdb_calls), 10)
        self.assertEqual(res_ref['quota']['tool_calls_today'], 10)
        self.assertEqual(res_ref['cache']['age_seconds'], 0)

        # 5. Cache expires after 6 hours -> automatic refresh (+5 calls = 15).
        res_exp = run_with({}, now_ts=1795262400.0 + 3601 + 21600)
        self.assertEqual(len(mdb_calls), 15)
        self.assertEqual(res_exp['quota']['tool_calls_today'], 15)

    def test_recommendations_quota_ceiling_and_429(self):
        # 1. Quota exhausted with no cache -> refuses quota_exhausted.
        state_dir_empty = self._make_recs_state_dir('quota-empty')
        pol_low = self._recs_policy(state_dir_empty, daily_ceiling=2)
        fake_client, _ = self._fake_mdblist()
        frame = copy.deepcopy(self.fixture['library_recommendations_frame'])
        frame['request'] = {}
        with patch.object(lib.time, 'time', return_value=1795262400.0), \
             patch.object(lib, 'MdblistClient', return_value=fake_client), \
             patch.object(lib, 'read_mdblist_key', return_value='synthetic-key-12345678'):
            orig = self.policy
            self.policy = pol_low
            try:
                with self.assertRaises(lib.LibraryRefusal) as ctx:
                    self._run(frame)
                self.assertEqual(ctx.exception.reason, 'quota_exhausted')
            finally:
                self.policy = orig

        # 2. Seed cache with ceiling=5, then after TTL expires -> serves stale cache with warning.
        state_dir_stale = self._make_recs_state_dir('quota-stale')
        pol_five = self._recs_policy(state_dir_stale, daily_ceiling=5)
        fake_ok, calls_ok = self._fake_mdblist()
        with patch.object(lib.time, 'time', return_value=1795262400.0), \
             patch.object(lib, 'MdblistClient', return_value=fake_ok), \
             patch.object(lib, 'read_mdblist_key', return_value='synthetic-key-12345678'):
            orig = self.policy
            self.policy = pol_five
            try:
                first = self._run(frame)
                self.assertFalse(first['cache']['stale'])
                self.assertEqual(len(calls_ok), 5)
            finally:
                self.policy = orig

        with patch.object(lib.time, 'time', return_value=1795262400.0 + 25000), \
             patch.object(lib, 'MdblistClient', return_value=fake_ok), \
             patch.object(lib, 'read_mdblist_key', return_value='synthetic-key-12345678'):
            orig = self.policy
            self.policy = pol_five
            try:
                stale_res = self._run(frame)
                self.assertTrue(stale_res['cache']['stale'])
                self.assertEqual(len(calls_ok), 5)
                self.assertTrue(any('quota ceiling' in w for w in stale_res['warnings']))
            finally:
                self.policy = orig

        # 3. HTTP 429 with existing cache -> serves stale cache with warning.
        pol_high = self._recs_policy(state_dir_stale, daily_ceiling=100)
        fake_429, _ = self._fake_mdblist(status_429_at=1)
        with patch.object(lib.time, 'time', return_value=1795262400.0 + 25000), \
             patch.object(lib, 'MdblistClient', return_value=fake_429), \
             patch.object(lib, 'read_mdblist_key', return_value='synthetic-key-12345678'):
            orig = self.policy
            self.policy = pol_high
            try:
                res_429 = self._run(frame)
                self.assertTrue(res_429['cache']['stale'])
                self.assertTrue(any('HTTP 429' in w for w in res_429['warnings']))
            finally:
                self.policy = orig

        # 4. HTTP 429 with no cache -> refuses quota_exhausted.
        state_dir_429 = self._make_recs_state_dir('429-empty')
        pol_429 = self._recs_policy(state_dir_429, daily_ceiling=100)
        fake_429_empty, _ = self._fake_mdblist(status_429_at=1)
        with patch.object(lib.time, 'time', return_value=1795262400.0), \
             patch.object(lib, 'MdblistClient', return_value=fake_429_empty), \
             patch.object(lib, 'read_mdblist_key', return_value='synthetic-key-12345678'):
            orig = self.policy
            self.policy = pol_429
            try:
                with self.assertRaises(lib.LibraryRefusal) as ctx:
                    self._run(frame)
                self.assertEqual(ctx.exception.reason, 'quota_exhausted')
            finally:
                self.policy = orig

    def test_read_mdblist_key_custody(self):
        if os.geteuid() != 0:
            self.skipTest('root-only custody check runs as uid 0 in Chaldea/CI')
        key_dir = Path(os.environ['TMPDIR']) / f'mdblist-key-{os.getpid()}'
        key_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(key_dir, 0o750)
        key_file = key_dir / 'mdblist.key'
        key_file.write_text('synthetic_key_value_1234', encoding='utf-8')
        os.chmod(key_file, 0o400)
        self.assertEqual(lib.read_mdblist_key(str(key_file)), 'synthetic_key_value_1234')

        # World-readable key must be refused.
        os.chmod(key_file, 0o644)
        with self.assertRaises(lib.Refused):
            lib.read_mdblist_key(str(key_file))
        os.chmod(key_file, 0o400)

        # Symlink must be refused.
        link = key_dir / 'mdblist.link'
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(key_file)
        with self.assertRaises(lib.Refused):
            lib.read_mdblist_key(str(link))


if __name__ == '__main__':
    unittest.main()
