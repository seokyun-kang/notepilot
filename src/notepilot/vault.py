"""Vault parsing, checks and generated regions. Python standard library only.

Frontmatter intentionally supports only a flat subset: strings, dates and
string lists. No arbitrary YAML evaluation, no date or body rewriting.
"""
from collections import defaultdict, deque
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote, unquote, urlsplit

CONFIG = 'notepilot.json'
KIT = '.notepilot'  # tool-owned folder, replaced on upgrade
TYPES = {'concept', 'comparison', 'lab', 'reference', 'index', 'report'}
STATES = {'draft', 'active', 'archived'}
VERIFICATIONS = {'unverified', 'source-checked', 'reproduced', 'needs-review'}
KEYS = ('type', 'tags', 'status', 'created', 'updated', 'related', 'verification')
LISTS = {'tags', 'related', 'aliases'}
MANAGED_REGIONS = ('toc', 'related', 'summary')
CALLOUTS = {'NOTE', 'TIP', 'IMPORTANT', 'WARNING', 'CAUTION'}
# Section names and the home file are fixed so .notepilot is identical in every vault.
# Folders may be renamed through the optional `folders` setting (see `notepilot rename`).
HOME = 'README.md'
FOLDERS = {'notes': 'notes', 'inbox': 'inbox', 'temp': 'temp', 'templates': 'templates', 'assets': 'assets'}
SECTIONS = {'toc': '목차', 'evidence': '근거와 참고 자료', 'related': '관련 문서'}
DEFAULT_CONFIG = {
    'schema_version': 1,
    'tag_pattern': '[a-z0-9]+(?:[-/][a-z0-9]+)*',
    'excluded_note_paths': [],
    'skills': [],
}
# Pattern-based only: catches common token shapes, not every possible secret.
SECRET_PATTERNS = (
    ('Slack 토큰', r'xox[abeprs]-[A-Za-z0-9-]{10,}'),
    ('Slack 앱 토큰', r'xapp-\d-[A-Z0-9]+-\d+-[a-f0-9]{16,}'),
    ('GitHub 토큰', r'(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})'),
    ('AWS 액세스 키', r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
    ('API 키(sk-)', r'\bsk-[A-Za-z0-9_-]{20,}'),
    ('Google API 키', r'\bAIza[0-9A-Za-z_-]{35}\b'),
    ('개인 키 블록', r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
)
TEXT_SUFFIXES = {'', '.md', '.txt', '.json', '.yml', '.yaml', '.toml', '.base', '.canvas', '.csv',
                 '.py', '.sh', '.env', '.ini', '.cfg', '.js', '.ts', '.html', '.xml'}


class VaultError(Exception):
    pass


def paths(cfg):
    """Actual folder names of this vault: defaults overridden by `folders`."""
    return {**FOLDERS, **cfg.get('folders', {})}


def check_folders(folders):
    """Validate a `folders` override mapping."""
    if not isinstance(folders, dict) or set(folders) - set(FOLDERS):
        raise VaultError('folders에는 ' + '·'.join(FOLDERS) + '만 쓸 수 있음')
    actual = {**FOLDERS, **folders}
    for role, value in actual.items():
        parts = Path(value).parts if isinstance(value, str) else ()
        if (not parts or '\\' in value or value != '/'.join(parts) or '..' in parts or Path(value).is_absolute()
                or parts[0].startswith('.') or any(x in value for x in ('#', '^', '|', '"'))):
            raise VaultError(f'folders.{role}: 보관함 안의 상대 폴더 이름이어야 하며 점으로 시작할 수 없음')
    names = list(actual.values())
    for i, one in enumerate(names):
        for other in names[i + 1:]:
            if one == other or one.startswith(other + '/') or other.startswith(one + '/'):
                raise VaultError(f'folders: {one} 와 {other} 는 같거나 서로 포함할 수 없음')


def find_root(start=None):
    start = Path(start or Path.cwd()).resolve()
    for folder in (start, *start.parents):
        if (folder / CONFIG).is_file():
            return folder
    raise VaultError(CONFIG + '을 찾을 수 없음; 보관함 안에서 실행하거나 --vault로 지정하세요')


def load_config(root):
    try:
        cfg = json.loads((root / CONFIG).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise VaultError(CONFIG + ' 없음: ' + str(root))
    except ValueError as exc:
        raise VaultError(CONFIG + ' JSON 오류: ' + str(exc))
    if cfg.get('schema_version') != 1:
        raise VaultError('지원하지 않는 schema_version; notepilot을 최신으로 올리세요')
    if 'sections' in cfg or 'paths' in cfg:
        raise VaultError('paths·sections 설정은 지원하지 않음: 절 이름은 고정, 폴더 이름은 folders로 지정')
    check_folders(cfg.get('folders', {}))

    def check_path(value):
        if not isinstance(value, str) or not value or '\\' in value:
            raise VaultError('경로는 비어 있지 않은 보관함 상대 POSIX 경로여야 함')
        path = Path(value)
        if path.is_absolute() or '..' in path.parts or not (root / path).resolve().is_relative_to(root):
            raise VaultError('보관함 밖 경로 금지: ' + value)

    try:
        re.compile(cfg.get('tag_pattern') or '(')
    except re.error:
        raise VaultError('tag_pattern 설정 오류')
    excluded = cfg.setdefault('excluded_note_paths', [])
    if not isinstance(excluded, list) or any(not isinstance(v, str) for v in excluded):
        raise VaultError('excluded_note_paths는 상대 경로 문자열 목록이어야 함')
    notes_root = (root / paths(cfg)['notes']).resolve()
    for value in excluded:
        check_path(value)
        target = (root / value).resolve()
        if target == notes_root or not target.is_relative_to(notes_root):
            raise VaultError('제외 경로는 정식 노트 폴더 안의 하위 경로여야 함')
    skills = cfg.setdefault('skills', [])
    if not isinstance(skills, list) or any(not isinstance(v, str) for v in skills):
        raise VaultError('skills는 문자열 목록이어야 함')
    return cfg


def save_config(root, cfg):
    (root / CONFIG).write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def scalar(raw):
    raw = raw.strip()
    if not raw or raw in ('null', '~'):
        return None
    if raw.startswith(('"', '[')) or raw in ('true', 'false'):
        value = json.loads(raw)
        if isinstance(value, list) and not all(isinstance(x, str) for x in value):
            raise ValueError('목록에는 문자열만 허용')
        return value
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("''", "'")
    if raw.startswith(('{', '|', '>', '&', '*', '!')) or ': ' in raw or ' #' in raw:
        raise ValueError('지원하지 않는 YAML 값; 문자열을 큰따옴표로 작성')
    return raw


def frontmatter(text):
    if not text.startswith('---\n'):
        raise ValueError('frontmatter 없음')
    parts = text.split('\n---\n', 1)
    if len(parts) != 2:
        raise ValueError('frontmatter 닫는 구분자 없음')
    meta = {}
    current = None
    for line in parts[0].splitlines()[1:]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        item = re.fullmatch(r'\s+-\s+(.*)', line)
        if item:
            if current not in LISTS:
                raise ValueError('목록 속성이 아닌 블록 목록')
            if meta[current] is None:
                meta[current] = []
            if not isinstance(meta[current], list):
                raise ValueError('목록의 인라인/블록 혼용')
            value = scalar(item[1])
            if not isinstance(value, str):
                raise ValueError('목록 값은 문자열이어야 함')
            meta[current].append(value)
            continue
        match = re.fullmatch(r'([a-z][a-z0-9_-]*):(?:\s+(.*))?', line)
        if not match or match[1] in meta:
            raise ValueError('잘못된 속성 또는 중복 키: ' + line)
        current = match[1]
        meta[current] = scalar(match[2] or '')
    for key in LISTS & meta.keys():
        if meta[key] is None:
            meta[key] = []  # Obsidian may save an emptied list property as a blank value.
    return meta, parts[1].lstrip('\n')


def prose(text):
    """Remove fenced code and comments while preserving line count."""
    lines = []
    fence = None
    for line in text.splitlines():
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line)
        if marker:
            token, tail = marker.groups()
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not tail.strip():
                fence = None
            lines.append('')
        else:
            lines.append('' if fence else line)
    clean = re.sub(r'<!--.*?-->', lambda m: '\n' * m[0].count('\n'), '\n'.join(lines), flags=re.S)
    return clean, fence


def bare_fences(text):
    """1-based line numbers of opening code fences that name no language."""
    found, fence = [], None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line)
        if not marker:
            continue
        token, tail = marker.groups()
        if fence is None:
            fence = token
            if not tail.strip():
                found.append(number)
        elif token[0] == fence[0] and len(token) >= len(fence) and not tail.strip():
            fence = None
    return found


def headings(text):
    clean, _ = prose(text)
    result = []
    for line in clean.splitlines():
        match = re.match(r'^(#{1,6})\s+(.+?)(?:\s+#+)?$', line)
        if match:
            result.append((len(match[1]), match[2].strip()))
    return result


def heading_link_error(title, titles):
    """Reject unsafe or ambiguous heading targets; do not rewrite source titles."""
    unsafe = [token for token in ('#', '|', '^', ':', '%%', '[[', ']]') if token in title]
    if unsafe:
        return '제목 링크에 부적합한 기호 ' + ', '.join(unsafe) + ': ' + title
    count = sum(candidate.casefold() == title.casefold() for candidate in titles)
    if count == 0:
        return '절 없음: ' + title
    if count > 1:
        return '제목 링크 대상 중복: ' + title
    return ''


def heading_fragment(title):
    """Obsidian resolves the original heading, not GitHub's hyphenated slug.

    A plain heading with no spaces/formatting shares its fragment with GitHub.
    Otherwise Obsidian takes precedence; GitHub's native outline stays available.
    """
    error = heading_link_error(title, [title])
    if error:
        raise ValueError(error)
    target = title.lower() if re.fullmatch(r'[\w-]+', title) else title
    return quote(target, safe='-_.~')


def label(text):
    return text.replace('[', '\\[').replace(']', '\\]').replace('|', '\\|')


def make_toc(body, toc_heading):
    if '<!-- toc:start -->' not in body:
        return ''
    return '\n'.join(('  ' if depth == 3 else '') + f'- [{label(title)}](#{heading_fragment(title)})'
                     for depth, title in headings(body)
                     if depth in (2, 3) and title != toc_heading)


def relative(source, target):
    return quote(os.path.relpath(target, source.parent).replace(os.sep, '/'), safe='/.-_')


def replace_region(text, name, value):
    # Only standalone markers outside fenced code are managed.
    masked = []
    fence = None
    for line in text.splitlines(keepends=True):
        token = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\n'))
        was_inside = fence is not None
        if token:
            if fence is None:
                fence = token[1]
            elif token[1][0] == fence[0] and len(token[1]) >= len(fence) and not token[2].strip():
                fence = None
        hidden = was_inside or fence is not None or token is not None
        masked.append(re.sub(r'[^\n]', ' ', line) if hidden else line)
    masked = ''.join(masked)
    starts = list(re.finditer(r'^<!-- ' + re.escape(name) + r':start -->[ \t]*$', masked, re.M))
    ends = list(re.finditer(r'^<!-- ' + re.escape(name) + r':end -->[ \t]*$', masked, re.M))
    if not starts and not ends:
        return text
    if len(starts) != 1 or len(ends) != 1 or starts[0].start() > ends[0].start():
        raise ValueError('관리 주석 오류: ' + name)
    value = value.strip()
    return text[:starts[0].end()] + '\n' + (value + '\n' if value else '') + text[ends[0].start():]


def get_links(text):
    clean, _ = prose(text)
    clean = re.sub(r'`+[^`\n]*`+', '', clean)
    # Destinations created by this workflow are URL encoded, including parentheses.
    return [m[1].strip('<>') for m in
            re.finditer(r'!?\[(?:\\.|[^\]\\])*\]\((<[^>]+>|[^\s)]+)(?:\s+"[^"\n]*")?\)', clean)]


def validate_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def section_bodies(body):
    """Read H2 section boundaries outside code/comments, preserving source bodies."""
    clean, _ = prose(body)
    matches, offset = [], 0
    for original, filtered in zip(body.splitlines(keepends=True), clean.splitlines()):
        match = re.fullmatch(r'##\s+(.+?)(?:\s+#+)?', filtered)
        if match:
            matches.append((match[1].strip(), offset, offset + len(original)))
        offset += len(original)
    return {title: body[end: matches[i + 1][1] if i + 1 < len(matches) else len(body)]
            for i, (title, start, end) in enumerate(matches)}


def region_pair(text, name):
    return (len(re.findall(r'^<!-- ' + name + r':start -->[ \t]*$', text, re.M)) == 1
            and len(re.findall(r'^<!-- ' + name + r':end -->[ \t]*$', text, re.M)) == 1)


def validate_layout(body, meta, cfg, template=False):
    """Enforce the portable common frame; do not prescribe subject-specific prose."""
    errors = []
    names = SECTIONS
    clean, _ = prose(body)
    blocks = section_bodies(body)
    h1 = re.search(r'^# [^\n]+\n', clean, re.M)
    lead = clean[h1.end():].lstrip() if h1 else ''
    if not lead.startswith('> [!NOTE]\n'):
        errors.append('H1 바로 뒤에 NOTE 요약 필요 (> [!NOTE])')
    elif not template:
        summary = []
        for line in lead.splitlines()[1:]:
            if not line.startswith('>'):
                break
            summary.append(re.sub(r'^> ?', '', line))
        if not '\n'.join(summary).strip():
            errors.append('NOTE 요약 내용 없음')
    for match in re.finditer(r'^>\s*\[!([^\]]+)\]([^\n]*)$', clean, re.M):
        if match[1] not in CALLOUTS or match[2].strip():
            errors.append('Callout은 NOTE·TIP·IMPORTANT·WARNING·CAUTION만, 첫 줄은 단독으로: ' + match[0].strip())
    ordered = [title for depth, title in headings(body) if depth == 2]
    required = [names['evidence'], names['related']]
    missing = [title for title in required if title not in blocks]
    if missing:
        errors.append('필수 절 누락: ' + ', '.join(missing))
    elif ordered[-2:] != required:
        errors.append('마지막 두 절은 ' + ' → '.join(required) + ' 순서여야 함')
    if not region_pair(blocks.get(names['related'], ''), 'related'):
        errors.append(names['related'] + ' 절에 related 관리 영역 한 쌍 필요')
    own = [title for title in ordered if title not in required and title != names['toc']]
    if (len(own) >= 2 or template) and not region_pair(blocks.get(names['toc'], ''), 'toc'):
        errors.append('본문 H2가 둘 이상이면 ' + names['toc'] + ' 절과 toc 관리 영역 필요')
    if template:
        return errors
    evidence = blocks.get(names['evidence'], '')
    if not prose(evidence)[0].strip():
        errors.append(names['evidence'] + ' 내용 필요 (미확인이면 그 사실을 적음)')
    if meta.get('verification') == 'source-checked' and not any(
            urlsplit(url).scheme in {'http', 'https'} for url in get_links(evidence)):
        errors.append('source-checked는 ' + names['evidence'] + '에 확인한 자료 링크 필요')
    local = [url for url in get_links(body)
             if not urlsplit(url).scheme and not urlsplit(url).netloc and unquote(urlsplit(url).path).endswith('.md')]
    if local and not meta.get('related') and meta.get('type') not in {'index', 'reference'}:
        errors.append('본문에 연결한 관련 노트를 related 속성에도 선별해 기록해야 함')
    return errors


def note_files(root, cfg):
    notes_root = root / paths(cfg)['notes']
    excluded = [(root / value).resolve() for value in cfg['excluded_note_paths']]
    for path in sorted(notes_root.rglob('*.md')):
        target = path.resolve()
        if not any(target == folder or target.is_relative_to(folder) for folder in excluded):
            yield path


def domain_of(path, root, cfg):
    parts = path.relative_to(root / paths(cfg)['notes']).parts
    return parts[0] if len(parts) > 1 else '—'


def scan(root, cfg):
    """Validate the vault and render generated regions without writing.

    Returns (errors, rendered, notes). rendered maps path -> final text.
    """
    errors, notes, original = [], {}, {}
    names = SECTIONS
    for path in note_files(root, cfg):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding='utf-8')
        original[path] = text
        try:
            meta, body = frontmatter(text)
            notes[path] = meta
            for key in KEYS:
                if key not in meta:
                    errors.append(f'{rel}: 필수 속성 없음 {key}')
            extra = set(meta) - set(KEYS) - {'aliases'}
            if extra:
                errors.append(f'{rel}: 허용하지 않은 속성 ' + ', '.join(sorted(extra)))
            for key, values in (('type', TYPES), ('status', STATES), ('verification', VERIFICATIONS)):
                if not isinstance(meta.get(key), str) or meta.get(key) not in values:
                    errors.append(f'{rel}: 잘못된 {key} (허용: ' + ' / '.join(sorted(values)) + ')')
            for key in LISTS & meta.keys():
                if not isinstance(meta[key], list) or not all(isinstance(x, str) for x in meta[key]):
                    errors.append(f'{rel}: {key}는 문자열 목록이어야 함')
                    meta[key] = []
                elif len(meta[key]) != len(set(meta[key])):
                    errors.append(f'{rel}: {key} 중복 값')
            for tag in meta.get('tags') or []:
                if not re.fullmatch(cfg['tag_pattern'], tag):
                    errors.append(f'{rel}: 잘못된 태그 {tag}')
            for key in ('created', 'updated'):
                if not validate_date(meta.get(key)):
                    errors.append(f'{rel}: 잘못된 날짜 {key} (YYYY-MM-DD)')
            if validate_date(meta.get('created')) and validate_date(meta.get('updated')) and meta['created'] > meta['updated']:
                errors.append(f'{rel}: created가 updated보다 늦음')
            hs = headings(body)
            lowered = [h[1].casefold() for h in hs]
            if len(lowered) != len(set(lowered)):
                errors.append(f'{rel}: 같은 제목의 절 중복; 구별 가능한 제목 사용 필요')
            if [h[1] for h in hs if h[0] == 1] != [path.stem]:
                errors.append(f'{rel}: 단일 H1이 파일명과 같아야 함 (# {path.stem})')
            clean, unclosed = prose(body)
            if unclosed:
                errors.append(f'{rel}: 닫히지 않은 코드 펜스')
            bare = bare_fences(text)
            if bare:
                errors.append(f'{rel}: 코드 블록에 언어 지정 필요 (언어가 없으면 text): ' + ', '.join(map(str, bare)) + '번째 줄')
            if '{{' in clean or re.search(r'<!--(?!\s*(?:toc|related):).*?-->', body, re.S):
                errors.append(f'{rel}: 작성 자리표시자 또는 안내 주석 잔존')
            if len(re.sub(r'^#{1,6} .*$', '', clean, flags=re.M).strip()) < 20:
                errors.append(f'{rel}: 본문 내용 부족')
            errors.extend(f'{rel}: {error}' for error in validate_layout(body, meta, cfg))
        except (ValueError, TypeError) as exc:
            errors.append(f'{rel}: {exc}')
    stems = defaultdict(list)
    for path in notes:
        stems[path.stem.casefold()].append(path)
    for same in stems.values():
        if len(same) > 1:
            errors.append('중복 제목(파일명): ' + ', '.join(p.relative_to(root).as_posix() for p in same))
    # Fail before rendering when metadata or required structure is invalid.
    if errors:
        return errors, {}, notes

    notes_root = (root / paths(cfg)['notes']).resolve()
    resolved = {path.resolve(): path for path in notes}
    rendered = {}
    for path, meta in notes.items():
        text = replace_region(original[path], 'toc', make_toc(frontmatter(original[path])[1], names['toc']))
        links = []
        for dest in meta['related']:
            target = (root / dest).resolve()
            if not target.is_relative_to(notes_root) or target not in resolved or target == path.resolve():
                errors.append(f'{path.relative_to(root).as_posix()}: 잘못된 related {dest} (보관함 기준 경로의 다른 정식 노트)')
                continue
            links.append(f'- [{label(target.stem)}]({relative(path, target)})')
        rendered[path] = replace_region(text, 'related', '\n'.join(links))

    if not (root / paths(cfg)['notes']).is_dir():
        errors.append(paths(cfg)['notes'] + '/ 폴더 없음: 직접 이름을 바꿨다면 `notepilot rename notes <새 이름> --moved`')
    home = root / HOME
    if not home.is_file():
        errors.append('진입 문서 없음: ' + HOME)
    else:
        active = {p: m for p, m in notes.items() if m['type'] != 'index' and m['status'] != 'archived'}
        by_domain = defaultdict(list)
        for path, meta in active.items():
            by_domain[domain_of(path, root, cfg)].append(meta['updated'])
        rows = ['| 분야 | 문서 수 | 최근 수정일 |', '| --- | ---: | --- |']
        rows += [f'| {name} | {len(days)} | {max(days)} |' for name, days in sorted(by_domain.items())]
        latest = max((m['updated'] for m in active.values()), default='—')
        rows.append(f'| **합계** | **{len(active)}** | **{latest}** |')
        rendered[home] = replace_region(home.read_text(encoding='utf-8'), 'summary', '\n'.join(rows))

    templates = root / paths(cfg)['templates']
    for path in sorted(templates.glob('*.md')) if templates.is_dir() else []:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding='utf-8')
        try:
            body = frontmatter(text)[1]
            errors.extend(f'{rel}: {error}' for error in validate_layout(body, {}, cfg, template=True))
            rendered[path] = replace_region(text, 'toc', make_toc(body, names['toc']))
        except ValueError as exc:
            errors.append(f'{rel}: {exc}')
    rules = KIT + '/RULES.md'
    if not (root / rules).is_file():
        errors.append(rules + ' 없음: `notepilot upgrade`로 복구')
    agents = root / 'AGENTS.md'
    if not agents.is_file():
        errors.append('AGENTS.md 없음: 에이전트가 규약을 찾을 수 없음')
    else:
        rendered[agents] = agents.read_text(encoding='utf-8')
        if rules not in rendered[agents]:
            errors.append('AGENTS.md에 ' + rules + ' 참조가 없어 에이전트가 공통 규약을 읽지 못함')

    # Validate every real link, including headings, before writing generated output.
    graph = defaultdict(set)
    for path, text in rendered.items():
        rel = path.relative_to(root).as_posix()
        try:
            for name in MANAGED_REGIONS:
                replace_region(text, name, '')
        except ValueError as exc:
            errors.append(f'{rel}: {exc}')
        clean, _ = prose(text)
        clean = re.sub(r'`+[^`\n]*`+', '', clean)
        for wiki in re.findall(r'!?\[\[([^\]]+)\]\]', clean):
            errors.append(f'{rel}: 위키 링크 대신 상대 경로 Markdown 링크 사용: [[{wiki}]]')
        for url in get_links(text):
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc:
                continue
            target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path.resolve()
            if not target.is_relative_to(root.resolve()):
                errors.append(f'{rel}: 보관함 밖 링크 {url}')
                continue
            if not target.exists():
                errors.append(f'{rel}: 링크 대상 없음 {url}')
                continue
            graph[path.resolve()].add(target)
            if parsed.fragment and target.suffix == '.md':
                source = resolved.get(target, target)
                target_text = rendered.get(source, target.read_text(encoding='utf-8'))
                if target_text.startswith('---\n'):
                    try:
                        target_text = frontmatter(target_text)[1]
                    except ValueError:
                        pass
                problem = heading_link_error(unquote(parsed.fragment), [h[1] for h in headings(target_text)])
                if problem:
                    errors.append(f'{rel}: {problem} ({url})')
    reached, queue = set(), deque([home.resolve()])
    while queue:
        current = queue.popleft()
        if current in reached:
            continue
        reached.add(current)
        queue.extend(graph[current] - reached)
    for target, path in resolved.items():
        if target not in reached:
            errors.append(f"{HOME}에서 링크로 도달할 수 없음: {path.relative_to(root).as_posix()}")
    return errors, rendered, notes


