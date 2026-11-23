"""Synthetic HoloNet library management v1 worker tests with fake HTTP backends.

No production credentials, live network calls, or writes outside scratch HOME.
"""
import contextlib
import copy
import hashlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import threading
import unittest
from unittest.mock import patch
import urllib.parse

import emby_operator_adapter as adapter
import emby_operator_library as lib
import emby_operator_library_prune as prune
import emby_operator_socket as sock
import emby_operator_torrents as tor

_contract_spec = importlib.util.spec_from_file_location(
    'library_packaging_contract', Path(__file__).resolve().parents[1] / 'filter_plugins/web_mcp.py')
assert _contract_spec is not None and _contract_spec.loader is not None
contract = importlib.util.module_from_spec(_contract_spec)
_contract_spec.loader.exec_module(contract)

FIXTURE = Path(__file__).resolve().parents[1] / 'docs/holonet-library-wire-v1.json'
FIXTURE_SHA256 = '651bce5b5fcaefe367d9e23bf7b38034877aa4f7fba4c5d8650c76838af8528a'

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
        self.radarr_queue_override = None
        self.sonarr_queue_override = None
        self.radarr_manualimport = {}
        self.sonarr_manualimport = {
            'a' * 40: [{
                'path': '/mnt/shared/downloads/complete/tv/Synthetic.Anime.S01/Synthetic.Anime.S01E01.1080p.DualAudio.mkv',
                'relativePath': 'Synthetic.Anime.S01E01.1080p.DualAudio.mkv',
                'size': 2048,
                'quality': {'quality': {'id': 7, 'name': 'Bluray-1080p'}, 'revision': {'version': 1, 'real': 0}},
                'languages': [{'id': 1, 'name': 'English'}, {'id': 8, 'name': 'Japanese'}],
                'releaseGroup': 'DualGroup',
                'downloadId': 'a' * 40,
                'series': {'id': 26, 'title': 'Synthetic Anime'},
                'seasonNumber': 1,
                'episodes': [{'id': 101, 'seriesId': 26, 'seasonNumber': 1, 'episodeNumber': 1, 'title': 'Ep One'}],
                'rejections': [{'reason': 'Existing file meets cutoff'}],
            }],
        }
        self.manualimport_queries = []
        self.commands_submitted = []
        self.command_poll_statuses = []
        self.fail_command_post = False
        self.torrent_files = {}


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
            if u.path == '/api/v3/queue' and 'downloadId' in urllib.parse.parse_qs(u.query):
                dl = urllib.parse.parse_qs(u.query)['downloadId'][0]
                override = state.radarr_queue_override
                recs = override if override is not None else [{
                    'downloadId': dl, 'status': 'completed', 'trackedDownloadState': 'importBlocked',
                }]
                return self._send(200, {'records': recs, 'totalRecords': len(recs)})
            if u.path == '/api/v3/queue':
                recs = state.radarr_queue_override if state.radarr_queue_override is not None else []
                total = state.queue_total_records if state.queue_total_records is not None else len(recs)
                return self._send(200, {'records': recs, 'totalRecords': total})
            if u.path == '/api/v3/manualimport':
                state.manualimport_queries.append(('radarr', u.query))
                dl = urllib.parse.parse_qs(u.query).get('downloadId', [''])[0]
                return self._send(200, state.radarr_manualimport.get(dl, []))
            if u.path.startswith('/api/v3/command/'):
                status = state.command_poll_statuses.pop(0) if state.command_poll_statuses else 'completed'
                return self._send(200, {'id': 701, 'status': status})
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
                if state.radarr_recommendations is None:
                    return self._send(500, {'message': 'upstream metadata failure'})
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
            if self.path == '/api/v3/command':
                if state.fail_command_post:
                    return self._send(500, {'error': 'command failed'})
                state.commands_submitted.append(('radarr', body))
                return self._send(201, {'id': 701, 'status': 'queued'})
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
            if u.path == '/api/v3/queue' and 'downloadId' in urllib.parse.parse_qs(u.query):
                dl = urllib.parse.parse_qs(u.query)['downloadId'][0]
                override = state.sonarr_queue_override
                recs = override if override is not None else [{
                    'downloadId': dl, 'status': 'completed', 'trackedDownloadState': 'importBlocked',
                }]
                return self._send(200, {'records': recs, 'totalRecords': len(recs)})
            if u.path == '/api/v3/queue':
                if state.sonarr_queue_override is not None:
                    recs = state.sonarr_queue_override
                else:
                    recs = [{
                        'title': 'Synthetic Anime S01 1080p', 'seriesId': 26, 'episodeId': 101,
                        'downloadId': 'a' * 40, 'seasonNumber': 1,
                        'series': state.series[0],
                        'status': 'downloading', 'trackedDownloadState': 'downloading',
                        'size': 2048, 'sizeleft': 1024, 'timeleft': '00:05:00',
                        'downloadClient': 'qBittorrent', 'statusMessages': [],
                    }]
                if state.sonarr_queue_override is None:
                    recs.append(dict(recs[0], episodeId=102))
                total = state.queue_total_records if state.queue_total_records is not None else len(recs)
                return self._send(200, {'records': recs, 'totalRecords': total})
            if u.path == '/api/v3/manualimport':
                state.manualimport_queries.append(('sonarr', u.query))
                dl = urllib.parse.parse_qs(u.query).get('downloadId', [''])[0]
                return self._send(200, state.sonarr_manualimport.get(dl, []))
            if u.path.startswith('/api/v3/command/'):
                status = state.command_poll_statuses.pop(0) if state.command_poll_statuses else 'completed'
                return self._send(200, {'id': 701, 'status': status})
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
            if self.path == '/api/v3/command':
                if state.fail_command_post:
                    return self._send(500, {'error': 'command failed'})
                state.commands_submitted.append(('sonarr', body))
                return self._send(201, {'id': 701, 'status': 'queued'})
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
            if u.path == '/api/v2/torrents/files':
                h = urllib.parse.parse_qs(u.query).get('hash', [''])[0]
                if h in state.torrent_files:
                    return self._send(200, json.dumps(state.torrent_files[h]).encode('utf-8'))
                return self._send(404, b'')
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
                **contract.canonical_torrent_descriptor(library=True),
                'origin': self.qbit_url,
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

    def test_remove_digest_ignores_live_seeding_counters(self):
        # A seeding torrent's ratio and seeding time advance between preview and
        # apply; the plan stays the same plan, so apply must not refuse.
        torrent = self.state.torrents[TORRENT_HASH_OK]
        torrent['content_path'] = '/mnt/shared/downloads/complete/movies/Synthetic.Movie.2024'
        preview = self._run(self.fixture['library_remove_preview_frame'])
        apply_frame = copy.deepcopy(self.fixture['library_remove_apply_frame'])
        apply_frame['request']['plan_sha256'] = preview['plan_sha256']
        # The content path stays bound: a moved payload is a different plan.
        torrent['content_path'] = '/mnt/shared/downloads/complete/movies/Other'
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(apply_frame)
        self.assertEqual(ctx.exception.reason, 'plan_changed')
        torrent['content_path'] = '/mnt/shared/downloads/complete/movies/Synthetic.Movie.2024'
        # So does the decision the counters made: dropping under the threshold flips it.
        torrent.update(ratio=0.1, seeding_time=60)
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(apply_frame)
        self.assertEqual(ctx.exception.reason, 'plan_changed')
        self.assertEqual(self.state.deleted_movies, [])
        torrent.update(ratio=1.5 + 0.25, seeding_time=15 * 86400 + 3600)
        applied = self._run(apply_frame)
        self.assertEqual(applied['plan_sha256'], preview['plan_sha256'])
        self.assertEqual(applied['plan']['torrents'][0]['seeding_time_seconds'], 15 * 86400 + 3600)
        self.assertEqual(applied['removed_torrent_hashes'], [TORRENT_HASH_OK])
        # The same holds with remove_torrents false.
        keep_preview = copy.deepcopy(self.fixture['library_remove_preview_frame'])
        keep_preview['request']['remove_torrents'] = False
        preview = self._run(keep_preview)
        keep_apply = copy.deepcopy(apply_frame)
        keep_apply['request'].update(remove_torrents=False, plan_sha256=preview['plan_sha256'],
                                     operation_id='c' * 32)
        torrent.update(ratio=2.5, seeding_time=16 * 86400)
        applied = self._run(keep_apply)
        self.assertEqual(applied['kept_torrent_hashes'], [TORRENT_HASH_OK])

    def test_prune_retires_torrents_only_with_hardlink_proof(self):
        # Prune drives the real library-remove. Arr history proves exclusive
        # ownership of the fixture torrent, but the hardlink verifier never runs
        # for it, so prune must keep it seeding.
        test = self

        class RealRemove:
            def remove(self, request):
                frame = copy.deepcopy(test.fixture['library_remove_preview_frame'])
                frame['request'] = request
                return test._run(frame)

            def qbit_rows(self):
                return [seed_row('c' * 40, 25 * TIB, 30),
                        seed_row(TORRENT_HASH_OK, 4096, 30, OTHER_TRACKER)]

        calls = []

        def must_be_called(qbit, torrent, torrent_hash, **kwargs):
            calls.append(torrent_hash)
            kwargs['deleted_inodes_cache'][torrent_hash] = [('/synthetic', 1, (1, 1))]
            return True, None

        title = {'library': 'movies', 'arr_id': 18}
        with patch.object(lib, '_verify_torrent_hardlinks', side_effect=must_be_called), \
             patch.object(lib, '_hardlink_after_media_removal', return_value=True):
            prepared = prune._prepare_removal(RealRemove(), title, {})
            self.assertEqual(prepared['plan']['torrents'][0]['ownership_proof'], 'arr_history')
            self.assertEqual((prepared['with_torrents'], prepared['torrent_gate']),
                             (False, {'ownership_not_hardlink': 1}))
            applied = prune._apply_removal(RealRemove(), prepared)
            self.assertEqual(calls, [])
            self.assertEqual(self.state.deleted_torrents, [])
            self.assertEqual(applied['kept_torrent_hashes'], [TORRENT_HASH_OK])
            self.assertEqual(len(self.state.deleted_movies), 1)
            # Without Arr history the verifier runs; only its pass lets the torrent go.
            self.state.radarr_history_by_dl = {}
            self.state.sonarr_history_by_dl = {}
            prepared = prune._prepare_removal(RealRemove(), title, {})
            self.assertEqual(calls, [TORRENT_HASH_OK])
            self.assertEqual(prepared['plan']['torrents'][0]['ownership_proof'], 'hardlink')
            self.assertEqual((prepared['with_torrents'], prepared['torrent_gate']), (True, {}))
            prune._apply_removal(RealRemove(), prepared)
        self.assertEqual(self.state.deleted_torrents, [{'hashes': [TORRENT_HASH_OK], 'deleteFiles': ['true']}])

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

        # 4. Sonarr history query failure while removing a movie -> hardlink fallback fails closed on unmapped save_path.
        self.state.sonarr_history_fail = True
        fail_res = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(fail_res['plan']['torrents'][0]['action'], 'keep')
        self.assertEqual(fail_res['plan']['torrents'][0]['reason'], 'hardlink_path_unmapped')
        self.assertEqual(fail_res['plan']['torrents'][0]['ownership_proof'], 'none')
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

        # 2. Empty histories in both Arrs -> hardlink fallback fails closed when save_path is unmapped.
        self.state.radarr_history_by_dl = {}
        self.state.sonarr_history_by_dl = {}
        res = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(res['plan']['torrents'][0]['action'], 'keep')
        self.assertEqual(res['plan']['torrents'][0]['reason'], 'hardlink_path_unmapped')
        self.assertEqual(res['plan']['torrents'][0]['ownership_proof'], 'none')

        # 3. Grabbed-only history (no import record) -> hardlink fallback fails closed when save_path is unmapped.
        self.state.radarr_history_by_dl = {
            TORRENT_HASH_OK: [
                {'id': 1, 'downloadId': TORRENT_HASH_OK, 'movieId': 18, 'eventType': 'grabbed'},
            ],
        }
        res = self._run(self.fixture['library_remove_preview_frame'])
        self.assertEqual(res['plan']['torrents'][0]['action'], 'keep')
        self.assertEqual(res['plan']['torrents'][0]['reason'], 'hardlink_path_unmapped')
        self.assertEqual(res['plan']['torrents'][0]['ownership_proof'], 'none')

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
        if os.geteuid() != 0:
            self.skipTest('root-only recommendations state runs in Chaldea/CI')
        home = Path(os.environ['HOME'])
        self.assertEqual(Path(os.environ['TMPDIR']), home / 'tmp')
        # Strict custody tests need a protected scratch HOME, including every ancestor.
        for parent in [home, *home.parents]:
            info = parent.lstat()
            self.assertEqual(info.st_uid, 0)
            self.assertEqual(info.st_mode & 0o022, 0)
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

        for key, value in (('daily_ceiling', 200), ('cache_ttl_seconds', 300),
                           ('min_refresh_age_seconds', 60)):
            pol = self._recs_policy('/var/lib/synthetic/recs')
            pol['library']['recommendations'][key] = value
            with self.subTest(key=key), self.assertRaises(lib.Refused):
                lib.validate_block(pol['library'])

        # Disabled when policy omits library.recommendations.
        frame = copy.deepcopy(self.fixture['library_recommendations_frame'])
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(frame)
        self.assertEqual(ctx.exception.reason, 'recommendations_disabled')

    def test_recommendations_anime_merge_and_cache_warning_validation(self):
        existing = {
            'kind': 'series', 'mediatype': 'show', 'sources': ['Top TV'],
            'ids': {'tmdb': None, 'tvdb': 888002, 'imdb': None}, 'scores': {},
        }
        incoming = copy.deepcopy(existing)
        incoming.update(kind='anime', sources=['Top Anime'])
        lib._merge_recommendation(existing, incoming)
        self.assertEqual(existing['kind'], 'anime')
        self.assertEqual(existing['sources'], ['Top TV', 'Top Anime'])
        self.assertEqual(lib._recommendation_add_target(existing),
                         {'library': 'anime', 'tvdb_id': 888002})
        # A later general-TV row cannot downgrade an anime classification.
        incoming.update(kind='series')
        lib._merge_recommendation(existing, incoming)
        self.assertEqual(existing['kind'], 'anime')

        cache = {'fetched_at_ts': 1, 'fetched_at': 'synthetic',
                 'items': [], 'lists': [], 'warnings': ['Synthetic warning']}
        with patch.object(lib, '_read_state_json', return_value=cache):
            self.assertIs(lib._load_cache(0), cache)
            for invalid in (None, 'warning', {}, [{}], [1], ['x' * 513],
                            ['warning'] * 65):
                cache['warnings'] = invalid
                self.assertIsNone(lib._load_cache(0))
            del cache['warnings']
            self.assertIsNone(lib._load_cache(0))
            cache['warnings'] = []
            self.assertIs(lib._load_cache(0), cache)

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
        res = run_with({'include_excluded': True}, now_ts=1795262400.0)
        self.assertEqual(len(mdb_calls), 5)
        self.assertEqual(res['quota'], {'mdblist_calls_today': 5, 'daily_ceiling': 100, 'mdblist_remaining': 994})
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

        default_res = run_with({})
        self.assertFalse(any(i['excluded'] for i in default_res['items']))
        scored = run_with({'source': 'radarr', 'min_score': 75})
        self.assertEqual(scored['items'][0]['score'], 78)
        self.assertEqual(scored['items'][0]['language'], 'ja')
        # 2. include_in_library=True shows 603 and 384541 from cache with 0 extra mdblist calls.
        res_all = run_with({'include_in_library': True}, now_ts=1795262500.0)
        self.assertEqual(len(mdb_calls), 5)
        self.assertEqual(res_all['cache']['age_seconds'], 100)
        in_lib_titles = {i['title'] for i in res_all['items'] if i['in_library']}
        self.assertEqual(in_lib_titles, {'Synthetic Movie', 'Synthetic Anime'})

        # 3. refresh=True under 1 hour does NOT bypass cache.
        ignored = run_with({'refresh': True}, now_ts=1795262400.0 + 1800)
        self.assertTrue(any('refresh ignored' in w for w in ignored['warnings']))
        self.assertEqual(len(mdb_calls), 5)

        # 4. refresh=True after 1 hour DOES refresh cache (+5 calls = 10).
        res_ref = run_with({'refresh': True}, now_ts=1795262400.0 + 3601)
        self.assertEqual(len(mdb_calls), 10)
        self.assertEqual(res_ref['quota']['mdblist_calls_today'], 10)
        self.assertEqual(res_ref['cache']['age_seconds'], 0)

        # 5. Cache expires after 6 hours -> automatic refresh (+5 calls = 15).
        res_exp = run_with({}, now_ts=1795262400.0 + 3601 + 21600)
        self.assertEqual(len(mdb_calls), 15)
        self.assertEqual(res_exp['quota']['mdblist_calls_today'], 15)

        # 6. A failing Radarr recommendations endpoint keeps the mdblist results.
        self.state.radarr_recommendations = None
        degraded = run_with({}, now_ts=1795262400.0 + 3601 + 21600 + 10)
        self.assertTrue(any('Radarr recommendations are unavailable' in w for w in degraded['warnings']))
        degraded_titles = [i['title'] for i in degraded['items']]
        self.assertIn('Synthetic Recommended Movie', degraded_titles)
        self.assertNotIn('Radarr Anime Film', degraded_titles)
        self.assertNotIn('radarr-recommendation',
                         [s for i in degraded['items'] for s in i['sources']])

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


    def test_recommendations_failed_attempts_are_durable_and_stale(self):
        state_dir = self._make_recs_state_dir('failed')
        cfg = self._recs_policy(state_dir)['library']
        frame = copy.deepcopy(self.fixture['library_recommendations_frame'])
        frame['request'] = {'source': 'mdblist'}
        arr = lambda backend, desc, budget: type('Arr', (), {'get': lambda self, path: []})()
        ok, _ = self._fake_mdblist()
        def run(client, now=1795262400.0):
            with patch.object(lib.time, 'time', return_value=now):
                return lib.op_recommendations(frame, cfg, arr, mdblist_factory=lambda _: client)
        # Assert the durable reservation is present while the request is in flight.
        class Broken:
            def close(self):
                pass
            def get_json(inner, route, params=None):
                count = json.loads((Path(state_dir) / 'quota.json').read_text())['calls']
                self.assertEqual(count, expected[0])
                if route == '/lists/user' and fail_route[0] != route:
                    return 200, [{'id': 101, 'slug': 'top-movies'}], 42
                raise OSError('synthetic TLS/timeout failure')
        expected, fail_route = [1], ['/lists/user']
        with self.assertRaises(lib.Refused):
            run(Broken())
        self.assertEqual(json.loads((Path(state_dir) / 'quota.json').read_text())['calls'], 1)
        # Seed complete cache, then fail both list discovery and item fetch.
        run(ok)
        for route, count in (('/lists/user', 7), ('/lists/101/items', 9)):
            fail_route[0], expected[0] = route, count if route == '/lists/user' else count - 1
            # Discovery reserves one call, then the item request reserves another.
            broken = Broken()
            if route != '/lists/user':
                original = broken.get_json
                def get_json(route, params=None):
                    if route != '/lists/user':
                        expected[0] = 9
                    return original(route, params)
                broken.get_json = get_json
            result = run(broken, 1795262400.0 + 25000)
            self.assertTrue(result['cache']['stale'])
            self.assertTrue(any('request failed' in w for w in result['warnings']))
            self.assertEqual(json.loads((Path(state_dir) / 'quota.json').read_text())['calls'], count)

    def test_recommendations_cached_warnings_and_item_mediatype(self):
        state_dir = self._make_recs_state_dir('missing-slug')
        block = self._recs_policy(state_dir)['library']
        frame = {'request': {'source': 'mdblist'}}
        arr = lambda backend, desc, budget: type('Arr', (), {'get': lambda self, path: []})()
        class Empty:
            def get_json(self, *args, **kwargs):
                return 200, [], 99
            def close(self):
                pass
        first = lib.op_recommendations(frame, block, arr, mdblist_factory=lambda _: Empty())
        cached = lib.op_recommendations(frame, block, arr)
        self.assertEqual(first['warnings'], cached['warnings'])
        self.assertTrue(any('slug not found' in w for w in cached['warnings']))
        for configured, media, kind, library in (
            ('movie', 'show', 'series', 'tv'),
            ('series', 'movie', 'movie', 'movies'),
            ('anime', 'movie', 'anime', 'movies'),
        ):
            item = lib._normalize_mdblist_item(
                {'title': 'Mismatch', 'mediatype': media, 'ids': {'tmdb': 1, 'tvdb': 2}},
                configured, 'movie', 'List')
            self.assertEqual(item['kind'], kind)
            self.assertEqual(lib._recommendation_add_target(item)['library'], library)
        self.assertEqual(lib._language_code('English'), 'en')
        self.assertEqual(lib._language_code('fr'), 'fr')
        self.assertIsNone(lib._language_code('unknown'))

    def test_mdblist_real_client_transport_contract(self):
        import io
        import urllib.error
        from email.message import Message
        key = 'synthetic-key-for-transport'
        seen = []
        class Response(io.BytesIO):
            status = 200
            headers = Message()
            headers['X-RateLimit-Remaining'] = ' 17 '
        class Opener:
            payload = b'[]'
            code = 200
            def open(self, req, timeout):
                seen.append(req.full_url)
                self_case.assertEqual(req.get_header('User-agent'), lib.MDBLIST_USER_AGENT)
                self_case.assertFalse(lib.MDBLIST_USER_AGENT.lower().startswith('python'))
                parsed = urllib.parse.urlsplit(req.full_url)
                self_case.assertEqual((parsed.scheme, parsed.netloc), ('https', 'api.mdblist.com'))
                if self.code != 200:
                    raise urllib.error.HTTPError(req.full_url, self.code, req.full_url, {}, None)
                return Response(self.payload)
        self_case, opener = self, Opener()
        def build(*handlers):
            self.assertTrue(any(isinstance(h, lib.NoRedirect) for h in handlers))
            self.assertIsNone(next(h for h in handlers if isinstance(h, lib.NoRedirect))
                              .redirect_request(None, None, 302, '', {}, 'https://evil.invalid'))
            return opener
        with patch.object(lib.urllib.request, 'build_opener', side_effect=build):
            client = lib.MdblistClient(key, request_guard=lambda: 60)
        self.assertEqual(client.get_json('/lists/user'), (200, [], 17))
        for origin in ('http://api.mdblist.com', 'https://evil.invalid'):
            with patch.object(lib, 'MDBLIST_ORIGIN', origin), self.assertRaises(lib.Refused):
                client.get_json('/lists/user')
        self.assertEqual(len(seen), 1)
        opener.payload = b'x' * (4 * 1024 * 1024 + 1)
        with self.assertRaises(lib.Refused):
            client.get_json('/lists/user')
        for code in (302, 500):
            opener.code = code
            with self.assertRaises(lib.Refused) as caught:
                client.get_json('/lists/user')
            self.assertNotIn(key, str(caught.exception))
            self.assertNotIn('https://', str(caught.exception))
        client.close()

    def test_recommendations_lock_respects_remaining_budget(self):
        block = self._recs_policy(self._make_recs_state_dir('locked'))['library']
        with patch.object(lib.fcntl, 'flock', side_effect=BlockingIOError), \
             self.assertRaisesRegex(lib.Refused, 'deadline'):
            lib.op_recommendations({'request': {}}, block, None, request_guard=lambda: 0)

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

    def test_queue_dedupe_sonarr_season_pack_and_bounds(self):
        dl_pack = 'A' * 40
        dl_single = 'B' * 40
        self.state.sonarr_queue_override = [
            {
                'title': 'Synthetic.Anime.S01.1080p.DualAudio',
                'seriesId': 26, 'episodeId': 102, 'seasonNumber': 1,
                'series': self.state.series[0],
                'downloadId': dl_pack,
                'status': 'completed', 'trackedDownloadState': 'importBlocked',
                'size': 4096, 'sizeleft': 0, 'timeleft': None,
                'downloadClient': 'qBittorrent',
                'statusMessages': [{'title': 'S01E02', 'messages': ['Existing file meets cutoff']}],
            },
            {
                'title': 'Synthetic.Anime.S01.1080p.DualAudio',
                'seriesId': 26, 'episodeId': 101,
                'episode': {'seasonNumber': 1},
                'series': self.state.series[0],
                'downloadId': dl_pack.lower(),
                'status': 'completed', 'trackedDownloadState': 'importBlocked',
                'size': 4096, 'sizeleft': 0, 'timeleft': '00:00:00',
                'downloadClient': 'qBittorrent',
                'statusMessages': [
                    {'title': 'S01E02', 'messages': ['Existing file meets cutoff']},
                    {'title': 'S01E01', 'messages': ['Not an upgrade']},
                ],
            },
            {
                'title': 'Synthetic.Anime.S02E01.1080p',
                'seriesId': 26, 'episodeId': 201, 'seasonNumber': 2,
                'series': self.state.series[0],
                'downloadId': dl_single,
                'status': 'downloading', 'trackedDownloadState': 'downloading',
                'size': 1024, 'sizeleft': 512, 'timeleft': '00:02:00',
                'downloadClient': 'qBittorrent', 'statusMessages': [],
            },
        ]
        self.state.radarr_queue_override = [{
            'title': 'Synthetic.Movie.2024.1080p', 'movieId': 18,
            'downloadId': 'C' * 40,
            'status': 'downloading', 'trackedDownloadState': 'downloading',
            'size': 8192, 'sizeleft': 2048, 'timeleft': '00:10:00',
            'downloadClient': 'qBittorrent', 'statusMessages': [],
        }]

        all_frame = copy.deepcopy(self.fixture['library_queue_frame'])
        all_frame['request'] = {}
        res = self._run(all_frame)
        self.assertFalse(res['truncated'])
        self.assertEqual(len(res['items']), 3)
        # Radarr item unchanged (with download_id).
        self.assertEqual(res['items'][0]['library'], 'movies')
        self.assertEqual(res['items'][0]['arr_id'], 18)
        self.assertEqual(res['items'][0]['download_id'], 'C' * 40)
        # Sonarr season pack collapsed into one entry.
        pack = res['items'][1]
        self.assertEqual(pack['library'], 'anime')
        self.assertEqual(pack['series_title'], 'Synthetic Anime')
        self.assertEqual(pack['download_id'], dl_pack)
        self.assertEqual(pack['seasons'], [1])
        self.assertEqual(pack['episode_count'], 2)
        self.assertEqual(pack['episode_ids'], [101, 102])
        self.assertEqual(pack['time_left'], '00:00:00')
        self.assertEqual(pack['status_messages'], ['S01E02: Existing file meets cutoff', 'S01E01: Not an upgrade'])
        # Separate download stays separate.
        self.assertEqual(res['items'][2]['download_id'], dl_single)
        self.assertEqual(res['items'][2]['seasons'], [2])
        self.assertEqual(res['items'][2]['episode_count'], 1)

        # Bounding: >100 episode IDs truncates list to 100 and sets truncated=True.
        big_dl = 'D' * 40
        self.state.sonarr_queue_override = [
            {
                'title': 'Huge Pack', 'seriesId': 26, 'episodeId': 1000 + i, 'seasonNumber': 1,
                'series': self.state.series[0], 'downloadId': big_dl,
                'status': 'downloading', 'trackedDownloadState': 'downloading',
                'size': 10000, 'sizeleft': 5000, 'timeleft': '01:00:00',
                'downloadClient': 'qBittorrent',
                'statusMessages': [{'title': f'M{i}', 'messages': [f'msg {i}']}],
            }
            for i in range(105)
        ]
        res_big = self._run(self.fixture['library_queue_frame'])
        self.assertTrue(res_big['truncated'])
        self.assertEqual(len(res_big['items']), 1)
        self.assertEqual(res_big['items'][0]['episode_count'], 105)
        self.assertEqual(len(res_big['items'][0]['episode_ids']), 100)
        self.assertEqual(len(res_big['items'][0]['status_messages']), 20)

    def test_import_preview_apply_and_refusals(self):
        # 1. Exact fixture round-trip (Sonarr anime preview & apply).
        prev = self._run(self.fixture['library_import_preview_frame'])
        self.assertEqual(prev, self.fixture['library_import_preview']['result'])
        self.assertIn(('sonarr', f"downloadId={'a' * 40}&filterExistingFiles=false"), self.state.manualimport_queries)
        for _, qs in self.state.manualimport_queries:
            self.assertNotIn('seriesId=', qs)
            self.assertNotIn('folder=', qs)

        applied = self._run(self.fixture['library_import_apply_frame'])
        self.assertEqual(applied, self.fixture['library_import_apply']['result'])
        self.assertEqual(len(self.state.commands_submitted), 1)
        backend, cmd = self.state.commands_submitted[0]
        self.assertEqual(backend, 'sonarr')
        self.assertEqual(cmd['name'], 'ManualImport')
        self.assertEqual(cmd['importMode'], 'auto')
        self.assertEqual(cmd['files'][0]['seriesId'], 26)
        self.assertEqual(cmd['files'][0]['episodeIds'], [101])

        # 2. Optional wait=True polls /api/v3/command/<id> until completed.
        self.state.command_poll_statuses = ['started', 'completed']
        wait_frame = copy.deepcopy(self.fixture['library_import_apply_frame'])
        wait_frame['request']['wait'] = True
        with patch.object(lib.time, 'sleep', return_value=None):
            waited = self._run(wait_frame)
        self.assertEqual(waited['command_status'], 'completed')

        # 3. Radarr movie preview & apply.
        dl_movie = 'b' * 40
        self.state.radarr_manualimport[dl_movie] = [{
            'path': '/mnt/shared/downloads/complete/movies/Synthetic.Movie.2024.Dual.mkv',
            'relativePath': 'Synthetic.Movie.2024.Dual.mkv',
            'size': 8192,
            'quality': {'quality': {'id': 7, 'name': 'Bluray-1080p'}},
            'languages': [{'id': 1, 'name': 'English'}, {'id': 8, 'name': 'Japanese'}],
            'releaseGroup': 'DualGrp',
            'downloadId': dl_movie,
            'movie': {'id': 18, 'title': 'Synthetic Movie'},
            'rejections': [{'reason': 'Existing file is higher quality'}],
        }]
        m_prev_frame = copy.deepcopy(self.fixture['library_import_preview_frame'])
        m_prev_frame['request'] = {
            'mode': 'preview', 'library': 'movies', 'arr_id': 18, 'download_id': dl_movie,
        }
        m_prev = self._run(m_prev_frame)
        self.assertEqual(m_prev['plan']['files'][0]['movie_id'], 18)
        self.assertEqual(m_prev['plan']['files'][0]['replaces']['id'], 501)
        self.assertIn(('radarr', f'downloadId={dl_movie}'), self.state.manualimport_queries)
        m_apply_frame = copy.deepcopy(m_prev_frame)
        m_apply_frame['request'].update({
            'mode': 'apply', 'operation_id': 'e' * 32, 'plan_sha256': m_prev['plan_sha256'],
        })
        m_apply = self._run(m_apply_frame)
        self.assertTrue(m_apply['applied'])
        self.assertEqual(self.state.commands_submitted[-1][0], 'radarr')
        self.assertEqual(self.state.commands_submitted[-1][1]['files'][0]['movieId'], 18)

        # 4. Refusal: target_mismatch when any candidate maps to a different arr_id.
        bad_dl = 'c' * 40
        self.state.sonarr_manualimport[bad_dl] = [
            self.state.sonarr_manualimport['a' * 40][0],
            dict(self.state.sonarr_manualimport['a' * 40][0], series={'id': 30, 'title': 'Synthetic TV'}),
        ]
        bad_frame = copy.deepcopy(self.fixture['library_import_preview_frame'])
        bad_frame['request'] = {
            'mode': 'preview', 'library': 'anime', 'arr_id': 26, 'download_id': bad_dl,
        }
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(bad_frame)
        self.assertEqual(ctx.exception.reason, 'target_mismatch')

        # 5. Refusal: recycle_bin_unset.
        orig_recycle = self.state.sonarr_recycle
        self.state.sonarr_recycle = ''
        try:
            with self.assertRaises(lib.LibraryRefusal) as ctx:
                self._run(self.fixture['library_import_preview_frame'])
            self.assertEqual(ctx.exception.reason, 'recycle_bin_unset')
        finally:
            self.state.sonarr_recycle = orig_recycle

        # 6. Refusal: no_import_candidates.
        empty_frame = copy.deepcopy(self.fixture['library_import_preview_frame'])
        empty_frame['request'] = {
            'mode': 'preview', 'library': 'anime', 'arr_id': 26, 'download_id': '0' * 40,
        }
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(empty_frame)
        self.assertEqual(ctx.exception.reason, 'no_import_candidates')

        # 7. Refusal: plan_changed on digest mismatch.
        wrong_sha = copy.deepcopy(self.fixture['library_import_apply_frame'])
        wrong_sha['request']['plan_sha256'] = '0' * 64
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(wrong_sha)
        self.assertEqual(ctx.exception.reason, 'plan_changed')

        # Changes to command metadata must invalidate the preview, even if names match.
        candidate = self.state.sonarr_manualimport['a' * 40][0]
        original_quality_id = candidate['quality']['quality']['id']
        candidate['quality']['quality']['id'] = 99
        try:
            with self.assertRaises(lib.LibraryRefusal) as ctx:
                self._run(self.fixture['library_import_apply_frame'])
            self.assertEqual(ctx.exception.reason, 'plan_changed')
        finally:
            candidate['quality']['quality']['id'] = original_quality_id

        original_episode_id = candidate['episodes'][0]['id']
        candidate['episodes'][0]['id'] = 999999
        try:
            with self.assertRaises(lib.LibraryRefusal) as ctx:
                self._run(self.fixture['library_import_preview_frame'])
            self.assertEqual(ctx.exception.reason, 'target_mismatch')
        finally:
            candidate['episodes'][0]['id'] = original_episode_id

        # 8. Post-dispatch failure raises LibraryPostDispatchError('uncertain').
        self.state.fail_command_post = True
        try:
            with self.assertRaises(lib.LibraryPostDispatchError) as ctx:
                self._run(self.fixture['library_import_apply_frame'])
            self.assertEqual(ctx.exception.code, 'uncertain')
        finally:
            self.state.fail_command_post = False

    def test_real_arr_upgrade_rejections(self):
        # Rendered messages from Sonarr v4.0.20.3014 and Radarr v6.4.4.10685.
        reasons = [
            "Existing file meets cutoff: Bluray-1080p",
            "Existing file on disk is of equal or higher preference: Bluray-1080p v1",
            "Existing file on disk is of equal or higher revision: v2",
            "Existing file on disk meets quality cutoff: Bluray-1080p",
            "Existing file on disk meets Custom Format cutoff: 100",
            "Existing file on disk has a equal or higher Custom Format score: 100",
            "Existing file on disk has Custom Format score within Custom Format score increment: 10",
            "Existing file on disk and Quality Profile 'HD' does not allow upgrades",
            "Existing file and the Quality profile does not allow upgrades",
            "Not an upgrade for existing episode file(s). Existing quality: Bluray-1080p. New Quality HDTV-720p.",
            "Not an upgrade for existing movie file. Existing quality: Bluray-1080p. New Quality HDTV-720p.",
            "Not a quality revision upgrade for existing episode file(s)",
            "Not a Custom Format upgrade for existing episode file(s). New: [Dual Audio] (50) do not improve on Existing: [Dual Audio, HEVC] (100)",
            "Not a quality revision upgrade for existing movie file(s)",
            "Not a Custom Format upgrade for existing movie file(s). New: [Dual Audio] (50) do not improve on Existing: [Dual Audio, HEVC] (100)",
        ]
        candidate = self.state.sonarr_manualimport['a' * 40][0]
        for reason in reasons:
            with self.subTest(reason=reason):
                candidate['rejections'] = [{'reason': reason}]
                self._run(self.fixture['library_import_preview_frame'])
                self.assertFalse(lib._import_rejections_allowed([reason, 'Sample']))
                self.assertFalse(lib._import_rejections_allowed([reason + '; Sample']))
        for reason in ('Custom Formats [] have score -10 below Series profile minimum 0',
                       "Custom Formats [] have score -10 below Movie's profile minimum 0"):
            self.assertFalse(lib._import_rejections_allowed([reason]))

    def test_import_candidate_gates_and_completion(self):
        frame = copy.deepcopy(self.fixture['library_import_preview_frame'])
        candidate = self.state.sonarr_manualimport['a' * 40][0]
        for reason in ('Sample', 'Unable to parse file', 'Unknown', 'Not an upgrade; Sample',
                       'Quality is not an upgrade; Sample', 'Not an upgrade for existing sample file'):
            with self.subTest(reason=reason):
                candidate['rejections'] = [{'reason': reason}]
                with self.assertRaises(lib.LibraryRefusal) as ctx:
                    self._run(frame)
                self.assertEqual(ctx.exception.reason, 'import_candidate_rejected')
        candidate['rejections'] = []
        duplicate = copy.deepcopy(candidate)
        duplicate['path'] += '.duplicate.mkv'
        self.state.sonarr_manualimport['a' * 40].append(duplicate)
        with patch.object(lib, 'LibraryTorrentClient', wraps=lib.LibraryTorrentClient) as factory:
            with self.assertRaises(lib.LibraryRefusal) as ctx:
                self._run(frame)
            self.assertEqual(ctx.exception.reason, 'ambiguous_import_candidates')
            factory.assert_called_once()
        self.state.sonarr_manualimport['a' * 40].pop()
        original_path = candidate['path']
        candidate['path'] = self.state.series[0]['path'] + '/existing.mkv'
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(frame)
        self.assertEqual(ctx.exception.reason, 'import_path_in_library')
        candidate['path'] = original_path

        preview = self._run(frame)
        self.state.sonarr_queue_override = [{
            'downloadId': 'a' * 40, 'status': 'downloading', 'trackedDownloadState': 'downloading',
        }]
        for mode in ('preview', 'apply'):
            request = copy.deepcopy(frame)
            if mode == 'apply':
                request['request'].update(mode='apply', operation_id='f' * 32,
                                          plan_sha256=preview['plan_sha256'])
            with self.assertRaises(lib.LibraryRefusal) as ctx:
                self._run(request)
            self.assertEqual(ctx.exception.reason, 'download_incomplete')
        self.assertEqual(self.state.commands_submitted, [])

        # qBittorrent can independently prove completion when Arr has no finished row.
        class CompleteTorrent:
            def login(self): pass
            def logout(self): pass
            def info_by_hashes(self, hashes):
                return [{'hash': hashes[0], 'progress': 1}]
        class Queue:
            def get(self, path): return {'records': []}
        self.assertEqual(lib._import_completion(Queue(), 'a' * 40, {'torrents': {}},
                                               lambda _: CompleteTorrent())['source'], 'qbittorrent')

        movie = copy.deepcopy(candidate)
        movie.update(movie={'id': 18}, rejections=[])
        self.state.radarr_manualimport['b' * 40] = [movie, dict(movie, path=original_path + '.extra')]
        movie_frame = copy.deepcopy(frame)
        movie_frame['request'].pop('season', None)
        movie_frame['request'].update(library='movies', arr_id=18, download_id='b' * 40)
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            self._run(movie_frame)
        self.assertEqual(ctx.exception.reason, 'ambiguous_import_candidates')

    def test_tv_and_anime_hardlink_payload_roots(self):
        class QBit:
            config = contract.canonical_torrent_descriptor(library=True)
            def files_by_hash(self, torrent_hash):
                return [{'name': 'Show/episode.mkv'}]
        def stat_file(path, depth, budget, held=None):
            if held is not None:
                held.append(999)
            return (1, 2, 2)
        from types import SimpleNamespace
        with patch.object(lib, '_walk_stat_regular', side_effect=stat_file) as walk, \
             patch.object(lib.os, 'fstat', return_value=SimpleNamespace(st_dev=1, st_ino=2, st_nlink=2)), \
             patch.object(lib.os, 'close'):
            for name, root in (('tv', '/mnt/shared/media/tv shows'),
                               ('anime', '/mnt/shared/media/anime')):
                with self.subTest(library=name):
                    evidence = {}
                    self.assertEqual(lib._verify_torrent_hardlinks(
                        QBit(), {'save_path': '/mnt/shared/downloads/complete/tv'}, 'a' * 40,
                        deleted_inodes_cache=evidence, files=[{'path': root + '/Show/episode.mkv'}],
                        lib_name=name, root_folder_path=root, stat_budget=[100]), (True, None))
                    self.assertEqual(evidence['a' * 40][0][:2],
                                     ('/mnt/shared/downloads/complete/tv/Show/episode.mkv', 5))
                    self.assertEqual(lib._verify_torrent_hardlinks(
                        QBit(), {'save_path': '/mnt/shared/downloads/complete/xxx'}, 'a' * 40,
                        deleted_inodes_cache={}, files=[{'path': root + '/Show/episode.mkv'}],
                        lib_name=name, root_folder_path=root, stat_budget=[100]),
                                     (False, 'hardlink_outside_download_root'))

    def test_remove_hardlink_ownership_proof(self):
        base = Path(os.environ['TMPDIR']) / f'holonet-hardlink-{os.getpid()}'
        if base.exists():
            import shutil
            shutil.rmtree(base)
        lib_root = base / 'media' / 'movies'
        dl_root = base / 'downloads' / 'complete' / 'movies'
        other_root = base / 'other'
        movie_dir = lib_root / 'Synthetic Movie (2024)'
        torrent_dir = dl_root / 'Synthetic.Movie.2024'
        movie_dir.mkdir(parents=True)
        torrent_dir.mkdir(parents=True)
        other_root.mkdir(parents=True)

        try:
            lib_file = movie_dir / 'Synthetic.mkv'
            tor_main = torrent_dir / 'Synthetic.mkv'
            tor_nfo = torrent_dir / 'info.nfo'
            tor_main.write_bytes(b'video-bytes')
            os.link(tor_main, lib_file)  # st_nlink == 2, same (dev, ino)
            tor_nfo.write_bytes(b'nfo-extra')  # st_nlink == 1

            # Point policy/descriptors/fake movie to our scratch roots and clear Arr history
            # so Arr history returns ownership_unverified and hardlink proof runs.
            self.policy['library']['libraries']['movies']['root_folder_path'] = str(lib_root)
            self.descriptors['/etc/holonet-playlists/qbit.json']['payload_prefixes'] = [str(dl_root)]
            self.state.movies[0]['path'] = str(movie_dir)
            self.state.movies[0]['movieFile']['path'] = str(lib_file)
            self.state.radarr_history_by_dl = {}
            self.state.sonarr_history_by_dl = {}
            self.state.torrents[TORRENT_HASH_OK]['save_path'] = str(dl_root)
            self.state.torrent_files[TORRENT_HASH_OK] = [
                {'name': 'Synthetic.Movie.2024/Synthetic.mkv'},
                {'name': 'Synthetic.Movie.2024/info.nfo'},
            ]

            # 1. Exclusive ownership proven via hardlink -> remove, proof='hardlink'.
            res = self._run(self.fixture['library_remove_preview_frame'])
            t0 = res['plan']['torrents'][0]
            self.assertEqual(t0['action'], 'remove')
            self.assertEqual(t0['reason'], 'meets_threshold')
            self.assertEqual(t0['ownership_proof'], 'hardlink')

            # Race: preview approved; another title gains a link and selected path
            # vanishes between the library and torrent stats in apply.
            apply_frame = copy.deepcopy(self.fixture['library_remove_apply_frame'])
            apply_frame['request']['plan_sha256'] = res['plan_sha256']
            walk = lib._walk_stat_regular
            raced = [False]
            third_race = other_root / 'race.mkv'
            def race_walk(path, depth, budget, held=None):
                if path == str(tor_main) and not raced[0]:
                    raced[0] = True
                    os.link(tor_main, third_race)
                    lib_file.unlink()
                return walk(path, depth, budget, held)
            with patch.object(lib, '_walk_stat_regular', side_effect=race_walk):
                with self.assertRaises(lib.LibraryRefusal) as ctx:
                    self._run(apply_frame)
            self.assertEqual(ctx.exception.reason, 'plan_changed')
            self.assertEqual(self.state.deleted_torrents, [])
            third_race.unlink()
            os.link(tor_main, lib_file)

            # A change after evaluation must also keep the torrent at dispatch.
            delete = lib.LibraryArrClient.delete
            def race_delete(client, path, *args, **kwargs):
                if path.startswith('/api/v3/movie/'):
                    os.link(tor_main, third_race)
                    lib_file.unlink()
                return delete(client, path, *args, **kwargs)
            with patch.object(lib.LibraryArrClient, 'delete', autospec=True, side_effect=race_delete):
                applied = self._run(apply_frame)
            self.assertTrue(third_race.exists())
            third_race.unlink()
            os.link(tor_main, lib_file)
            self.assertIn(TORRENT_HASH_OK, applied['kept_torrent_hashes'])
            self.assertEqual(applied['removed_torrent_hashes'], [])
            self.assertEqual(applied['plan']['torrents'][0]['reason'], 'hardlink_changed_before_delete')

            # 2. Seeding threshold still enforced after hardlink proof.
            orig_ratio = self.state.torrents[TORRENT_HASH_OK]['ratio']
            orig_seed = self.state.torrents[TORRENT_HASH_OK]['seeding_time']
            self.state.torrents[TORRENT_HASH_OK]['ratio'] = 0.1
            self.state.torrents[TORRENT_HASH_OK]['seeding_time'] = 60
            res_low = self._run(self.fixture['library_remove_preview_frame'])
            self.assertEqual(res_low['plan']['torrents'][0]['action'], 'keep')
            self.assertEqual(res_low['plan']['torrents'][0]['reason'], 'seeding_threshold_not_met')
            self.assertEqual(res_low['plan']['torrents'][0]['ownership_proof'], 'hardlink')
            self.state.torrents[TORRENT_HASH_OK]['ratio'] = orig_ratio
            self.state.torrents[TORRENT_HASH_OK]['seeding_time'] = orig_seed

            # 3. nlink > 2 (3rd hardlink elsewhere) -> kept with hardlink_extra_links.
            third = other_root / 'third.mkv'
            os.link(tor_main, third)
            res_n3 = self._run(self.fixture['library_remove_preview_frame'])
            self.assertEqual(res_n3['plan']['torrents'][0]['action'], 'keep')
            self.assertEqual(res_n3['plan']['torrents'][0]['reason'], 'hardlink_extra_links')
            self.assertEqual(res_n3['plan']['torrents'][0]['ownership_proof'], 'none')
            third.unlink()

            # 4. Non-deleted extra file hardlinked elsewhere -> hardlink_linked_elsewhere.
            nfo_other = other_root / 'other.nfo'
            os.link(tor_nfo, nfo_other)
            res_else = self._run(self.fixture['library_remove_preview_frame'])
            self.assertEqual(res_else['plan']['torrents'][0]['action'], 'keep')
            self.assertEqual(res_else['plan']['torrents'][0]['reason'], 'hardlink_linked_elsewhere')
            nfo_other.unlink()

            # 5. Symlink in torrent -> hardlink_symlink.
            sym = torrent_dir / 'sym.mkv'
            sym.symlink_to(tor_main)
            self.state.torrent_files[TORRENT_HASH_OK].append({'name': 'Synthetic.Movie.2024/sym.mkv'})
            res_sym = self._run(self.fixture['library_remove_preview_frame'])
            self.assertEqual(res_sym['plan']['torrents'][0]['action'], 'keep')
            self.assertEqual(res_sym['plan']['torrents'][0]['reason'], 'hardlink_symlink')
            self.state.torrent_files[TORRENT_HASH_OK].pop()
            sym.unlink()

            # 6. Missing torrent file -> hardlink_missing.
            self.state.torrent_files[TORRENT_HASH_OK].append({'name': 'Synthetic.Movie.2024/gone.mkv'})
            res_miss = self._run(self.fixture['library_remove_preview_frame'])
            self.assertEqual(res_miss['plan']['torrents'][0]['action'], 'keep')
            self.assertEqual(res_miss['plan']['torrents'][0]['reason'], 'hardlink_missing')
            self.state.torrent_files[TORRENT_HASH_OK].pop()

            # 7. Path outside download root -> hardlink_outside_download_root.
            self.state.torrents[TORRENT_HASH_OK]['save_path'] = str(other_root)
            res_out = self._run(self.fixture['library_remove_preview_frame'])
            self.assertEqual(res_out['plan']['torrents'][0]['action'], 'keep')
            self.assertEqual(res_out['plan']['torrents'][0]['reason'], 'hardlink_outside_download_root')
            self.state.torrents[TORRENT_HASH_OK]['save_path'] = str(dl_root)

            # 8. No torrent file matches deleted file (all nlink == 1) -> hardlink_no_match.
            self.state.torrent_files[TORRENT_HASH_OK] = [{'name': 'Synthetic.Movie.2024/info.nfo'}]
            res_nomatch = self._run(self.fixture['library_remove_preview_frame'])
            self.assertEqual(res_nomatch['plan']['torrents'][0]['action'], 'keep')
            self.assertEqual(res_nomatch['plan']['torrents'][0]['reason'], 'hardlink_no_match')
        finally:
            import shutil
            shutil.rmtree(base, ignore_errors=True)


