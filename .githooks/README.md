# .githooks/

이 폴더의 hook 들은 **opt-in** 입니다. 한 번 설정해두면 git 명령 시 자동 동작.

## 설치 (개발자 PC 1회)

```bash
git config core.hooksPath .githooks
```

## 포함된 hook

### `pre-commit`

`playwright-allinone/e2e-test/unit/` 슈트 발사 (A 그룹 emit/generator unit, < 30s). 회귀 시 commit 차단. 설계 근거: [`PLAN_E2E_REWRITE.md`](../playwright-allinone/docs/PLAN_E2E_REWRITE.md) §4 (4슬롯) + §5 (그룹 A).

### `pre-push`

두 가지 일을 한다 (순서 중요):

1. **`playwright-allinone/e2e-test/` 슈트 발사** — 실패 시 push 차단. CI 없음 전제 (github actions 비용 0 정책), pre-commit 보다 빈도 낮은 push 시점이 e2e 자동 가드의 유일 슬롯. 시간 예산 < 5분. 설계 근거: [`PLAN_E2E_REWRITE.md`](../playwright-allinone/docs/PLAN_E2E_REWRITE.md).
2. **Replay UI 휴대용 자산 stale 검출 + 자동 갱신** — 경고만, push 비차단.

**왜 필요한가** — `playwright-allinone/replay-ui/` 폴더는 `zip -r` 한 줄로 외부 반출되는 휴대용 번들 그 자체이다. 그 안의 `embedded-python/`, `site-packages/`, `chromium/`, `recording_shared/`, `zero_touch_qa/` 같은 자산은 `.gitignore` 라 git 으로 추적되지 않는다. 출시 담당자가 `pack-windows.ps1` / `pack-macos.sh` 호출을 깜빡하면 *옛 자산이 든 zip* 이 그대로 나간다.

**동작** — push 직전:

1. 자산 source 폴더들(`shared/`, `replay-ui/replay_service`, `replay-ui/monitor`, `replay-ui-portable-build/templates`)의 현재 commit tree SHA 산출.
2. `replay-ui/.pack-stamp` 와 비교.
3. 같으면 조용히 push 진행.
4. 다르면 OS 자동 감지해 `pack-*` 실행 → stamp 갱신 후 push 진행.
5. 실패해도 push 차단은 안 함 (개발 흐름 보호) — 경고만 출력.

**전제 조건** — `.replay-ui-cache/cache/` 의 wheels/chromium 캐시가 채워져 있어야 자동 갱신 성공. 첫 빌드는 다음 한 줄 (Git Bash 또는 WSL2):

```bash
bash playwright-allinone/replay-ui-portable-build/build-cache.sh --target win64
# 또는 macos-arm64 / all
```

이후엔 hook 이 캐시를 그대로 재사용해 빠르게 동작.