def pending(rendered):
    return [path for path, text in rendered.items() if path.read_text(encoding='utf-8') != text]


def git_files(root, staged=False):
    """Paths git would track. None when the vault is not a git repository."""
    if not (root / '.git').exists():
        return None
    command = (['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'] if staged
               else ['git', 'ls-files', '-co', '--exclude-standard', '-z'])
    try:
        out = subprocess.run(command, cwd=root, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [name for name in out.decode('utf-8', 'replace').split('\0') if name]


def scan_secrets(root, cfg, staged=False):
    """Return 'path:line: kind' findings without echoing the secret itself."""
    names = git_files(root, staged)
    if staged and names is None:
        raise VaultError('git 저장소가 아니어서 --staged를 쓸 수 없음')
    if names is None:
        temp = paths(cfg)['temp']
        names = [rel for rel in (p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())
                 if rel.split('/')[0] not in {'.git', '.obsidian'} and not rel.startswith(temp + '/')]
    findings = []
    for name in sorted(names):
        path = root / name
        if Path(name).suffix.lower() not in TEXT_SUFFIXES and not Path(name).name.startswith('.env'):
            continue
        try:
            if staged:
                data = subprocess.run(['git', 'show', ':' + name], cwd=root, capture_output=True, check=True).stdout
            else:
                if not path.is_file() or path.stat().st_size > 2_000_000:
                    continue
                data = path.read_bytes()
        except (OSError, subprocess.CalledProcessError):
            continue
        text = data.decode('utf-8', 'replace')
        for number, line in enumerate(text.splitlines(), 1):
            for kind, pattern in SECRET_PATTERNS:
                match = re.search(pattern, line)
                if match and 'EXAMPLE' not in match[0].upper():
                    findings.append(f'{name}:{number}: {kind} 의심 ({match[0][:6]}…)')
    return findings


def report(root, cfg, days=30):
    """Non-failing content health list. Candidates for a human or agent to review."""
    errors, rendered, notes = scan(root, cfg)
    now = date.today()

    def age(value):
        return (now - date.fromisoformat(value)).days if validate_date(value) else None

    def link(path):
        return path.relative_to(root).as_posix()

    out = [f'# 보관함 점검 ({now.isoformat()})', '',
           '검사 오류가 아니라 사람이 판단할 후보 목록이다. 내용의 사실 여부는 확인하지 않는다.', '']
    if errors:
        out += [f'> 구조 오류 {len(errors)}개가 있어 일부 항목이 부정확할 수 있음. 먼저 `notepilot check`.', '']
    counts = defaultdict(int)
    for meta in notes.values():
        counts[meta.get('verification')] += 1
    out += ['## 검증 상태', ''] + [f'- {key}: {counts[key]}' for key in sorted(VERIFICATIONS)] + ['']

    def listing(title, rows, empty='없음'):
        out.extend(['## ' + title, ''] + ([f'- {row}' for row in rows] or [empty]) + [''])

    stale = sorted(((age(m.get('updated')), p) for p, m in notes.items()
                    if m.get('status') == 'draft' and (age(m.get('updated')) or 0) >= days), reverse=True)
    listing(f'{days}일 넘게 draft인 노트', [f'{link(p)} ({d}일)' for d, p in stale])
    review = sorted(((age(m.get('updated')) or 0, p) for p, m in notes.items()
                     if m.get('verification') == 'needs-review'), reverse=True)
    listing('needs-review 노트', [f'{link(p)} ({d}일)' for d, p in review])
    old = sorted(((age(m.get('updated')) or 0, p) for p, m in notes.items()
                  if m.get('verification') == 'unverified' and m.get('status') == 'active'), reverse=True)[:20]
    listing('오래된 unverified 노트 (최대 20개)', [f'{link(p)} ({d}일)' for d, p in old])

    resolved = {p.resolve(): p for p in notes}
    outgoing = {}
    for path in notes:
        targets = set()
        for url in get_links(path.read_text(encoding='utf-8')):
            parsed = urlsplit(url)
            if not parsed.scheme and not parsed.netloc and parsed.path:
                targets.add((path.parent / unquote(parsed.path)).resolve())
        outgoing[path] = targets
    inbound = defaultdict(set)
    for path, targets in outgoing.items():
        if notes[path].get('type') == 'index':
            continue
        for target in targets:
            if target in resolved and resolved[target] != path:
                inbound[resolved[target]].add(path)
    lonely = [p for p, m in notes.items() if m.get('type') != 'index' and not inbound[p]]
    listing('index 말고는 들어오는 링크가 없는 노트', [link(p) for p in sorted(lonely)])

    mentions = []
    for path, meta in notes.items():
        text = prose(path.read_text(encoding='utf-8'))[0]
        text = re.sub(r'\[(?:\\.|[^\]\\])*\]\([^)]*\)', '', text)
        for other, other_meta in notes.items():
            if other == path or other.resolve() in outgoing[path] or other_meta.get('type') == 'index':
                continue
            for name in [other.stem, *(other_meta.get('aliases') or [])]:
                if len(name) >= 4 and re.search(r'(?<![A-Za-z0-9_-])' + re.escape(name) + r'(?![A-Za-z0-9_-])', text, re.I):
                    mentions.append(f'{link(path)} → {link(other)} ("{name}")')
                    break
    listing('링크 없이 언급만 된 노트 후보 (최대 30개)', sorted(mentions)[:30])

    inbox = root / paths(cfg)['inbox']
    waiting = []
    for path in sorted(inbox.rglob('*.md')) if inbox.is_dir() else []:
        waited = (now - date.fromtimestamp(path.stat().st_mtime)).days
        if waited >= days:
            waiting.append(f'{link(path)} ({waited}일)')
    listing(f'{days}일 넘게 inbox에 있는 메모', waiting)
    return '\n'.join(out)


AGENTS_BLANKS = ('무엇을 모으는 보관함인지 한두 문장으로 적는다.', '\n| | |\n')


def setup_warnings(root, cfg):
    """Setup gaps that never fail a check: unfilled AGENTS.md, missing folders, Obsidian settings.

    Obsidian's JSON keys are not documented, so anything missing or
    unreadable there is skipped silently.
    """
    def load(name):
        try:
            data = json.loads((root / '.obsidian' / name).read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    actual, app, warnings = paths(cfg), load('app.json'), []
    agents = root / 'AGENTS.md'
    if agents.is_file() and any(blank in agents.read_text(encoding='utf-8') for blank in AGENTS_BLANKS):
        warnings.append('AGENTS.md의 용도·분야가 비어 있음: 채워야 에이전트가 이 보관함의 분류를 따름')
    for role, name in actual.items():
        if role != 'notes' and not (root / name).is_dir():
            warnings.append(f'{name}/ 폴더 없음: 직접 이름을 바꿨다면 `notepilot rename {role} <새 이름> --moved`')
    if 'newFileLocation' in app and (app['newFileLocation'] != 'folder' or app.get('newFileFolderPath') != actual['temp']):
        warnings.append(f"Obsidian의 새 노트 기본 위치가 {actual['temp']}/ 가 아님: 새 파일이 git에 추적되는 곳에 생길 수 있음")
    where = app.get('attachmentFolderPath')
    if isinstance(where, str) and where != actual['assets'] and not where.startswith(actual['assets'] + '/'):
        warnings.append(f"Obsidian의 첨부 기본 위치({where})가 {actual['assets']}/ 밖임")
    folder = load('templates.json').get('folder')
    if isinstance(folder, str) and folder != actual['templates']:
        warnings.append(f"Obsidian의 템플릿 폴더({folder})가 {actual['templates']}/ 와 다름")
    return warnings
