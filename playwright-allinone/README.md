# playwright-allinone 문서 입구

Zero-Touch QA All-in-One은 테스트 자동화에 필요한 Jenkins, Dify, DB를 하나의 Docker 컨테이너로 올리고, Ollama와 실제 브라우저 실행은 사용자의 PC에서 처리하는 배포본이다.

처음이라면 [playwright-allinone_QUICKSTART.md](playwright-allinone_QUICKSTART.md)만 열고 순서대로 따라간다. 운영 중 필요한 절차와 세부 값은 나머지 두 문서에서 찾는다.

| 문서 | 목적 |
| --- | --- |
| [playwright-allinone_QUICKSTART.md](playwright-allinone_QUICKSTART.md) | 처음 설치하고 Jenkins Pipeline과 Recording UI를 한 번 성공시키는 따라하기 문서 |
| [playwright-allinone_OPERATIONS.md](playwright-allinone_OPERATIONS.md) | 재배포, 수동 `docker run`, 백업/복원, 모델 변경, 장애 대응 절차 |
| [playwright-allinone_REFERENCE.md](playwright-allinone_REFERENCE.md) | 포트, 파일 위치, 환경변수, 데이터 구조, DSL/API 계약 |

가장 짧은 시작:

```bash
cd playwright-allinone
chmod +x *.sh
./build.sh --redeploy
```

이 명령은 오래 걸릴 수 있다. 완료 후 브라우저에서 Jenkins `http://localhost:18080`에 접속한다.
