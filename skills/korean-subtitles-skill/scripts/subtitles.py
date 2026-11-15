#!/usr/bin/env python3
"""Korean subtitle mechanics. Language, evidence interpretation and semantic impact are human/LLM judgments."""
import argparse
import domain_store
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import selectors
import signal
import stat
import sys
import tempfile
import time
import sqlite3
from contextlib import contextmanager, nullcontext
from functools import wraps

CONTRACT = 'english-ssot-output-v7'
ENGINE_HASH = hashlib.sha256(b''.join((Path(__file__).parent / name).read_bytes() for name in ('subtitles.py', 'domain_store.py', 'telemetry.py'))).hexdigest()
TEXT_CODECS = {'subrip', 'ass', 'ssa', 'webvtt', 'mov_text', 'text'}
MEDIA = {'.mkv', '.mp4', '.m4v', '.mov', '.avi', '.ts', '.webm'}
STAMP = re.compile(r'^(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})$')
TIME = re.compile(r'^(\S+) --> (\S+)$')

class Error(Exception):
    pass

def source_ids(s):
    return set(expand(s['patch']['scope']['ids'])) if s.get('patch') else all_ids(s)

def require(ok, message):
    if not ok:
        raise Error(message)

def packed(obj):
    if isinstance(obj, dict) and isinstance(obj.get('history'), History):
        obj = dict(obj)
        history = obj['history']
        obj['history'] = list(history)
        obj['_history_offset'] = history.count
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')

def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else packed(value)).hexdigest()

def filehash(path):
    p = Path(path)
    if not p.exists():
        return None
    signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    before = signature(p.stat())
    key = (str(p.resolve()), before)
    if _OPERATION is not None and key in _OPERATION['hashes']:
        return _OPERATION['hashes'][key]
    h = hashlib.sha256()
    with p.open('rb') as f:
        opened = signature(os.fstat(f.fileno()))
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
        after = signature(os.fstat(f.fileno()))
    require(before == opened == after == signature(p.stat()), 'Artifact changed during verification')
    result = h.hexdigest()
    if _OPERATION is not None:
        _OPERATION['hashes'][key] = result
    return result

def identity(path):
    s = Path(path).stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]

# One domain store owns active rows and immutable history. Logical JSON names
# address headers/shared records; source and response files are explicit artifacts.
_CONNECTIONS = {}
_OPERATION = None
RECORDS = {'state.json', 'context.json', 'roles.json', 'batch.json', 'placement-results.json'}

class History(list):
    """Only new events are copied/serialized; old immutable events load on demand."""
    def __init__(self, path, count):
        super().__init__()
        self.path, self.count = path, count

    def __len__(self):
        return self.count + super().__len__()

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self))) ]
        if index < 0:
            index += len(self)
        if index < self.count:
            with transaction(store_root(self.path)) as db:
                row = db.execute('SELECT body FROM events WHERE path=? AND revision=?', (str(self.path), index)).fetchone()
                if row is None:
                    raise IndexError(index)
                return json.loads(row[0])
        return super().__getitem__(index - self.count)

    def __deepcopy__(self, memo):
        result = History(self.path, self.count)
        result.extend(copy.deepcopy(list(self), memo))
        return result

def store_root(path):
    parent = Path(path).resolve().parent
    locator = parent / 'item.json'
    if locator.is_file():
        return Path(json.loads(locator.read_text())['root'])
    return parent.parent if parent.name == 'context-history' else parent

def database(root):
    return Path(root) / 'state.sqlite3'

def record_exists(path):
    root = store_root(path)
    if not database(root).is_file():
        return Path(path).is_file()
    with transaction(root) as db:
        return db.execute('SELECT 1 FROM records WHERE path=?', (str(Path(path).resolve()),)).fetchone() is not None

def read_record(path):
    root = store_root(path)
    if not database(root).is_file():
        return json.loads(Path(path).read_text())
    with transaction(root) as db:
        row = db.execute('SELECT body FROM records WHERE path=?', (str(Path(path).resolve()),)).fetchone()
        require(row is not None, 'Missing state record: ' + str(path))
        value = json.loads(row[0])
        store = domain_store.Store(db, globals())
        if Path(path).name == 'state.json':
            return store.export_state(Path(path).resolve(), value)
        if Path(path).name == 'context.json':
            return store.export_context(Path(path).resolve(), value)
        if Path(path).name == 'roles.json':
            return store.export_roles(Path(path).resolve(), value)
        return value

@contextmanager
def transaction(root, create=False):
    global _OPERATION
    root = Path(root).resolve()
    key = str(root)
    if key in _CONNECTIONS:
        yield _CONNECTIONS[key]
        return
    require(create or database(root).is_file(), 'Run prepare fresh for the SQLite contract')
    if create:
        private_mkdir(root)
    # Create before SQLite opens it: library callers also get private DB/journals.
    db_path = database(root)
    fd = os.open(db_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Unsafe database file')
        require(info.st_mode & 0o077 == 0, 'Database must be owner-only')
    finally:
        os.close(fd)
    previous_umask = os.umask(0o077)
    try:
        db = sqlite3.connect(db_path, timeout=5)
    finally:
        os.umask(previous_umask)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA synchronous=FULL')
    initialize = create and not db.execute("SELECT 1 FROM sqlite_master WHERE name='records'").fetchone()
    if not initialize and db.execute('PRAGMA user_version').fetchone()[0] != 7:
        db.close()
        raise Error('Old storage contract; prepare fresh without approval migration')
    db.execute('BEGIN IMMEDIATE')
    previous = _OPERATION
    _OPERATION = {'hashes': {}, 'collection': {}}
    _CONNECTIONS[key] = db
    try:
        db.execute('CREATE TABLE IF NOT EXISTS records(path TEXT PRIMARY KEY, body BLOB NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS events(path TEXT NOT NULL, revision INTEGER NOT NULL, body BLOB NOT NULL, PRIMARY KEY(path,revision))')
        if initialize:
            for statement in domain_store.SCHEMA:
                db.execute(statement)
            db.execute('INSERT INTO root_version(root) VALUES(?)', (str(root),))
            db.execute('PRAGMA user_version=7')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        del _CONNECTIONS[key]
        _OPERATION = previous
        db.close()

@contextmanager
def command_lock(root):
    with (Path(root) / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def state_operation(fn):
    @wraps(fn)
    def run(s, *args, **kwargs):
        with transaction(store_root(Path(s['work']) / 'state.json')) as db:
            result = fn(s, *args, **kwargs)
            kind = args[0].get('kind') if fn.__name__ == 'submit' and args else None
            if kind in {'approval', 'seal', 'resolve'}:
                domain_store.Store(db, globals()).save_phase(s, kind)
            elif kind not in {'context', 'study'} and not (fn.__name__ == 'view' and args and args[0].role == 'study'):
                write_record(Path(s['work']) / 'state.json', packed(s))
            return result
    return run

def write_record(path, content):
    path = Path(path).resolve()
    require(path.name in RECORDS or path.parent.name == 'context-history', 'Unknown database record')
    with transaction(store_root(path)) as db:
        value = json.loads(content)
        if _OPERATION is not None:
            _OPERATION['collection'].clear()
        if path.name == 'state.json':
            history = value.pop('history', [])
            offset = value.pop('_history_offset', 0)
            for index, event in enumerate(history, offset):
                db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?)', (str(path), index, packed(event)))
            value['_history_count'] = offset + len(history)
        store = domain_store.Store(db, globals())
        if path.name == 'state.json':
            store.save_state(path, value)
        else:
            if path.name == 'context.json':
                value = store.context_record(path, value)
            if path.name == 'roles.json':
                value = store.roles(path, value)
            db.execute('INSERT INTO records VALUES(?,?) ON CONFLICT(path) DO UPDATE SET body=excluded.body', (str(path), packed(value)))
            if path.name == 'batch.json':
                db.execute('UPDATE root_version SET request_epoch=request_epoch+1 WHERE root=?', (str(path.parent),))
            if path.name == 'context.json':
                db.execute('UPDATE root_version SET context_epoch=context_epoch+1 WHERE root=?', (str(path.parent),))
                # Context submissions are wide operations until consumer-specific
                # invalidation is connected. Never leave another episode's cache stale.
                for state_path, in db.execute("SELECT path FROM records WHERE path LIKE '%/state.json'").fetchall():
                    store.rebuild(load(Path(state_path).parent)[0])

def private_mkdir(path):
    path = Path(path)
    if path.is_dir():
        return
    private_mkdir(path.parent)
    path.mkdir(mode=0o700, exist_ok=True)


def directory_binding(path):
    parent = Path(path).absolute().parent
    while not parent.exists():
        parent = parent.parent
    info = parent.stat()
    return {'path': str(parent.resolve()), 'identity': [info.st_dev, info.st_ino]}


def check_directory(binding):
    path = Path(binding['path'])
    require(str(path.resolve()) == binding['path'] and path.is_dir(), 'Write directory changed')
    info = path.stat()
    require([info.st_dev, info.st_ino] == binding['identity'], 'Write directory changed')


def protected_paths(s):
    work = Path(s['work'])
    root = store_root(work / 'state.json')
    paths = {s['media'], str(database(root)), str(root / '.lock'),
             s['context_path'], str(work / 'item.json'), str(work / 'base.srt')}
    for source in s['sources'].values():
        paths.update(source.get(key) for key in ('path', 'original_path'))
        # Bounded patches explicitly authorize replacing their deployed baseline;
        # the preserved baseline snapshot remains protected.
        origin = source.get('origin_path')
        baseline_target = (s.get('patch') and source.get('origin') == 'baseline' and
                           origin and Path(origin).resolve() == Path(s['target']).resolve())
        if origin and not baseline_target:
            paths.add(origin)
    paths.update(e.get('path') for e in context(s)['evidence'].values())
    return {Path(p).resolve() for p in paths if p}


def check_destination(path, protected):
    path = Path(path).absolute()
    require(not path.is_symlink(), 'Destination cannot be a symlink')
    resolved = path.resolve()
    require(resolved == path, 'Write directory changed')
    for source in protected:
        require(resolved != source and not (path.exists() and source.exists() and
                os.path.samefile(path, source)), 'Destination aliases a protected file')


def atomic(path, content, guard=None):
    path = Path(path).absolute()
    require(not path.is_symlink(), 'Refuse symlink write destination')
    private_mkdir(path.parent)
    # Anchor each directory lookup; never follow a swapped directory symlink.
    parent = path.parent.resolve()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    name = None
    try:
        for component in parent.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        require(Path(os.path.realpath(path.parent)) == parent, 'Write directory changed')
        import secrets
        name = '.' + path.name + '.' + secrets.token_hex(12)
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            st = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            require(stat.S_ISREG(st.st_mode), 'Destination must be a regular file')
        except FileNotFoundError:
            pass
        require(path.parent.resolve() == parent and
                os.stat(parent).st_ino == os.fstat(fd).st_ino and
                os.stat(parent).st_dev == os.fstat(fd).st_dev, 'Write directory changed')
        if guard is not None:
            guard()
        os.replace(name, path.name, src_dir_fd=fd, dst_dir_fd=fd)
        name = None
        os.fsync(fd)
    finally:
        if name is not None:
            os.unlink(name, dir_fd=fd)
        os.close(fd)

MAX_INPUT = 64 * 1024 * 1024
PROBE_TIMEOUT = 120
COMMAND_TIMEOUT = 300
MAX_STDERR = 1024 * 1024

def input_bytes(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_INPUT + 1)
    require(len(raw) <= MAX_INPUT, 'Input exceeds 64 MiB; nothing was truncated')
    return raw


def ms(value):
    m = STAMP.fullmatch(value)
    require(m, 'Invalid SRT timestamp: ' + value)
    h, minute, second, milli = map(int, m.groups())
    require(minute < 60 and second < 60, 'Invalid timestamp range')
    return ((h * 60 + minute) * 60 + second) * 1000 + milli

def stamp(value):
    h, rest = divmod(value, 3600000)
    minute, rest = divmod(rest, 60000)
    second, milli = divmod(rest, 1000)
    return f'{h:02}:{minute:02}:{second:02},{milli:03}'

def parse(raw, prefix):
    try:
        text = raw.decode('utf-8-sig').replace('\r\n', '\n')
    except UnicodeError as e:
        raise Error('Source must be explicitly converted to UTF-8 before selection') from e
    require('\r' not in text and '\x00' not in text, 'Unsupported control characters')
    result = []
    blocks = re.split(r'\n[ \t]*\n', text.strip('\n'))
    for n, block in enumerate(blocks, 1):
        lines = block.split('\n')
        require(len(lines) >= 3 and lines[0].isdigit(), f'Malformed SRT block {n}; nothing was discarded')
        match = TIME.fullmatch(lines[1])
        require(match, f'Malformed timing in block {n}')
        cue = {'id': f'{prefix}{n}', 'start': ms(match[1]), 'end': ms(match[2]), 'text': '\n'.join(lines[2:])}
        require(cue['text'].strip(), f'Empty cue {n}')
        require(cue['start'] >= 0 and cue['end'] > cue['start'], f'Invalid interval {n}')
        result.append(cue)
    require(result, 'Empty subtitle')
    return result

def render(cues, duration):
    ordered = list(cues)
    require(len({c['id'] for c in ordered}) == len(ordered), 'Duplicate cue IDs')
    parts = []
    for i, c in enumerate(ordered, 1):
        require(type(c['start']) is int and type(c['end']) is int, 'Times must be integer milliseconds')
        require(0 <= c['start'] < c['end'] <= duration, 'Cue outside media duration: ' + c['id'])
        require(isinstance(c['text'], str) and c['text'].strip(), 'Empty text: ' + c['id'])
        require(not any(x in c['text'] for x in ('\r', '\x00', '\n\n')), 'Unserializable SRT text: ' + c['id'])
        parts.append(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}\n")
    require(parts, 'No Korean output')
    raw = ('\n'.join(parts)).encode('utf-8')
    parse(raw, 'K')
    return raw

def command(args):
    args = list(args)
    tool = Path(args[0]).name
    if tool in {'ffmpeg', 'ffprobe'}:
        args[1:1] = ['-protocol_whitelist', 'file,pipe']
    timeout = PROBE_TIMEOUT if tool == 'ffprobe' else COMMAND_TIMEOUT
    try:
        with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, start_new_session=True) as proc:
            buffers = {proc.stdout: bytearray(), proc.stderr: bytearray()}
            deadline = time.monotonic() + timeout
            try:
                with selectors.DefaultSelector() as poll:
                    for stream in buffers:
                        poll.register(stream, selectors.EVENT_READ)
                    while poll.get_map():
                        require(time.monotonic() < deadline, 'Command timed out')
                        for key, _ in poll.select(min(0.1, max(0, deadline - time.monotonic()))):
                            chunk = os.read(key.fileobj.fileno(), 65536)
                            if not chunk:
                                poll.unregister(key.fileobj)
                                continue
                            buffer = buffers[key.fileobj]
                            buffer.extend(chunk)
                            limit = MAX_INPUT if key.fileobj is proc.stdout else MAX_STDERR
                            require(len(buffer) <= limit, 'Command output limit exceeded')
                require(proc.wait(timeout=max(.01, deadline-time.monotonic())) == 0,
                        'Command failed: ' + tool)
                return bytes(buffers[proc.stdout])
            except BaseException:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                raise
    except (OSError, subprocess.SubprocessError) as e:
        raise Error('Command failed: ' + tool) from e


