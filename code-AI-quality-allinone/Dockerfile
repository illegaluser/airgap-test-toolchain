# syntax=docker/dockerfile:1.7
# ============================================================================
# TTC 4-Pipeline All-in-One Integrated Image
#
# 4개 파이프라인을 단일 컨테이너(오프라인/폐쇄망 전제)에서 실행:
#   1. 코드 사전학습          — repo_context_builder.py → Dify KB
#   2. 코드 정적분석           — SonarQube + SonarScanner
#   3. 정적분석 결과분석+이슈등록 — sonar_issue_exporter / dify_sonar_issue_analyzer
#                              / gitlab_issue_creator
#   4. AI 평가                — eval_runner (Ollama judge + Playwright local)
#
# Dify + Jenkins + PG/Redis/Qdrant 런타임 기반으로 SonarQube + SonarScanner +
# 파이프라인 1~3 Python 스크립트 + 파이프라인 4 eval_runner 를 통합. 형제 스택
# playwright-allinone/ 과 포트가 겹치지 않도록 28xxx / 50002 대역 사용:
#
#   playwright-allinone (형제) → 본 이미지
#     18080 Jenkins            → 28080
#     18081 Dify gateway       → 28081
#     50001 Jenkins agent      → 50002
#     (신규) SonarQube         → 29000
#
# 호스트 하이브리드 설계 유지:
#   - Ollama: 호스트에서 실행 (host.docker.internal:11434)
#   - GitLab: 외부 서비스 (API 호출만)
#   - Jenkins agent: 호스트에서 JNLP 연결 (Playwright headed 테스트용)
#
# 빌드 컨텍스트: **이 폴더 자체** (자체 완결). 폴더만 압축해서 다른 머신으로
# 옮긴 뒤 scripts/download-plugins.sh (온라인 필요) + scripts/build-{wsl2,mac}.sh
# 한 번씩 돌리면 완성된 이미지가 생성된다.
#
# 멀티 아키 (폴더 내부에서): \
#   cd code-AI-quality-allinone && \
#   docker buildx build --platform linux/<amd64|arm64> -f Dockerfile .
#
# 결과 이미지 크기 예상: 7-9GB (SonarQube 추가로 기존 대비 ~2GB 증가).
# ============================================================================

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Dify 공식 이미지에서 애플리케이션 파일 확보
# ─────────────────────────────────────────────────────────────────────────────
FROM langgenius/dify-api:1.13.3               AS dify-api-src
FROM langgenius/dify-web:1.13.3               AS dify-web-src
FROM langgenius/dify-plugin-daemon:0.5.3-local AS dify-plugin-src

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: SonarQube Community 이미지에서 앱 디렉토리 추출
# PoC: sonarqube:10.7.0-community 는 linux/amd64, linux/arm64 둘 다 공식 발매.
# /opt/sonarqube 만 떠다 옮기면 OpenJDK 는 베이스의 JDK21 을 재사용.
# ─────────────────────────────────────────────────────────────────────────────
FROM sonarqube:10.7.0-community AS sonarqube-src

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3: 최종 런타임 — Jenkins LTS (JDK 21) 기반
# ─────────────────────────────────────────────────────────────────────────────
FROM jenkins/jenkins:lts-jdk21

USER root
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Seoul

# ─────────────────────────────────────────────────────────────────────────────
# OS 패키지 — PostgreSQL 15 (pgdg), redis, nginx, supervisor, Python,
# Playwright 런타임 의존성, SonarQube 런타임 (unzip/fonts 등)
# ─────────────────────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg lsb-release && \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list && \
    apt-get update && apt-get install -y --no-install-recommends \
      supervisor \
      postgresql-15 postgresql-client-15 \
      redis-server \
      nginx \
      python3 python3-pip python3-venv python3-dev \
      build-essential libssl-dev zlib1g-dev \
      jq tini sudo unzip \
      libgtk-3-0 libdbus-glib-1-2 libx11-xcb1 \
      poppler-utils libreoffice-impress \
      fonts-noto-cjk fonts-dejavu \
      && (apt-get install -y --no-install-recommends libasound2t64 2>/dev/null \
          || apt-get install -y --no-install-recommends libasound2) \
      && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────────────────────
# Qdrant (벡터 스토어)
# ─────────────────────────────────────────────────────────────────────────────
ARG QDRANT_VERSION=v1.8.3
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) QDRANT_ARCH=x86_64-unknown-linux-gnu ;; \
      arm64) QDRANT_ARCH=aarch64-unknown-linux-musl ;; \
      *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fL -o /tmp/qdrant.tar.gz \
      "https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/qdrant-${QDRANT_ARCH}.tar.gz"; \
    tar xzf /tmp/qdrant.tar.gz -C /usr/local/bin; \
    chmod +x /usr/local/bin/qdrant; \
    rm /tmp/qdrant.tar.gz; \
    /usr/local/bin/qdrant --version

