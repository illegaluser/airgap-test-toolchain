---
name: Commit message style — plain language + structured layout
description: 커밋 메시지는 비개발자도 이해할 수 있게 쉬운 한국어로, 본문은 항목별로 일목요연하게 정리
type: feedback
originSessionId: 28e4e736-6d1c-4650-811d-05f3c1009ee7
---
커밋 메시지는 **비개발자도 이해할 수 있게** 간결하고 쉬운 한국어로 풀어 작성한다. 본문은 줄글이 아니라 **항목별로 일목요연하게** 정리한다.

**Why:** 사용자(또는 사용자 주변 비개발자 이해관계자)가 변경 이력을 빠르게 훑어 이해할 수 있어야 함. 2026-04-29 plain-language 지시, 2026-05-01 "항목별 일목요연 정리" 추가 지시 (앞으로도 일관 적용 명시).

**How to apply:**
- 제목(첫 줄): "왜/무엇이 좋아졌는지" 한 줄. conventional commits prefix(`feat:`, `fix:`, `docs:` 등)는 기존 리포 관행이 있을 때만 사용 (현 리포는 사용 중).
- 본문: 줄글 단락 ❌. 다음 중 하나의 구조로:
  - **불릿 목록** (변경 항목이 3개 이내일 때 가장 간단)
  - **소제목 + 불릿** (영역이 여러 개일 때 — 예: `## 변경` / `## 부수 효과` / `## 검증`)
- 각 항목은 한 문장으로 끝내고, 기술 디테일·약어·파일명 나열은 지양 (예: "TR.6 enricher subprocess refactor" ❌ → "녹화 결과를 자동으로 분석해 테스트 계획서로 정리" ✅).
- 검증/테스트 결과를 별도 항목으로 명시하면 신뢰도↑ (예: "통합 테스트 110건 통과").
- Co-Authored-By trailer 는 그대로 유지.

**Example skeleton:**
```
feat: <한 줄 요약 — 사용자 가치 중심>

## 변경
- <항목 1: 사용자 관점 효과>
- <항목 2>

## 부수 효과 (있을 때만)
- <라벨/문구 변경 등 곁가지>

## 검증
- <e2e 110건 통과 등>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```
