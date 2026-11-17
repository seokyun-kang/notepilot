"""Rename one vault folder and repair everything that referred to it."""
import json
import posixpath
import re
from urllib.parse import quote, unquote, urlsplit

from .vault import FOLDERS, HOME, VaultError, check_folders, paths, save_config

LINK = re.compile(r'(\]\()(<[^>]+>|[^\s)]+)')
OBSIDIAN_KEYS = {'temp': ('app.json', 'newFileFolderPath'), 'assets': ('app.json', 'attachmentFolderPath'),
                 'templates': ('templates.json', 'folder')}


def remap(path, old, new):
    if path == old:
        return new
    return new + path[len(old):] if path.startswith(old + '/') else path


def rewrite_links(text, old_file, new_file, old, new):
    """Recompute relative link destinations for a file that may itself have moved."""
    old_dir, new_dir = posixpath.dirname(old_file) or '.', posixpath.dirname(new_file) or '.'

    def change(match):
        raw = match[2].strip('<>')
        parsed = urlsplit(raw)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return match[0]
        target = posixpath.normpath(posixpath.join(old_dir, unquote(parsed.path)))
        if target.startswith('..'):
            return match[0]
        before = posixpath.relpath(target, old_dir)
        after = posixpath.relpath(remap(target, old, new), new_dir)
        if before == after:
            return match[0]
        dest = quote(after, safe='/.-_') + ('/' if parsed.path.endswith('/') else '')
        dest += ('?' + parsed.query if parsed.query else '') + ('#' + parsed.fragment if parsed.fragment else '')
        return match[1] + ('<' + dest + '>' if match[2].startswith('<') else dest)

    out, fence = [], None
    for line in text.splitlines(keepends=True):
        token = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\n'))
        if token:
            if fence is None:
                fence = token[1]
            elif token[1][0] == fence[0] and len(token[1]) >= len(fence) and not token[2].strip():
                fence = None
            out.append(line)
        elif fence:
            out.append(line)
        else:
            parts = re.split(r'(`+[^`\n]*`+)', line)
            out.append(''.join(part if i % 2 else LINK.sub(change, part) for i, part in enumerate(parts)))
    return ''.join(out)


def rewrite_related(text, old, new):
    """related holds vault-root paths, so only its prefix changes."""
    if not text.startswith('---\n') or '\n---\n' not in text:
        return text
    head, body = text.split('\n---\n', 1)
    lines, inside = [], False
    for line in head.split('\n'):
        key = re.match(r'([a-z][a-z0-9_-]*):', line)
        if key:
            inside = key[1] == 'related'
        if inside:
            line = re.sub(r'(?<=["\'\s\[])' + re.escape(old + '/'), new + '/', line)
        lines.append(line)
    return '\n'.join(lines) + '\n---\n' + body


def rename(root, cfg, role, new, moved=False):
    if role not in FOLDERS:
        raise VaultError('바꿀 수 있는 폴더: ' + ', '.join(FOLDERS))
    new = new.strip('/')
    old = paths(cfg)[role]
    if new == old:
        raise VaultError(f'{role} 폴더는 이미 {old}/ 임')
    folders = {**cfg.get('folders', {}), role: new}
    check_folders(folders)
    source, target = root / old, root / new
    if moved:
        if not target.is_dir():
            raise VaultError(f'{new}/ 폴더가 없음: --moved는 이미 직접 옮긴 뒤에 씀')
        if source.exists():
            raise VaultError(f'{old}/ 가 아직 남아 있음: 내용을 {new}/ 로 합친 뒤 다시 실행')
    else:
        if not source.is_dir():
            raise VaultError(f'{old}/ 폴더가 없음: 이미 직접 옮겼다면 --moved')
        if target.exists():
            raise VaultError(f'{new} 이(가) 이미 있음')
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    lines = [f'{role}: {old}/ → {new}/' + (' (이미 옮겨진 폴더에 맞춤)' if moved else '')]

    changed, stale = 0, []
    for path in sorted(root.rglob('*.md')):
        current = path.relative_to(root).as_posix()
        if any(part.startswith('.') for part in current.split('/')):
            continue
        before = path.read_text(encoding='utf-8')
        text = rewrite_links(before, remap(current, new, old), current, old, new)
        if role == 'notes':
            text = rewrite_related(text, old, new)
            if current == HOME:
                text = text.replace(f'file.inFolder("{old}")', f'file.inFolder("{new}")')
        if text != before:
            path.write_text(text, encoding='utf-8')
            changed += 1
        if re.search(r'(?<![\w/.-])' + re.escape(old) + '/', text):
            stale.append(current)
    lines.append(f'링크·related 수정: {changed}개 파일')

    if folders[role] == FOLDERS[role]:
        del folders[role]
    cfg.pop('folders', None)
    if folders:
        cfg['folders'] = folders
    cfg['excluded_note_paths'] = [remap(value, old, new) for value in cfg['excluded_note_paths']]
    save_config(root, cfg)
    lines.append('설정 수정: notepilot.json')

    if role == 'temp':
        ignore = root / '.gitignore'
        current = ignore.read_text(encoding='utf-8') if ignore.is_file() else ''
        rows = [f'/{new}/' if row.strip() == f'/{old}/' else row for row in current.splitlines()]
        if f'/{new}/' not in rows:
            rows.append(f'/{new}/')
        ignore.write_text('\n'.join(rows) + '\n', encoding='utf-8')
        lines.append(f'.gitignore: /{new}/ 를 추적 제외')
    if role in OBSIDIAN_KEYS:
        name, key = OBSIDIAN_KEYS[role]
        path = root / '.obsidian' / name
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            value = remap(data.get(key), old, new) if isinstance(data.get(key), str) else None
            if value is not None and value != data[key]:
                data[key] = value
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                lines.append(f'Obsidian 설정 수정: .obsidian/{name} (앱이 열려 있으면 다시 열어 확인)')
        except (OSError, ValueError, AttributeError):
            pass
    if stale:
        lines.append(f'본문에 `{old}/` 라는 글자가 남은 파일 (설명 문구라 자동으로 고치지 않음):')
        lines += ['  ' + name for name in stale]
    return lines
