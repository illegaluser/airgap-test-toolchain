---
name: 네이버 로그인은 통과점 — 진짜 대상은 연동 서비스
description: airgap-test-toolchain 의 인증 프로파일(auth-profile) 설계는 "네이버 자체 테스트" 가 아니라 "네이버로 로그인되는 외부 서비스 테스트" 를 전제로 한다.
type: project
originSessionId: a02f5231-dbb0-443c-8a95-77ee572021da
---
airgap-test-toolchain 의 Recording UI / `auth_profiles` 설계에서 네이버 로그인은 **목적지가 아니라 통과점**이다. 사용자의 실제 테스트 대상은 *"네이버로 로그인" 을 지원하는 외부 서비스* (예: 회사 사내 시스템, 커머스, 예약 서비스 등) 이다.

**Why:** 사용자가 2026-04-29 명시. "네이버를 직접 제어하기보다는 네이버 계정과 연동된 다른 서비스에 접속해서 테스트를 수행하기 위함."

**How to apply:**
- `seed_url` 의 기본 가정은 *서비스의 진입 페이지* (예: `https://service.example.com/`) 이지 `nid.naver.com` 이 아님. 사용자가 별도 창에서 서비스의 "네이버로 로그인" 버튼을 직접 누르고, 네이버 화면에서 ID/PW + 2중 확인을 통과한 뒤, 서비스로 redirect 되어 자체 세션이 발급되는 *전체 OAuth 라운드트립* 을 한 번에 수행한다.
- `verify_url` / `verify_selector` 는 **서비스 측의 로그인 전용 페이지/요소** 로 잡는다 (예: `https://service.example.com/mypage` 의 사용자 이름 노출). 네이버 측 마이페이지는 부적절 — 우리가 알고 싶은 건 *서비스가 우리를 로그인된 사용자로 인식하는가*.
- storageState 는 두 도메인의 쿠키/localStorage 를 모두 포함해야 함 (Playwright 기본 동작은 모든 도메인 보존). 네이버만 살리고 서비스 쿠키가 빠지면 무용지물.
- 녹화 / 재생의 `target_url` 은 *서비스 도메인 안의 기능 페이지* (예: 회의실 예약, 결재 승인). 네이버 도메인이 직접 등장하는 건 시드 1회 + 만료 후 재시드뿐.
- UI/CLI 안내 문구는 "테스트 대상 서비스의 시작 URL" 로 적어야지 "네이버 로그인 URL" 이 아님. 사용자 혼동 방지.
