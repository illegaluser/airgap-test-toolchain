Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

Tradeoff: These guidelines bias toward caution over speed. For trivial tasks, use judgment.

Lint 오류 — NON-NEGOTIABLE

파일 작성·편집·**조회** 중 발견되는 모든 lint 오류(markdownlint / ruff / eslint / 기타)는 같은 작업 단위 안에서 해결한다. 사람·에이전트 모두 적용. "내가 편집한 영역 외" / "기존 거라 무관" 같은 핑계 금지.

- 코드 lint 는 가능하면 `--fix` 자동 수정 후 남는 것은 수동 수정.
- 한국어 산문 문서는 MD013(80자) 같은 라인 길이 규칙이 부적합하므로 repo 루트 `.markdownlint.json` 으로 정책 차단 (이미 적용됨). 새 규칙을 끄거나 켤 때는 거기서.
- 누적 금지 — "나중에 일괄 정리" 는 신호 대비 잡음을 키운다. 발견 즉시.
- 비활성 정책에 새로 추가하려면 이유를 commit 메시지나 PR 설명에 남길 것.

구독형 LLM 자동화 금지 — NON-NEGOTIABLE

Claude Max·ChatGPT Plus/Pro·Gemini Advanced 등 소비자 구독을 프로그래매틱·자동화 백엔드로 쓰는 것은 3사 모두 약관상 금지. 본 저장소의 자동화(Jenkins job, provision 스크립트, eval runner 등) 백엔드로 소비자 구독을 끼우자는 제안이 나오면 즉시 차단할 것.

- 합법 경로는 API 키뿐. 비용이 부담이면 로컬 모델(Ollama 계열) 업그레이드가 정공법.
- 위반 시 계정 정지·구독 취소·환불 의무 없음 — 기술적 가능 여부와 약관 허용 여부는 별개.
- 근거: Anthropic Consumer Terms §3, Google ToS "automated means" 조항, OpenAI Usage Policy.

0. Read Docs First — NON-NEGOTIABLE

Before implementing, modifying, restarting services, or changing structure on **any** part of this repo, you MUST consult the project's own documentation. Speaking or coding from your own assumptions about the architecture is forbidden.

What to read, in order, before acting:

1. Top-level `README.md` (this repo) — the high-level architecture, what runs on host vs container vs WSL2, network topology, the canonical entry-points (e.g. `./run-recording-ui.sh`, `./build.sh`).
2. The sub-package `README.md` for the area you are touching (e.g. `playwright-allinone/README.md`, `playwright-allinone/docs/recording-ui.md`).
3. Planning / decision documents under `**/docs/PLAN_*.md` for the feature you are touching. The decision log (D1, D2, …) records *why* the current shape exists — do not undo decisions silently.
4. Any `CLAUDE.md` files at deeper paths.

Hard rules:

- **Service launch / restart** — use the documented launcher (e.g. `./run-recording-ui.sh restart`). Do **not** invent your own `Start-Process`, `nohup`, `Popen`, or `wsl.exe` invocations to spin services up. If the documented launcher fails, fix the launcher or surface the failure — do not bypass it.
- **Host-vs-container-vs-WSL2 split** — every service in this repo has a designated execution location (host native / container / WSL2 venv). Read the README to find which, then respect it. In particular: **Playwright browser execution must run on host (Mac / Windows native), headed**. WSL2 / WSLg / container Playwright is a violation of the project's design.
- **Path / mount semantics** — when a flow crosses host ↔ container ↔ WSL2 (e.g. docker bind mounts, recording dirs, storage_state), check the existing build/launcher scripts for the canonical mount source. Don't fabricate alternative paths.
- **Don't "fix" by relocating** — if a flow seems broken, the answer is almost never "re-host the service somewhere else." First, read the docs, find the documented topology, restore that topology, and only then debug whatever real bug remains.

If the docs are missing, ambiguous, or contradict the code, **say so explicitly to the user and ask** before picking a side. Silent reinterpretation of architecture is the failure mode this rule exists to prevent.

This rule applies to everyone working on the repo, human or agent. If you are tempted to skip it because "this is just a quick restart" — that is exactly the case where this rule has been violated most.

1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them - don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, stop. Name what's confusing. Ask.
2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

3. Surgical Changes
Touch only what you must. Clean up only your own mess.

When editing existing code:

Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:

Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

4. Goal-Driven Execution
Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

These guidelines are working if: fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

5. 팀 공통 기술 사실 노트

이 절은 코드/git history 만으로 빠르게 파악하기 어려운, 한 번 잘못 짚으면 시간을 크게 잃는 사실만 모았다. 변경 시 같이 갱신할 것.

### Dify 1.13 plugin-based model provider 등록

Dify 1.13 에서 Ollama 같은 model provider 는 **플러그인 기반**이다. provision 순서는 고정:

1. 플러그인 설치: `POST /console/api/workspaces/current/plugin/upload/pkg` (multipart `pkg=@xxx.difypkg`) → `unique_identifier` → `POST /plugin/install/pkg` 로 설치 (비동기 task_id).
2. provider 존재 확인: `GET /console/api/workspaces/current/model-providers` 의 `data[].provider` 에 `langgenius/ollama/ollama` 노출 시 설치 완료.
3. **커스텀 모델 등록**: `POST /workspaces/current/model-providers/langgenius/ollama/ollama/models/credentials` with `{model, model_type, credentials, name}`.
4. workspace 기본 모델 지정: `POST /workspaces/current/default-model` — high_quality Dataset 생성 전 필수.