# --- Library prune ------------------------------------------------------------

TIB = 1 << 40
GIB = 1 << 30
NOW = prune.parse_time('2026-11-22T00:00:00Z')
OLD = NOW - 400 * prune.DAY
ADMIN = 'b' * 32
VIEWER = 'c' * 32
SEEDPOOL = 'https://tracker.seedpool.org/announce'
OTHER_TRACKER = 'https://tracker.example.org/announce'


def emby_movie_id(arr_id):
    return str(40000 + arr_id)


class PruneWorld:
    """Synthetic Radarr/Sonarr/Emby/qBittorrent facts for prune tests."""

    def __init__(self):
        self.movies = []
        self.series = []
        self.episode_files = {}
        self.profiles = {
            'radarr': [{'id': 11, 'name': 'English Movies'}, {'id': 13, 'name': prune.PROTECTED_PROFILE}],
            'sonarr': [{'id': 6, 'name': 'HD - 720p/1080p'}, {'id': 7, 'name': prune.PROTECTED_PROFILE}],
        }
        self.queue = {'radarr': [], 'sonarr': []}
        self.items = {}
        self.favorites = {}
        self.played = {}
        self.resumable = {}
        self.activity = [{'Id': 1, 'Type': 'system.backupcompleted', 'Date': prune.iso(OLD)}]
        self.torrents = []
        self.arr_fail = set()
        self.emby_fail = False
        self.history_fail = False
        self.qbit_fail = False
        self.arr_calls = []
        self.remove_calls = []
        self.removed = []
        self.plan_torrents = []
        self.plan_bytes = {}
        self.remove_error = None
        self.apply_errors = {}
        self.post_dispatch_on = None
        self.on_preview = None
        self.after_apply = None
        self.seed_clock = 0

    def movie(self, arr_id, size, *, added=OLD, file_added=OLD, profile=11, title=None):
        self.movies.append({
            'id': arr_id, 'title': title or f'Movie {arr_id}', 'year': 2001, 'tmdbId': 100 + arr_id,
            'path': f'/mnt/shared/media/movies/Movie {arr_id} (2001)', 'sizeOnDisk': size,
            'added': prune.iso(added), 'hasFile': True,
            'movieFile': {'dateAdded': prune.iso(file_added)}, 'qualityProfileId': profile,
            'genres': ['Drama'],
        })
        self.items[emby_movie_id(arr_id)] = {
            'Id': emby_movie_id(arr_id), 'Type': 'Movie',
            'ProviderIds': {'Tmdb': str(100 + arr_id)},
            'Path': f'/mnt/shared/media/movies/Movie {arr_id} (2001)/movie.mkv',
        }

    def show(self, arr_id, size, *, library='tv', added=OLD, file_added=OLD, profile=6):
        root = '/mnt/shared/media/anime' if library == 'anime' else '/mnt/shared/media/tv shows'
        self.series.append({
            'id': arr_id, 'title': f'Show {arr_id}', 'year': 2010, 'tvdbId': 7000 + arr_id,
            'path': f'{root}/Show {arr_id}', 'statistics': {'sizeOnDisk': size},
            'added': prune.iso(added), 'qualityProfileId': profile, 'genres': ['Comedy'],
        })
        self.episode_files[arr_id] = [{'id': arr_id * 10, 'dateAdded': prune.iso(file_added)}]
        sid = str(70000 + arr_id)
        self.items[sid] = {'Id': sid, 'Type': 'Series', 'ProviderIds': {'Tvdb': str(7000 + arr_id)},
                           'Path': f'{root}/Show {arr_id}'}
        self.items[str(80000 + arr_id)] = {'Id': str(80000 + arr_id), 'Type': 'Episode', 'SeriesId': sid,
                                           'Path': f'{root}/Show {arr_id}/S01E01.mkv'}

    def play(self, item_id, ts, user=VIEWER, kind='playback.start'):
        self.activity.append({'Id': len(self.activity) + 1, 'Type': kind, 'ItemId': item_id,
                              'UserId': user, 'Date': prune.iso(ts)})


