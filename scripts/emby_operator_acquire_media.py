"""Bounded staging-only metadata read through the maintained ffprobe executable."""
import datetime
import os
import re
import selectors
import stat
import subprocess
import time

from emby_operator_acquire import need, PREFIX, binding, episode_set, base, inspect, text


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')


def open_payload(operation_id, relative):
    """No-follow descriptor walk; never resolve a caller-supplied absolute path."""
    names = (PREFIX + '/' + operation_id + '/' + relative).split('/')[1:]
    need(len(names) <= 64 and all(n and n not in ('.', '..') for n in names))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for n in names[:-1]:
            nxt = os.open(n, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            need(not os.fstat(fd).st_mode & 0o002)
        leaf = os.open(names[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        s = os.fstat(leaf)
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or not 0 < s.st_size <= 1 << 40:
            os.close(leaf)
            need(False)
        return leaf
    finally:
        os.close(fd)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_nlink)


def extract(fd, container, guard):
    """At most16MiB input/128KiB output, pipe protocol only, no URLs or filenames."""
    executable = '/usr/bin/ffprobe'
    s = os.stat(executable, follow_symlinks=False)
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022)
    guard()
    data = os.read(fd, 16 * 1024 * 1024)
    memory = os.memfd_create('holonet-metadata', os.MFD_CLOEXEC)
    proc = None
    try:
        view = memoryview(data)
        while view:
            n = os.write(memory, view)
            need(n > 0)
            view = view[n:]
        os.lseek(memory, 0, os.SEEK_SET)
        command = [executable, '-v', 'error', '-protocol_whitelist', 'pipe',
                   '-f', 'matroska' if container == 'mkv' else 'mov',
                   '-show_entries', 'stream=codec_type,codec_name:stream_tags=language,title',
                   '-of', 'json', '-i', 'pipe:' + str(memory)]
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, pass_fds=(memory,), close_fds=True,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        end = time.monotonic() + min(5, guard())
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                need(guard() > 0 and time.monotonic() < end)
                if not selector.select(min(.05, max(0, end - time.monotonic()))):
                    continue
                part = os.read(proc.stdout.fileno(), min(65536, 131073 - len(output)))
                if not part:
                    break
                output.extend(part)
                need(len(output) <= 131072)
        need(proc.wait(timeout=max(.001, min(guard(), end - time.monotonic()))) == 0)
        from emby_operator_adapter import strict_json
        value = strict_json(bytes(output))
        need(type(value) is dict and set(value) == {'streams'} and
             type(value['streams']) is list and len(value['streams']) <= 64)
        audio, subtitles = [], []
        for stream in value['streams']:
            need(type(stream) is dict)
            tags = stream.get('tags', {})
            need(type(tags) is dict)
            lang = tags.get('language')
            title = tags.get('title')
            need(lang is None or text(lang, 64))
            need(title is None or text(title, 128))
            if stream.get('codec_type') == 'audio':
                need(text(stream.get('codec_name'), 64))
                audio.append(dict(language=lang, codec=stream['codec_name'], name=title))
            elif stream.get('codec_type') == 'subtitle':
                subtitles.append(dict(language=lang))
        need(len(audio) <= 16 and len(subtitles) <= 32)
        return audio, subtitles
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()
        os.close(memory)


def verify(call, block, sonarr, emby, torrent):
    q = call['request']
    before = stamp()
    observed = inspect(call, block, sonarr, emby)
    need(binding(observed) == q['target'] and episode_set(observed) == q['expected_episodes'])
    inventory = torrent.status(call['caller'])
    need(inventory['tags_match_operation'] and inventory['client_reports_complete'] and
         inventory['save_path'] == PREFIX + '/' + q['operation_id'])
    expected = {e['episode_number']: e for e in q['expected_episodes']}
    seen, files = set(), []
    all_english = True
    for f in inventory['files']:
        name = f['name']
        extension = name.rsplit('.', 1)[-1].lower()
        if extension not in ('mkv', 'mp4'):
            # Unknown payloads cannot be silently called harmless sidecars.
            need(extension in ('nfo', 'txt', 'srt', 'ass', 'ssa', 'jpg', 'png'))
            continue
        # Samples/extras cannot satisfy episode coverage, even if episode-named.
        need(not re.search(r'(?i)(?<![A-Za-z0-9])(samples?|extras?)(?![A-Za-z0-9])', name))
        match = re.findall(r'(?i)(?<![A-Za-z0-9])S([0-9]{1,2})E([0-9]{1,4})(?![0-9])', name)
        need(len(match) == 1 and int(match[0][0]) == q['target']['season'])
        number = int(match[0][1])
        need(number in expected and number not in seen)
        seen.add(number)
        fd = open_payload(q['operation_id'], name)
        try:
            original = os.fstat(fd)
            need(original.st_size == f['size'])
            audio, subtitles = extract(fd, extension, torrent.guard)
            stable = identity(original) == identity(os.fstat(fd))
            need(stable)
        finally:
            os.close(fd)
        # Reopen by name to exclude a path replacement while the original fd stayed valid.
        again = open_payload(q['operation_id'], name)
        try:
            need(identity(original) == identity(os.fstat(again)))
        finally:
            os.close(again)
        all_english = all_english and any(t['language'] in ('en', 'eng') for t in audio)
        files.append(dict(episode=expected[number], relative_filename=name, size=f['size'],
            container=extension, audio_tracks=audio, subtitle_tracks=subtitles,
            parse_status='parsed', identity_stable=True))
    need(seen == set(expected))
    result = base(call, 'acquire_verify_v1', torrent.config['resource_id'])
    result.update(sonarr_resource_id=sonarr._config['resource_id'],
        operation_id=q['operation_id'], info_hash=q['info_hash'], target=q['target'],
        observed_from=before, observed_through=stamp(), files=files,
        english_audio_metadata_verified=all_english)
    return result
