"""notepilot command line."""
import argparse
from pathlib import Path
import sys

from . import __version__, scaffold
from .rename import rename
from .vault import (FOLDERS, TYPES, VaultError, find_root, load_config, paths, pending, report, scan, scan_secrets,
                    setup_warnings)

GUIDES = {'writing': '자료 참조 범위 · 근거 기준 · 설명과 개념 분리',
          'format': '속성 · 공통 형식 · 링크와 목차 호환성',
          'workflow': '생성 · 이동 · 통합 · 보관 · inbox 편입 · 완료 조건'}


def open_vault(args):
    root = find_root(args.vault)
    return root, load_config(root)


def cmd_init(args):
    print('\n'.join(scaffold.init(args.path, skills=args.skill or (), git=args.git, dry_run=args.dry_run)))
    if not args.dry_run:
        print('\n다음: Obsidian에서 이 폴더를 보관함으로 열고, AGENTS.md에 용도와 분야를 적으세요.')
    return 0


def run_checks(args, write):
    root, cfg = open_vault(args)
    # An outdated kit often explains the errors below, so it is shown even when scanning fails.
    outdated = scaffold.kit_warning(root)
    if outdated:
        print('경고: ' + outdated)
    errors, rendered, notes = scan(root, cfg)
    if errors:
        print('\n'.join(errors))
        print(f'\n오류 {len(errors)}개: 아무것도 쓰지 않음')
        return 1
    changed = pending(rendered)
    if write:
        for path in changed:
            path.write_text(rendered[path], encoding='utf-8')
        print(f'노트 {len(notes)}개 · 갱신 {len(changed)}개')
        return 0
    secrets = scan_secrets(root, cfg)
    if secrets:
        print(f"비밀 값으로 보이는 내용 ({paths(cfg)['temp']}/로 옮기거나 지우세요):\n" + '\n'.join(secrets))
    if changed:
        print('먼저 `notepilot sync` 필요:\n' + '\n'.join(p.relative_to(root).as_posix() for p in changed))
    for warning in setup_warnings(root, cfg):
        print('경고: ' + warning)
    print(f'노트 {len(notes)}개 · 갱신 필요 {len(changed)}개 · 비밀 값 의심 {len(secrets)}개')
    return 1 if changed or secrets else 0


def cmd_secrets(args):
    root, cfg = open_vault(args)
    findings = scan_secrets(root, cfg, staged=args.staged)
    if findings:
        print('비밀 값으로 보이는 내용:\n' + '\n'.join(findings), file=sys.stderr)
        print(f"\n{paths(cfg)['temp']}/로 옮기거나 지운 뒤 다시 시도하세요. 패턴 검사라 오탐일 수 있습니다.", file=sys.stderr)
        return 1
    return 0


def cmd_report(args):
    root, cfg = open_vault(args)
    print(report(root, cfg, days=args.days))
    return 0


def cmd_new(args):
    root, cfg = open_vault(args)
    print('\n'.join(scaffold.new_note(root, cfg, args.domain, args.title, kind=args.type)))
    return 0


def cmd_upgrade(args):
    root, cfg = open_vault(args)
    print('\n'.join(scaffold.upgrade(root, cfg, dry_run=args.dry_run)))
    return 0


def cmd_rename(args):
    root, cfg = open_vault(args)
    print('\n'.join(rename(root, cfg, args.folder, args.name, moved=args.moved)))
    errors, _, _ = scan(root, load_config(root))
    print(f'\n남은 검사 오류 {len(errors)}개: `notepilot check`로 확인' if errors else '\n다음: `notepilot sync` 후 `notepilot check`')
    return 0


def cmd_hook(args):
    root, _ = open_vault(args)
    print(scaffold.install_hook(root))
    return 0


def cmd_guide(args):
    if not args.topic:
        print('\n'.join(f'notepilot guide {name:<9} {text}' for name, text in GUIDES.items()))
        return 0
    print(scaffold.read('guide/' + args.topic + '.md'))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='notepilot', description='에이전트와 함께 쓰는 Markdown 노트 보관함 구성·점검 도구')
    parser.add_argument('--version', action='version', version='notepilot ' + __version__)
    parser.add_argument('--vault', help='보관함 경로 (기본: 현재 위치에서 상위로 탐색)')
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('init', help='새 보관함 구성')
    p.add_argument('path', nargs='?', default='.')
    p.add_argument('--skill', nargs='+', choices=sorted(scaffold.SKILL_PATHS), help='에이전트용 note 스킬 연결 (여러 개 가능)')
    p.add_argument('--git', action='store_true', help='git init과 비밀 값 검사 훅 설치')
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(func=cmd_init)

    p = sub.add_parser('new', help='양식으로 정식 노트 만들기')
    p.add_argument('domain', help='분야 (notes/ 바로 아래 폴더)')
    p.add_argument('title', help='제목 = 파일명')
    p.add_argument('--type', default='concept', choices=sorted(TYPES))
    p.set_defaults(func=cmd_new)

    p = sub.add_parser('sync', help='목차·관련 문서·현황 영역 갱신')
    p.set_defaults(func=lambda a: run_checks(a, write=True))
    p = sub.add_parser('check', help='쓰지 않고 구조·링크·비밀 값 검사')
    p.set_defaults(func=lambda a: run_checks(a, write=False))

    p = sub.add_parser('report', help='내용 점검 후보 목록 (실패하지 않음)')
    p.add_argument('--days', type=int, default=30)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser('upgrade', help='.notepilot/을 최신으로 교체하고 고치지 않은 양식·스킬 사본 갱신')
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(func=cmd_upgrade)

    p = sub.add_parser('rename', help='폴더 이름을 바꾸고 링크·설정을 함께 고침')
    p.add_argument('folder', choices=sorted(FOLDERS))
    p.add_argument('name', help='새 폴더 이름')
    p.add_argument('--moved', action='store_true', help='이미 직접 이름을 바꾼 폴더에 맞춰 나머지만 고침')
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser('secrets', help='비밀 값 패턴 검사')
    p.add_argument('--staged', action='store_true', help='git에 스테이징된 내용만')
    p.set_defaults(func=cmd_secrets)

    p = sub.add_parser('hook', help='이 저장소에 pre-commit 훅 설치')
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser('guide', help='세부 작성 규칙 출력')
    p.add_argument('topic', nargs='?', choices=sorted(GUIDES))
    p.set_defaults(func=cmd_guide)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (VaultError, ValueError, OSError) as exc:
        print('중단: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