class FakePruneArr:
    def __init__(self, world, backend):
        self.world, self.backend = world, backend

    def get(self, route):
        self.world.arr_calls.append((self.backend, route))
        if self.backend in self.world.arr_fail:
            raise prune.Refused(lib.FAILURE)
        if route == '/api/v3/movie':
            return copy.deepcopy(self.world.movies)
        if route == '/api/v3/series':
            return copy.deepcopy(self.world.series)
        if route == '/api/v3/qualityprofile':
            return copy.deepcopy(self.world.profiles[self.backend])
        for prefix, rows in (('/api/v3/movie/', self.world.movies), ('/api/v3/series/', self.world.series)):
            if route.startswith(prefix):
                found = [r for r in rows if r['id'] == int(route[len(prefix):])]
                if not found:
                    raise prune.Refused(lib.FAILURE)
                return copy.deepcopy(found[0])
        if route.startswith('/api/v3/queue?page=1&pageSize=200&'):
            recs = self.world.queue[self.backend]
            return {'page': 1, 'pageSize': 200, 'totalRecords': len(recs), 'records': recs}
        if route.startswith('/api/v3/episodefile?seriesId='):
            return copy.deepcopy(self.world.episode_files[int(route.split('=')[1])])
        raise AssertionError(route)

    def close(self):
        pass