def inspect_one(media):
    media = Path(media).resolve()
    before = identity(media)
    try:
        probe = json.loads(command(['ffprobe', '-v', 'error', '-select_streams', 's',
            '-show_entries', 'format=duration:stream=index,codec_type,codec_name:stream_tags=language,title:stream_disposition=default,forced,hearing_impaired',
            '-of', 'json', str(media)]))
        require(identity(media) == before, 'Media changed during inspection')
    except (Error, ValueError, OSError) as e:
        return {'media': str(media), 'decision': 'held', 'reason': 'probe_failed', 'error': str(e)}
    tracks = [s for s in probe.get('streams', []) if s.get('codec_type') == 'subtitle']
    korean, uncertain = [], []
    for track in tracks:
        tags = {k.lower(): str(v) for k, v in track.get('tags', {}).items()}
        language = tags.get('language', '').strip().lower().replace('_', '-')
        is_korean = language in {'ko', 'kor'} or language.startswith(('ko-', 'kor-'))
        korean_title = bool(re.search(r'한국어|한글|조선말|\bkorean\b', tags.get('title', ''), re.I))
        if is_korean and re.search(r'\benglish\b|영어', tags.get('title', ''), re.I):
            uncertain.append(track)
        elif is_korean:
            korean.append(track)
        elif korean_title:
            uncertain.append(track)
    return {'media': str(media), 'media_identity': before,
            'decision': 'skipped' if korean else 'held' if uncertain else 'ready',
            'reason': 'embedded_korean_present' if korean else 'korean_metadata_conflict' if uncertain else 'no_tagged_embedded_korean',
            'korean_tracks': korean, 'uncertain_tracks': uncertain, '_probe': probe, '_tracks': tracks}


def media_items(media):
    media = Path(media).resolve()
    if media.is_file():
        return [media]
    require(media.is_dir(), 'Name a media file or directory')
    items = [p for p in sorted(media.iterdir()) if p.suffix.lower() in MEDIA and p.is_file()]
    require(items, 'No media in this directory; explicitly name a season directory or media')
    return items


def inspection_result(row):
    return {k: v for k, v in row.items() if not k.startswith('_')}


def inspect_media(media):
    return {'items': [inspection_result(inspect_one(p)) for p in media_items(media)]}


def prepare_one(media, work, target=None, shared=None, base=None, scope=None,
                intent='collect', authorization=None, inspection=None):
    media, work = Path(media).resolve(), Path(work).resolve()
    require(not record_exists(work / 'state.json'), 'Project already exists; use status')
    require(intent in {'collect', 'audit'}, 'Use collect or explicitly authorized audit intent')
    if base is not None and scope is not None:
        authorization = json.loads(input_bytes(scope)).get('authorization')
        intent = 'audit'
    require(intent != 'audit' or nonempty(authorization), 'Audit needs the explicit user request in --authorization')
    inspected = inspection if inspection is not None else inspect_one(media)
    if inspected['decision'] == 'held' or (inspected['decision'] == 'skipped' and intent == 'collect'):
        return inspection_result(inspected)
    require(inspected['media'] == str(media) and identity(media) == inspected['media_identity'], 'Media changed since inspection')
    probe = inspected['_probe']
    duration = round(float(probe['format']['duration']) * 1000)
    require(duration > 0, 'Unknown media duration')
    output = Path(target).absolute() if target else media.with_suffix('.ko.srt')
    require(not output.is_symlink(), 'Output cannot be a symlink')
    output = output.resolve()
    require(not (output.exists() and os.path.samefile(output, media)), 'Output aliases the media')
    require(output != media, 'Output cannot be the media')
    tracks = inspected['_tracks']
    require((base is None) == (scope is None), '--base and --scope must be used together')
    state = {'contract': CONTRACT, 'engine': ENGINE_HASH, 'media': str(media), 'media_identity': inspected['media_identity'], 'duration': duration,
             'admission': {'intent': intent, 'authorization': authorization, 'inspection': inspection_result(inspected)},
             'target': str(output), 'target_hash': filehash(output),
             'target_directory': directory_binding(output), 'tracks': tracks,
             'sidecars': [str(p) for p in sorted(media.parent.glob(media.stem + '*.srt'))],
             'context_path': str(Path(shared).resolve()) if shared else str(work / 'context.json'),
             'work': str(work), 'author': None, 'sealed': None, 'revision': 0,
             'sources': {}, 'outputs': {}, 'supplements': {}, 'author_readings': {},
             'readings': {}, 'reading_workers': {}, 'reviewer': None,
             'findings': {}, 'approval': None, 'history': [], 'receipts': {},
             'metrics': {'view_calls': 0, 'presented_bytes': 0, 'cue_presentations': 0,
                         'repeat_presentations': 0, 'metadata_bytes': 0, 'metadata_calls': 0},
             'presented': {}}
    patch_raw = None
    if base is not None:
        base = Path(base).resolve()
        require(base.is_file() and base != media and base != work / 'ko.selected.srt', 'Invalid patch base')
        patch_raw = input_bytes(base)
        cues = parse(patch_raw, 'K')
        record = json.loads(input_bytes(scope))
        require(isinstance(record, dict) and nonempty(record.get('reason')) and
                nonempty(record.get('authorization')) and isinstance(record.get('changes'), list) and record['changes'],
                'Patch scope needs reason, authorization and explicit changes')
        ids = set(expand(record.get('ids', [])))
        require(ids and all(re.fullmatch(r'[EK][1-9]\d*', i) for i in ids), 'Patch scope needs E/K IDs')
        known = {c['id']: c for c in cues}
        changed = set()
        for change in record['changes']:
            require(set(change) == {'id', 'before', 'after'} and change['id'] in known and
                    change['id'] in ids and change['id'] not in changed and
                    change['before'] == known[change['id']]['text'] and nonempty(change['after']) and
                    change['before'] != change['after'],
                    'Patch changes need unique scoped K ID, exact before/after and decision references')
            changed.add(change['id'])
        require(all(i in known for i in ids if i.startswith('K')), 'Unknown patch Korean ID')
        state['patch'] = {'base_path': str(base), 'base_hash': digest(patch_raw),
                          'snapshot': str(work / 'base.srt'), 'scope': record}
        # This is a deployed baseline, never relabelled a collected translation.
        state['sources']['ko'] = {'language': 'ko', 'origin': 'baseline', 'path': str(work / 'ko.selected.srt'),
                                 'hash': digest(patch_raw), 'origin_path': str(base), 'cues': cues,
                                 'provenance': {'kind': 'deployed_baseline', 'authorization': record['authorization']},
                                 'presentation_evidence': {'checked': 'Exact baseline snapshot; inspect scoped text before learning.',
                                     'preserved': 'Output preserves bytes outside approved text changes.', 'limitations': [], 'resolved': True}}
    require(output != work and work not in output.parents, 'Output cannot be inside work storage')
    private_mkdir(work)
    root = Path(shared).resolve().parent if shared else work
    with transaction(root, create=True):
        pass
    if root != work:
        atomic(work / 'item.json', packed({'root': str(root), 'item': str(work)}))
    if patch_raw is not None:
        atomic(work / 'base.srt', patch_raw)
        atomic(work / 'ko.selected.srt', patch_raw)
    write_record(work / 'state.json', packed(state))
    return {'project': str(work), 'media': str(media), 'tracks': tracks, 'sidecars': state['sidecars']}

def prepare(args):
    media = Path(args.media).resolve()
    intent, authorization = getattr(args, 'intent', 'collect'), getattr(args, 'authorization', None)
    if media.is_file():
        return prepare_one(media, args.work, args.target, base=getattr(args, 'base', None), scope=getattr(args, 'scope', None), intent=intent, authorization=authorization)
    require(not getattr(args, 'base', None) and not getattr(args, 'scope', None), 'Patch prepare names one media item')
    require(media.is_dir() and not args.target, 'Directory prepare cannot use --target')
    # Do not recurse into extras or old work directories. Explicitly name extras when wanted.
    items = media_items(media)
    require(intent in {'collect', 'audit'} and (intent != 'audit' or nonempty(authorization)), 'Audit needs the explicit user request in --authorization')
    inspected = [inspect_one(item) for item in items]
    excluded = [inspection_result(x) for x in inspected if x['decision'] == 'held' or (x['decision'] == 'skipped' and intent == 'collect')]
    active = [(i, item, row) for i, (item, row) in enumerate(zip(items, inspected), 1)
              if row['decision'] == 'ready' or (row['decision'] == 'skipped' and intent == 'audit')]
    # Inspect all requested items before mutation; each prepared item reuses its probe.
    require(all(identity(item) == row['media_identity'] for _, item, row in active), 'Media changed since inspection')
    require(all(float(row['_probe'].get('format', {}).get('duration', 0)) > 0 for _, _, row in active), 'Unknown media duration')
    require(all(not record_exists(Path(args.work) / f'item-{i:03}' / 'state.json') for i, _, _ in active), 'Project already exists; use status')
    result = []
    if active:
        with transaction(Path(args.work), create=True):
            result = [prepare_one(item, Path(args.work) / f'item-{i:03}', shared=Path(args.work) / 'context.json',
                                  intent=intent, authorization=authorization, inspection=row) for i, item, row in active]
            if result:
                write_record(Path(args.work) / 'batch.json', packed({'contract': CONTRACT, 'items': [x['project'] for x in result], 'excluded': excluded}))
    return {'items': result, 'excluded': excluded, 'prepared': len(result),
            'skipped': sum(x['decision'] == 'skipped' for x in excluded), 'held': sum(x['decision'] == 'held' for x in excluded)}

def input_evidence(s, ctx):
    required = {key[9:] for key in author_metadata(s, ctx) if key.startswith('evidence:')}
    facts = {key for unit in s.get('outputs', {}).values() for key in unit.get('facts', [])}
    required.update(ref for unit in s.get('outputs', {}).values() for ref in lookup_refs(unit))
    required.update(supp['event'] for supp in s.get('supplements', {}).values() if supp.get('event'))
    required.update(ref for key in facts for ref in ctx['facts'].get(key, {}).get('evidence', []))
    return {key: value for key, value in ctx['evidence'].items() if key in required}


def verify_inputs(s, evidence=None):
    if evidence is None:
        with transaction(store_root(Path(s['work']) / 'state.json')) as db:
            ctx = domain_store.Store(db, globals()).context_header(s)
        evidence = input_evidence(s, ctx)
    for evidence in evidence.values():
        if evidence.get('path'):
            require(filehash(evidence['path']) == evidence.get('hash'), 'Evidence file changed; inspect and revise its judgment')
    require(identity(s['media']) == s['media_identity'], 'Media changed since prepare')
    if s.get('patch'):
        p = s['patch']
        require(filehash(p['snapshot']) == p['base_hash'], 'Patch baseline snapshot changed')
        if p['base_path'] != s['target']:
            require(filehash(p['base_path']) == p['base_hash'], 'Patch original baseline changed')
    for source in s['sources'].values():
        require(filehash(source['path']) == source['hash'], 'Selected source changed: ' + source['path'])
        origin = source.get('origin_path')
        if source['origin'] != 'embedded' and origin:
            require(origin != s['target'] or s.get('patch'), 'Collected original cannot also be output target')
            if origin != s['target']:
                require(filehash(origin) == source['hash'], 'Original collected source changed: ' + origin)
        if source.get('original_path'):
            require(filehash(source['original_path']) == source['original_hash'], 'Original presentation source changed')

def all_ids(s):
    return {c['id'] for source in s['sources'].values() for c in source['cues']}

def nonempty(value):
    return isinstance(value, str) and bool(value.strip())

