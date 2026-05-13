DSCORE Replay UI 휴대용 패키지 (macOS arm64)
==============================================

이 폴더는 zip 으로 받은 그대로 동작합니다.
별도 설치 / 관리자 권한 / 인터넷 연결 불필요.

사용법
------

1) zip 을 임의 폴더에 풉니다 (예: ~/replay-ui/ , 또는 USB 드라이브).
2) [최초 1회만] macOS 의 Gatekeeper 격리 해제.
   Finder 에서 Launch-ReplayUI.command 를 우클릭(또는 Control-클릭) → "열기"
   → 경고 다이얼로그에서 다시 "열기".
   또는 터미널에서 한 번:
     xattr -dr com.apple.quarantine /path/to/이-폴더
3) Launch-ReplayUI.command 더블클릭.
4) 잠시 후 기본 웹브라우저에 http://127.0.0.1:18094/ 이 뜹니다.
5) 종료하려면 Stop-ReplayUI.command 를 더블클릭하세요.

데이터 위치
-----------

이 폴더 안의 data/ 에 모든 상태가 저장됩니다.
  - data/auth-profiles/   로그인 프로파일
  - data/scenarios/       .py 시나리오 파일
  - data/scripts/         실행 후보 시나리오
  - data/runs/            실행 결과 (trace.zip, 보고서, 로그)

폴더를 통째로 이동해도 데이터가 같이 따라갑니다.

문제 해결
---------

- 브라우저가 열리지 않으면 직접 http://127.0.0.1:18094/ 로 접속해 보세요.
- 포트 18094 가 이미 사용 중이면 기존 인스턴스로 자동 연결됩니다.
- 첫 실행 시 macOS 가 차단하면 위 2번 절차를 따르세요 (사내 비공식 배포 — Apple notarization 없음).
- 로그/오류 흔적은 data/runs/replay-ui.stderr.log 에서 확인 가능합니다.