class FakePruneEmby:
    actor_id = ADMIN

    def __init__(self, world):
        self.world = world
        self.allowed = {'/Users'}

    def allow_read(self, path):
        assert lib.LibraryEmbyClient.PRUNE_READ_ROUTE.fullmatch(path), path
        self.allowed.add(path)

    def close(self):
        pass

    @staticmethod
    def _page(rows, query):
        start, limit = int(query.get('StartIndex', 0)), int(query.get('Limit', 500))
        return {'TotalRecordCount': len(rows), 'Items': copy.deepcopy(rows[start:start + limit])}

    def _http(self, method, path, query=None):
        assert method == 'GET' and path in self.allowed, path
        query = query or {}
        if self.world.emby_fail:
            raise prune.Refused('Operator HTTP failed; no retry')
        if path == '/Users':
            return [{'Id': ADMIN, 'Name': 'Admin'}, {'Id': VIEWER, 'Name': 'Viewer'}]
        if path == '/System/ActivityLog/Entries':
            if self.world.history_fail:
                raise prune.Refused('Operator HTTP failed; no retry')
            rows = sorted(self.world.activity, key=lambda e: e['Date'], reverse=True)
            if 'MinDate' in query:
                rows = [e for e in rows if e['Date'] >= query['MinDate']]
            return self._page(rows, query)
        user = path.split('/')[2]
        if 'Ids' in query:
            rows = [self.world.items[i] for i in query['Ids'].split(',') if i in self.world.items]
        else:
            source = {'IsFavorite': self.world.favorites, 'IsPlayed': self.world.played,
                      'IsResumable': self.world.resumable}[query['Filters']]
            rows = [self.world.items[i] for i in source.get(user, [])]
        return self._page(rows, query)


