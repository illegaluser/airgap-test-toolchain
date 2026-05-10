"""recording_tools — D17 일원화 후 deprecated.

이전 역할: 녹화 sess_dir 에서 bundle.zip 을 만드는 CLI (`auth_flow.pack_bundle` 위임).

D17 (2026-05-11) 부로 번들 zip 흐름 폐기 — 본 CLI 도 제거. 같은 결과를 얻으려면
호스트 측 sanitize 통과 .py 다운로드를 직접 받으세요:

  GET http://127.0.0.1:18092/recording/sessions/<sid>/original?download=1

응답은 `auth_flow.sanitize_script` 통과한 안전한 .py 본문. Replay UI 의 시나리오
스크립트 카드에서 그 .py 를 그대로 업로드 → 실행하면 됩니다.

본 모듈은 import-back-compat 만 위해 남겨 두었으며, 호출 시 NotImplementedError 를
raise 하는 stub 입니다.
"""

from __future__ import annotations


def main() -> int:
    raise NotImplementedError(
        "D17 — recording_tools CLI 폐기. "
        "GET /recording/sessions/<sid>/original?download=1 로 sanitize 통과 .py 받아 사용하세요."
    )


if __name__ == "__main__":
    raise SystemExit(main())