def candidate_id(candidate):
    require(candidate.get('origin') in {'embedded', 'external', 'sidecar'}, 'Invalid candidate origin')
    if candidate['origin'] == 'embedded':
        require(type(candidate.get('stream')) is int, 'Candidate needs absolute stream index')
        return 'stream:' + str(candidate['stream'])
    return 'file:' + str(Path(candidate['path']).resolve())

def english_selection(s, p):
    require(p.get('profile') in {'sdh', 'ordinary'} and isinstance(p.get('selection_reason'), str) and
            p['selection_reason'].strip(), 'English profile and selection reason required')
    assessments = p.get('candidate_assessments')
    require(isinstance(assessments, list) and assessments, 'Assess the selected and available English candidates')
    assessed = {}
    for candidate in assessments:
        key = candidate_id(candidate)
        require(key not in assessed and type(candidate.get('usable')) is bool and
                candidate.get('profile') in {'sdh', 'ordinary', 'unknown'} and
                isinstance(candidate.get('evidence'), str) and candidate['evidence'].strip(),
                'Candidate needs unique identity, profile, usability and concrete evidence')
        require(not candidate['usable'] or candidate['profile'] != 'unknown', 'Inspect usable candidate profile')
        if candidate['origin'] == 'embedded':
            tracks = [t for t in s['tracks'] if t['index'] == candidate['stream']]
            require(len(tracks) == 1, 'Unknown assessed embedded track')
            require(not candidate['usable'] or (tracks[0].get('codec_name') in TEXT_CODECS and
                    not tracks[0].get('disposition', {}).get('forced')), 'Forced/image candidate cannot be usable')
        assessed[key] = candidate
    expected = {'stream:' + str(t['index']) for t in s['tracks'] if t.get('codec_name') in TEXT_CODECS and
                t.get('tags', {}).get('language', '').lower() in {'eng', 'en'} and
                not t.get('disposition', {}).get('forced')}
    require(expected <= set(assessed), 'Assess every apparent embedded English text candidate')
    selected = assessed.get(candidate_id(p))
    require(selected and selected['usable'] and selected['profile'] == p['profile'],
            'Selected English candidate must have a matching usable assessment')
    def rank(candidate):
        return (0 if candidate['origin'] == 'embedded' else 2) + (candidate['profile'] != 'sdh')
    require(not any(c['usable'] and rank(c) < rank(selected) for c in assessments),
            'Higher-priority usable English candidate exists; document actual defects before choosing lower')
    if p['origin'] != 'embedded':
        require(p.get('fallback_reason'), 'Explain why no embedded English is usable')

def presentation_evidence(p):
    evidence = p.get('presentation_evidence')
    require(isinstance(evidence, dict) and all(isinstance(evidence.get(k), str) and evidence[k].strip()
            for k in ('checked', 'preserved')) and isinstance(evidence.get('limitations'), list) and
            all(isinstance(x, str) and x.strip() for x in evidence['limitations']),
            'Presentation evidence needs checked scope, preserved information and limitations')
    require(evidence.get('resolved') is True, 'Unresolved presentation loss; hold this work before selection')

def source_selection(s, p, work):
    locked = bool(s['outputs'] or s['author_readings'] or study_record(s))
    previous = s['sources'].get(p.get('language'))
    if locked:
        require(previous and p.get('origin') == previous['origin'], 'Original source is fixed; prepare fresh for another source')
        if p['origin'] == 'embedded':
            require(p.get('stream') == previous.get('stream'), 'Original embedded stream is fixed')
        else:
            require(str(Path(p['path']).resolve()) == previous['origin_path'] and filehash(p['path']) == previous['hash'], 'Original source bytes/path are fixed')
        require(p.get('original_path') == previous.get('original_path'), 'Original presentation authority is fixed')
    require(p['language'] in {'en', 'ko'}, 'Invalid language')
    lang = p['language']
    require(p.get('verified_language') is True and p.get('complete') is True and p.get('release_match') is True and p.get('evidence'), 'Source language/completeness/release evidence required')
    require(p.get('automatic') is False, 'Automatic/unverified captions cannot be selected')
    require(p.get('provenance') and p.get('origin') in {'embedded', 'external', 'sidecar'}, 'Source provenance required')
    presentation_evidence(p)
    if lang == 'ko':
        require(not s.get('patch'), 'Patch baseline is fixed by prepare; do not relabel or replace it as collected Korean')
        require(p.get('ordinary') is True and p.get('translator_status') in {'credited', 'uncredited', 'unknown'}, 'Ordinary Korean original required; SDH-only means hold')
        require(p.get('generated') is False, 'Generated local output is not a collected translator original')
    if lang == 'en':
        english_selection(s, p)
    if p['origin'] == 'embedded':
        matches = [t for t in s['tracks'] if t['index'] == p['stream']]
        require(len(matches) == 1 and matches[0]['codec_name'] in TEXT_CODECS and not matches[0].get('disposition', {}).get('forced'), 'Embedded full text track required')
        if lang == 'ko':
            require(matches[0]['codec_name'] == 'subrip', 'Embedded Korean styling conversion is not presentation-faithful; select verified SRT or hold')
        path = work / f'{lang}.selected.srt'
        raw = input_bytes(previous['path']) if locked else command(['ffmpeg', '-v', 'error', '-copyts', '-i', s['media'], '-map', f"0:{p['stream']}", '-f', 'srt', '-'])
        cues = parse(raw, 'E' if lang == 'en' else 'K')
    else:
        path = Path(p['path']).resolve()
        raw = input_bytes(path)
        cues = parse(raw, 'E' if lang == 'en' else 'K')
    origin_path = str(path)
    require(origin_path != s['target'], 'Collected original cannot also be output target')
    path = work / f'{lang}.selected.srt'
    require(str(path) != s['target'], 'Output cannot overwrite a selected source snapshot')
    if lang == 'ko':
        require(any(re.search('[가-힣]', c['text']) for c in cues), 'No Hangul in Korean source')
    require(all(c['end'] <= s['duration'] for c in cues), 'Source cue exceeds video duration; resolve source before selection')
    details = {'original_reference': {'media': s['media'], 'stream': p['stream'], 'codec': matches[0]['codec_name']}
               if p['origin'] == 'embedded' else {'path': origin_path}}
    if p.get('original_path'):
        require(p['origin'] != 'embedded', 'Embedded originals are identified by media and stream, not original_path')
        original = Path(p['original_path']).resolve()
        require(original.is_file() and str(original) not in {s['target'], str(path)}, 'Invalid original presentation path')
        details.update(original_path=str(original), original_hash=filehash(original))
        details['original_reference'] = {'path': str(original)}
    path = work / f'{lang}.{digest(raw)}.srt'
    atomic(path, raw)
    s['sources'][lang] = {**p, **details, 'path': str(path), 'hash': digest(raw), 'origin_path': origin_path, 'cues': cues}
    if not locked:
        s['sealed'] = None

def expand(ids):
    result = []
    require(isinstance(ids, list), 'IDs must be a list')
    for item in ids:
        require(isinstance(item, str), 'ID must be text')
        m = re.fullmatch(r'([EKOS])([1-9]\d*)-\1([1-9]\d*)', item)
        if m:
            prefix, first, last = m.groups()
            require(int(first) <= int(last) and int(last) - int(first) < 100000, 'Invalid ID range')
            result.extend(f'{prefix}{n}' for n in range(int(first), int(last) + 1))
        else:
            require(re.fullmatch(r'[EKOS][1-9]\d*', item), 'Invalid cue ID: ' + item)
            result.append(item)
    return list(dict.fromkeys(result))

def compact(ids):
    ids = expand(list(ids))
    result = []
    for prefix in ('E', 'K', 'O', 'S'):
        numbers = sorted({int(i[1:]) for i in ids if i.startswith(prefix)})
        if not numbers:
            continue
        first = last = numbers[0]
        for n in numbers[1:] + [None]:
            if n is not None and n == last + 1:
                last = n
            else:
                result.append(f'{prefix}{first}' if first == last else f'{prefix}{first}-{prefix}{last}')
                first = last = n
    return result

def source_map(s):
    return {c['id']: c for v in s['sources'].values() for c in v['cues']}

def shape(text):
    """Mechanical layout, tag order and whole/partial emphasis; meaning needs reading.

    Korean can place a particle after a marked name where English placed its
    article before it. Text-before/text-after flags would enforce English grammar.
    """
    result, active = [], []
    for line in text.split('\n'):
        tags, coverage, cursor = [], [False, False], 0
        for m in re.finditer(r'<[^>]*>|\{[^}]*\}', line):
            if re.search(r'\w', line[cursor:m.start()]):
                coverage[bool(active)] = True
            tags.append(m[0])
            tag = re.match(r'<(/?)([A-Za-z]+)', m[0])
            if tag:
                close, name = tag.groups()
                if close:
                    if active: active.pop()
                else:
                    active.append(name.lower())
            cursor = m.end()
        if re.search(r'\w', line[cursor:]):
            coverage[bool(active)] = True
        result.append((bool(re.match(r'\s*[-–—]', line)), tags, tuple(coverage),
                       re.findall(r'[?!…♪♫]', re.sub(r'<[^>]*>|\{[^}]*\}', '', line))))
    return result

def markup_valid(text):
    stack = []
    for match in re.finditer(r'<(/?)([A-Za-z]+)(?:\s[^>]*)?>', text):
        close, tag = match.groups()
        tag = tag.lower()
        require(tag in {'i', 'b', 'u', 'font'}, 'Unsupported SRT tag; inspect original and hold')
        if close:
            require(stack and stack.pop() == tag, 'Unbalanced SRT markup')
        else:
            stack.append(tag)
    require(not stack, 'Unbalanced SRT markup')

def artist_lookup(part):
    identity_info = part.get('identity', {})
    require(isinstance(identity_info, dict) and identity_info and set(identity_info) <= {'title', 'artist'} and
            all(nonempty(v) for v in identity_info.values()), 'Track identity needs literal source title/artist')
    lookup = part.get('artist_lookup')
    if 'artist' not in identity_info or lookup is not None:
        require(isinstance(lookup, dict) and lookup.get('outcome') in {'verified', 'not_found', 'unavailable'} and
                nonempty(lookup.get('evidence')) and isinstance(lookup.get('sources'), list) and lookup['sources'],
                'Missing source artist requires official OST lookup and recorded outcome')
        for ref in lookup['sources']:
            require(ref.get('kind') in {'official_ost', 'official_film_credits'} and
                    nonempty(ref.get('url')) and ref['url'].startswith('https://') and
                    nonempty(ref.get('checked_at')) and nonempty(ref.get('evidence')) and
                    ref.get('access') in {'body', 'unavailable'}, 'Lookup requires dated official source/access evidence')
        require(any(x['kind'] == 'official_ost' for x in lookup['sources']), 'Inspect official OST first')
        if lookup['outcome'] == 'verified':
            require(nonempty(lookup.get('artist')) and nonempty(lookup.get('version_evidence')) and
                    any(x['access'] == 'body' for x in lookup['sources']) and 'artist' not in identity_info,
                    'Artist supplement requires actual-version evidence and an absent source field')
        else:
            require('artist' not in lookup and 'version_evidence' not in lookup and
                    (lookup['outcome'] != 'not_found' or any(x['access'] == 'body' for x in lookup['sources'])),
                    'Unverified attribution cannot supply performer; not_found requires body access')
    return lookup

def classified_parts(cue, unit):
    kinds = {'dialogue', 'lyrics', 'track_info', 'nonverbal', 'speaker'}
    parts = unit.get('parts', {}).get(cue['id'])
    if parts is None:
        parts = [{'span': [0, len(cue['text'])], 'kind': unit['kind'],
                  **{k: unit[k] for k in ('identity', 'artist_lookup') if k in unit}}]
    require(isinstance(parts, list) and parts, 'Mixed cue requires explicit source parts')
    covered, previous = set(), 0
    for part in parts:
        require(isinstance(part, dict) and part.get('kind') in kinds and
                set(part) <= {'span', 'kind', 'identity', 'artist_lookup'}, 'Invalid source part')
        span = part.get('span')
        require(isinstance(span, list) and len(span) == 2 and all(type(x) is int for x in span) and
                previous <= span[0] < span[1] <= len(cue['text']), 'Source parts must be ordered nonoverlapping valid spans')
        previous = span[1]
        require(cue['text'][span[0]:span[1]].strip(), 'Source part cannot be whitespace only')
        covered.update(range(*span))
        if part['kind'] == 'track_info':
            artist_lookup(part)
            raw = re.sub(r'\s+', ' ', cue['text'][span[0]:span[1]])
            require(all(re.sub(r'\s+', ' ', v) in raw for v in part['identity'].values()),
                    'Song identity must occur literally in exact source span')
    require(all(n in covered for n, c in enumerate(cue['text']) if not c.isspace()),
            'Classify all nonwhitespace source text; omission is not an unclassified gap')
    return parts

def projection(cue, unit):
    text = list(cue['text'])
    parts = classified_parts(cue, unit)
    for part in parts:
        if part['kind'] in {'nonverbal', 'speaker'}:
            for n in range(*part['span']):
                if text[n] != '\n':
                    text[n] = ''
    lines = []
    for line in ''.join(text).split('\n'):
        line = line.strip()
        # Empty markup/standalone speaker dashes belong only to excluded material.
        previous = None
        while previous != line:
            previous = line
            line = re.sub(r'<(i|b|u|font)(?:\s[^>]*)?>\s*</\1>', '', line).strip()
        if line and re.sub(r'<[^>]*>|\{[^}]*\}', '', line).strip(' -–—'):
            lines.append(line)
    projected = '\n'.join(lines)
    if projected:
        markup_valid(projected)
    return projected, parts

