# notepilot

에이전트(Claude Code, Codex 등)와 함께 쓰는 Markdown 노트 보관함을 만들고 점검하는 명령줄 도구다. 만들어진 보관함은 Obsidian 보관함이자 git 저장소로 그대로 쓸 수 있고 GitHub에서도 읽힌다. 규약·양식·절 제목은 한국어로만 제공한다.

- `notepilot init` 한 번으로 폴더·규약·양식·Obsidian 설정을 만든다.
- 에이전트가 읽을 규약(AGENTS.md)과 형식 검사기를 함께 제공해 누가 써도 같은 형식의 노트가 나온다.
- 도구가 관리하는 파일은 `.notepilot/` 폴더에만 둔다. `notepilot upgrade`는 그 폴더만 교체하므로 직접 고친 README·AGENTS.md·양식은 그대로 남는다.
- API 키 같은 비밀 값이 커밋되지 않도록 임시 폴더를 git에서 제외하고 커밋 전에 패턴을 검사한다.

Python 3.9 이상이면 되고 다른 패키지는 필요 없다.

## 설치

```sh
uv tool install git+https://github.com/seokyun-kang/notepilot
# 또는
pipx install git+https://github.com/seokyun-kang/notepilot
```

설치 후 `notepilot`을 찾을 수 없다고 나오면 `uv tool update-shell`(pipx는 `pipx ensurepath`)을 실행하고 터미널을 다시 연다.

도구 자체의 업데이트는 `uv tool upgrade notepilot` 또는 `pipx upgrade notepilot`이다.

## 시작

```sh
notepilot init my-notes --git --skill claude-code codex
cd my-notes
```

| 옵션 | 뜻 |
| --- | --- |
| `--git` | `git init`을 하고 비밀 값 검사 pre-commit 훅을 설치 |
| `--skill claude-code codex` | 에이전트용 `note` 스킬을 보관함 안에 설치. 하나만 또는 둘 다 지정 가능. Claude Code는 `CLAUDE.md`도 생성 |
| `--dry-run` | 만들 파일만 보여 주고 쓰지 않음 |

기존 파일과 내용이 다르면 아무것도 쓰지 않고 충돌 목록을 보여 준다. 이후 Obsidian에서 이 폴더를 보관함으로 열고 `AGENTS.md`에 용도와 분야를 적는다.

나중에 스킬을 더하려면 `notepilot.json`의 `skills`에 `"claude-code"`나 `"codex"`를 넣고 `notepilot upgrade`를 실행한다.

## 만들어지는 구조

```text
my-notes/
├── .notepilot/      도구 소유. 공통 규약(RULES.md)과 양식·스킬 원본. upgrade가 교체
├── README.md        홈. 분야 링크, 현황 표, Obsidian Bases 조회
├── AGENTS.md        이 보관함만의 지침. 공통 규약을 가리킴
├── notepilot.json   태그 형식·검사 제외 경로·스킬·바꾼 폴더 이름
├── notes/           정식 노트. 바로 아래 폴더가 분야
├── inbox/           정리 대기 메모 (git 추적)
├── temp/            임시 작업 (git 미추적, Obsidian 새 파일 기본 위치)
├── templates/       note · comparison · lab 양식
└── assets/          첨부
```

정식 노트는 파일명이 제목이고, 속성 일곱 개(type, tags, status, created, updated, related, verification)와 `NOTE 요약 → 본문 → 근거와 참고 자료 → 관련 문서` 구조를 따른다. 자세한 내용은 `notepilot guide format`.

새 노트는 `notepilot new <분야> <제목> [--type comparison|lab|…]`로 만든다. 양식에 제목과 오늘 날짜를 채워 `notes/<분야>/<제목>.md`를 만들고, 다른 파일은 고치지 않는다. Obsidian에서 직접 만들 때는 새 노트를 만들고(`temp/`에 생긴다) 명령 팔레트의 `Templates: Insert template`으로 양식을 넣은 뒤 `notes/<분야>/`로 옮긴다. 양식의 `{{title}}`은 그 노트의 파일명으로 바뀐다.

폴더 다섯 개의 이름은 `notepilot rename`으로 바꿀 수 있다. 노트의 속성과 공통 절 제목, `README.md`는 고정이다. `.notepilot/`의 내용은 모든 보관함에서 같다. 폴더를 더 만드는 것은 자유이며 그 역할은 `AGENTS.md`에 적는다.

`.notepilot/` 밖의 파일은 모두 사용자 것이다. `sync`가 주석 표시 사이(목차, 관련 문서, 현황 표)를 채우는 것 말고는 도구가 고치지 않는다.

## 명령

| 명령 | 하는 일 |
| --- | --- |
| `notepilot new <분야> <제목> [--type]` | 양식으로 정식 노트 생성. 같은 제목이나 파일명에 못 쓰는 문자는 거부 |
| `notepilot sync` | 노트의 목차·관련 문서 목록과 홈의 현황 표를 갱신 |
| `notepilot check` | 쓰지 않고 형식·링크·도달성·비밀 값 패턴을 검사. 문제가 있으면 종료 코드 1. AGENTS.md 빈칸, `.notepilot/` 버전 차이, Obsidian 설정 불일치는 경고만 |
| `notepilot report` | 오래된 초안, 재검토 필요, 링크 없이 언급된 노트 등 점검 후보 목록 |
| `notepilot upgrade` | `.notepilot/`을 최신으로 교체. 양식·스킬 사본은 고치지 않았을 때만 함께 갱신 |
| `notepilot rename <폴더> <새 이름> [--moved]` | 폴더 이름을 바꾸고 링크·related·설정·`.gitignore`·Obsidian 설정을 함께 고침 |
| `notepilot guide [writing\|format\|workflow]` | 세부 작성 규칙 출력 |
| `notepilot secrets [--staged]` | 비밀 값 패턴 검사 |
| `notepilot hook` | 복제해 온 저장소에 pre-commit 훅 설치 |

보관함 밖에서 실행할 때는 `notepilot --vault 경로 check`처럼 지정한다.

## 폴더 이름 바꾸기

```sh
notepilot rename notes wiki          # 폴더를 옮기고 나머지를 고침
notepilot rename temp scratch --moved   # 이미 직접 이름을 바꾼 뒤에 나머지만 고침
```

대상은 `notes`, `inbox`, `temp`, `templates`, `assets`다. 본문에 글자로 적힌 옛 이름(예: 설명 문장 속의 `temp/`)은 고치지 않고 해당 파일 목록만 알려 준다. `check`는 Obsidian의 새 노트·첨부·템플릿 위치가 보관함 폴더와 다르면 경고한다.

## 한계

- `check` 통과는 형식과 링크가 맞다는 뜻이다. 내용의 사실 여부는 검사하지 않는다.
- 비밀 값 검사는 흔한 토큰 형태만 잡는 패턴 검사다. 통과를 안전 보증으로 보면 안 된다.
- Obsidian Bases 조회는 Obsidian에서만 동작한다. GitHub에서는 홈의 정적 현황 표를 본다.

## 개발

```sh
python3 -m unittest discover -s tests
```

릴리스는 `src/notepilot/__init__.py`의 `__version__`을 올리고 `CHANGELOG.md`에 그 버전 절을 쓴 뒤 `v<버전>` 태그를 push한다. 태그가 버전과 같은지 확인하고 테스트·빌드를 거쳐 GitHub Release가 만들어진다. 특정 버전은 `uv tool install git+https://github.com/seokyun-kang/notepilot@v0.1.0`처럼 설치한다.