class FakePruneBackends:
    def __init__(self, world):
        self.world = world

    def arr(self, backend):
        return FakePruneArr(self.world, backend)

    def emby(self):
        return FakePruneEmby(self.world)

    def _tick(self):
        # Live seeding counters advance on every read, as on a seeding client.
        self.world.seed_clock += 1
        for row in self.world.torrents:
            if type(row.get('seeding_time')) is int:
                row['seeding_time'] += 60
            row['ratio'] = round(row.get('ratio', 1.0) + 0.01, 4)

    def qbit_rows(self):
        if self.world.qbit_fail:
            raise prune.Refused(prune.QBIT_FAILURE)
        self._tick()
        return copy.deepcopy(self.world.torrents)

    def remove(self, req):
        self.world.remove_calls.append(copy.deepcopy(req))
        if self.world.remove_error is not None:
            raise self.world.remove_error
        key = (req['library'], req['arr_id'])
        if req['mode'] == 'apply' and key in self.world.apply_errors:
            raise self.world.apply_errors[key]
        movie = [m for m in self.world.movies if m['id'] == req['arr_id']]
        show = [s for s in self.world.series if s['id'] == req['arr_id']]
        size = movie[0]['sizeOnDisk'] if req['library'] == 'movies' else show[0]['statistics']['sizeOnDisk']
        size = self.world.plan_bytes.get(key, size)
        self._tick()
        clock = self.world.seed_clock
        torrents = [dict(t, action=t['action'] if req['remove_torrents'] else 'keep',
                         reason='meets_threshold' if req['remove_torrents'] else 'remove_torrents_false',
                         ratio=1.0 + clock / 100, seeding_time_seconds=15 * prune.DAY + clock * 60)
                    for t in self.world.plan_torrents]
        plan = {'library': req['library'], 'arr_id': req['arr_id'], 'delete_title': True,
                'remove_torrents': req['remove_torrents'], 'torrents': torrents, 'total_bytes': size}
        # The production digest: it must not move with the advancing counters.
        digest = lib.remove_plan_digest(plan)
        if req['mode'] == 'preview':
            if self.world.on_preview is not None:
                self.world.on_preview(req)
            return {'mode': 'preview', 'plan': plan, 'plan_sha256': digest}
        if req['plan_sha256'] != digest:
            raise lib.LibraryRefusal('plan_changed', 'Plan changed since preview')
        if self.world.post_dispatch_on == key:
            raise lib.LibraryPostDispatchError('uncertain', 'Title delete dispatched', [])
        self.world.removed.append(key)
        if self.world.after_apply is not None:
            self.world.after_apply(req)
        return {'mode': 'apply', 'plan': plan,
                'removed_torrent_hashes': [t['hash'] for t in torrents if t['action'] == 'remove'],
                'kept_torrent_hashes': [t['hash'] for t in torrents if t['action'] != 'remove']}


def prune_block(**overrides):
    cfg = {
        'enabled': True, 'target_bytes': TIB, 'min_idle_days': 180, 'grace_days': 7,
        'max_bytes_per_run': TIB, 'max_titles_per_run': 40,
        'state_dir': '/var/lib/holonet-operator/prune',
        'keep_journal': '/var/lib/holonet/janitor/state.json',
    }
    cfg.update(overrides)
    return {
        'enabled': True, 'target_library': '3', 'revision': 'e' * 40, 'subjects': ['1' * 64],
        'libraries': {
            'movies': {'library_id': '3', 'kind': 'movie', 'arr': 'radarr',
                       'descriptor': '/etc/holonet-playlists/radarr.json',
                       'root_folder_path': '/mnt/shared/media/movies', 'quality_profile_id': 11},
            'tv': {'library_id': '1132', 'kind': 'series', 'arr': 'sonarr',
                   'descriptor': '/etc/holonet-playlists/sonarr.json',
                   'root_folder_path': '/mnt/shared/media/tv shows', 'quality_profile_id': 6},
            'anime': {'library_id': '615', 'kind': 'series', 'arr': 'sonarr',
                      'descriptor': '/etc/holonet-playlists/sonarr.json',
                      'root_folder_path': '/mnt/shared/media/anime', 'quality_profile_id': 6},
        },
        'torrents': '/etc/holonet-playlists/qbit.json',
        'allowed_categories': ['radarr-movies', 'tv-sonarr'],
        'removal_thresholds': {'min_ratio': 1.0, 'min_seeding_days': 14},
        'limits': {'lookup_max_rows': 25, 'releases_max_rows': 50, 'subtitles_default_chars': 20000,
                   'subtitles_max_chars': 60000, 'remove_max_files': 500},
        'prune': cfg,
    }


def seed_row(torrent_hash, size, seeded_days, tracker=SEEDPOOL):
    return {'hash': torrent_hash, 'tracker': tracker, 'total_size': size, 'size': size, 'progress': 1,
            'amount_left': 0, 'seeding_time': seeded_days * prune.DAY, 'state': 'uploading', 'ratio': 1.0}