def output_raw(s, final=None):
    final = output_complete(s) if final is None else final
    if not s.get('patch'):
        return render(final, s['duration'])
    original = s['sources']['ko']['cues']
    by_id = {c['id']: c for c in original}
    used, replacements = set(), {}
    for unit in s['outputs'].values():
        for en in expand(unit['en']):
            out = next((c for c in final if c['id'] == 'O' + en[1:]), None)
            require(out is not None, 'Patch cannot remove source output; prepare full work')
            matches = [by_id[k] for k in expand(unit['ko']) if (by_id[k]['start'], by_id[k]['end']) == (out['start'], out['end'])]
            require(len(matches) == 1 and matches[0]['id'] not in used and shape(matches[0]['text']) == shape(out['text']),
                    'Patch baseline does not match SSOT structure; prepare full work')
            used.add(matches[0]['id']); replacements[matches[0]['id']] = out['text']
    require(not s['supplements'], 'Patch cannot insert supplementary cues; prepare full work')
    allowed = {c['id']: c for c in s['patch']['scope']['changes']}
    for key, text in replacements.items():
        require(text == (allowed[key]['after'] if key in allowed else by_id[key]['text']), 'Patch output differs from authorized exact changes')
    require(set(allowed) <= set(replacements), 'Declared patch changes missing')
    raw = input_bytes(s['patch']['snapshot'])
    require(digest(raw) == s['patch']['base_hash'], 'Patch snapshot changed')
    text = raw.decode('utf-8-sig')
    pattern = re.compile(r'^\d+\r?\n[^\r\n]+ --> [^\r\n]+\r?\n(?P<text>.*?)(?=\r?\n[ \t]*\r?\n|\r?\n?\Z)', re.M | re.S)
    matches = list(pattern.finditer(text))
    require(len(matches) == len(original), 'Cannot identify baseline text spans')
    chunks, cursor = [], 0
    for match, cue in zip(matches, original):
        require(match['text'].replace('\r\n', '\n') == cue['text'], 'Baseline serialization mismatch')
        chunks.append(text[cursor:match.start('text')])
        newline = '\r\n' if '\r\n' in match[0] else '\n'
        chunks.append(replacements[cue['id']].replace('\n', newline) if cue['id'] in allowed else match['text'])
        cursor = match.end('text')
    chunks.append(text[cursor:])
    result = (b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'') + ''.join(chunks).encode('utf-8')
    parse(result, 'K')
    return result

def now():
    return command(['date', '-u', '+%Y-%m-%dT%H:%M:%SZ']).decode().strip()


def load(work):
    path = Path(work).resolve() / 'state.json'
    require(record_exists(path), 'Run prepare first')
    s = read_record(path)
    count = s.pop('_history_count', 0)
    if database(store_root(path)).is_file():
        s['history'] = History(path, count)
    require(s.get('contract') == CONTRACT, 'Old contract; prepare fresh, without approval migration')
    require(s.get('engine') == ENGINE_HASH, 'Engine changed; prepare fresh without approval migration')
    return s, path


def context(s):
    p = Path(s['context_path'])
    return read_record(p) if record_exists(p) else {'facts': {}, 'evidence': {}, 'revision': 0, 'global_revision': 0, 'touches': {}}


def request_manifest(s):
    path = Path(s['context_path']).with_name('batch.json')
    if not record_exists(path):
        return {'contract': CONTRACT, 'items': [s['work']], 'excluded': []}
    manifest = read_record(path)
    require(manifest.get('contract') == CONTRACT, 'Wrong batch contract')
    manifest['items'] = [str(Path(work).resolve()) for work in manifest['items']]
    require(s['work'] in manifest['items'], 'Current item is outside requested batch')
    return manifest


def source_binding(s):
    return {'media': s['media'], 'media_identity': s['media_identity'],
            'sources': {k: {'hash': v['hash'], 'original_reference': v.get('original_reference'),
                            'original_hash': v.get('original_hash')} for k, v in s['sources'].items()},
            'scope': s.get('patch', {}).get('scope')}


def collection(s):
    key = s['context_path']
    if _OPERATION is not None and key in _OPERATION['collection']:
        return _OPERATION['collection'][key]
    root = store_root(Path(s['work']) / 'state.json')
    with transaction(root) as db:
        store = domain_store.Store(db, globals())
        binding = store.collection_metadata(s)
        items = [store.header(work) for work in request_manifest(s)['items']]
    result = items, binding
    if _OPERATION is not None:
        _OPERATION['collection'][key] = result
    return result


def study_record(s, c=None):
    c = context(s) if c is None else c
    matches = [dict(v, id=k) for k, v in c.get('studies', {}).items() if s['work'] in v['items']]
    require(len(matches) <= 1, 'Overlapping study groups')
    return matches[0] if matches else None


def study_ready(s, c=None):
    c = context(s) if c is None else c
    record = study_record(s, c)
    require(record and record['complete'], 'Complete shared work study before correction')
    require(record['request'] == digest(request_manifest(s)) and record['items'].get(s['work']) == source_binding(s),
            'Study source/scope binding changed; prepare fresh')
    facts = [c['facts'][k] for k in record['facts'] if k in c['facts']]
    require(facts and not any(f['status'] == 'open' and f.get('material', True) for f in facts),
            'Resolve material study gaps before correction')
    return record


def study_view(s, args):
    require(nonempty(args.worker), 'Use the actual study worker')
    items, binding = collection(s)
    remaining = [item for item in items if not study_record(item) or not study_record(item)['complete']]
    require(remaining, 'Requested works already studied; update factual context instead')
    r = registry(s)
    c = domain_store.Store(_CONNECTIONS[str(store_root(Path(s['work']) / 'state.json').resolve())], globals()).context_header(s)
    issued = time.time()
    key = digest({'worker': args.worker, 'collection': binding, 'revision': c['revision'], 'issued': issued})[:20]
    r.setdefault('study_receipts', {})[key] = {'worker': args.worker, 'collection': binding,
        'base': c['revision'], 'items': [x['work'] for x in remaining], 'issued': issued, 'started_at': now()}
    result = {'mode': 'study', 'packet': key,
        'items': [{'work': x['work'], 'media': x['media'],
                   'sources': {k: {a: b for a, b in v.items() if a != 'cues'} for k, v in x['sources'].items()}}
                  for x in remaining],
        'submit': {'kind': 'study', 'packet': key, 'works': [{'identity': {'title': '', 'year': '', 'season': ''},
            'items': [x['work'] for x in remaining], 'facts': {}, 'evidence': {}, 'decision': None, 'unresolved': []}]}}
    metrics = r.setdefault('study_metrics', {'view_calls': 0, 'presented_bytes': 0, 'submissions': 0, 'packet_elapsed_ms': 0})
    metrics['view_calls'] += 1
    metrics['presented_bytes'] += len(packed(result))
    save_registry(s, r)
    return result


def submit_study(s, p):
    require(set(p) == {'kind', 'packet', 'works'} and isinstance(p['works'], list) and p['works'],
            'Use generated study packet and actual work facts/evidence')
    items, binding = collection(s)
    by_work = {x['work']: x for x in items}
    r = registry(s)
    c = domain_store.Store(_CONNECTIONS[str(store_root(Path(s['work']) / 'state.json').resolve())], globals()).context_header(s)
    receipt = r.get('study_receipts', {}).get(p['packet'])
    require(receipt and receipt['collection'] == binding and receipt['base'] == c['revision'], 'Stale study packet')
    seen, facts, evidence, studies = set(), {}, {}, {}
    for number, group in enumerate(p['works'], 1):
        require(set(group) == {'identity', 'items', 'facts', 'evidence', 'decision', 'unresolved'}, 'Use study work template fields')
        require(group['decision'] in {'ready', 'needs_evidence'}, 'The LLM must explicitly judge study readiness')
        require(isinstance(group['identity'], dict) and nonempty(group['identity'].get('title')), 'Identify actual work')
        named = group['items']
        require(isinstance(named, list) and named and len(named) == len(set(named)) and
                set(named) <= set(receipt['items']) and not seen.intersection(named), 'Study each displayed item at most once')
        require(all(not domain_store.Store(_CONNECTIONS[str(store_root(Path(s['work']) / 'state.json').resolve())], globals()).has_correction(x) for x in named),
                'Initial study must precede correction; update existing facts instead')
        require(isinstance(group['facts'], dict) and group['facts'] and isinstance(group['evidence'], dict) and group['evidence'],
                'Actual study facts and inspected supporting evidence required')
        require(any(e.get('access') == 'body' for e in group['evidence'].values() if isinstance(e, dict)),
                'Study requires actually inspected reference material, not access failures alone')
        require(isinstance(group['unresolved'], list) and all(isinstance(q, dict) and nonempty(q.get('question')) and
                type(q.get('material', True)) is bool for q in group['unresolved']), 'Record genuine study questions')
        study_id = f"study-{c['revision'] + 1}-{number}"
        keys = lambda key: study_id + '-' + key
        for key, value in group['evidence'].items():
            require(nonempty(key) and isinstance(value, dict), 'Named study evidence required')
            evidence[keys(key)] = copy.deepcopy(value)
        for key, value in group['facts'].items():
            require(nonempty(key) and isinstance(value, dict) and isinstance(value.get('evidence'), list) and
                    value['evidence'] and set(value['evidence']) <= group['evidence'].keys(), 'Study fact needs actual evidence references')
            facts[keys(key)] = {**copy.deepcopy(value), 'evidence': [keys(x) for x in value['evidence']]}
        gaps = any(q.get('material', True) for q in group['unresolved']) or any(
            f.get('status') == 'open' and f.get('material', True) for f in group['facts'].values())
        require(group['decision'] != 'ready' or not gaps, 'Ready declaration conflicts with declared material gaps')
        complete = group['decision'] == 'ready'
        studies[study_id] = {'identity': group['identity'], 'items': {x: source_binding(by_work[x]) for x in named},
            'request': digest(request_manifest(s)), 'facts': [keys(k) for k in group['facts']],
            'evidence': [keys(k) for k in group['evidence']],
            'worker': receipt['worker'], 'decision': group['decision'], 'complete': complete,
            'unresolved': group['unresolved'], 'started_at': receipt['started_at'], 'completed_at': now() if complete else None}
        seen.update(named)
    replaced = {k for k, v in c.get('studies', {}).items() if set(v['items']) & seen}
    require(all(set(c['studies'][k]['items']) <= seen for k in replaced), 'Replace an incomplete study group as a whole')
    retained = {key: study for key, study in c.get('studies', {}).items() if key not in replaced}
    retained.update(studies)
    active_facts = {key for study in retained.values() for key in study['facts']}
    remove_facts = {key for old_id in replaced for key in c['studies'][old_id]['facts']} - active_facts
    remaining_facts = {**{key: value for key, value in c['facts'].items() if key not in remove_facts}, **facts}
    used_evidence = {key for fact in remaining_facts.values() for key in fact['evidence']}
    used_evidence.update(key for study in retained.values() for key in study.get('evidence', []))
    store = domain_store.Store(_CONNECTIONS[str(Path(s['context_path']).parent.resolve())], globals())
    used_evidence.update(store.referenced_evidence())
    remove_evidence = {key for old_id in replaced for key in c['studies'][old_id].get('evidence', [])} - used_evidence
    result = submit_context(s, {'kind': 'context', 'worker': receipt['worker'], 'base': c['revision'],
        'reason': 'Pre-correction work study after complete requested source collection.', 'facts': facts, 'evidence': evidence,
        'remove_facts': sorted(remove_facts), 'remove_evidence': sorted(remove_evidence)},
        studies=studies, replace_studies=replaced)
    del r['study_receipts'][p['packet']]
    elapsed = round((time.time() - receipt['issued']) * 1000, 1)
    r['study_metrics']['submissions'] += 1
    r['study_metrics']['packet_elapsed_ms'] += elapsed
    save_registry(s, r)
    return {**result, 'packet_elapsed_ms': elapsed, 'studies': {k: {'complete': v['complete'], 'items': list(v['items']), 'unresolved': v['unresolved']}
                               for k, v in studies.items()}}


def registry(s):
    p = Path(s['context_path']).with_name('roles.json')
    return read_record(p) if record_exists(p) else {'metadata': {}}


def save_registry(s, r):
    write_record(Path(s['context_path']).with_name('roles.json'), packed(r))


def siblings(s):
    p = Path(s['context_path']).with_name('batch.json')
    if not record_exists(p):
        return [s]
    batch = read_record(p)
    require(batch.get('contract') == CONTRACT, 'Wrong batch contract')
    return [s if Path(w).resolve() == Path(s['work']) else load(w)[0] for w in batch['items']]






def fact_payload(c, key):
    f = c['facts'].get(key)
    if f is None:
        return None
    return {'fact': f, 'evidence': {k: c['evidence'][k] for k in f['evidence']}}


def entry_owners(c,field,key):
    owners={study_id for study_id,study in c.get('studies',{}).items() if key in study.get(field,[])}
    return owners or set(c.get('entry_scope',{}).get(field,{}).get(key,[]))


def entry_allowed(s,c,field,key):
    owners=entry_owners(c,field,key)
    study=study_record(s,c)
    return not owners or ('item:'+s['work']) in owners or bool(study and study['id'] in owners)


