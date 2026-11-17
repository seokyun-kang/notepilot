"""Create a vault and upgrade the tool-owned .notepilot folder.

Everything under .notepilot belongs to the tool and is replaced on upgrade.
Everything else belongs to the user. Templates and skills need a visible copy
(Obsidian and agents do not read .notepilot), so a copy follows its original
only while the user has not edited it.
"""
import copy
from datetime import date
import json
from pathlib import Path
import re
import subprocess

from . import __version__
from .vault import CONFIG, DEFAULT_CONFIG, HOME, KIT, VaultError, paths, pending, save_config, scan

DATA = Path(__file__).parent / 'data'
TEMPLATES = ('note.md', 'comparison.md', 'lab.md')
TEMPLATE_FOR = {'comparison': 'comparison.md', 'lab': 'lab.md'}  # every other type uses note.md
BAD_TITLE = ('/', '\\', ':', '*', '?', '"', '<', '>', '|', '#', '^', '[', ']')
SKILL_PATHS = {'claude-code': '.claude/skills/note/SKILL.md', 'codex': '.agents/skills/note/SKILL.md'}
IGNORE_LINES = ('.DS_Store', '.obsidian/workspace*.json', '.obsidian/cache/', '.trash/')
HOOK = '''#!/bin/sh
# notepilot: 비밀 값으로 보이는 내용이 스테이징되면 커밋을 막는다.
if ! command -v notepilot >/dev/null 2>&1; then
  echo "notepilot 명령을 찾을 수 없어 비밀 값 검사를 못 했습니다." >&2
  echo "notepilot을 설치하거나, 검사를 건너뛰려면 git commit --no-verify 를 쓰세요." >&2
  exit 1
fi
exec notepilot secrets --staged
'''


def read(name):
    return (DATA / name).read_text(encoding='utf-8')


def kit_files():
    """Tool-owned originals under .notepilot: path -> content."""
    files = {KIT + '/RULES.md': read('RULES.md'),
             KIT + '/skill/SKILL.md': read('skill/SKILL.md'),
             KIT + '/version': __version__ + '\n'}
    for name in TEMPLATES:
        files[f'{KIT}/templates/{name}'] = read('templates/' + name)
    return files


def copies(cfg):
    """Visible copies that follow an original until the user edits them: copy -> original."""
    unknown = [name for name in cfg['skills'] if name not in SKILL_PATHS]
    if unknown:
        raise VaultError('알 수 없는 skills 값: ' + ', '.join(unknown) + ' (허용: ' + ', '.join(sorted(SKILL_PATHS)) + ')')
    result = {f"{paths(cfg)['templates']}/{name}": f'{KIT}/templates/{name}' for name in TEMPLATES}
    result.update({SKILL_PATHS[name]: KIT + '/skill/SKILL.md' for name in cfg['skills']})
    return result


def initial_files(cfg):
    """Files created once and then owned by the user."""
    names = paths(cfg)
    ignore = '\n'.join(('# 임시 작업 공간: 키·붙여넣기 등 추적하지 않을 내용', '/' + names['temp'] + '/', '',
                        '# 기기별 상태', *IGNORE_LINES)) + '\n'
    files = {
        'AGENTS.md': read('AGENTS.md'),
        HOME: read('home.md'),
        '.gitignore': ignore,
        names['notes'] + '/.gitkeep': '',
        names['inbox'] + '/.gitkeep': '',
        names['assets'] + '/.gitkeep': '',
        '.obsidian/app.json': json.dumps({
            'alwaysUpdateLinks': True, 'useMarkdownLinks': True, 'newLinkFormat': 'relative',
            'newFileLocation': 'folder', 'newFileFolderPath': names['temp'],
            'attachmentFolderPath': names['assets']}, indent=2) + '\n',
        '.obsidian/templates.json': json.dumps({'folder': names['templates']}, ensure_ascii=False, indent=2) + '\n',
        # Undocumented format, taken from files Obsidian writes: lets Bases sort created/updated as dates.
        '.obsidian/types.json': json.dumps({'types': {
            'aliases': 'aliases', 'tags': 'tags', 'related': 'multitext', 'created': 'date', 'updated': 'date',
            'type': 'text', 'status': 'text', 'verification': 'text'}}, indent=2) + '\n',
        '.obsidian/core-plugins.json': json.dumps(
            ['file-explorer', 'global-search', 'switcher', 'backlink', 'tag-pane', 'properties',
             'page-preview', 'outline', 'templates', 'bases'], indent=2) + '\n',
    }
    if 'claude-code' in cfg['skills']:
        # Claude Code reads CLAUDE.md, not AGENTS.md; imports load both files for real.
        files['CLAUDE.md'] = f'@AGENTS.md\n@{KIT}/RULES.md\n'
    return files