# ─────────────────────────────────────────────────────────────────────────────
# Dify 바이너리/앱
# ─────────────────────────────────────────────────────────────────────────────
COPY --from=dify-api-src    /app              /opt/dify-api
COPY --from=dify-web-src    /app              /opt/dify-web
COPY --from=dify-plugin-src /app              /opt/dify-plugin-daemon
COPY --from=dify-plugin-src /usr/local/bin/uv  /usr/local/bin/uv
COPY --from=dify-plugin-src /usr/local/bin/uvx /usr/local/bin/uvx

# dify-plugin-daemon 0.5.3-local query param binding 패치
RUN sed -i 's|"plugin_unique_identifier": plugin_unique_identifier|"PluginUniqueIdentifier": plugin_unique_identifier|g' \
    /opt/dify-api/api/core/plugin/impl/plugin.py && \
    grep -c 'PluginUniqueIdentifier' /opt/dify-api/api/core/plugin/impl/plugin.py

# ─────────────────────────────────────────────────────────────────────────────
# Python 의존성 — eval_runner / 파이프라인 1~3 공통
# (requirements.txt: requests, playwright, deepeval 등)
# ─────────────────────────────────────────────────────────────────────────────
COPY requirements.txt /tmp/requirements.txt
COPY scripts/requirements-pipelines.txt             /tmp/requirements-pipelines.txt
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed \
        -r /tmp/requirements.txt && \
    pip3 install --no-cache-dir --break-system-packages --ignore-installed \
        -r /tmp/requirements-pipelines.txt && \
    pip3 install --no-cache-dir --break-system-packages --ignore-installed \
        deepeval==1.3.5 --no-deps

# Playwright Chromium
RUN python3 -m playwright install --with-deps chromium

# eval_runner 용 venv (TARGET_TYPE=ui_chat 일 때 browser_adapter 가 참조 가능)
RUN /usr/bin/python3 -m venv --system-site-packages /opt/qa-venv && \
    /opt/qa-venv/bin/python -c "import requests, playwright, PIL; print('qa-venv deps OK')"

# ─────────────────────────────────────────────────────────────────────────────
# Dify 서비스 런타임 보강 (Python 3.12 + Node.js 22)
# ─────────────────────────────────────────────────────────────────────────────
COPY --from=dify-api-src /usr/local/bin/python3.12         /usr/local/bin/python3.12
COPY --from=dify-api-src /usr/local/bin/python3.12-config  /usr/local/bin/python3.12-config
COPY --from=dify-api-src /usr/local/lib/libpython3.12.so.1.0 /usr/local/lib/libpython3.12.so.1.0
COPY --from=dify-api-src /usr/local/lib/libpython3.so       /usr/local/lib/libpython3.so
COPY --from=dify-api-src /usr/local/lib/python3.12          /usr/local/lib/python3.12
RUN ln -sf python3.12 /usr/local/bin/python3 && \
    ln -sf python3    /usr/local/bin/python && \
    ln -sf python3.12-config /usr/local/bin/python3-config && \
    ln -sf libpython3.12.so.1.0 /usr/local/lib/libpython3.12.so && \
    ldconfig

ARG NODE_VERSION=22.22.1
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) NODE_ARCH=x64 ;; \
      arm64) NODE_ARCH=arm64 ;; \
      *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" -o /tmp/node.tar.xz; \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
      --no-same-owner --wildcards '*/bin/node' '*/include/node'; \
    rm /tmp/node.tar.xz; \
    node --version

# /app 호환 심볼릭 (dify entrypoint 가 하드코딩)
RUN mkdir -p /app && \
    ln -sfn /opt/dify-api/api       /app/api && \
    ln -sfn /opt/dify-web/web       /app/web && \
    ln -sfn /opt/dify-plugin-daemon /app/plugin_daemon