**함정**: `/models` 엔드포인트(끝에 `/credentials` 없음)는 **load-balancing 전용**. 200 `{"result":"success"}` 를 돌려주지만 실제 모델 등록은 안 된다. embedding 이 "등록됐는데도" `Default model not found for text-embedding` 이 나는 사고의 실제 원인이 이것이었다.

구현체: [code-AI-quality-allinone/scripts/provision.sh](code-AI-quality-allinone/scripts/provision.sh) 의 `dify_install_ollama_plugin` / `dify_register_ollama_provider`.

### tree-sitter-languages 버전 핀

[code-AI-quality-allinone/Dockerfile](code-AI-quality-allinone/Dockerfile) 의 Phase 1 AST 청킹 의존성은 `tree-sitter<0.22` + `tree-sitter-languages==1.10.2` 로 핀돼 있다. `tree-sitter-languages==1.10.2` 의 native C 바인딩이 `tree-sitter<0.22` 용으로 빌드돼 있어서, 최신 `tree-sitter==0.25.x` 를 같이 설치하면 `get_parser('python')` 호출 시 `TypeError: __init__() takes exactly 1 argument (2 given)` 로 즉시 실패한다.

**조용한 실패**: `repo_context_builder.py` 가 files=0 chunks=0 으로 통과해 JSONL 이 생성되지 않고 doc_processor 가 legacy MD 폴백을 타기 때문에 P1 Job 은 SUCCESS 인데 RAG 데이터는 단일 MD 1건뿐인 상태가 된다. 발견이 매우 어렵다.

Phase 1.5 이후 버전업이 필요해지면 `tree-sitter-languages` 를 `tree-sitter-language-pack` (신규 API 지원) 로 교체할 것.

### e2e 슈트 자동 실행 — 4슬롯 구조 (2026-05-16 전면 재작성)

기존 18094-18098 daemon e2e 슈트 5개 + popup/tour/journey/headed 슈트 9개는 2026-05-16 일괄 폐기됨 (들인 시간 대비 실제 회귀 가드 효과가 낮아). 새 구조 + 설계 근거 + 폐기된 슈트 진단은 [playwright-allinone/docs/PLAN_E2E_REWRITE.md](playwright-allinone/docs/PLAN_E2E_REWRITE.md) 참조.

| 슬롯 | 발사 | 가드 |
|---|---|---|
| pre-commit | 매 commit | A 그룹 emit/generator unit (`e2e-test/unit/`, < 30s, 26 tests) |
| pre-push | 매 push | playwright-allinone/e2e-test/ 전체 (< 5min, 33 tests) + Replay UI 휴대용 자산 stale 자동 갱신 |
| build-time selftest | `./build.sh` 끝 | D 그룹 (`e2e-test/selftest_build/run.sh`, warn-only) — d4d957b, 0da0036 등 P0 surface |
| receiving-PC selftest | `Launch-ReplayUI` 최초 1회 | E 그룹 (`e2e-test/selftest_receive/run.py`) — Python/Playwright/Chromium/replay_service 자가진단 |

자동화 불가 잔여 (popup Stop&Convert hang, R-Plus replay, agent 자동연결, 외부 SUT 벤치) 는 [playwright-allinone/docs/RELEASE_CHECKLIST.md](playwright-allinone/docs/RELEASE_CHECKLIST.md) 의 release 직전 수동 항목.

**Replay UI 의 두 실행 형태 — 다른 포트**:

| 형태 | 포트 | 어디서 띄우는가 |
|---|---|---|
| 호스트 Replay UI | 18093 | dev/빌드 머신에서 직접 `python -m uvicorn replay_service.server:app --port 18093`. Recording UI cross-link 도 여기로. |
| 휴대용 Replay UI | 18099 | 받는 PC 가 zip 풀고 `Launch-ReplayUI.{bat,command}` 더블클릭. |
| Recording UI (호스트 데몬) | 18092 | agent-setup step 6.5 가 띄움 |

원래 모든 Replay UI 가 18094 였는데 18094 가 e2e 슈트 포트라 commit 회귀가 조용히 스킵되는 사고가 있어, 2026-05-14 사용자 결정으로 **호스트 Replay UI 는 18093, 휴대용 Replay UI 는 18099** 로 분리. e2e 포트 18094-18098 은 폐기와 함께 회수됨.

- 설치: `bash playwright-allinone/scripts/install-git-hooks.sh` 1회.
- 일시 우회: `git commit --no-verify` / `git push --no-verify` — 사용자가 명시 요청한 경우 외 금지.

**새 슈트 추가 가이드**: `playwright-allinone/e2e-test/{unit,integration,flow}/` 에 추가. 1 슈트 = 1 회귀 commit 매핑. docstring 첫 줄에 표적 commit hash. fixture 는 `e2e-test/fixtures/` 에 self-served HTML.