def metadata(s, c=None):
    c = context(s) if c is None else c
    used = {k for u in s['outputs'].values() for k in u.get('facts', [])}
    study = study_record(s, c)
    relevant = set(study['facts']) if study else set()
    other = {k for item in c.get('studies', {}).values() if s['work'] not in item['items'] for k in item['facts']}
    facts = {k: v for k, v in c['facts'].items() if k not in other and entry_allowed(s,c,'facts',k) and (v.get('global') or k in used or k in relevant)}
    evidence = {k for f in facts.values() for k in f['evidence']}
    evidence.update(x['event'] for x in s['supplements'].values() if x.get('event'))
    evidence.update(k for u in s['outputs'].values() for k in lookup_refs(u))
    if study:
        evidence.update(study.get('evidence', []))
    result = {'fact:' + k: v for k, v in facts.items()}
    result.update({'evidence:' + k: c['evidence'].get(k, c.get('notices', {}).get('evidence:' + k)) for k in evidence})
    if study:
        result['study:' + study['id']] = {k: study[k] for k in ('identity', 'worker', 'completed_at', 'unresolved')}
    prefix = s['media'] + '#'
    result.update({prefix + 'source:' + k: {a: b for a, b in v.items() if a != 'cues'} for k, v in s['sources'].items()})
    for key, notice in c.get('notices', {}).items():
        if notice['global'] or s['media'] in notice['media']:
            result[key] = notice
    return result


def author_metadata(s, c=None):
    c = context(s) if c is None else c
    other_facts = {k for item in c.get('studies', {}).values() if s['work'] not in item['items'] for k in item['facts']}
    other_evidence = {k for item in c.get('studies', {}).values() if s['work'] not in item['items'] for k in item.get('evidence', [])}
    facts = {k:v for k,v in c['facts'].items() if k not in other_facts and entry_allowed(s,c,'facts',k)}
    required_evidence = {ref for fact in facts.values() for ref in fact['evidence']}
    result = {'fact:' + k: v for k, v in facts.items()}
    result.update({'evidence:' + k: v for k, v in c['evidence'].items() if k in required_evidence or (k not in other_evidence and entry_allowed(s,c,'evidence',k))})
    result.update({k: v for k, v in c.get('notices', {}).items() if v['global'] or s['media'] in v['media']})
    study = study_record(s, c)
    if study:
        result['study:' + study['id']] = {k: study[k] for k in ('identity', 'worker', 'completed_at', 'unresolved')}
    return result






def seal_binding(s, c=None):
    c = context(s) if c is None else c
    study = study_record(s, c)
    return digest({'sources': {k: v['hash'] for k, v in s['sources'].items()}, 'scope': s.get('patch', {}).get('scope'),
                   'study': study['id'] if study else None})


def sealed(s, c=None):
    return bool(s['sealed']) and s['sealed']['binding'] == seal_binding(s, c)


def lookup_refs(unit):
    records = [unit] + [p for parts in unit.get('parts', {}).values() for p in parts]
    return {r['artist_lookup'] for r in records if isinstance(r.get('artist_lookup'), str)}


def resolve_lookups(unit, ctx, allow_retired=False):
    if not lookup_refs(unit):
        return unit
    result = copy.deepcopy(unit)
    for r in [result] + [p for parts in result.get('parts', {}).values() for p in parts]:
        if isinstance(r.get('artist_lookup'), str):
            evidence = ctx['evidence'].get(r['artist_lookup'], {})
            if not evidence and allow_retired:
                evidence = ctx.get('notices', {}).get('evidence:' + r['artist_lookup'], {}).get('previous', {})
            require(isinstance(evidence.get('artist_lookup'), dict), 'Read the referenced song/version lookup evidence')
            r['artist_lookup'] = evidence['artist_lookup']
    return result


def validate_output(s, units=None, supplements=None, ctx=None, allow_retired=False):
    c = context(s) if ctx is None else ctx
    units = s['outputs'] if units is None else units
    supplements = s['supplements'] if supplements is None else supplements
    originals, valid, result = source_map(s), source_ids(s), []
    valid_ko = {i for i in valid if i.startswith('K')}
    for key, u in units.items():
        require(key in valid and key.startswith('E') and u['en'] == [key], 'Canonical English anchor required')
        require(set(expand(u['ko'])) <= valid_ko, 'Unknown Korean correspondence')
        require(all((k in c['facts'] and (allow_retired or entry_allowed(s,c,'facts',k))) or (allow_retired and 'fact:' + k in c.get('notices', {})) for k in u.get('facts', [])), 'Retired/unknown fact reference; reassess the affected output')
        projected, parts = projection(originals[key], resolve_lookups(u, c, allow_retired))
        require(set(u['texts']) == ({key} if projected else set()), 'Translate every and only surviving English cue')
        if not projected:
            continue
        text = u['texts'][key]
        require(nonempty(text) and not any(x in text for x in ('\r', '\x00', '\n\n')), 'Invalid Korean text: ' + key)
        markup_valid(text)
        require(shape(projected) == shape(text), 'SSOT line/speaker/emphasis/expressive punctuation structure changed: ' + key)
        for part in parts:
            if part['kind'] == 'track_info':
                visible = re.sub(r'\s+', ' ', re.sub(r'<[^>]*>|\{[^}]*\}', '', text))
                require(all(re.sub(r'\s+', ' ', v) in visible for v in part['identity'].values()), 'Source song identity omitted')
        result.append({'id': 'O' + key[1:], 'start': originals[key]['start'], 'end': originals[key]['end'], 'text': text})
    for key, x in supplements.items():
        require(re.fullmatch(r'S[1-9]\d*', key) and x['id'] == key, 'Stable supplement ID required')
        require(set(x) <= {'id', 'start', 'end', 'text', 'links', 'event', 'fields', 'reason', 'kind', 'origin'}, 'Unknown supplement fields')
        require(x.get('kind') in {'screen_text', 'track_info', 'dialogue', 'lyrics'} and nonempty(x.get('text')) and nonempty(x.get('reason')),
                'Supplement text/kind/reason required')
        require(set(expand(x.get('links', []))) <= valid, 'Unknown supplement link')
        require(type(x.get('start')) is int and type(x.get('end')) is int and 0 <= x['start'] < x['end'] <= s['duration'], 'Invalid supplement interval')
        if x.get('origin') == 'collected':
            links = expand(x.get('links', []))
            require(not s.get('patch') and links and set(links) <= valid_ko and
                    not x.get('event') and not x.get('fields') and x['kind'] in {'screen_text', 'dialogue', 'lyrics'},
                    'Collected supplement requires Korean original links and no claimed observation')
            require(any(originals[i]['start'] < x['end'] and x['start'] < originals[i]['end'] for i in links),
                    'Collected supplement must overlap its linked original timing')
        else:
            require(x.get('origin') in (None, 'observed'), 'Unknown supplement origin')
            require(x['kind'] in {'screen_text', 'track_info'}, 'Observed supplement kind unsupported')
            e = c['evidence'].get(x.get('event'), {})
            if not e and allow_retired:
                e = c.get('notices', {}).get('evidence:' + x.get('event', ''), {}).get('previous', {})
            require(e.get('kind') in {'audiovisual', 'official'} and e.get('access') == 'body' and e.get('verified') is True,
                    'Supplement requires actually inspected audiovisual/official evidence')
            if e['kind'] == 'audiovisual':
                require(e.get('media') == s['media'] and e.get('observed') is True and e.get('modality') in {'video', 'audio_video'},
                        'Screen supplement requires actual visual observation of this media')
                require(x.get('fields') and all(f in e['fields'] for f in x['fields']), 'Identify observed information fields')
                for f in x['fields']:
                    field = e['fields'][f]
                    require(field['available'] <= x['start'] < x['end'] <= field['until'], 'Supplement precedes field availability or exceeds observed hold')
            else:
                require(x['kind'] == 'track_info' and e['ref'].startswith('https://'), 'Official source cannot verify unobserved screen text')
        markup_valid(x['text'])
        require(not any(y['start'] == x['start'] and y['end'] == x['end'] and y['text'] == x['text'] for y in result), 'Duplicate supplement')
        result.append({k: x[k] for k in ('id', 'start', 'end', 'text')})
    return sorted(result, key=lambda x: (x['start'], x['id'][0] == 'S', int(x['id'][1:])))


def compiled(s, ctx=None):
    return validate_output(s, ctx=ctx, allow_retired=True)


def output_complete(s, final=None):
    require(set(s['outputs']) == {i for i in source_ids(s) if i.startswith('E')}, 'Classify the entire English scope')
    final = compiled(s) if final is None else final
    require(final, 'No Korean output')
    return final


def unit_members(unit):
    key = unit['id']
    return {key, 'O' + key[1:]} | set(expand(unit['ko']))


def unit_marker(ctx, media, unit):
    refs = lookup_refs(unit)
    touches = ctx['touches'].get(media, {})
    return digest({'unit': unit,
                   'facts': {k: fact_payload(ctx, k) for k in unit.get('facts', [])},
                   **({'lookups': {k: ctx['evidence'].get(k) for k in refs}} if refs else {}),
                   'touches': {key: touches.get(key, 0) for key in unit_members(unit)}})


def supplement_marker(ctx, supplement):
    return digest({'supplement': supplement, 'event': ctx['evidence'].get(supplement.get('event'))})


class Analysis:
    """Mechanical fingerprints of declared judgments; no automatic semantic analysis."""
    def __init__(self, s, ctx=None):
        self.ctx = context(s) if ctx is None else ctx
        # Disposable deterministic index, never an approval/semantic assertion.
        # Raw source/evidence guards run at each CLI entry and before placement.
        cache_key = digest({'engine': ENGINE_HASH, 'contract': CONTRACT, 'duration': s['duration'], 'sources': s['sources'], 'scope': s.get('patch'),
                            'outputs': s['outputs'], 'supplements': s['supplements'], 'context': self.ctx})
        cached = s.get('_analysis', {})
        payload = cached.get('payload', {})
        hit = cached.get('key') == cache_key and cached.get('checksum') == digest(payload)
        self.final = copy.deepcopy(payload['final']) if hit else compiled(s, self.ctx)
        scoped = source_ids(s)
        self.u = {i: v for i, v in source_map(s).items() if i in scoped}
        self.u.update({x['id']: x for x in self.final})
        if hit:
            self.output_sha256 = payload.get('output_sha256')
            self.sig = payload['signatures'].copy()
            self.required = set(self.u)
            return
        self.sig = {i: digest({'cue': x, 'global': self.ctx['global_revision'],
                              'touch': self.ctx['touches'].get(s['media'], {}).get(i, 0)}) for i, x in self.u.items()}
        # A packet is not an edge. Only explicit source/output correspondences share changes.
        for en, u in s['outputs'].items():
            ids = unit_members(u)
            marker = unit_marker(self.ctx, s['media'], u)
            for i in ids & self.sig.keys():
                self.sig[i] = digest([self.sig[i], marker])
        for key, x in s['supplements'].items():
            marker = supplement_marker(self.ctx, x)
            for i in ({key} | set(expand(x['links']))) & self.sig.keys():
                self.sig[i] = digest([self.sig[i], marker])
        self.required = set(self.u)
        complete = set(s['outputs']) == {i for i in scoped if i.startswith('E')}
        self.output_sha256 = digest(output_raw(s, self.final)) if complete and self.final else None
        payload = {'final': self.final, 'signatures': self.sig, 'output_sha256': self.output_sha256}
        s['_analysis'] = {'key': cache_key, 'checksum': digest(payload), 'payload': copy.deepcopy(payload)}


def pending(s, a=None, worker=None):
    a = a or Analysis(s)
    seen = s['readings'] if worker is None else s['reading_workers'].get(worker, {})
    return {i for i, h in a.sig.items() if seen.get(i) != h}




def metadata_pending(s, worker):
    entries = metadata(s)
    seen = registry(s)['metadata'].get(worker, {})
    # Namespace source/understanding entries by media; common facts/evidence can be reused across episodes.
    return {k: v for k, v in entries.items() if seen.get(k) != digest(v)}


def ready(s, a=None):
    verify_inputs(s)
    study_ready(s)
    a = a or Analysis(s)
    require(sealed(s, a.ctx), 'Complete source correction and author seal required')
    output_complete(s, a.final)
    validate_output(s, ctx=a.ctx)
    require(s['reviewer'] and not pending(s, a, s['reviewer']), 'Current assigned independent coverage incomplete')
    require(not any(f['status'] == 'open' for f in s['findings'].values()), 'Unresolved finding')
    used_facts = {k for u in s['outputs'].values() for k in u.get('facts', [])} | set(study_ready(s)['facts'])
    applicable = set(metadata(s, a.ctx))
    require(not any(f['status'] == 'open' and f.get('material', True) for k, f in a.ctx['facts'].items()
                    if 'fact:' + k in applicable and (f.get('global') or k in used_facts)), 'Unresolved material work judgment')
    require(all(k in s['findings'] and s['findings'][k]['status'] == 'resolved' for k in s['sealed']['unresolved']), 'Unresolved author question')
    return output_raw(s, a.final)


def approval_valid(s, a=None):
    a = a or Analysis(s)
    p = s['approval']
    return bool(p) and p['worker'] == s['reviewer'] and p['signature'] == digest(a.sig) and p['hash'] == digest(output_raw(s, a.final)) and p['findings'] == digest(s['findings']) and not metadata_pending(s, p['worker'])


def independent(s, worker):
    require(nonempty(worker) and worker != s['author'], 'Actual independent worker required')


def packet(s, kind, worker, body, pins, template):
    p = {'kind': kind, 'worker': worker, 'pins': pins, 'issued': time.time()}
    key = digest(p)[:20]
    s['receipts'][key] = p
    s['metrics']['cue_presentations'] += len(displayed_cues(body))
    result = {'mode': kind, **body, 'packet': key,
              'submit': {'packet': key, **template}}
    s['metrics']['view_calls'] += 1
    size = len(packed(result))
    s['metrics']['presented_bytes'] += size
    if kind == 'metadata':
        s['metrics']['metadata_calls'] += 1
        s['metrics']['metadata_bytes'] += size
    return result


