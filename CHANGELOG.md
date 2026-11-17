# 변경 기록

이 프로젝트의 버전은 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다. 규약·양식·스킬(`.notepilot/`에 들어가는 내용)이 바뀌면 버전을 올린다. 보관함에서는 `notepilot upgrade`로 반영한다.

## [Unreleased]

## [0.1.0] - 2026-11-17

첫 공개 버전.

- `init`: 폴더·규약·양식·Obsidian 설정·스킬(Claude Code, Codex)·git 훅을 갖춘 보관함 생성
- `new`: 양식으로 정식 노트 생성
- `sync` / `check`: 목차·관련 문서·현황 표 갱신과 형식·링크·도달성·비밀 값 검사
- `report`: 내용 점검 후보 목록
- `upgrade`: `.notepilot/` 교체와 고치지 않은 양식·스킬 사본 갱신
- `rename`: 폴더 이름 변경과 사후 적용(`--moved`)
- `secrets` / `hook`: 비밀 값 패턴 검사와 pre-commit 훅
- `guide`: 세부 작성 규칙

[Unreleased]: https://github.com/seokyun-kang/notepilot/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/seokyun-kang/notepilot/releases/tag/v0.1.0
