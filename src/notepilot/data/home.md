# Notes

이 보관함의 시작 문서다. Obsidian에서는 이 폴더를 보관함으로 열고, GitHub에서는 이 문서가 첫 화면이 된다.

## 분야별로 읽기

분야의 index나 첫 노트가 생기면 여기에 연결한다. 모든 정식 노트는 이 문서에서 링크를 따라 도달할 수 있어야 한다.

| 분야 | 시작 문서 |
| --- | --- |
| | |

## 노트 현황

`notepilot sync`를 실행할 때 갱신되는 정적 표다. index와 보관 문서는 세지 않는다.

<!-- summary:start -->
<!-- summary:end -->

Obsidian에서는 아래 Bases 조회가 속성을 실시간으로 읽는다. GitHub에서는 설정 코드로만 보인다.

```base
filters:
  and:
    - file.inFolder("notes")
    - file.ext == "md"
    - note.type != "index"
views:
  - type: table
    name: 분야별
    filters: note.status != "archived"
    groupBy:
      property: file.folder
      direction: ASC
    order:
      - file.name
      - note.updated
      - note.status
      - note.verification
    sort:
      - property: note.updated
        direction: DESC
  - type: table
    name: 최근 수정
    filters: note.status != "archived"
    order:
      - file.name
      - file.folder
      - note.updated
    sort:
      - property: note.updated
        direction: DESC
    limit: 10
  - type: table
    name: 작성 중
    filters: note.status == "draft"
    order:
      - file.name
      - file.folder
      - note.updated
    sort:
      - property: note.updated
        direction: DESC
```

## 쓰는 방법

| 하려는 일 | 방법 |
| --- | --- |
| 빠르게 적기 | 새 파일은 `temp/`에 생긴다. git에 올라가지 않는다 |
| 나중에 정리할 메모 | `inbox/`로 옮긴다. 자유 형식이며 git에 올라간다 |
| 정식 노트 만들기 | `notepilot new <분야> <제목>`. Obsidian에서는 새 노트에 [note](templates/note.md) · [comparison](templates/comparison.md) · [lab](templates/lab.md) 양식을 삽입하고 `notes/<분야>/`로 옮긴다 |
| 에이전트에게 맡기기 | [AGENTS.md](AGENTS.md)를 읽게 한다. 공통 규약은 `.notepilot/RULES.md` |
| 목차·현황 갱신과 검사 | `notepilot sync` 후 `notepilot check` |
| 내용 점검 후보 보기 | `notepilot report` |
| 공통 규약·양식 최신화 | `notepilot upgrade`. `.notepilot/`만 교체하고 직접 고친 파일은 그대로 둔다 |

API 키나 비밀번호는 `temp/`에만 적는다.