def install_hook(root):
    """Return a short status line. Never overwrites a foreign hook."""
    hooks = root / '.git' / 'hooks'
    if not (root / '.git').is_dir():
        return 'git 저장소가 아님: 훅을 설치하지 않음'
    path = hooks / 'pre-commit'
    if path.exists() and path.read_text(encoding='utf-8', errors='replace') != HOOK:
        return '기존 pre-commit 훅이 있어 건드리지 않음: 직접 `notepilot secrets --staged`를 추가하세요'
    hooks.mkdir(parents=True, exist_ok=True)
    path.write_text(HOOK, encoding='utf-8')
    path.chmod(0o755)
    return 'pre-commit 훅 설치: 커밋 전에 비밀 값 패턴 검사'


def init(target, skills=(), git=False, dry_run=False):
    root = Path(target).expanduser().resolve()
    if (root / CONFIG).exists():
        raise VaultError('이미 notepilot 보관함임: 갱신은 `notepilot upgrade`')
    if root.exists() and not root.is_dir():
        raise VaultError('대상이 폴더가 아님: ' + str(root))
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg['skills'] = list(dict.fromkeys(skills))
    originals = kit_files()
    files = {**initial_files(cfg), **originals,
             **{name: originals[source] for name, source in copies(cfg).items()}}
    conflicts, plan = [], []
    for name, content in sorted(files.items()):
        path = root / name
        if not path.exists():
            plan.append((name, content))
        elif name == '.gitignore':
            current = path.read_text(encoding='utf-8')
            wanted = ['/' + paths(cfg)['temp'] + '/', *IGNORE_LINES]
            missing = [line for line in wanted if line not in current.splitlines()]
            if missing:
                plan.append((name, current.rstrip('\n') + '\n\n# notepilot\n' + '\n'.join(missing) + '\n'))
        elif path.is_dir() or path.read_text(encoding='utf-8', errors='replace') != content:
            conflicts.append(name)
    if conflicts:
        raise VaultError('기존 파일과 충돌해 아무것도 만들지 않음:\n' + '\n'.join('  ' + c for c in conflicts))
    word = '만들 예정: ' if dry_run else '생성: '
    lines = [word + name for name, _ in plan] + [word + CONFIG, word + paths(cfg)['temp'] + '/ (git 미추적)']
    if dry_run:
        if git:
            lines.append('예정: git init · pre-commit 훅')
        return lines
    for name, content in plan:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    (root / paths(cfg)['temp']).mkdir(parents=True, exist_ok=True)
    save_config(root, cfg)
    # Fill generated regions so a fresh vault passes check as created.
    errors, rendered, _ = scan(root, cfg)
    if not errors:
        for path in pending(rendered):
            path.write_text(rendered[path], encoding='utf-8')
    if git:
        if not (root / '.git').exists():
            try:
                subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
                lines.append('git init 완료')
            except (OSError, subprocess.CalledProcessError) as exc:
                lines.append('git init 실패: ' + str(exc))
        lines.append(install_hook(root))
    return lines


