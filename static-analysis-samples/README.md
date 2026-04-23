# Static Analysis Sample Repos

이 디렉터리는 **정적분석 테스트용 외부 샘플 레포 묶음**이다.

현재 파이프라인 특성:

- Jenkins `02 코드 정적분석` Job 은 **빌드/테스트를 거의 수행하지 않고**
  SonarScanner CLI 를 바로 실행한다.
- 따라서 **Java/Maven 레포보다 JS/TS 레포가 즉시 분석에 더 잘 맞는다.**

## 추천 순서

### 1. `NodeGoat` — 1순위

경로:

- `static-analysis-samples/NodeGoat`

원본:

- `https://github.com/OWASP/NodeGoat`

이유:

- OWASP Top 10 기반의 **의도적으로 취약한 Node.js 앱**
- JS 소스라서 현재 SonarJS 파이프라인과 궁합이 좋음
- 보안 취약점, 보안 핫스팟, 코드 스멜, 잠재 버그를 **한 번에 많이 보기 좋음**
- `sonar-project.properties` 를 추가해 **바로 스캔 가능하게 구성**해 둠

권장 Jenkins 파라미터:

- `REPO_URL = http://gitlab:80/root/nodegoat.git`
- `SONAR_PROJECT_KEY = nodegoat`
- `GITLAB_PROJECT = root/nodegoat`

### 2. `BenchmarkJava` — 보안 취약점 밀도 최고

경로:

- `static-analysis-samples/BenchmarkJava`

원본:

- `https://github.com/OWASP-Benchmark/BenchmarkJava`

이유:

- OWASP Benchmark 는 **SAST 도구 정확도 측정용 공식 벤치마크**
- 취약점 종류와 건수가 매우 많아 **보안 이슈 수집량**은 가장 좋음

주의:

- Java 분석 품질을 제대로 내려면 **컴파일 산출물**이 필요하다.
- 현재 PoC 파이프라인처럼 build 를 생략하면 NodeGoat 대비 운영성이 떨어진다.
- 따라서 **본격 보안 벤치마크용 2순위**로 권장한다.

### 3. `sonar-training-examples` — SonarQube 기능별 데모

경로:

- `static-analysis-samples/sonar-training-examples`

원본:

- `https://github.com/SonarSource/sonar-training-examples`

이유:

- SonarSource 공식 교육용 예제
- complexity, security, external issues 등 **기능별 실험**에 적합
- “이슈가 많이 나오는 대형 샘플”보다는 **특정 Sonar 기능 검증용**에 가깝다

## 바로 쓰는 흐름

### NodeGoat 를 GitLab 에 올려 Sonar 파이프라인 실행

1. 샘플 디렉터리로 이동

```bash
cd static-analysis-samples/NodeGoat
```

2. 원격 GitLab 프로젝트 생성 후 push

예시:

```bash
git remote rename origin upstream
git remote add origin http://localhost:28090/root/nodegoat.git
git push -u origin HEAD:main
```

3. Jenkins `00 코드 분석 체인` 또는 `02 코드 정적분석` 실행

권장 값:

- `REPO_URL = http://gitlab:80/root/nodegoat.git`
- `SONAR_PROJECT_KEY = nodegoat`
- `GITLAB_PROJECT = root/nodegoat`

## 운영 메모

- `NodeGoat` 는 **현재 파이프라인 기준 즉시 사용 가능한 실전형 샘플**
- `BenchmarkJava` 는 **보안 이슈 다양성은 최고**지만 build 단계 보강 후 쓰는 편이 맞다
- `sonar-training-examples` 는 Sonar 특정 기능 검증 시 보조용으로 유지
