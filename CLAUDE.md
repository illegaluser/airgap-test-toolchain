Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

Tradeoff: These guidelines bias toward caution over speed. For trivial tasks, use judgment.

Lint 오류 — NON-NEGOTIABLE

작업 중 발견되는 모든 lint 오류(markdownlint / ruff / eslint / 기타)는 같은 작업 단위 안에서 해결한다. 사람·에이전트 모두 적용.

- 코드 lint 는 가능하면 `--fix` 자동 수정 후 남는 것은 수동 수정.
- 한국어 산문 문서는 MD013(80자) 같은 라인 길이 규칙이 부적합하므로 repo 루트 `.markdownlint.json` 으로 정책 차단 (이미 적용됨). 새 규칙을 끄거나 켤 때는 거기서.
- 누적 금지 — "나중에 일괄 정리" 는 신호 대비 잡음을 키운다. 발견 즉시.
- 비활성 정책에 새로 추가하려면 이유를 commit 메시지나 PR 설명에 남길 것.

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