def new_note(root, cfg, domain, title, kind='concept'):
    """Create a formal note from the vault's template. Never overwrites, never edits other files."""
    names, title = paths(cfg), title.strip()
    if not title or title.startswith('.') or any(c in title for c in BAD_TITLE):
        raise VaultError('제목은 파일명이 됨: 점으로 시작하거나 다음 문자를 쓸 수 없음 ' + ' '.join(BAD_TITLE))
    if not domain or domain.startswith('.') or any(c in domain for c in '/\\'):
        raise VaultError(f"분야는 {names['notes']}/ 바로 아래 폴더 이름 하나여야 함")
    notes_root = root / names['notes']
    if notes_root.is_dir() and any(p.stem.casefold() == title.casefold() for p in notes_root.rglob('*.md')):
        raise VaultError('같은 제목의 노트가 이미 있음: ' + title)
    name = TEMPLATE_FOR.get(kind, 'note.md')
    source = root / names['templates'] / name
    if not source.is_file():
        source = root / KIT / 'templates' / name
    text = re.sub(r'\{\{date(?::YYYY-MM-DD)?\}\}', date.today().isoformat(), source.read_text(encoding='utf-8'))
    text = text.replace('{{title}}', title)
    if kind not in TEMPLATE_FOR:
        # Types without their own template share the concept frame.
        text = re.sub(r'^type: .*$', 'type: ' + kind, text, count=1, flags=re.M)
    path = notes_root / domain / (title + '.md')
    lines = ['생성: ' + path.relative_to(root).as_posix()]
    if not path.parent.is_dir():
        lines.append(f'새 분야 {domain}/: AGENTS.md와 {HOME}의 분야 표에 한 줄을 더하세요')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    lines.append(f'다음: 안내 주석을 지우고 본문을 쓴 뒤 {HOME} 또는 분야 index에서 링크하고 `notepilot sync` → `notepilot check`')
    return lines


def kit_warning(root):
    """Warn when .notepilot was written by a different notepilot version than the one running."""
    path = root / KIT / 'version'
    kit = path.read_text(encoding='utf-8').strip() if path.is_file() else ''
    if not kit or kit == __version__:
        return None
    try:
        older = tuple(map(int, kit.split('.'))) < tuple(map(int, __version__.split('.')))
    except ValueError:
        older = True
    if older:
        return f'{KIT}/ 이(가) {kit} 기준임 (설치된 notepilot {__version__}): `notepilot upgrade`로 규약·양식을 맞추세요'
    return f'{KIT}/ 이(가) 더 새로운 {kit} 기준임 (설치된 notepilot {__version__}): notepilot을 최신으로 올리세요'


def upgrade(root, cfg, dry_run=False):
    """Replace .notepilot, then refresh visible copies the user has not edited."""
    def current(name):
        path = root / name
        return path.read_text(encoding='utf-8') if path.is_file() else None

    originals = kit_files()
    previous = (current(KIT + '/version') or '없음').strip()
    lines, writes = [], {}
    for name, source in sorted(copies(cfg).items()):
        mine, new = current(name), originals[source]
        if mine == new:
            continue
        if mine is None:
            lines.append('추가: ' + name)
        elif mine == current(source):
            lines.append('갱신: ' + name)
        else:
            # Mention an edited copy only when its original actually changes in this run.
            if current(source) != new:
                lines.append(f'그대로 둠(직접 고친 파일): {name} — 새 원본은 {source}')
            continue
        writes[name] = new
    changed = sorted(name for name, content in originals.items() if current(name) != content)
    if changed:
        lines.insert(0, f'{KIT}/ 교체: {previous} → {__version__} ({len(changed)}개 파일)')
    writes.update({name: originals[name] for name in changed})
    if not lines:
        return ['이미 최신 (' + __version__ + ')']
    if dry_run:
        return ['[dry-run] ' + line for line in lines]
    for name, content in writes.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    return lines