# ─────────────────────────────────────────────────────────────────────────────
# SonarQube Community — 파이프라인 2 (정적분석 엔진)
# PoC: sonarqube:10.7.0-community 는 /opt/sonarqube/ 에 모든 앱이 있고 Elasticsearch
# 를 내장. sonar 사용자 (uid/gid 1000) 가 필요하지만, 기존 jenkins user(uid 1000)
# 와 충돌하므로 별도 uid 1100 의 sonar 사용자로 소유권 재설정.
# ─────────────────────────────────────────────────────────────────────────────
COPY --from=sonarqube-src /opt/sonarqube /opt/sonarqube
# SonarQube 10.7 은 Java 17 필수 (Java 21 은 SecurityManager 제거로 기동 시
# UnsupportedOperationException). 공식 이미지가 번들한 jres/ tarball 에서 호스트
# 아키텍처용 JRE 17 을 추출하고 /opt/sonarqube/jres/current 안정 경로로 심볼릭.
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) JRE_TAR="OpenJDK17U-jre_x64_linux_hotspot_17.0.11_9.tar.gz" ;; \
      arm64) JRE_TAR="OpenJDK17U-jre_aarch64_linux_hotspot_17.0.11_9.tar.gz" ;; \
      *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    cd /opt/sonarqube/jres && \
    tar -xzf "$JRE_TAR" && \
    JRE_DIR=$(ls -d jdk-17*/ | head -n1 | sed 's#/$##'); \
    ln -sfn "/opt/sonarqube/jres/$JRE_DIR" /opt/sonarqube/jres/current && \
    /opt/sonarqube/jres/current/bin/java -version
RUN groupadd -g 1100 sonar && \
    useradd -u 1100 -g sonar -M -d /opt/sonarqube -s /bin/bash sonar && \
    chown -R sonar:sonar /opt/sonarqube && \
    sed -i 's|^#*sonar.web.port=.*|sonar.web.port=9000|' /opt/sonarqube/conf/sonar.properties || true && \
    echo "sonar.es.bootstrap.checks.disable=true" >> /opt/sonarqube/conf/sonar.properties

# Elasticsearch vm.max_map_count 요구를 런타임에 sysctl 가능한 호스트에서만 처리.
# 오프라인 컨테이너에서는 sonar.search.javaAdditionalOpts 로 ES JVM 튜닝.

# ─────────────────────────────────────────────────────────────────────────────
# SonarScanner CLI — Jenkins 파이프라인 2 가 호출하는 클라이언트
# ─────────────────────────────────────────────────────────────────────────────
ARG SONAR_SCANNER_VERSION=5.0.1.3006
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SS_ARCH=linux ;; \
      arm64) SS_ARCH=linux-aarch64 ;; \
      *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fL -o /tmp/sonar-scanner.zip \
      "https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-${SONAR_SCANNER_VERSION}-${SS_ARCH}.zip"; \
    unzip -q /tmp/sonar-scanner.zip -d /opt && \
    mv /opt/sonar-scanner-${SONAR_SCANNER_VERSION}-${SS_ARCH} /opt/sonar-scanner && \
    rm /tmp/sonar-scanner.zip && \
    ln -sfn /opt/sonar-scanner/bin/sonar-scanner /usr/local/bin/sonar-scanner && \
    sonar-scanner --version

# ─────────────────────────────────────────────────────────────────────────────
# Jenkins 플러그인 seed + Dify 플러그인 seed (download-plugins.sh 로 준비)
# ─────────────────────────────────────────────────────────────────────────────
COPY jenkins-plugins/ /opt/seed/jenkins-plugins/
COPY dify-plugins/    /opt/seed/dify-plugins/

# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL initdb + Dify/SonarQube DB 생성
# (dify / dify_plugin / sonar 3 개 DB 를 pg-init.sh 가 생성)
# ─────────────────────────────────────────────────────────────────────────────
COPY scripts/pg-init.sh /opt/pg-init.sh
RUN bash /opt/pg-init.sh

# ─────────────────────────────────────────────────────────────────────────────
# supervisord / nginx 설정 — 통합 이미지 전용 (포트 28080/28081/29000/50002)
# ─────────────────────────────────────────────────────────────────────────────
COPY scripts/supervisord.conf /etc/supervisor/supervisord.conf
COPY scripts/nginx.conf       /etc/nginx/nginx.conf

# ─────────────────────────────────────────────────────────────────────────────
# 파이프라인 1~3 Python 스크립트 + 파이프라인 4 eval_runner
# (ZeroTouch-QA 는 형제 스택 playwright-allinone 이 담당)
# ─────────────────────────────────────────────────────────────────────────────
RUN mkdir -p /opt/pipeline-scripts
COPY pipeline-scripts/repo_context_builder.py           /opt/pipeline-scripts/
COPY pipeline-scripts/doc_processor.py                  /opt/pipeline-scripts/
COPY pipeline-scripts/sonar_issue_exporter.py           /opt/pipeline-scripts/
COPY pipeline-scripts/dify_sonar_issue_analyzer.py      /opt/pipeline-scripts/
COPY pipeline-scripts/gitlab_issue_creator.py           /opt/pipeline-scripts/
COPY eval_runner                       /opt/eval_runner

