import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from notepilot.cli import main  # noqa: E402
from notepilot.vault import load_config, save_config  # noqa: E402

NOTE = '''---
type: concept
tags: ["demo"]
status: active
created: "2026-01-02"
updated: "2026-01-03"
related: {related}
verification: unverified
---

# {title}

> [!NOTE]
> 검사용으로 만든 짧은 노트이며 형식만 확인한다.

## 목차

<!-- toc:start -->
<!-- toc:end -->

## 첫 절

본문 첫 절의 설명이다.{extra}

## 둘째 절

본문 둘째 절의 설명이다.

## 근거와 참고 자료

확인한 외부 자료 없음. 미검증 상태다.

## 관련 문서

<!-- related:start -->
<!-- related:end -->
'''


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / 'vault'
        code, out = run('init', str(self.root))
        self.assertEqual(code, 0, out)
        self.assertIn('AGENTS.md의 용도·분야가 비어 있음', self.cli('check')[1])
        agents = self.root / 'AGENTS.md'
        text = agents.read_text(encoding='utf-8').replace('무엇을 모으는 보관함인지 한두 문장으로 적는다.', '시험용 보관함.')
        agents.write_text(text.replace('\n| | |\n', '\n| general | 시험 |\n'), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *argv):
        return run('--vault', str(self.root), *argv)

    def note(self, title, related='[]', extra='', folder='general'):
        path = self.root / 'notes' / folder / (title + '.md')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(NOTE.format(title=title, related=related, extra=extra), encoding='utf-8')
        return path

    def link_home(self, *titles, folder='general'):
        home = self.root / 'README.md'
        links = ''.join(f'\n- [{t}](notes/{folder}/{t.replace(" ", "%20")}.md)' for t in titles)
        home.write_text(home.read_text(encoding='utf-8') + links + '\n', encoding='utf-8')

    def test_fresh_vault_is_clean_and_idempotent(self):
        self.assertEqual(self.cli('check')[0], 0)
        self.assertEqual(self.cli('sync')[0], 0)
        self.assertEqual(self.cli('check')[0], 0)
        self.assertTrue((self.root / 'temp').is_dir())
        self.assertIn('/temp/', (self.root / '.gitignore').read_text(encoding='utf-8'))
        types = json.loads((self.root / '.obsidian/types.json').read_text(encoding='utf-8'))['types']
        self.assertEqual((types['created'], types['updated']), ('date', 'date'))

    def test_init_refuses_existing_vault_and_conflicts(self):
        code, out = run('init', str(self.root))
        self.assertEqual(code, 1)
        other = Path(self.tmp.name) / 'other'
        other.mkdir()
        (other / 'AGENTS.md').write_text('mine\n', encoding='utf-8')
        code, out = run('init', str(other))
        self.assertEqual(code, 1)
        self.assertIn('AGENTS.md', out)
        self.assertFalse((other / 'notepilot.json').exists())
        self.assertEqual((other / 'AGENTS.md').read_text(encoding='utf-8'), 'mine\n')

    def test_dry_run_writes_nothing(self):
        target = Path(self.tmp.name) / 'dry'
        code, out = run('init', str(target), '--dry-run')
        self.assertEqual(code, 0)
        self.assertFalse(target.exists())

    def test_sync_generates_regions(self):
        a = self.note('Alpha Note', related='["notes/general/Beta Note.md"]',
                      extra=' [베타](Beta%20Note.md#%EC%B2%AB%20%EC%A0%88) 참고.')
        self.note('Beta Note')
        self.link_home('Alpha Note', 'Beta Note')
        code, out = self.cli('check')
        self.assertEqual(code, 1)
        self.assertIn('sync', out)
        self.assertEqual(self.cli('sync')[0], 0)
        text = a.read_text(encoding='utf-8')
        self.assertIn('- [첫 절](#%EC%B2%AB%20%EC%A0%88)', text)
        self.assertIn('- [Beta Note](Beta%20Note.md)', text)
        self.assertIn('| general | 2 | 2026-01-03 |', (self.root / 'README.md').read_text(encoding='utf-8'))
        self.assertEqual(self.cli('check')[0], 0)
        code, out = self.cli('sync')
        self.assertIn('갱신 0개', out)

    def test_structural_errors_block_writing(self):
        cases = {
            'H1': lambda t: t.replace('# Bad', '# Other'),
            'title': lambda t: t.replace('type: concept', 'type: concept\ntitle: "Bad"'),
            'NOTE': lambda t: t.replace('> [!NOTE]\n> 검사용으로 만든 짧은 노트이며 형식만 확인한다.\n', '요약이 Callout이 아니다.\n'),
            '필수 절': lambda t: t.replace('## 근거와 참고 자료', '## 근거'),
            '위키': lambda t: t.replace('본문 둘째 절의 설명이다.', '[[Other]] 참고.'),
            '링크 대상 없음': lambda t: t.replace('본문 둘째 절의 설명이다.', '[없음](Missing.md) 참고.').replace('related: []', 'related: ["notes/general/Bad.md"]'),
            'created': lambda t: t.replace('created: "2026-01-02"', 'created: "2026-02-30"'),
            'source-checked': lambda t: t.replace('verification: unverified', 'verification: source-checked'),
            '언어 지정': lambda t: t.replace('본문 둘째 절의 설명이다.', '예시:\n\n```\nx = 1\n```'),
            '잘못된 type': lambda t: t.replace('type: concept', 'type: handoff'),
        }
        for expected, mutate in cases.items():
            with self.subTest(expected):
                path = self.note('Bad')
                self.link_home('Bad')
                before = mutate(path.read_text(encoding='utf-8'))
                path.write_text(before, encoding='utf-8')
                code, out = self.cli('sync')
                self.assertEqual(code, 1, out)
                self.assertTrue(expected in out or 'related' in out, out)
                self.assertEqual(path.read_text(encoding='utf-8'), before)

    def test_unreachable_note_is_reported(self):
        self.note('Lonely')
        code, out = self.cli('check')
        self.assertEqual(code, 1)
        self.assertIn('도달할 수 없음', out)

    def test_secrets_fail_check_but_temp_is_ignored(self):
        token = 'xoxb-' + '1234567890-abcdefghij'
        (self.root / 'temp' / 'keys.md').write_text(token + '\n', encoding='utf-8')
        self.assertEqual(self.cli('check')[0], 0)
        (self.root / 'inbox' / 'memo.md').write_text('token ' + token + '\n', encoding='utf-8')
        code, out = self.cli('check')
        self.assertEqual(code, 1)
        self.assertIn('inbox/memo.md:1', out)
        self.assertNotIn(token, out)
        # A nested temp folder hides only itself, not its parent.
        (self.root / 'inbox' / 'memo.md').unlink()
        self.assertEqual(self.cli('rename', 'temp', 'work/temp')[0], 0)
        (self.root / 'work' / 'other.md').write_text(token + '\n', encoding='utf-8')
        code, out = self.cli('check')
        self.assertIn('work/other.md:1', out)
        self.assertNotIn('work/temp/keys.md', out)

    def test_staged_secret_scan_and_hook(self):
        git = lambda *a: subprocess.run(['git', *a], cwd=self.root, capture_output=True, check=True)
        git('init', '-q')
        self.assertIn('pre-commit', self.cli('hook')[1])
        self.assertTrue((self.root / '.git/hooks/pre-commit').is_file())
        (self.root / 'temp' / 'keys.md').write_text('ghp_' + 'a' * 36 + '\n', encoding='utf-8')
        git('add', '-A')
        self.assertEqual(self.cli('secrets', '--staged')[0], 0)
        (self.root / 'inbox' / 'memo.md').write_text('ghp_' + 'a' * 36 + '\n', encoding='utf-8')
        git('add', '-A')
        self.assertEqual(self.cli('secrets', '--staged')[0], 1)

    def test_upgrade_replaces_kit_and_only_untouched_copies(self):
        self.assertIn('이미 최신', self.cli('upgrade')[1])
        kit = self.root / '.notepilot'
        current = (kit / 'RULES.md').read_text(encoding='utf-8')
        # Simulate an older release: old originals, one copy untouched, one edited by the user.
        (kit / 'RULES.md').write_text('old rules\n', encoding='utf-8')
        (kit / 'version').write_text('0.0.1\n', encoding='utf-8')
        for name in ('note.md', 'lab.md'):
            (kit / 'templates' / name).write_text('old ' + name + '\n', encoding='utf-8')
        (self.root / 'templates' / 'note.md').write_text('old note.md\n', encoding='utf-8')
        (self.root / 'templates' / 'lab.md').write_text('my own lab template\n', encoding='utf-8')
        agents = self.root / 'AGENTS.md'
        agents.write_text(agents.read_text(encoding='utf-8') + '\n내 규칙\n', encoding='utf-8')
        self.assertIn('경고: .notepilot/ 이(가) 0.0.1 기준임', self.cli('check')[1])
        self.cli('upgrade', '--dry-run')
        self.assertEqual((kit / 'RULES.md').read_text(encoding='utf-8'), 'old rules\n')
        code, out = self.cli('upgrade')
        self.assertEqual(code, 0, out)
        self.assertIn('0.0.1 →', out)
        self.assertIn('갱신: templates/note.md', out)
        self.assertIn('그대로 둠(직접 고친 파일): templates/lab.md', out)
        self.assertEqual((kit / 'RULES.md').read_text(encoding='utf-8'), current)
        self.assertEqual((self.root / 'templates/note.md').read_text(encoding='utf-8'),
                         (kit / 'templates/note.md').read_text(encoding='utf-8'))
        self.assertEqual((self.root / 'templates/lab.md').read_text(encoding='utf-8'), 'my own lab template\n')
        self.assertIn('내 규칙', agents.read_text(encoding='utf-8'))
        self.assertIn('이미 최신', self.cli('upgrade')[1])
        self.assertNotIn('경고', self.cli('check')[1])
        (kit / 'version').write_text('99.0.0\n', encoding='utf-8')
        self.assertIn('notepilot을 최신으로 올리세요', self.cli('check')[1])

    def test_fixed_names_cannot_be_configured(self):
        cfg = load_config(self.root)
        cfg['paths'] = {'notes': 'wiki'}
        save_config(self.root, cfg)
        code, out = self.cli('check')
        self.assertEqual(code, 1)
        self.assertIn('고정', out)

    def test_check_requires_rules_pointer_in_agents(self):
        (self.root / 'AGENTS.md').write_text('# 내 안내\n', encoding='utf-8')
        code, out = self.cli('check')
        self.assertEqual(code, 1)
        self.assertIn('.notepilot/RULES.md', out)

    def test_skills_for_both_agents_and_added_later(self):
        target = Path(self.tmp.name) / 'skilled'
        self.assertEqual(run('init', str(target), '--skill', 'claude-code', 'codex')[0], 0)
        self.assertEqual((target / 'CLAUDE.md').read_text(encoding='utf-8'), '@AGENTS.md\n@.notepilot/RULES.md\n')
        skill = (target / '.notepilot/skill/SKILL.md').read_text(encoding='utf-8')
        for path in ('.claude/skills/note/SKILL.md', '.agents/skills/note/SKILL.md'):
            self.assertEqual((target / path).read_text(encoding='utf-8'), skill)
        self.assertEqual(run('--vault', str(target), 'check')[0], 0)
        # An existing vault gains a skill by listing it in the config and upgrading.
        cfg = load_config(self.root)
        cfg['skills'] = ['codex']
        save_config(self.root, cfg)
        self.assertIn('추가: .agents/skills/note/SKILL.md', self.cli('upgrade')[1])
        self.assertTrue((self.root / '.agents/skills/note/SKILL.md').is_file())

    def test_report_lists_unlinked_mentions(self):
        self.note('Alpha Note', extra=' Beta Note와 비교한다.')
        self.note('Beta Note')
        self.link_home('Alpha Note', 'Beta Note')
        code, out = self.cli('report')
        self.assertEqual(code, 0)
        self.assertIn('Alpha Note.md → notes/general/Beta Note.md', out)

    def linked_vault(self):
        (self.root / 'assets' / 'pic.png').write_bytes(b'png')
        self.note('Alpha Note', related='["notes/general/Beta Note.md"]',
                  extra=' [베타](Beta%20Note.md) 참고. ![그림](../../assets/pic.png)')
        self.note('Beta Note')
        self.link_home('Alpha Note', 'Beta Note')
        self.assertEqual(self.cli('sync')[0], 0)
        self.assertEqual(self.cli('check')[0], 0)

    def assert_clean(self):
        code, out = self.cli('sync')
        self.assertEqual(code, 0, out)
        code, out = self.cli('check')
        self.assertEqual(code, 0, out)
        self.assertNotIn('경고', out)

    def test_rename_every_folder_keeps_vault_valid(self):
        self.linked_vault()
        for role, new in (('notes', 'wiki'), ('assets', 'files'), ('templates', 'forms'),
                          ('inbox', 'drafts'), ('temp', 'scratch')):
            with self.subTest(role):
                code, out = self.cli('rename', role, new)
                self.assertEqual(code, 0, out)
                self.assertTrue((self.root / new).is_dir())
                self.assertFalse((self.root / role).exists())
                self.assert_clean()
        home = (self.root / 'README.md').read_text(encoding='utf-8')
        self.assertIn('(wiki/general/Alpha%20Note.md)', home)
        self.assertIn('file.inFolder("wiki")', home)
        self.assertIn('(forms/note.md)', home)
        alpha = (self.root / 'wiki/general/Alpha Note.md').read_text(encoding='utf-8')
        self.assertIn('related: ["wiki/general/Beta Note.md"]', alpha)
        self.assertIn('(../../files/pic.png)', alpha)
        self.assertIn('/scratch/', (self.root / '.gitignore').read_text(encoding='utf-8').splitlines())
        self.assertNotIn('/temp/', (self.root / '.gitignore').read_text(encoding='utf-8').splitlines())
        self.assertEqual(load_config(self.root)['folders'],
                         {'notes': 'wiki', 'assets': 'files', 'templates': 'forms', 'inbox': 'drafts', 'temp': 'scratch'})
        # Renaming back to the default drops the override again.
        self.assertEqual(self.cli('rename', 'notes', 'notes')[0], 0)
        self.assertNotIn('notes', load_config(self.root)['folders'])
        self.assert_clean()

    def test_rename_to_a_deeper_folder_fixes_outgoing_links(self):
        self.linked_vault()
        self.assertEqual(self.cli('rename', 'notes', 'docs/notes')[0], 0)
        alpha = (self.root / 'docs/notes/general/Alpha Note.md').read_text(encoding='utf-8')
        self.assertIn('(../../../assets/pic.png)', alpha)
        self.assertIn('(Beta%20Note.md)', alpha)
        self.assert_clean()

    def test_rename_after_manual_move(self):
        self.linked_vault()
        (self.root / 'notes').rename(self.root / 'wiki')
        code, out = self.cli('check')
        self.assertEqual(code, 1)
        self.assertIn('--moved', out)
        self.assertEqual(self.cli('rename', 'notes', 'wiki')[0], 1)
        code, out = self.cli('rename', 'notes', 'wiki', '--moved')
        self.assertEqual(code, 0, out)
        (self.root / 'temp').rename(self.root / 'scratch')
        self.assertIn('rename temp', self.cli('check')[1])
        self.assertEqual(self.cli('rename', 'temp', 'scratch', '--moved')[0], 0)
        self.assert_clean()

    def test_rename_rejects_bad_targets(self):
        for role, new in (('notes', 'inbox'), ('notes', 'inbox/sub'), ('temp', '.hidden'), ('assets', '../out')):
            with self.subTest(new):
                self.assertEqual(self.cli('rename', role, new)[0], 1)
        self.assertEqual(self.cli('check')[0], 0)

    def test_obsidian_mismatch_is_a_warning_only(self):
        app = self.root / '.obsidian' / 'app.json'
        app.write_text(app.read_text(encoding='utf-8').replace('"temp"', '"inbox"'), encoding='utf-8')
        code, out = self.cli('check')
        self.assertEqual(code, 0)
        self.assertIn('경고', out)
        app.unlink()
        self.assertNotIn('경고', self.cli('check')[1])

    def test_new_creates_note_from_vault_template(self):
        code, out = self.cli('new', 'general', 'Fresh Note', '--type', 'lab')
        self.assertEqual(code, 0, out)
        self.assertIn('새 분야 general/', out)
        path = self.root / 'notes/general/Fresh Note.md'
        text = path.read_text(encoding='utf-8')
        self.assertIn('# Fresh Note\n', text)
        self.assertIn('type: lab', text)
        self.assertIn('## 결과와 해석', text)
        self.assertNotIn('{{', text)
        self.assertRegex(text, r'created: "\d{4}-\d{2}-\d{2}"')
        # The user's edited template is the source; types without a template use note.md.
        (self.root / 'templates/note.md').write_text(
            (self.root / 'templates/note.md').read_text(encoding='utf-8').replace('## 예제', '## 내 예제'), encoding='utf-8')
        self.assertEqual(self.cli('new', 'general', 'Ref Note', '--type', 'reference')[0], 0)
        ref = (self.root / 'notes/general/Ref Note.md').read_text(encoding='utf-8')
        self.assertIn('type: reference', ref)
        self.assertIn('## 내 예제', ref)
        self.assertNotIn('새 분야', self.cli('new', 'general', 'Third')[1])
        for domain, title in (('general', 'fresh note'), ('general', 'a/b'), ('general', 'x:y'), ('a/b', 'Ok'), ('.hidden', 'Ok')):
            with self.subTest(title):
                self.assertEqual(self.cli('new', domain, title)[0], 1)
        self.assertEqual(text, path.read_text(encoding='utf-8'))

    def test_guides_ship_with_package(self):
        for topic in ('writing', 'format', 'workflow'):
            code, out = run('guide', topic)
            self.assertEqual(code, 0)
            self.assertGreater(len(out), 500)


if __name__ == '__main__':
    unittest.main()