def comparison_body(cues, units, supplements, anchor, visible):
    comparisons, emitted = [], set()
    for en in sorted(anchor, key=lambda i: (cues[i]['start'], int(i[1:]))):
        linked = expand(units[en]['ko'])
        collected = [i if i in emitted else cues[i] for i in linked]
        emitted.update(linked)
        out = cues.get('O' + en[1:])
        output = None
        if out is not None:
            same = next((i for i in linked if cues[i]['text'] == out['text']), None)
            output = {'id': out['id'], 'text_ref': same} if same else {k: out[k] for k in ('id', 'text')}
        comparisons.append({'english': cues[en], 'collected': collected, 'output': output,
                            **{k: v for k, v in units[en].items() if k not in {'id', 'en', 'ko', 'texts'} and v not in ([], {}, None)}})
    return {'comparisons': comparisons,
            'korean_sources': [cues[i] for i in sorted(visible, key=lambda i: (cues[i]['start'], i)) if i.startswith('K') and i not in emitted],
            'supplements': [{**supplements[i], **cues[i]} for i in sorted(visible, key=lambda i: (cues[i]['start'], i)) if i.startswith('S')]}




@state_operation
def view(s, args):
    role, worker = args.role, args.worker
    require(nonempty(worker) and role in {'study', 'author', 'metadata', 'reviewer'}, 'Use an actual worker and supported role')
    if role == 'study':
        return study_view(s, args)
    r = registry(s)
    if role == 'author':
        require(s['author'] in (None, worker), 'Keep the assigned episode author')
        require(set(s['sources']) == {'en', 'ko'}, 'Select both sources first')
        if not s['author_readings']:
            collection(s)
        study_ready(s)
        s['author'] = worker
        scoped = source_ids(s)
        u = {i: x for i, x in source_map(s).items() if i in scoped}
        u.update({i: dict(x) for i, x in s['supplements'].items()})
        ids = sorted(u, key=lambda i: (u[i]['start'], i[0], int(i[1:])))
        if args.pending:
            ids = [i for i in ids if s['author_readings'].get(i) != digest(u[i])]
        chosen = choose(args, ids)
        rows = [editable_row(i, s['outputs'].get(i)) for i in chosen if i.startswith('E')]
        meta = author_metadata(s)
        return packet(s, 'author', worker,
                      {'context_changes': {k: v for k, v in meta.items() if r['metadata'].get(worker, {}).get(k) != digest(v)},
                       'cues': [u[i] for i in chosen], 'remaining': len(ids) - len(chosen)},
                      {'sources': {i: digest(u[i]) for i in chosen},
                       'outputs': {i: digest(s['outputs'].get(i)) for i in chosen if i.startswith('E')},
                       'global': context(s)['global_revision'], 'metadata': {k: digest(v) for k, v in meta.items()}},
                      {'default_kind': None, 'rows': rows})
    independent(s, worker)
    require(sealed(s), 'Complete episode source reading and first correction before review')
    if role == 'metadata':
        entries = {**author_metadata(s), **metadata(s)}
        seen = r['metadata'].get(worker, {})
        entries = {k: v for k, v in entries.items() if seen.get(k) != digest(v)}
        keys = choose(args, sorted(entries))
        return packet(s, 'metadata', worker, {'entries': {k: entries[k] for k in keys}, 'remaining': len(entries) - len(keys)},
                      {k: digest(entries[k]) for k in keys}, {})
    owner = s['reviewer']
    if owner not in (None, worker):
        require(nonempty(getattr(args, 'reassign', None)), 'Episode reviewer already assigned: ' + owner)
        s['approval'] = None
        s['history'].append({'kind': 'reviewer_handover', 'previous': owner, 'worker': worker, 'reason': args.reassign})
    s['reviewer'] = worker
    a = Analysis(s)
    ids = sorted(a.u, key=lambda i: (a.u[i]['start'], 'EKOS'.index(i[0]), int(i[1:])))
    if args.pending:
        todo = pending(s, a, worker)
        ids = [i for i in ids if i in todo]
    chosen = choose(args, ids)
    # Expand visible comparison anchors, not semantic dependencies or claimed unseen coverage.
    anchor = {i if i.startswith('E') else 'E' + i[1:] for i in chosen if i[0] in 'EO'}
    chosen_set = set(chosen)
    for en, unit in s['outputs'].items():
        if set(expand(unit['ko'])) & chosen_set:
            anchor.add(en)
    visible = set(chosen)
    for supplement in s['supplements'].values():
        if supplement['id'] in chosen_set or set(expand(supplement['links'])) & chosen_set:
            visible.update({supplement['id']} & a.u.keys())
            visible.update(expand(supplement['links']))
    for en in anchor:
        visible.update({en, 'O' + en[1:]} & a.u.keys())
        visible.update(expand(s['outputs'][en]['ko']))
    comparison = comparison_body(a.u, s['outputs'], s['supplements'], anchor, visible)
    meta = metadata(s)
    body = {**comparison,
            'context_changes': {k: v for k, v in meta.items() if r['metadata'].get(worker, {}).get(k) != digest(v)},
            'findings': {k: f for k, f in s['findings'].items() if f['status'] == 'open' and set(f['ids']) & visible},
            'remaining': len(set(ids) - visible)}
    actual = displayed_cues(body)
    seen = s['presented'].setdefault(worker, {})
    for i in actual:
        s['metrics']['repeat_presentations'] += seen.get(i) == a.sig[i]
        seen[i] = a.sig[i]
    return packet(s, 'reviewer', worker, body,
                  {'cues': {i: a.sig[i] for i in actual}, 'anchors': sorted(anchor),
                   'metadata': {k: digest(v) for k, v in meta.items()}},
                  {'corrections': [], 'findings': [], 'resolutions': {}, 'impact': None})


def choose(args, ids):
    if args.ids:
        result = expand(args.ids.split(',')) if all(re.fullmatch(r'[EKOS]\d+(?:-[EKOS]\d+)?', x) for x in args.ids.split(',')) else args.ids.split(',')
        require(set(result) <= set(ids), 'Unknown or unavailable requested IDs')
        return result
    require(args.offset >= 0 and args.limit > 0, 'Positive page size and nonnegative offset required')
    return ids[args.offset:args.offset + args.limit]


def exact_parts(cue, parts):
    out = []
    for p in parts:
        p = copy.deepcopy(p)
        if 'text' in p:
            literal = p.pop('text')
            starts = [m.start() for m in re.finditer(re.escape(literal), cue['text'])] if nonempty(literal) else []
            occurrence = p.pop('occurrence', 1)
            require(type(occurrence) is int and 1 <= occurrence <= len(starts) and (len(starts) == 1 or 'occurrence' in parts[len(out)]), 'Ambiguous source span; choose its literal occurrence')
            at = starts[occurrence - 1]
            p['span'] = [at, at + len(literal)]
        out.append(p)
    return out


def add_findings(s, findings, worker, allowed):
    require(isinstance(findings, list), 'Findings must be a list')
    for f in findings:
        require(set(f) == {'ids', 'question'} and nonempty(f['question']), 'Finding needs affected IDs and actual question')
        ids = expand(f['ids'])
        require(ids and set(ids) <= set(allowed), 'Finding references unseen/out-of-scope material')
        key = 'F' + str(len(s['findings']) + 1)
        s['findings'][key] = {'ids': ids, 'question': f['question'], 'worker': worker, 'status': 'open'}


def impact_ids(s, impact, known):
    require(isinstance(impact, dict) and nonempty(impact.get('reason')), 'Semantic change needs impact judgment and reason')
    scope = impact.get('scope')
    require(scope in {'local', 'episode', 'global'}, 'Impact is local, episode or global')
    ids = set(expand(impact.get('ids', [])))
    require(ids <= set(known), 'Unknown impact IDs')
    require(scope != 'local' or ids, 'Local impact needs explicit affected IDs')
    return scope, set(known) if scope in {'episode', 'global'} else ids


def touch_context(c, media, ids):
    table = c['touches'].setdefault(media, {})
    for i in ids:
        table[i] = table.get(i, 0) + 1


def submit_context(s, p, studies=None, replace_studies=()):
    require(set(p) <= {'kind', 'worker', 'base', 'facts', 'evidence', 'remove_facts', 'remove_evidence', 'impact', 'reason'}, 'Unknown context fields')
    store = domain_store.Store(_CONNECTIONS[str(Path(s['context_path']).parent.resolve())], globals())
    old = store.context_header(s)
    require(p.get('base') == old['revision'], 'Stale shared context revision')
    require(nonempty(p.get('worker')) and nonempty(p.get('reason')), 'Context change needs actual author and reason')
    c = copy.deepcopy(old)
    if studies is not None:
        for key in replace_studies:
            c.setdefault('studies', {}).pop(key, None)
        c.setdefault('studies', {}).update(copy.deepcopy(studies))
    current_study = study_record(s,c)
    current_scope = current_study['id'] if current_study else 'item:'+s['work']
    for field in ('facts', 'evidence'):
        scopes=c.setdefault('entry_scope',{}).setdefault(field,{})
        for key in set(p.get(field,{})) | set(p.get('remove_'+field,[])):
            if studies is None and entry_owners(old,field,key):
                owners=entry_owners(old,field,key)
                active_scopes=set(old.get('studies',{})) | {'item:'+work for work in request_manifest(s)['items']}
                retiring_inactive = key in p.get('remove_'+field,[]) and not (owners & active_scopes)
                allowed = retiring_inactive or entry_allowed(s,old,field,key) or (field=='evidence' and 'evidence:'+key in author_metadata(s,old))
                require(allowed,'Context entry belongs to another work; use its item or a distinct ID')
            if key in p.get(field,{}):
                scopes.setdefault(key,sorted(entry_owners(c,field,key) or {current_scope}))
        for key, value in p.get(field, {}).items():
            require(nonempty(key) and isinstance(value, dict), 'Named context entry required')
            c[field][key] = copy.deepcopy(value)
        for key in p.get('remove_' + field, []):
            require(key in c[field], 'Unknown removed context entry')
            del c[field][key]
    for key, e in c['evidence'].items():
        require(e.get('kind') in {'selected_text', 'external_subtitle', 'official', 'official_ost', 'official_film_credits', 'encyclopedia', 'dictionary', 'audiovisual', 'user'} and
                nonempty(e.get('ref')) and e.get('access') in {'body', 'unavailable'} and nonempty(e.get('observation')),
                'Evidence needs origin, actual access and relevant observation')
        if isinstance(e.get('artist_lookup'), dict):
            for ref in e['artist_lookup'].get('sources', []):
                if 'checked_at' not in ref:
                    ref['checked_at'] = now()
        if e.get('path'):
            e['path'] = str(Path(e['path']).resolve())
        previous = old['evidence'].get(key, {})
        def semantic(x):
            if isinstance(x, dict):
                return {k: semantic(v) for k, v in x.items() if k not in {'checked_at', 'hash'}}
            if isinstance(x, list):
                return [semantic(v) for v in x]
            return x
        if semantic(e) != semantic(previous):
            e['checked_at'] = now()
            if e.get('path'):
                path = Path(e['path']).resolve()
                require(path.is_file(), 'Evidence file missing')
                e.update(path=str(path), hash=filehash(path))
        else:
            e.clear(); e.update(copy.deepcopy(previous))
        if e['kind'] == 'audiovisual':
            interval = e.get('interval', [])
            require(e.get('media') == s['media'] and len(interval) == 2 and all(type(x) is int for x in interval) and
                    0 <= interval[0] < interval[1] <= s['duration'] and e.get('modality') in {'audio', 'video', 'audio_video'}, 'Audiovisual observation needs media/modality/interval')
            for f in e.get('fields', {}).values():
                require(nonempty(f.get('text')) and type(f.get('available')) is int and type(f.get('until')) is int and
                        interval[0] <= f['available'] < f['until'] <= interval[1], 'Observed field availability must fit observation interval')
    for f in c['facts'].values():
        require(nonempty(f.get('text')) and type(f.get('global', False)) is bool and f.get('status') in {'established', 'open', 'intentional'} and
                isinstance(f.get('evidence'), list) and f['evidence'] and set(f['evidence']) <= c['evidence'].keys(), 'Fact needs judgment/status and inspected evidence references')
    changes = {field: [key for key in old[field].keys() | c[field].keys() if old[field].get(key) != c[field].get(key)] for field in ('facts', 'evidence')}
    if not any(changes.values()):
        return {'changed': {}, 'context_revision': old['revision']}
    affected_facts = set(changes['facts']) | {k for ctx in (old, c) for k, f in ctx['facts'].items() if set(f['evidence']) & set(changes['evidence'])}
    active_scopes=set(old.get('studies',{})) | {'item:'+work for work in request_manifest(s)['items']}
    replacing_unread = {key for study_id in replace_studies for key in old.get('studies', {}).get(study_id, {}).get('facts', [])} if studies is not None else set()
    changed_core = any(k not in replacing_unread and old['facts'].get(k, {}).get('global') and (not entry_owners(old,'facts',k) or bool(entry_owners(old,'facts',k) & active_scopes)) for k in affected_facts)
    study_consumers = {work for study in old.get('studies', {}).values() if affected_facts.intersection(study['facts'])
                       for work in study['items']}
    global_change = False
    existing_use, has_study_consumers = store.context_consumers(s, affected_facts, set(changes['evidence']), study_consumers)
    changed_core = changed_core or has_study_consumers
    if existing_use or changed_core or p.get('impact') is not None:
        declared = p.get('impact')
        known = store.impact_scope(s, declared)
        scope, ids = impact_ids(s, declared, known)
        global_change = scope == 'global'
        if scope == 'global':
            c['global_revision'] += 1
        else:
            store.load_touches(s, ids, c)
            touch_context(c, s['media'], ids)
        for media, used in existing_use.items():
            store.load_touches({**s,'media':media}, used, c)
            touch_context(c, media, used)
    c['revision'] += 1
    for field in ('facts', 'evidence'):
        for key in changes[field]:
            label = ('fact:' if field == 'facts' else 'evidence:') + key
            if key not in c[field]:
                c.setdefault('notices', {})[label] = {'retired': True, 'global': global_change, 'media': sorted(existing_use),
                    'previous': old[field][key], 'reason': p['reason'], 'revision': c['revision']}
            else:
                c.setdefault('notices', {}).pop(label, None)
    root = Path(s['context_path']).parent
    write_record(root / 'context-history' / f"{old['revision']:06}.json", packed({'before': old, 'change': p,
           **({'studies': studies, 'replaced_studies': sorted(replace_studies)} if studies is not None else {})}))
    store.write_context_delta(s,c)
    store.refresh_context(s,old,c)
    return {'changed': changes, 'context_revision': c['revision'], 'global_reassessment': global_change}