# SCRIPTS_HOME 심볼릭 (eval_runner 전용)
RUN mkdir -p /opt/scripts-home && \
    ln -sfn /opt/eval_runner /opt/scripts-home/eval_runner

# ─────────────────────────────────────────────────────────────────────────────
# 4개 Jenkinsfile seed + URL/자격 치환
# (원본은 compose 서비스명 api/sonarqube/gitlab 을 가정하므로
#  통합 이미지 내부 주소(127.0.0.1) 로 치환. GitLab 만 별도 compose 서비스
#  `gitlab` 을 쓰므로 호스트 그대로 유지, 포트는 기본 80 으로 정규화.
#  하드코딩된 GitLab PAT 은 빈 문자열로 치환 — provision 단계에서 발급된
#  PAT 을 Jenkins Credentials 'gitlab-pat' 으로 주입 + withCredentials 로 런타임 치환)
# ─────────────────────────────────────────────────────────────────────────────
COPY jenkinsfiles/ /opt/jenkinsfiles/
COPY scripts/jenkins-seed/ /opt/seed/jenkins-home/

RUN set -eux; \
    cd /opt/jenkinsfiles; \
    # Dify API 내부 주소 (컨테이너 local PG/Redis 옆에서 돌고 있음)
    sed -i 's|http://api:5001|http://127.0.0.1:5001|g' *.jenkinsPipeline; \
    # SonarQube 도 동일 컨테이너 내부
    sed -i 's|http://sonarqube:9000|http://127.0.0.1:9000|g' *.jenkinsPipeline; \
    # GitLab 은 별도 compose 서비스. 기본 external_url 80 포트 정규화
    sed -i 's|http://gitlab:8929|http://gitlab:80|g' *.jenkinsPipeline; \
    # 하드코딩된 GitLab PAT 방어선 — 소스에서 이미 제거되어 있어야 하며, provision.sh
    # 가 런타임에 credentials('gitlab-pat') 으로 재치환한다. 이 grep 은 향후 커밋에서
    # 실수로 실제 PAT 가 섞여 들어오는 것을 빌드 타임에 차단하는 belt-and-suspenders.
    sed -i -E "s|'glpat-[A-Za-z0-9._-]+'|''|g" *.jenkinsPipeline; \
    sed -i -E 's|"glpat-[A-Za-z0-9._-]+"|""|g' *.jenkinsPipeline; \
    if grep -l 'glpat-' *.jenkinsPipeline 2>/dev/null; then \
        echo "[ERROR] GitLab PAT 잔존 — 빌드 중단" >&2; exit 1; \
    fi; \
    echo "Jenkinsfile URL/PAT 치환 완료"

# jenkins-init groovy 스크립트 (관리자 계정 초기화)
COPY jenkins-init /opt/jenkins-init
RUN mkdir -p /opt/seed/jenkins-home/init.groovy.d && \
    cp /opt/jenkins-init/*.groovy /opt/seed/jenkins-home/init.groovy.d/ 2>/dev/null || true

# ─────────────────────────────────────────────────────────────────────────────
# Dify 자산 (Workflow DSL, Dataset 정의) — provision.sh 가 자동 import
# ─────────────────────────────────────────────────────────────────────────────
COPY scripts/dify-assets/ /opt/dify-assets/

# ─────────────────────────────────────────────────────────────────────────────
# entrypoint + provisioner
# ─────────────────────────────────────────────────────────────────────────────
COPY scripts/entrypoint.sh /entrypoint.sh
COPY scripts/provision.sh  /opt/provision.sh
RUN chmod +x /entrypoint.sh /opt/provision.sh

# ─────────────────────────────────────────────────────────────────────────────
# 환경 변수 (런타임 기본값)
# ─────────────────────────────────────────────────────────────────────────────
ENV OLLAMA_BASE_URL=http://host.docker.internal:11434 \
    OLLAMA_MODEL=gemma4:e4b \
    JENKINS_PORT=28080 \
    JENKINS_AGENT_PORT=50002 \
    DIFY_GATEWAY_PORT=28081 \
    SONARQUBE_PORT=29000 \
    SONAR_HOST_URL=http://127.0.0.1:29000

# ─────────────────────────────────────────────────────────────────────────────
# 포트 / 볼륨 / 진입점
# ─────────────────────────────────────────────────────────────────────────────
EXPOSE 28080 28081 29000 50002
VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/tini","--","/entrypoint.sh"]