class LibraryPruneTests(unittest.TestCase):
    def setUp(self):
        self.world = PruneWorld()
        self.backends = FakePruneBackends(self.world)
        self.block = prune_block()
        lib.validate_block(self.block)

    def run_prune(self, mode='run', now=NOW, marks=None, keep_ids=None, block=None):
        return prune.prune(block or self.block, self.backends, mode=mode, now=now,
                           keep_ids=keep_ids or {'movies': [], 'tv': [], 'anime': []}, marks=marks or {})

    def reasons(self, keep_ids=None):
        facts = prune.collect_arr(self.block, self.backends.arr)
        emby = prune.collect_emby(FakePruneEmby(self.world), prune.TitleIndex(facts['titles'], self.block),
                                  now=NOW, window_days=180, keep_ids=keep_ids or {})
        dates = prune.SeriesDates(self.backends.arr)
        return {key: prune.eligibility(t, facts, emby, self.block['prune'], NOW, dates)[1]
                for key, t in facts['titles'].items()}

    def test_parse_time_and_iso(self):
        self.assertEqual(prune.parse_time('2026-11-23T02:42:42.1880000Z'), prune.parse_time('2026-11-23T02:42:42Z'))
        self.assertEqual(prune.parse_time('2026-11-23T03:42:42+01:00'), prune.parse_time('2026-11-23T02:42:42Z'))
        for bad in (None, 5, '', 'yesterday', '2026-13-01T00:00:00Z', 'x' * 50):
            self.assertIsNone(prune.parse_time(bad))
        self.assertEqual(prune.iso(NOW), '2026-11-22T00:00:00Z')

    def test_prune_policy_block_validation(self):
        self.assertIn('prune', lib.validate_block(self.block))
        for change in ({'target_bytes': GIB}, {'min_idle_days': 1}, {'grace_days': 0},
                       {'max_bytes_per_run': 2 * TIB}, {'max_titles_per_run': 0},
                       {'max_titles_per_run': 201}, {'enabled': 'yes'}, {'state_dir': 'relative'},
                       {'keep_journal': '/var/../etc/shadow'}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(prune.Refused):
                lib.validate_block(prune_block(**change))
        block = prune_block()
        del block['prune']['grace_days']
        with self.assertRaises(prune.Refused):
            lib.validate_block(block)
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            prune.prune_config({k: v for k, v in self.block.items() if k != 'prune'})
        self.assertEqual(ctx.exception.reason, 'prune_disabled')

    def test_new_read_requests_are_bounded(self):
        self.assertIn('library-prune-status', lib.READ_OPERATIONS)
        self.assertIn('library-watch-history', lib.READ_OPERATIONS)
        self.assertEqual(lib.required_action('library-watch-history'), lib.READ_ACTION)
        lib.request('library-prune-status', {})
        lib.request('library-watch-history', {})
        lib.request('library-watch-history', {'days': 365, 'limit': 200})
        for op, value in (('library-prune-status', {'mode': 'run'}),
                          ('library-watch-history', {'days': 0}),
                          ('library-watch-history', {'days': 366}),
                          ('library-watch-history', {'days': True}),
                          ('library-watch-history', {'limit': 201}),
                          ('library-watch-history', {'user': 'x'})):
            with self.subTest(op=op, value=value), self.assertRaises(prune.Refused):
                lib.request(op, value)

    def test_eligibility_exclusions(self):
        w = self.world
        for arr_id in range(1, 10):
            w.movie(arr_id, 100 * GIB)
        w.movies[2]['qualityProfileId'] = 13                     # 3: Favorites - UHD + Original
        w.favorites[VIEWER] = [emby_movie_id(2), str(80000 + 70)]  # 2 and series 70 via an episode
        w.queue['radarr'] = [{'movieId': 5}]
        w.movies[5]['added'] = prune.iso(NOW - 10 * prune.DAY)    # 6: recently added
        w.movies[6]['movieFile']['dateAdded'] = prune.iso(NOW - 10 * prune.DAY)  # 7: new file
        w.play(emby_movie_id(8), NOW - 5 * prune.DAY)              # 8: watched in the window
        w.played[VIEWER] = [emby_movie_id(9)]                    # 9: played at an unknown time
        w.play(None, NOW - 100 * prune.DAY, kind='VideoPlayback')  # unattributable: incomplete
        w.show(70, 100 * GIB)
        w.show(71, 100 * GIB)
        w.show(72, 100 * GIB, file_added=NOW - 3 * prune.DAY)
        w.show(80, 100 * GIB, library='anime')
        w.queue['sonarr'] = []
        reasons = self.reasons(keep_ids={'movies': [emby_movie_id(4)], 'tv': [], 'anime': []})
        self.assertEqual(reasons, {
            ('movies', 1): None, ('movies', 2): 'favorite', ('movies', 3): 'favorite_profile',
            ('movies', 4): 'kept', ('movies', 5): 'downloading', ('movies', 6): 'recently_added',
            ('movies', 7): 'recent_file', ('movies', 8): 'watched', ('movies', 9): 'history_incomplete',
            ('tv', 70): 'favorite', ('tv', 71): None, ('tv', 72): 'recent_file', ('anime', 80): None,
        })
        # A complete window (log reaches past it, every start attributable) clears 9.
        w.activity = [e for e in w.activity if e['Type'] != 'VideoPlayback']
        self.assertIsNone(self.reasons()[('movies', 9)])
        # A log that starts inside the window is incomplete again.
        w.activity = [e for e in w.activity if e['Id'] != 1]
        self.assertEqual(self.reasons()[('movies', 9)], 'history_incomplete')
        # Series-level favourites, queue membership and the series profile also protect.
        w.favorites[VIEWER] = [str(70071)]
        w.queue['sonarr'] = [{'seriesId': 80}]
        w.series[2]['qualityProfileId'] = 7
        r = self.reasons()
        self.assertEqual((r[('tv', 71)], r[('anime', 80)], r[('tv', 72)]),
                         ('favorite', 'downloading', 'favorite_profile'))

    def test_marks_in_activity_order_until_projection_meets_target(self):
        w = self.world
        w.movie(1, TIB, profile=13)                      # protected anchor keeps us over target
        w.movie(2, 100 * GIB)
        w.movie(3, 200 * GIB)
        w.movie(4, 100 * GIB, added=OLD + 20 * prune.DAY)
        w.movie(5, 50 * GIB, added=OLD + 30 * prune.DAY)
        w.movie(6, 50 * GIB, added=OLD + 40 * prune.DAY)
        summary, marks = self.run_prune(mode='plan')
        # Needs 500 GiB: oldest activity first, ties larger first, stop at the target.
        self.assertEqual([(m['arr_id'], m['size_bytes']) for m in summary['marked']],
                         [(3, 200 * GIB), (2, 100 * GIB), (4, 100 * GIB), (5, 50 * GIB), (6, 50 * GIB)])
        self.assertEqual(summary['projected_bytes'], TIB)
        self.assertEqual(w.remove_calls, [])
        self.assertEqual(len(marks), 5)
        # Two more small titles than needed: the newest stays unmarked.
        w.movie(7, 50 * GIB, added=OLD + 50 * prune.DAY)
        w.movies[0]['sizeOnDisk'] = TIB - 50 * GIB
        summary, _ = self.run_prune(mode='plan')
        self.assertNotIn(7, [m['arr_id'] for m in summary['marked']])

    def test_grace_period_then_removal(self):
        w = self.world
        w.movie(1, TIB, profile=13)
        w.movie(2, 300 * GIB)
        summary, marks = self.run_prune()
        mark = marks[('movies', 2)]
        self.assertEqual(mark['marked_at'], prune.iso(NOW))
        self.assertEqual(mark['remove_after'], prune.iso(NOW + 7 * prune.DAY))
        self.assertEqual(w.remove_calls, [])
        summary, marks = self.run_prune(now=NOW + 6 * prune.DAY, marks=marks)
        self.assertEqual(w.remove_calls, [])
        self.assertIn(('movies', 2), marks)
        self.assertEqual(summary['marked'], [])
        summary, marks = self.run_prune(now=NOW + 7 * prune.DAY, marks=marks)
        self.assertEqual(w.removed, [('movies', 2)])
        self.assertEqual([c['mode'] for c in w.remove_calls], ['preview', 'apply'])
        self.assertEqual(w.remove_calls[0]['keep_emby_ids'], [])
        self.assertEqual(w.remove_calls[0]['target'], 'title')
        self.assertEqual(summary['removed'][0]['removed_bytes'], 300 * GIB)
        self.assertEqual(summary['library_bytes_after'], TIB)
        self.assertEqual(marks, {})

    def test_apply_rechecks_eligibility_and_target(self):
        w = self.world
        w.movie(1, TIB, profile=13)
        w.movie(2, 300 * GIB)
        w.movie(3, 300 * GIB)
        w.movie(4, 300 * GIB)
        _, marks = self.run_prune()
        self.assertEqual(set(marks), {('movies', 2), ('movies', 3), ('movies', 4)})
        later = NOW + 8 * prune.DAY
        w.favorites[VIEWER] = [emby_movie_id(2)]
        w.play(emby_movie_id(3), later - prune.DAY)
        summary, marks = self.run_prune(now=later, marks=marks)
        self.assertEqual({(u['arr_id'], u['reason']) for u in summary['unmarked']},
                         {(2, 'favorite'), (3, 'watched')})
        self.assertEqual(w.removed, [('movies', 4)])
        self.assertNotIn(('movies', 4), marks)
        self.assertNotIn(4, [m['arr_id'] for m in summary['marked']])
        # Kept at apply: library-remove refuses keep_listed, which unmarks.
        w.movie(5, 300 * GIB)
        w.movie(6, 300 * GIB)
        _, marks = self.run_prune(now=later, marks={})
        w.remove_calls.clear()
        w.remove_error = lib.LibraryRefusal('keep_listed', 'Title is protected by keep list')
        summary, marks = self.run_prune(now=later + 8 * prune.DAY, marks=marks)
        self.assertTrue(summary['unmarked'] and all(u['reason'] == 'kept' for u in summary['unmarked']))
        self.assertEqual(marks, {})
        # Under target at apply time: nothing is removed and marks are released.
        w.remove_error = None
        _, marks = self.run_prune(now=later, marks={})
        self.assertTrue(marks)
        for m in w.movies:
            m['sizeOnDisk'] = GIB
        w.remove_calls.clear()
        summary, marks = self.run_prune(now=later + 8 * prune.DAY, marks=marks)
        self.assertEqual(w.remove_calls, [])
        self.assertEqual(marks, {})
        self.assertTrue(all(u['reason'] == 'under_target' for u in summary['unmarked']))

    def test_unmarks_gone_and_ineligible_on_every_run(self):
        w = self.world
        w.movie(1, TIB, profile=13)
        w.movie(2, 300 * GIB)
        w.movie(3, 300 * GIB)
        _, marks = self.run_prune()
        w.movies = [m for m in w.movies if m['id'] != 2]
        w.queue['radarr'] = [{'movieId': 3}]
        summary, marks = self.run_prune(now=NOW + prune.DAY, marks=marks)
        self.assertEqual({(u['arr_id'], u['reason']) for u in summary['unmarked']},
                         {(2, 'gone'), (3, 'downloading')})
        self.assertEqual(marks, {})

    def test_per_run_caps(self):
        w = self.world
        w.movie(1, TIB, profile=13)
        for arr_id in range(2, 8):
            w.movie(arr_id, 300 * GIB, added=OLD + arr_id * prune.DAY)
        summary, marks = self.run_prune(block=prune_block(max_titles_per_run=2))
        self.assertEqual([m['arr_id'] for m in summary['marked']], [2, 3])
        summary, marks = self.run_prune(block=prune_block(max_bytes_per_run=700 * GIB))
        self.assertEqual([m['arr_id'] for m in summary['marked']], [2, 3])
        # A title larger than the byte cap is skipped, smaller ones still mark.
        w.movie(9, 800 * GIB, added=OLD)
        summary, _ = self.run_prune(block=prune_block(max_bytes_per_run=700 * GIB))
        self.assertEqual([m['arr_id'] for m in summary['marked']], [2, 3])
        # Removal obeys the title cap too.
        _, marks = self.run_prune()
        w.remove_calls.clear()
        summary, marks = self.run_prune(now=NOW + 8 * prune.DAY, marks=marks,
                                        block=prune_block(max_titles_per_run=1))
        self.assertEqual(len(w.removed), 1)
        self.assertEqual(len(summary['removed']), 1)

    def test_fail_closed_on_any_read_error(self):
        w = self.world
        w.movie(1, TIB, profile=13)
        w.movie(2, 300 * GIB)
        _, marks = self.run_prune()
        for setup, reason in (
            (lambda: w.arr_fail.add('radarr'), 'arr_unavailable'),
            (lambda: w.arr_fail.add('sonarr'), 'arr_unavailable'),
            (lambda: setattr(w, 'emby_fail', True), 'emby_unavailable'),
            (lambda: setattr(w, 'history_fail', True), 'history_unavailable'),
            (lambda: setattr(w, 'qbit_fail', True), 'qbit_unavailable'),
        ):
            w.arr_fail.clear()
            w.emby_fail = w.history_fail = w.qbit_fail = False
            setup()
            with self.subTest(reason=reason), self.assertRaises(prune.PruneAbort) as ctx:
                self.run_prune(now=NOW + 8 * prune.DAY, marks=marks)
            self.assertEqual(ctx.exception.reason, reason)
            self.assertEqual(w.remove_calls, [])
        # Unresolvable SeriesId on a favourite refuses instead of dropping protection.
        w.arr_fail.clear()
        w.emby_fail = w.history_fail = w.qbit_fail = False
        w.items['999'] = {'Id': '999', 'Type': 'Episode', 'SeriesId': '424242'}
        w.favorites[VIEWER] = ['999']
        with self.assertRaises(prune.PruneAbort) as ctx:
            self.run_prune(now=NOW + 8 * prune.DAY, marks=marks)
        self.assertEqual(ctx.exception.reason, 'emby_unavailable')
        # A backend failure during apply stops further removals and marking.
        w.favorites = {}
        w.movie(3, 300 * GIB)
        _, marks = self.run_prune(marks={})
        w.remove_error = prune.Refused(lib.FAILURE)
        summary, after = self.run_prune(now=NOW + 8 * prune.DAY, marks=marks)
        self.assertEqual((summary['outcome'], summary['abort_reason']), ('aborted', 'backend_error'))
        self.assertEqual(len(w.remove_calls), 1)
        self.assertEqual(summary['marked'], [])
        self.assertEqual(set(after), set(marks))

    def test_post_dispatch_uncertainty_stops_the_run(self):
        w = self.world
        w.movie(1, TIB, profile=13)
        w.movie(2, 300 * GIB)
        w.movie(3, 300 * GIB, added=OLD + prune.DAY)
        _, marks = self.run_prune()
        w.post_dispatch_on = ('movies', 2)
        summary, _ = self.run_prune(now=NOW + 8 * prune.DAY, marks=marks)
        self.assertEqual((summary['outcome'], summary['abort_reason']), ('partial', 'post_dispatch_uncertain'))
        self.assertEqual(w.removed, [])
        self.assertEqual([c['arr_id'] for c in w.remove_calls if c['mode'] == 'apply'], [2])
        self.assertEqual(summary['marked'], [])

    def test_torrent_gate_seedpool_policy(self):
        h1, h2, big = 'a' * 40, 'b' * 40, 'c' * 40
        rows = [seed_row(big, 25 * TIB, 30), seed_row(h1, TIB, 11), seed_row(h2, TIB, 5)]
        self.assertEqual(prune.torrent_gate(rows, [h1]), (True, {}))
        self.assertEqual(prune.torrent_gate(rows, [h2]), (False, {'minimum_seed_age_not_met': 1}))
        # Retiring would break the 20 TiB active floor.
        floor = [seed_row(big, 20 * TIB - GIB, 30), seed_row(h1, TIB, 11)]
        self.assertEqual(prune.torrent_gate(floor, [h1]), (False, {'projected_below_floor': 1}))
        other = [seed_row(big, 25 * TIB, 30), seed_row(h1, TIB, 11, OTHER_TRACKER), seed_row(h2, TIB, 5, OTHER_TRACKER)]
        self.assertEqual(prune.torrent_gate(other, [h1]), (True, {}))
        self.assertEqual(prune.torrent_gate(other, [h2]), (False, {'minimum_seed_age_not_met': 1}))
        self.assertEqual(prune.torrent_gate(rows + [seed_row('d' * 40, TIB, 30, '')], ['d' * 40])[0], False)
        self.assertEqual(prune.torrent_gate(rows, ['e' * 40]), (False, {'torrent_missing': 1}))
        # Any unknown torrent in the snapshot blocks SeedPool retirement.
        broken = rows + [{'hash': 'f' * 40, 'tracker': SEEDPOOL}]
        self.assertFalse(prune.torrent_gate(broken, [h1])[0])

    def test_removal_torrent_gating(self):
        w = self.world
        w.movie(2, 300 * GIB)
        title = prune.collect_arr(self.block, self.backends.arr)['titles'][('movies', 2)]
        h = 'a' * 40

        def remove_one(keep_ids):
            prepared = prune._prepare_removal(self.backends, title, keep_ids)
            applied = prune._apply_removal(self.backends, prepared)
            result, warning = prune._removal_result(self.backends, prepared, applied)
            self.assertIsNone(warning)
            return result

        w.plan_torrents = [{'hash': h, 'action': 'remove', 'ownership_proof': 'hardlink'}]
        w.torrents = [seed_row('c' * 40, 25 * TIB, 30), seed_row(h, 300 * GIB, 11)]
        result = remove_one({'movies': ['123']})
        self.assertEqual([(c['mode'], c['remove_torrents']) for c in w.remove_calls],
                         [('preview', True), ('apply', True)])
        self.assertEqual(w.remove_calls[0]['keep_emby_ids'], ['123'])
        self.assertEqual((result['torrents_removed'], result['torrents_kept'], result['bytes_held_by_torrents']),
                         (1, 0, 0))
        # Seeded under 10 days: keep the torrent, remove only the library title.
        w.remove_calls.clear()
        w.torrents[1]['seeding_time'] = 3 * prune.DAY
        result = remove_one({})
        self.assertEqual([(c['mode'], c['remove_torrents']) for c in w.remove_calls],
                         [('preview', True), ('preview', False), ('apply', False)])
        self.assertEqual((result['torrents_removed'], result['torrents_kept'], result['bytes_held_by_torrents']),
                         (0, 1, 300 * GIB))
        self.assertEqual(result['torrent_gate'], {'minimum_seed_age_not_met': 1})
        # Arr history is not proof enough here: no hardlink proof, the torrent keeps
        # seeding whatever the SeedPool snapshot says.
        w.remove_calls.clear()
        w.torrents[1]['seeding_time'] = 30 * prune.DAY
        w.plan_torrents = [{'hash': h, 'action': 'remove', 'ownership_proof': 'arr_history'}]
        result = remove_one({})
        self.assertEqual([(c['mode'], c['remove_torrents']) for c in w.remove_calls],
                         [('preview', True), ('preview', False), ('apply', False)])
        self.assertEqual((result['torrents_removed'], result['torrents_kept'], result['torrent_gate']),
                         (0, 1, {'ownership_not_hardlink': 1}))
        # One unproven torrent keeps every torrent of the title.
        w.remove_calls.clear()
        w.torrents.append(seed_row('b' * 40, GIB, 30))
        w.plan_torrents = [{'hash': h, 'action': 'remove', 'ownership_proof': 'hardlink'},
                           {'hash': 'b' * 40, 'action': 'remove', 'ownership_proof': 'arr_history'}]
        result = remove_one({})
        self.assertEqual(w.remove_calls[-1]['remove_torrents'], False)
        self.assertEqual((result['torrents_removed'], result['torrents_kept']), (0, 2))
        # Hardlink proof failed (library-remove keeps it): apply repeats the previewed flag.
        w.remove_calls.clear()
        w.plan_torrents = [{'hash': h, 'action': 'keep', 'ownership_proof': 'none'}]
        w.torrents = [seed_row('c' * 40, 25 * TIB, 30), seed_row(h, 300 * GIB, 11)]
        result = remove_one({})
        self.assertEqual([(c['mode'], c['remove_torrents']) for c in w.remove_calls],
                         [('preview', True), ('apply', True)])
        self.assertEqual(result['bytes_held_by_torrents'], 300 * GIB)
        # A malformed size in the qBittorrent snapshot cannot fail after the removal.
        w.torrents[1]['size'] = 'large'
        self.assertEqual(remove_one({})['bytes_held_by_torrents'], 0)

    def _due_world(self):
        """Movies 2 and 3 marked and due; the protected anchor keeps the library over target."""
        self.world = PruneWorld()
        self.backends = FakePruneBackends(self.world)
        w = self.world
        w.movie(1, TIB, profile=13)
        w.movie(2, 300 * GIB)
        w.movie(3, 300 * GIB, added=OLD + prune.DAY)
        _, marks = self.run_prune()
        self.assertEqual(set(marks), {('movies', 2), ('movies', 3)})
        return w, marks

    def test_protection_rechecked_right_before_apply(self):
        later = NOW + 8 * prune.DAY
        changes = {
            'favorite': lambda w: w.favorites.__setitem__(ADMIN, [emby_movie_id(2)]),
            'watched': lambda w: w.resumable.__setitem__(VIEWER, [emby_movie_id(2)]),
            'downloading': lambda w: w.queue.__setitem__('radarr', [{'movieId': 2}]),
            'favorite_profile': lambda w: w.movies[1].__setitem__('qualityProfileId', 13),
        }
        changes['watched_log'] = lambda w: w.play(emby_movie_id(2), later + 60)
        for reason, change in changes.items():
            with self.subTest(reason=reason):
                w, marks = self._due_world()
                # The change lands after this run collected its facts, before the apply.
                w.on_preview = lambda req, w=w, change=change: req['arr_id'] == 2 and change(w)
                summary, marks = self.run_prune(now=later, marks=marks)
                self.assertEqual(summary['outcome'], 'ok')
                self.assertNotIn(('movies', 2), w.removed)
                self.assertEqual([c['mode'] for c in w.remove_calls if c['arr_id'] == 2], ['preview'])
                self.assertIn((2, reason.replace('_log', '')), {(u['arr_id'], u['reason']) for u in summary['unmarked']})
                # The next due title is still removed, and the protected one is not re-marked.
                self.assertEqual(w.removed, [('movies', 3)])
                self.assertNotIn(('movies', 2), marks)
                self.assertNotIn(2, [m['arr_id'] for m in summary['marked']])
        # A re-check read failure stops the run before that apply.
        w, marks = self._due_world()
        w.on_preview = lambda req: setattr(w, 'emby_fail', True)
        summary, after = self.run_prune(now=later, marks=marks)
        self.assertEqual((summary['outcome'], summary['abort_reason']), ('aborted', 'recheck_unavailable'))
        self.assertEqual(w.removed, [])
        self.assertEqual(set(after), set(marks))

    def test_recheck_fails_closed_for_played_title_when_fresh_history_is_incomplete(self):
        # Movie 2 was played long ago and history was complete at collection, so
        # it is due. Before its apply the activity log loses coverage and a new
        # resume state appears: the fresh history cannot prove the title idle.
        later = NOW + 8 * prune.DAY
        w, marks = self._due_world()
        w.played[VIEWER] = [emby_movie_id(2)]

        def change(req, w=w):
            if req['arr_id'] == 2:
                w.activity = []
                w.resumable[VIEWER] = [emby_movie_id(2)]
        w.on_preview = change
        summary, marks = self.run_prune(now=later, marks=marks)
        self.assertNotIn(('movies', 2), w.removed)
        self.assertEqual([c['mode'] for c in w.remove_calls if c['arr_id'] == 2], ['preview'])
        self.assertIn((2, 'history_incomplete'),
                      {(u['arr_id'], u['reason']) for u in summary['unmarked']})

    def test_byte_cap_uses_fresh_plan_size(self):
        w = self.world
        w.movie(1, 2 * TIB, profile=13)
        w.movie(2, 300 * GIB)
        _, marks = self.run_prune()
        self.assertEqual(marks[('movies', 2)]['size_bytes'], 300 * GIB)
        # The title grew since the snapshot: the fresh plan deletes 3 TiB; the cap is 1 TiB.
        w.plan_bytes[('movies', 2)] = 3 * TIB
        later = NOW + 8 * prune.DAY
        summary, marks = self.run_prune(now=later, marks=marks)
        self.assertEqual([c['mode'] for c in w.remove_calls], ['preview'])
        self.assertEqual(w.removed, [])
        self.assertEqual([(s['arr_id'], s['reason'], s['size_bytes']) for s in summary['remove_skipped']],
                         [(2, 'run_byte_cap', 3 * TIB)])
        self.assertEqual(summary['removed_bytes'], 0)
        self.assertIn(('movies', 2), marks)
        # Under the cap, the receipt and the accounting carry the plan's bytes.
        w.plan_bytes[('movies', 2)] = 200 * GIB
        summary, marks = self.run_prune(now=later, marks=marks)
        self.assertEqual(w.removed, [('movies', 2)])
        self.assertEqual((summary['removed'][0]['size_bytes'], summary['removed'][0]['removed_bytes']),
                         (200 * GIB, 200 * GIB))
        self.assertEqual(summary['removed_bytes'], 200 * GIB)
        self.assertEqual(summary['library_bytes_after'], summary['library_bytes_before'] - 200 * GIB)

    def test_reads_finish_before_the_first_removal(self):
        w, marks = self._due_world()
        w.show(70, 300 * GIB, added=OLD + 2 * prune.DAY)
        # Sonarr fails as soon as anything was removed: every file-date read the
        # marking phase needs must already be done.
        w.after_apply = lambda req: w.arr_fail.add('sonarr')
        w.movies[0]['sizeOnDisk'] = TIB + 300 * GIB
        summary, marks = self.run_prune(now=NOW + 8 * prune.DAY, marks=marks,
                                        block=prune_block(max_titles_per_run=1))
        self.assertEqual((summary['outcome'], summary['abort_reason']), ('ok', None))
        self.assertEqual(w.removed, [('movies', 2)])
        self.assertIn(70, [m['arr_id'] for m in summary['marked']])
        self.assertIn(('tv', 70), marks)

    def test_failure_after_a_removal_keeps_the_receipt(self):
        # A read failure (re-check) and an unexpected exception (apply) after
        # one removal: both end partial with the removal and surviving marks.
        for kind in ('refused', 'unexpected'):
            with self.subTest(kind=kind):
                w, marks = self._due_world()
                if kind == 'refused':
                    w.after_apply = lambda req, w=w: setattr(w, 'emby_fail', True)
                    expected = 'recheck_unavailable'
                else:
                    w.apply_errors[('movies', 3)] = RuntimeError('injected')
                    expected = 'unexpected'
                summary, after = self.run_prune(now=NOW + 8 * prune.DAY, marks=marks)
                self.assertEqual((summary['outcome'], summary['abort_reason']), ('partial', expected))
                self.assertEqual([r['arr_id'] for r in summary['removed']], [2])
                self.assertEqual(summary['removed_bytes'], 300 * GIB)
                self.assertEqual(set(after), {('movies', 3)})
                self.assertEqual(summary['marked'], [])
                skipped = [(s['arr_id'], s['reason']) for s in summary['remove_skipped']]
                self.assertEqual(skipped, [] if kind == 'refused' else [(3, 'uncertain')])
                self.assertEqual(json.loads(prune.event(summary))['removed_count'], 1)

    def test_history_incomplete_for_unmapped_play_in_a_managed_library(self):
        w = self.world
        w.movie(1, 100 * GIB)
        w.items['45555'] = {'Id': '45555', 'Type': 'Movie', 'ProviderIds': {'Tmdb': '999999'},
                            'Path': '/mnt/shared/media/movies/Orphan (1999)/orphan.mkv'}
        w.items['46666'] = {'Id': '46666', 'Type': 'Movie', 'ProviderIds': {},
                            'Path': '/mnt/shared/media/home videos/clip.mkv'}

        def coverage():
            facts = prune.collect_arr(self.block, self.backends.arr)
            return prune.collect_emby(FakePruneEmby(w), prune.TitleIndex(facts['titles'], self.block),
                                      now=NOW, window_days=180, touched=False)['history']['coverage']

        # Plays of deleted items or items outside the managed libraries do not matter.
        w.play('31337', NOW - prune.DAY)
        w.play('46666', NOW - prune.DAY)
        self.assertTrue(coverage()['complete'])
        # An existing item inside a managed library that maps to no title might be any
        # title, so the window is incomplete and played titles stay protected.
        w.play('45555', NOW - prune.DAY)
        cov = coverage()
        self.assertFalse(cov['complete'])
        self.assertEqual(cov['unmapped_plays'], 3)

    def test_keep_journal_snapshot(self):
        home = Path(os.environ['HOME'])
        tmp = Path(os.environ['TMPDIR'])
        self.assertTrue(tmp == home / 'tmp' or home in tmp.parents)
        tmp.mkdir(parents=True, exist_ok=True)
        path = tmp / f'keeps-{os.getpid()}.json'
        record = {'server': 'a' * 32, 'library': '3', 'target': '40002', 'active': True, 'revision': 1}
        state = {'version': 1, 'previews': {}, 'janitor_keeps': {
            '1': record, '2': dict(record, target='40003', active=False),
            '3': dict(record, target='40004', server='f' * 32),
            '4': dict(record, target='70071', library='1132'),
            '5': dict(record, target='not-an-id'),
        }}
        try:
            path.write_text(json.dumps(state))
            os.chmod(path, 0o600)
            self.assertEqual(prune.read_keep_journal(str(path), 'a' * 32),
                             {'movies': ['40002'], 'tv': ['70071'], 'anime': []})
            os.chmod(path, 0o640)
            with self.assertRaises(prune.Refused):
                prune.read_keep_journal(str(path), 'a' * 32)
            os.chmod(path, 0o600)
            for bad in ({'version': 2}, {'version': 1, 'janitor_keeps': []},
                        {'version': 1, 'janitor_keeps': {'1': dict(record, active='yes')}}):
                path.write_text(json.dumps(bad))
                with self.subTest(bad=bad), self.assertRaises(prune.Refused):
                    prune.read_keep_journal(str(path), 'a' * 32)
            path.write_text('{"version": 1, "version": 1}')
            with self.assertRaises(prune.Refused):
                prune.read_keep_journal(str(path), 'a' * 32)
            with self.assertRaises(prune.Refused):
                prune.read_keep_journal(str(tmp / 'missing.json'), 'a' * 32)
        finally:
            path.unlink(missing_ok=True)

    def test_activity_entries_with_numeric_user_ids_are_attributed(self):
        # Live Emby 4.x activity entries carry the user's short internal number
        # ("6"), not the 32-hex user Id. Those plays must still protect titles.
        w = self.world
        w.movie(1, 3 * TIB, profile=13)
        w.movie(2, 100 * GIB)
        w.movie(3, 100 * GIB)
        for item, user, name in ((emby_movie_id(2), '6', 'Viewer is playing Movie 2 '),
                                 (emby_movie_id(2), '9', 'Someone is playing Movie 2 ')):
            w.activity.append({'Id': len(w.activity) + 1, 'Type': 'playback.start', 'ItemId': item,
                               'UserId': user, 'Name': name, 'Date': prune.iso(NOW - 5 * prune.DAY)})
        out = prune.op_watch_history({'request': {}}, self.block, lambda b, d, s: FakePruneArr(w, b),
                                     lambda: FakePruneEmby(w), now=NOW)
        self.assertEqual(out['history']['unattributable_in_window'], 0)
        self.assertEqual([(i['arr_id'], i['play_count'], i['users']) for i in out['items']],
                         [(2, 2, ['Viewer', 'user 9'])])
        summary, marks = self.run_prune()
        self.assertNotIn(('movies', 2), marks)
        self.assertIn(('movies', 3), marks)

    def test_watch_history_operation(self):
        w = self.world
        w.movie(1, 100 * GIB)
        w.movie(2, 100 * GIB)
        w.show(70, 100 * GIB)
        w.play(emby_movie_id(1), NOW - 2 * prune.DAY)
        w.play(emby_movie_id(1), NOW - prune.DAY, user=ADMIN)
        w.play(str(80070), NOW - 3 * prune.DAY)
        w.play(emby_movie_id(2), NOW - 100 * prune.DAY)   # outside a 90-day window
        w.play('31337', NOW - prune.DAY)                  # deleted item: unmapped
        w.favorites[ADMIN] = [emby_movie_id(2)]
        call = {'request': {}}
        out = prune.op_watch_history(call, self.block, lambda b, d, s: FakePruneArr(w, b),
                                     lambda: FakePruneEmby(w), now=NOW)
        self.assertEqual(out['days'], 90)
        self.assertEqual([(i['arr_id'], i['play_count'], i['users']) for i in out['items']],
                         [(1, 2, ['Admin', 'Viewer']), (70, 1, ['Viewer'])])
        self.assertEqual(out['items'][0]['tmdb_id'], 101)
        self.assertEqual(out['items'][1]['tvdb_id'], 7070)
        self.assertEqual(out['items'][0]['genres'], ['Drama'])
        self.assertEqual(out['items'][0]['last_played'], prune.iso(NOW - prune.DAY))
        self.assertEqual([(f['arr_id'], f['users']) for f in out['favorites']], [(2, ['Admin'])])
        self.assertEqual(out['history']['unmapped_plays'], 1)
        self.assertTrue(out['history']['complete'])
        out = prune.op_watch_history({'request': {'days': 365, 'limit': 1}}, self.block,
                                     lambda b, d, s: FakePruneArr(w, b), lambda: FakePruneEmby(w), now=NOW)
        self.assertEqual((len(out['items']), out['items_total'], out['truncated']), (1, 3, True))
        # The shared wire fixture carries exactly the worker's result shape.
        fixture = json.loads(FIXTURE.read_text())['library_watch_history']['result']
        self.assertEqual(set(out), set(fixture))
        self.assertEqual(set(out['items'][0]), set(fixture['items'][0]))
        self.assertEqual(set(out['favorites'][0]), set(fixture['favorites'][0]))
        self.assertEqual(set(out['history']), set(fixture['history']))
        # Watch history needs no profile, queue or per-title reads.
        self.assertFalse([r for _, r in w.arr_calls if 'queue' in r or 'profile' in r or 'episodefile' in r])

    def _state_dir(self, name):
        if os.geteuid() != 0:
            self.skipTest('root-only prune state runs in Chaldea/CI')
        home = Path(os.environ['HOME'])
        self.assertEqual(Path(os.environ['TMPDIR']), home / 'tmp')
        for parent in [home, *home.parents]:
            info = parent.lstat()
            self.assertEqual(info.st_uid, 0)
            self.assertEqual(info.st_mode & 0o022, 0)
        base = home / 'tmp' / f'prune-{name}-{os.getpid()}'
        if base.exists():
            import shutil
            shutil.rmtree(base)
        base.mkdir(parents=True, mode=0o700)
        os.chmod(base, 0o700)
        self.addCleanup(lambda: __import__('shutil').rmtree(base, ignore_errors=True))
        return base

    def test_state_custody_atomic_writes_and_abort(self):
        base = self._state_dir('custody')
        block = prune_block(state_dir=str(base))
        w = self.world
        w.movie(1, TIB, profile=13)
        w.movie(2, 300 * GIB)
        keep = {'movies': [], 'tv': [], 'anime': []}
        summary = prune.run_locked(block, self.backends, mode='plan', now=NOW, keep_ids=keep)
        self.assertEqual([m['arr_id'] for m in summary['would_mark']], [2])
        self.assertEqual(sorted(p.name for p in base.iterdir()), [])
        summary = prune.run_locked(block, self.backends, mode='run', now=NOW, keep_ids=keep)
        self.assertEqual(summary['outcome'], 'ok')
        self.assertEqual(sorted(p.name for p in base.iterdir()), ['.lock', 'last_run.json', 'marks.json'])
        for name in ('marks.json', 'last_run.json'):
            info = (base / name).lstat()
            self.assertEqual((stat.S_IMODE(info.st_mode), info.st_uid, info.st_nlink), (0o600, 0, 1))
        marks = json.loads((base / 'marks.json').read_text())
        self.assertEqual([(m['library'], m['arr_id']) for m in marks['marks']], [('movies', 2)])
        # Status reads the same state.
        status = prune.op_prune_status({'request': {}}, block, lambda b, d, s: FakePruneArr(w, b))
        self.assertEqual((status['library_bytes'], status['pending_count'], status['projected_bytes']),
                         (TIB + 300 * GIB, 1, TIB))
        self.assertEqual(status['pending'][0]['remove_after'], prune.iso(NOW + 7 * prune.DAY))
        self.assertEqual(status['last_run']['marked'][0]['arr_id'], 2)
        self.assertIn('protects', status['protection'])
        fixture = json.loads(FIXTURE.read_text())['library_prune_status']['result']
        self.assertEqual(set(status), set(fixture))
        self.assertEqual(set(status['pending'][0]), set(fixture['pending'][0]))
        self.assertEqual(status['protection'], fixture['protection'])
        # A failed write leaves the previous file intact and no temporary file behind.
        before = (base / 'marks.json').read_bytes()
        with patch.object(lib.os, 'replace', side_effect=OSError('injected')):
            with self.assertRaises(prune.Refused):
                prune.run_locked(block, self.backends, mode='run', now=NOW + prune.DAY, keep_ids=keep)
        self.assertEqual((base / 'marks.json').read_bytes(), before)
        self.assertEqual(sorted(p.name for p in base.iterdir()), ['.lock', 'last_run.json', 'marks.json'])
        # A read failure aborts, records it and keeps the marks unchanged.
        w.emby_fail = True
        summary = prune.run_locked(block, self.backends, mode='run', now=NOW + 8 * prune.DAY, keep_ids=keep)
        self.assertEqual((summary['outcome'], summary['abort_reason']), ('aborted', 'emby_unavailable'))
        self.assertEqual((base / 'marks.json').read_bytes(), before)
        self.assertEqual(json.loads((base / 'last_run.json').read_text())['abort_reason'], 'emby_unavailable')
        self.assertEqual(w.remove_calls, [])
        # Corrupt marks refuse status and abort a run without removing anything.
        w.emby_fail = False
        (base / 'marks.json').write_text('{"version": 1, "marks": [{"library": "movies"}]}')
        summary = prune.run_locked(block, self.backends, mode='run', now=NOW + 8 * prune.DAY, keep_ids=keep)
        self.assertEqual(summary['abort_reason'], 'state_invalid')
        with self.assertRaises(lib.LibraryRefusal) as ctx:
            prune.op_prune_status({'request': {}}, block, lambda b, d, s: FakePruneArr(w, b))
        self.assertEqual(ctx.exception.reason, 'prune_state_invalid')
        self.assertEqual(w.remove_calls, [])
        # A concurrent run holds the lock: the second run is busy and changes nothing.
        (base / 'marks.json').unlink()
        dir_fd = os.open(str(base), os.O_RDONLY | os.O_DIRECTORY)
        held = prune.StateLock(dir_fd)
        try:
            summary = prune.run_locked(block, self.backends, mode='run', now=NOW, keep_ids=keep)
            self.assertEqual(summary['abort_reason'], 'busy')
            self.assertFalse((base / 'marks.json').exists())
        finally:
            held.release()
            os.close(dir_fd)
        # Disabled: a run records only that it was disabled.
        summary = prune.run_locked(prune_block(state_dir=str(base), enabled=False), self.backends,
                                   mode='run', now=NOW, keep_ids=keep)
        self.assertEqual(summary['outcome'], 'disabled')
        self.assertFalse((base / 'marks.json').exists())
        # Unsafe state directory permissions refuse.
        os.chmod(base, 0o755)
        with self.assertRaises(prune.Refused):
            prune.run_locked(block, self.backends, mode='run', now=NOW, keep_ids=keep)

    def test_partial_run_persists_receipt(self):
        base = self._state_dir('partial')
        block = prune_block(state_dir=str(base))
        keep = {'movies': [], 'tv': [], 'anime': []}
        w = self.world
        w.movie(1, TIB, profile=13)
        w.movie(2, 300 * GIB)
        w.movie(3, 300 * GIB, added=OLD + prune.DAY)
        prune.run_locked(block, self.backends, mode='run', now=NOW, keep_ids=keep)
        w.apply_errors[('movies', 3)] = ValueError('injected')
        summary = prune.run_locked(block, self.backends, mode='run', now=NOW + 8 * prune.DAY, keep_ids=keep)
        self.assertEqual((summary['outcome'], summary['abort_reason']), ('partial', 'unexpected'))
        last_run = json.loads((base / 'last_run.json').read_text())
        self.assertEqual((last_run['outcome'], [r['arr_id'] for r in last_run['removed']]), ('partial', [2]))
        self.assertEqual(last_run['removed_bytes'], 300 * GIB)
        marks = json.loads((base / 'marks.json').read_text())['marks']
        self.assertEqual([(m['library'], m['arr_id']) for m in marks], [('movies', 3)])
        # A state write failure after removals still returns the receipt for the event.
        w.apply_errors.clear()
        with patch.object(lib.os, 'replace', side_effect=OSError('injected')):
            summary = prune.run_locked(block, self.backends, mode='run', now=NOW + 9 * prune.DAY,
                                       keep_ids=keep)
        self.assertEqual((summary['outcome'], summary['abort_reason']), ('partial', 'state_write_failed'))
        self.assertEqual([r['arr_id'] for r in summary['removed']], [3])

    def test_main_plan_end_to_end(self):
        base = self._state_dir('main')
        block = prune_block(state_dir=str(base))
        keep = {'movies': [], 'tv': [], 'anime': []}
        w = self.world
        w.movie(1, TIB, profile=13)
        w.movie(2, 300 * GIB)
        prune.run_locked(block, self.backends, mode='run', now=NOW, keep_ids=keep)
        before = (base / 'marks.json').read_bytes()
        policy = {'library': block, 'recipients': {'c' * 32: '/var/lib/holonet-operator/synthetic/config.json'},
                  'caller': {'server': 'a' * 32}}
        out = io.StringIO()
        with patch.object(sock, 'load_policy', return_value=policy), \
             patch.object(sock, 'selected_config', return_value={}), \
             patch.object(prune, 'read_keep_journal', return_value=keep), \
             patch.object(prune, 'Backends', return_value=self.backends), \
             patch.object(prune.time, 'time', return_value=NOW + 8 * prune.DAY), \
             contextlib.redirect_stdout(out):
            code = prune.main(['plan'])
        lines = out.getvalue().strip().split('\n')
        printed = json.loads('\n'.join(lines[:-1]))
        line = json.loads(lines[-1])
        self.assertEqual(code, 0)
        self.assertEqual((printed['mode'], printed['outcome']), ('plan', 'ok'))
        self.assertEqual([r['arr_id'] for r in printed['would_remove']], [2])
        self.assertNotIn('removed', printed)
        self.assertEqual((line['event'], line['mode'], line['outcome'], line['removed_count']),
                         ('holonet_library_prune', 'plan', 'ok', 1))
        # A plan touches no backend mutation and no state.
        self.assertEqual(w.remove_calls, [])
        self.assertEqual((base / 'marks.json').read_bytes(), before)

    def test_wire_fixture_prune_frames_and_summary_shape(self):
        fixture = json.loads(FIXTURE.read_text())
        for key, op in (('library_prune_status_frame', 'library-prune-status'),
                        ('library_watch_history_frame', 'library-watch-history')):
            frame = fixture[key]
            self.assertEqual(frame['operation'], op)
            self.assertEqual(frame['caller']['actions'], ['library.read'])
            lib.request(op, frame['request'])
        w = self.world
        w.movie(1, TIB, profile=13)
        w.movie(2, 300 * GIB)
        summary, marks = self.run_prune()
        last_run = fixture['library_prune_status']['result']['last_run']
        self.assertEqual(set(summary), set(last_run))
        self.assertEqual(set(summary['history']), set(last_run['history']))
        self.assertEqual(set(summary['marked'][0]), set(last_run['marked'][0]))
        self.assertEqual(set(marks[('movies', 2)]), set(fixture['library_prune_status']['result']['pending'][0]))
        for reason in ('prune_disabled', 'prune_state_invalid'):
            self.assertEqual(fixture['refusals'][reason]['error']['reason'], reason)

    def test_event_line_has_counts_only(self):
        w = self.world
        w.movie(1, TIB, profile=13, title='Secret Title')
        w.movie(2, 300 * GIB, title='Another Secret')
        summary, _ = self.run_prune()
        line = prune.event(summary)
        decoded = json.loads(line)
        self.assertEqual(decoded['event'], 'holonet_library_prune')
        self.assertEqual((decoded['marked_count'], decoded['pending_count']), (1, 1))
        self.assertNotIn('Secret', line)
        self.assertNotIn('\n', line)


if __name__ == '__main__':
    unittest.main()