@state_operation
def submit(s, p, work=None):
    verify_inputs(s)
    if p.get('kind') == 'study':
        return submit_study(s, p)
    kind = p.get('kind')
    if kind == 'context':
        return submit_context(s, p)
    t = copy.deepcopy(s)
    new_context = None
    r = registry(s)
    if kind == 'source':
        source_selection(t, p, Path(s['work']))
        if s['author_readings']:
            scope, ids = impact_ids(s, p.get('impact'), Analysis(s).u)
            new_context = context(s)
            if scope == 'global':
                new_context['global_revision'] += 1
            else:
                touch_context(new_context, s['media'], ids)
            new_context['revision'] += 1
        result = {'selected': p['language'], 'source_hash': t['sources'][p['language']]['hash']}
    elif kind == 'seal':
        studied = study_ready(s)
        require(p.get('worker') == s['author'] and isinstance(p.get('unresolved', []), list), 'Actual correction author and unresolved questions required')
        originals = source_map(s)
        require(set(s['sources']) == {'en', 'ko'} and all(s['author_readings'].get(i) == digest(originals[i]) for i in source_ids(s)), 'Author must read all selected English/Korean')
        output_complete(s)
        gaps = []
        for q in p.get('unresolved', []):
            f = q if isinstance(q, dict) else {'ids': compact(source_ids(s)), 'question': q}
            before = set(t['findings'])
            add_findings(t, [f], p['worker'], source_ids(s))
            gaps.extend(set(t['findings']) - before)
        t['sealed'] = {'study': studied['id'], 'unresolved': sorted(gaps), 'binding': seal_binding(s)}
        result = {'draft_ready': True, 'unresolved': sorted(gaps)}
    elif kind == 'resolve':
        independent(s, p.get('worker'))
        require(p['worker'] == s['reviewer'], 'Assigned reviewer resolves current findings')
        resolve_findings(t, p['worker'], p.get('resolutions', {}))
        result = {'resolved': list(p.get('resolutions', {}))}
    elif kind == 'approval':
        worker = p.get('worker')
        independent(s, worker)
        a = Analysis(s)
        raw = ready(s, a)
        require(worker == s['reviewer'] and not metadata_pending(s, worker) and not pending(s, a, worker), 'Assigned reviewer must personally compare the current episode and context')
        t['approval'] = {'worker': worker, 'hash': digest(raw), 'signature': digest(a.sig), 'findings': digest(s['findings']),
                         'comparison_epoch': _CONNECTIONS[str(store_root(Path(s['work']) / 'state.json').resolve())].execute('SELECT comparison_epoch FROM episode_index WHERE work=?', (s['work'],)).fetchone()[0]}
        result = {'approved_sha256': digest(raw)}
    else:
        key = p.get('packet')
        require(key in t['receipts'], 'Use a generated view packet')
        rec = t['receipts'][key]
        kind, worker, pins = rec['kind'], rec['worker'], rec['pins']
        allowed_fields = {'packet', 'default_kind', 'rows', 'corrections', 'supplements', 'remove_supplements', 'findings', 'resolutions', 'impact'}
        require(set(p) <= allowed_fields, 'Only language judgments/results belong in packet submission')
        current_meta = {**author_metadata(s), **metadata(s)}
        if kind == 'metadata':
            require(set(p) == {'packet'} and all(k in current_meta and digest(current_meta[k]) == h for k, h in pins.items()), 'Stale or invalid metadata receipt')
            r['metadata'].setdefault(worker, {}).update(pins)
            result = {'metadata_acknowledged': list(pins)}
        else:
            if kind == 'author':
                study_ready(s)
                require(not any(k in p for k in ('corrections', 'findings', 'resolutions')), 'Author submits translations; reviewer submits corrections/findings')
                originals = source_map(s)
                require(worker == s['author'] and pins['global'] == context(s)['global_revision'] and
                        all(digest(originals.get(i, s['supplements'].get(i))) == h for i, h in pins['sources'].items()) and
                        all(digest(s['outputs'].get(i)) == h for i, h in pins['outputs'].items()), 'Stale author packet')
                require(all(k in current_meta and digest(current_meta[k]) == h for k, h in pins['metadata'].items()), 'Stale author context')
                apply_rows(t, p.get('rows', []), pins['outputs'], pins['metadata'], p.get('default_kind'))
                apply_supplements(t, p, pins['sources'])
                t['author_readings'].update({i: h for i, h in pins['sources'].items() if i.startswith(('E', 'K', 'S'))})
            else:
                require(kind == 'reviewer' and worker == s['reviewer'] and 'rows' not in p and 'default_kind' not in p, 'Assigned reviewer submits correction rows')
                independent(s, worker)
                a = Analysis(s)
                require(all(a.sig.get(i) == h for i, h in pins['cues'].items()), 'Stale comparison packet')
                meta = metadata(s)
                require({k: digest(v) for k, v in meta.items()} == pins['metadata'], 'Context changed; inspect current comparison context')
                seen = {**r['metadata'].get(worker, {}), **pins['metadata']}
                apply_rows(t, p.get('corrections', []), pins['anchors'], seen, partial=True)
                apply_supplements(t, p, pins['cues'])
                t['readings'].update(pins['cues'])
                t['reading_workers'].setdefault(worker, {}).update(pins['cues'])
                add_findings(t, p.get('findings', []), worker, pins['cues'])
            changed = {i for i in t['outputs'].keys() | s['outputs'].keys() if t['outputs'].get(i) != s['outputs'].get(i)}
            before_s, after_s = s['supplements'], t['supplements']
            changed_s = {i for i in before_s.keys() | after_s.keys() if before_s.get(i) != after_s.get(i)}
            if any(i in s['outputs'] for i in changed) or changed_s:
                known = set(source_ids(s)) | {'O' + i[1:] for i in t['outputs']} | set(after_s) | set(before_s)
                scope, extra = impact_ids(s, p.get('impact'), known)
                new_context = context(s)
                if scope == 'global':
                    new_context['global_revision'] += 1
                else:
                    touch_context(new_context, s['media'], extra)
                new_context['revision'] += 1
            if changed or changed_s:
                final = validate_output(t, ctx=new_context)
                if t.get('patch') and set(t['outputs']) == {i for i in source_ids(t) if i.startswith('E')}:
                    output_raw(t, final)
            after = a if kind == 'reviewer' and not changed and not changed_s else Analysis(t, new_context)
            if kind == 'reviewer':
                resolve_findings(t, worker, p.get('resolutions', {}), after)
            r['metadata'].setdefault(worker, {}).update(pins['metadata'])
            result = {'changed': sorted(changed), 'supplements_added': sorted(after_s.keys() - before_s.keys()),
                      'supplements_changed': sorted(changed_s & before_s.keys() & after_s.keys()),
                      'supplements_removed': sorted(before_s.keys() - after_s.keys()), 'supplements_current': sorted(after_s),
                      'pending': compact(pending(t, after, worker if kind == 'reviewer' else None)),
                      'output_sha256': after.output_sha256}
        del t['receipts'][key]
        result['packet_elapsed_ms'] = round((time.time() - rec['issued']) * 1000, 1)
    t['revision'] += 1
    t['history'].append({'revision': t['revision'], 'kind': kind, 'worker': p.get('worker') or (rec['worker'] if p.get('packet') else None),
                         'judgment': p, 'result': result,
                         'previous_outputs': {k: v for k, v in s['outputs'].items() if t['outputs'].get(k) != v},
                         'previous_supplements': {k: v for k, v in s['supplements'].items() if t['supplements'].get(k) != v},
                         'previous_sources': {k: v for k, v in s['sources'].items() if t['sources'].get(k) != v}})
    if new_context is not None:
        write_record(s['context_path'], packed(new_context))
    save_registry(s, r)
    s.clear(); s.update(t)
    return result


def status(s):
    try:
        a = Analysis(s)
        worker = s['reviewer']
        todo = pending(s, a, worker)
        try:
            ready(s, a)
            blockers = []
        except Error as e:
            blockers = [str(e)]
        ok = not blockers and approval_valid(s, a)
        draft = sealed(s)
        phase = 'approved' if ok else 'authoring' if not draft else 'review_ready' if worker is None else 'held' if not todo and blockers else 'reviewing'
        try:
            if not s['author_readings']:
                _, collection_hash = collection(s)
            else:
                collection_hash = None
        except Error as e:
            phase, blockers = 'collecting', [str(e)]
        else:
            try:
                study_ready(s)
            except Error as e:
                receipts = registry(s).get('study_receipts', {}).values()
                phase = 'held' if s['author_readings'] else 'studying' if any(
                    s['work'] in r['items'] and r['collection'] == collection_hash and
                    r['base'] == context(s)['revision'] for r in receipts) else 'study_required'
                blockers = [str(e)]
        return {'contract': CONTRACT, 'media': s['media'], 'author': s['author'], 'reviewer': worker, 'draft_ready': draft,
                'study': {k: study_record(s, a.ctx)[k] for k in ('id', 'worker', 'decision')} if study_record(s, a.ctx) else None,
                'phase': phase, 'context_revision': a.ctx['revision'], 'pending': compact(todo), 'pending_count': len(todo),
                'approved': ok, 'approved_sha256': s['approval']['hash'] if ok else None,
                'blocking': blockers if blockers else ([] if ok else ['Current exact-byte approval required']),
                'output_cues': len(a.final), 'metrics': s['metrics'], 'tokens': 'not_measured'}
    except (Error, KeyError) as e:
        return {'contract': CONTRACT, 'approved': False, 'phase': 'held', 'blocking': [str(e)], 'metrics': s['metrics'], 'tokens': 'not_measured'}


def finalize(s, batch_preflight=False):
    require(batch_preflight or not record_exists(Path(s['context_path']).with_name('batch.json')), 'Finalize the batch parent for a directory request')
    a = Analysis(s)
    raw = ready(s, a)
    require(approval_valid(s, a), 'Exact current independent approval required')
    current = filehash(s['target'])
    intent_path = Path(s['work']) / 'placement-results.json'
    previous = read_record(intent_path) if record_exists(intent_path) else {}
    recovered = previous.get('intent') == digest(raw) and current == digest(raw)
    require(current == s['target_hash'] or recovered, 'Target changed; inspect before retry')
    write_record(intent_path, packed({'intent': digest(raw), 'previous': s['target_hash'], 'target': s['target'], 'complete': False}))
    require(Path(s['target']).suffix == '.srt', 'Output must be SRT')
    require(not Path(s['target']).is_symlink(), 'Output cannot be a symlink')
    for source in s['sources'].values():
        for key in ('path',):
            original = source.get(key)
            require(not (original and Path(original).exists() and Path(s['target']).exists() and os.path.samefile(original, s['target'])), 'Output aliases a source')
    def guard():
        require(ready(s) == raw, 'Inputs changed during placement')
        check_directory(s['target_directory'])
        check_destination(s['target'], protected_paths(s))
        require(filehash(s['target']) == current, 'Target changed during placement')
    guard()
    atomic(s['target'], raw, guard=guard)
    require(filehash(s['target']) == digest(raw), 'Placed bytes differ; inspect before retry')
    s['target_hash'] = digest(raw)
    write_record(intent_path, packed({'intent': digest(raw), 'target': s['target'], 'complete': True}))
    return {'placed': s['target'], 'sha256': digest(raw), 'cues': len(a.final)}


def finalize_batch(work):
    root = Path(work).resolve()
    manifest = read_record(root / 'batch.json')
    require(not any(x['decision'] == 'held' for x in manifest.get('excluded', [])), 'Requested items held during inspection; resolve with fresh preparation')
    loaded = [load(p) for p in manifest['items']]
    require(len({s['target'] for s, _ in loaded}) == len(loaded), 'Duplicate targets')
    for s, _ in loaded:
        ready(s)
        check_directory(s['target_directory'])
        check_destination(s['target'], protected_paths(s))
        require(approval_valid(s) and (filehash(s['target']) == s['target_hash'] or (record_exists(Path(s['work']) / 'placement-results.json') and read_record(Path(s['work']) / 'placement-results.json').get('intent') == filehash(s['target']) == Analysis(s).output_sha256)) and Path(s['target']).suffix == '.srt', 'Batch preflight failed')
    results = []
    for s, path in loaded:
        try:
            results.append(finalize(s, batch_preflight=True))
            with transaction(root) as db:
                domain_store.Store(db, globals()).save_phase(s, 'finalize')
            write_record(root / 'placement-results.json', packed({'complete': False, 'results': results}))
        except (Error, OSError) as e:
            write_record(root / 'placement-results.json', packed({'complete': False, 'results': results, 'error': str(e), 'inspect_before_retry': True}))
            raise
    result = {'complete': True, 'results': results, 'excluded': manifest.get('excluded', [])}
    write_record(root / 'placement-results.json', packed(result))
    return result


def public_usage(result):
    return {key: result[key] for key in ('usage', 'complete', 'command_interval_union_ms', 'limits')}


def usage_logs(paths, start, end, root_turn_ids=()):
    from telemetry import usage_logs as collect, Error as TelemetryError
    try:
        return collect(paths, start, end, root_turn_ids)
    except TelemetryError as error:
        raise Error(str(error)) from error


def displayed_cues(body):
    result = {x['id']: x for x in body.get('cues', [])}
    result.update({x['id']: x for x in body.get('korean_sources', [])})
    result.update({x['id']: x for x in body.get('supplements', [])})
    for row in body.get('comparisons', []):
        result.update({x['id']: x for x in row['collected'] if isinstance(x, dict)})
    for row in body.get('comparisons', []):
        result[row['english']['id']] = row['english']
        if row['output'] is not None:
            output = row['output']
            text = result[output['text_ref']]['text'] if 'text_ref' in output else output['text']
            result[output['id']] = {'start': row['english']['start'], 'end': row['english']['end'], 'id': output['id'], 'text': text}
    return result


def editable_row(en, old=None):
    row = {'id': en, 'text': old['texts'].get(en, '') if old else '', 'ko': old['ko'] if old else []}
    if old:
        row.update({k: old[k] for k in ('kind', 'facts', 'identity', 'artist_lookup') if k in old})
        if en in old.get('parts', {}):
            row['parts'] = old['parts'][en]
    return row


def apply_rows(s, rows, allowed, meta_seen, default_kind=None, partial=False, ctx=None):
    originals, ctx = source_map(s), context(s) if ctx is None else ctx
    ids = [x.get('id') for x in rows]
    require(len(ids) == len(set(ids)) and set(ids) <= set(allowed), 'Unique rows inside displayed English anchors required')
    if not partial:
        require(set(ids) == set(allowed), 'Return every and only packet English row')
    profile = None
    for change in rows:
        require(set(change) <= {'id', 'text', 'ko', 'kind', 'parts', 'identity', 'artist_lookup', 'facts'}, 'Row contains management/unknown fields')
        en = change['id']
        row = {**editable_row(en, s['outputs'].get(en)), **change} if partial else change
        cls = row.get('kind', default_kind)
        require(cls in {'dialogue', 'lyrics', 'track_info', 'nonverbal'}, 'Actual source classification required')
        unit = {'id': en, 'en': [en], 'ko': compact(expand(row.get('ko', []))), 'kind': cls,
                'texts': {en: row['text']} if row.get('text') else {}, 'facts': row.get('facts', [])}
        if row.get('parts') is not None:
            unit['parts'] = {en: exact_parts(originals[en], row['parts'])}
        for field in ('identity', 'artist_lookup'):
            if field in row:
                unit[field] = row[field]
        require(all(meta_seen.get('fact:' + k) == digest(ctx['facts'].get(k)) for k in unit['facts']) and
                all(meta_seen.get('evidence:' + k) == digest(ctx['evidence'].get(k)) for k in lookup_refs(unit)),
                'Read current fact/lookup evidence before applying it')
        require(all(entry_allowed(s,ctx,'facts',key) for key in unit['facts']), 'Fact belongs to another work')
        lookups = lookup_refs(unit)
        if lookups:
            if profile is None:
                profile = author_metadata(s,ctx)
            require(all('evidence:'+key in profile for key in lookups), 'Lookup evidence belongs to another work')
        s['outputs'][en] = unit


def apply_supplements(s, p, displayed=None):
    rows = p.get('supplements', [])
    ids = [x['id'] for x in rows]
    require(len(ids) == len(set(ids)), 'Duplicate supplement ID')
    for x in rows:
        if displayed is not None:
            require(x['id'] in displayed if x['id'] in s['supplements'] else bool(set(expand(x.get('links', []))) & set(displayed)), 'Read the current supplement before editing; additions need linked sources')
        s['supplements'][x['id']] = x
    for x in p.get('remove_supplements', []):
        require(set(x) == {'id', 'reason'} and x['id'] in s['supplements'] and nonempty(x['reason']), 'Explicit existing supplement removal with reason required')
        require(displayed is None or x['id'] in displayed, 'Read a supplement before removing it')
        del s['supplements'][x['id']]


def resolve_findings(s, worker, resolutions, a=None):
    for key, answer in resolutions.items():
        require(key in s['findings'] and nonempty(answer), 'Resolution needs existing finding and actual reassessment')
        require(not set(s['findings'][key]['ids']) & pending(s, a, worker), 'Read current affected material before resolving')
        s['findings'][key].update(status='resolved', resolution=answer, resolved_by=worker)


def save_template(root, dest, result, items, persist=True):
    root = Path(root).resolve()
    if dest is True:
        dest = root / 'responses' / ('packet-' + digest({'mode':result.get('mode'),'packet':result['packet']})[:20] + '.json')
    require(not Path(dest).is_symlink(), 'Template cannot be a symlink')
    dest = Path(dest).resolve()
    require(root / 'responses' in dest.parents, 'Templates belong under WORK/responses/')
    guarded = {Path(item['target']).resolve() for item in items}
    for item in items:
        guarded.update(protected_paths(item))
    def guard():
        check_destination(dest, guarded)
    guard()
    before = len(packed(result))
    atomic(dest, packed(result.pop('submit')), guard=guard)
    result['response_template'] = str(dest)
    delta = len(packed(result)) - before
    if result.get('mode') == 'study':
        r = registry(items[0])
        r['study_metrics']['presented_bytes'] += delta
        save_registry(items[0], r)
    else:
        items[0]['metrics']['presented_bytes'] += delta
        if result.get('mode') == 'metadata':
            items[0]['metrics']['metadata_bytes'] += delta
        if persist:
            write_record(Path(items[0]['work']) / 'state.json', packed(items[0]))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('inspect')
    p.add_argument('media')
    p = commands.add_parser('prepare')
    p.add_argument('media'); p.add_argument('--work', required=True); p.add_argument('--target')
    p.add_argument('--base'); p.add_argument('--scope')
    p.add_argument('--intent', choices=('collect', 'audit'), default='collect')
    p.add_argument('--authorization', help='Exact explicit user request to audit embedded Korean')
    for name in ('view', 'submit', 'status', 'finalize', 'handoff'):
        p = commands.add_parser(name); p.add_argument('work')
        if name == 'view':
            p.add_argument('--role', choices=('study', 'author', 'metadata', 'reviewer'), required=True)
            p.add_argument('--worker', required=True); p.add_argument('--offset', type=int, default=0)
            p.add_argument('--limit', type=int, default=200); p.add_argument('--ids'); p.add_argument('--pending', action='store_true')
            p.add_argument('--reassign', help='Reason for stopped-reviewer handover; cannot reuse personal coverage')
            p.add_argument('--template', nargs='?', const=True, help='Write the response template; omitted path uses a safe generated WORK/responses path')
        elif name == 'handoff':
            p.add_argument('--worker', required=True)
            p.add_argument('--limit', type=int, default=200)
        elif name == 'submit':
            p.add_argument('record')
            p.add_argument('--next', dest='next_job', action='store_true',
                           help='After explicit seal/approval, return the same worker\'s next eligible packet')
        elif name == 'status':
            p.add_argument('--include-session-details', action='store_true')
            p.add_argument('--session-log', action='append', default=[]); p.add_argument('--start'); p.add_argument('--end')
            p.add_argument('--root-turn-id', action='append', default=[])
    p = commands.add_parser('telemetry')
    p.add_argument('--include-session-details', action='store_true')
    p.add_argument('--session-log', action='append', required=True)
    p.add_argument('--root-turn-id', action='append', default=[])
    p.add_argument('--start', required=True); p.add_argument('--end', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'telemetry':
            result = usage_logs(args.session_log, args.start, args.end, args.root_turn_id)
        elif args.command == 'inspect':
            result = inspect_media(args.media)
        elif args.command == 'prepare':
            result = prepare(args)
        else:
            root = Path(args.work).resolve()
            batch = not record_exists(root / 'state.json')
            if batch:
                require(record_exists(root / 'batch.json'), 'No prepared work here; skipped collections create no work')
                require(args.command in {'status', 'finalize', 'submit', 'handoff'} or
                        (args.command == 'view' and args.role == 'study'), 'Use an immediate item directory for correction/review')
                lock_root = root
            else:
                lock_root = store_root(root / 'state.json')
            # Brief filesystem serialization only; model work happens outside this lock.
            lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with command_lock(lock_root), (transaction(lock_root) if args.command != 'finalize' else nullcontext()):
                if batch:
                    manifest = read_record(root / 'batch.json')
                    require(manifest.get('contract') == CONTRACT and manifest['items'], 'Invalid batch contract/items; prepare fresh')
                    if args.command == 'handoff':
                        store = domain_store.Store(_CONNECTIONS[str(lock_root.resolve())], globals())
                        result = store.handoff(manifest['items'], args.worker, args.limit)
                    elif args.command == 'finalize':
                        result = finalize_batch(root)
                    elif args.command == 'status':
                        store = domain_store.Store(_CONNECTIONS[str(lock_root.resolve())], globals())
                        result = {'items': [store.status(store.header(p)) for p in manifest['items']], 'excluded': manifest.get('excluded', []),
                                  'shared_study_metrics': store.study_metrics(store.header(manifest['items'][0]))}
                    else:
                        store = domain_store.Store(_CONNECTIONS[str(lock_root.resolve())], globals())
                        s = store.header(manifest['items'][0])
                        if args.command == 'view':
                            result = study_view(s, args)
                            if args.template:
                                save_template(root, args.template, result, collection(s)[0])
                        else:
                            record = json.loads(input_bytes(args.record))
                            require(not args.next_job, '--next requires an item seal or approval')
                            require(record.get('kind') == 'study', 'Only study submission uses the batch parent')
                            result = submit_study(s, record)
                else:
                    store = domain_store.Store(_CONNECTIONS[str(lock_root.resolve())], globals()) if args.command != 'finalize' else None
                    native = args.command in {'status','view','handoff'}
                    record = json.loads(input_bytes(args.record)) if args.command == 'submit' else None
                    if record is not None and args.next_job:
                        require(record.get('kind') in {'seal', 'approval'}, '--next requires an explicit seal or approval')
                    if record and record.get('packet'):
                        receipt = store.db.execute('SELECT body FROM receipt WHERE work=? AND id=?',(str(root),record['packet'])).fetchone()
                        current = json.loads(receipt[0]) if receipt else {}
                        native = current.get('kind') in {'author','metadata','reviewer'} and '_epochs' in current.get('pins',{})
                    if record and record.get('kind') in {'context','study'}:
                        native = True
                    if native:
                        s = store.header(root)
                        if args.command == 'handoff':
                            result = store.handoff([str(root)], args.worker, args.limit)
                        elif args.command == 'status':
                            result = store.status(s)
                        elif record and record.get('kind') == 'study':
                            result = submit_study(s,record)
                        elif record and record.get('kind') == 'context':
                            verify_inputs(s,evidence=store.guard_evidence(s,None))
                            result = submit_context(s,record)
                        elif args.command == 'view':
                            result = study_view(s,args) if args.role == 'study' else store.view(s,args)
                            if args.template:
                                save_template(root,args.template,result,[s],persist=False)
                                if args.role != 'study':
                                    store.save_header(s)
                        else:
                            if current['kind'] == 'metadata' or (current['kind'] == 'reviewer' and not any(record.get(k) for k in ('corrections','findings','resolutions','supplements','remove_supplements','impact'))):
                                result = store.acknowledge(s,record)
                            else:
                                result = store.submit_rows(s,record)
                    else:
                        s, path = load(root)
                        verify_inputs(s)
                        if args.command == 'view':
                            result = view(s, args)
                            if args.template:
                                save_template(root, args.template, result, [s])
                        elif args.command == 'submit':
                            result = submit(s, record)
                        elif args.command == 'finalize':
                            result = finalize(s)
                        else:
                            result = status(s)
                        if args.command == 'finalize':
                            with transaction(lock_root) as db:
                                domain_store.Store(db, globals()).save_phase(s, 'finalize')
                    if record is not None and args.next_job:
                        # The state operation has persisted the validated terminal
                        # phase. Reuse canonical dispatch in this same transaction;
                        # no automatic seal, review, approval or placement.
                        result['next'] = store.handoff([str(root)], record['worker'])
        if args.command == 'status' and args.session_log:
            # Historical log reading is independent of the status snapshot and
            # must not hold the work lock or SQLite write transaction.
            result['telemetry'] = usage_logs(args.session_log, args.start, args.end, args.root_turn_id)
        if args.command == 'telemetry' and not args.include_session_details:
            result = public_usage(result)
        elif args.command == 'status' and args.session_log and not args.include_session_details:
            result['telemetry'] = public_usage(result['telemetry'])
        # Preserve comparison presentation order; canonical sorting remains for bindings.
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    except (Error, OSError, KeyError, TypeError, ValueError, sqlite3.Error) as e:
        print(json.dumps({'error': str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
