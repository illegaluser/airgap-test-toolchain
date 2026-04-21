#!/usr/bin/env bash
# ============================================================================
# TTC 4-Pipeline All-in-One — 최초 프로비저닝 (완전 자동화)
#
# 최초 기동 시 1회만 수행. 각 단계는 멱등성 보장 — 중간 실패 후 재실행 OK.
# 자동화 범위:
#   A. Jenkins 4개 Job 등록 (사전학습/정적분석/결과분석+이슈등록/AI평가)
#   B. Dify 관리자 setup + 로그인
#   C. Dify Ollama provider 등록 (호스트 Ollama 가리킴)
#   D. Dify Knowledge Dataset 생성 (code-context-kb)
#   E. Dify Sonar Analyzer Workflow import
#   F. Dify API Key 발급 (Dataset, Workflow)
#   G. GitLab root PAT 발급 via REST API
#   H. Jenkins Credentials 자동 주입
#       - dify-dataset-id
#       - dify-knowledge-key
#       - dify-workflow-key
#       - gitlab-pat
#   I. Jenkinsfile 사본에 credentials('gitlab-pat') 참조 삽입
#   J. SonarQube 초기 비번 변경 + 토큰 발급 → Jenkins 'sonarqube-token' 주입
#
# 필수 환경변수 (docker-compose 에서 주입):
#   JENKINS_URL             http://127.0.0.1:${JENKINS_PORT:-28080}
#   DIFY_URL                http://127.0.0.1:${DIFY_GATEWAY_PORT:-28081}
#   SONAR_URL               http://127.0.0.1:9000
#   GITLAB_URL_INTERNAL     http://gitlab:80 (docker 내부 DNS)
#   GITLAB_ROOT_PASSWORD    초기 GitLab root 비밀번호
#   OLLAMA_BASE_URL         http://host.docker.internal:11434
# ============================================================================
set -uo pipefail

LOG_PREFIX="[provision]"
# log 도 stderr 로 보낸다. stdout 은 함수의 "순수 반환값" 전용이다 — 함수 내부에서
# log "..." 를 찍으면 $(fn_call) 로 받는 caller 변수가 로그로 오염된다.
log()  { printf '%s %s\n' "$LOG_PREFIX" "$*" >&2; }
warn() { printf '%s WARN:  %s\n' "$LOG_PREFIX" "$*" >&2; }
err()  { printf '%s ERROR: %s\n' "$LOG_PREFIX" "$*" >&2; }

# ─ 설정 ────────────────────────────────────────────────────────────────────
JENKINS_URL="${JENKINS_URL:-http://127.0.0.1:28080}"
JENKINS_USER="${JENKINS_USER:-admin}"
JENKINS_PASSWORD="${JENKINS_PASSWORD:-password}"
JENKINSFILE_DIR="${OFFLINE_JENKINSFILE_DIR:-/opt/jenkinsfiles}"

DIFY_URL="${DIFY_URL:-http://127.0.0.1:28081}"
DIFY_ADMIN_EMAIL="${DIFY_ADMIN_EMAIL:-admin@ttc.local}"
DIFY_ADMIN_NAME="${DIFY_ADMIN_NAME:-admin}"
DIFY_ADMIN_PASSWORD="${DIFY_ADMIN_PASSWORD:-TtcAdmin!2026}"

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-gemma4:e4b}"

GITLAB_URL="${GITLAB_URL_INTERNAL:-http://gitlab:80}"
GITLAB_ROOT_PASSWORD="${GITLAB_ROOT_PASSWORD:-ChangeMe!Pass}"

SONAR_URL="${SONAR_URL:-http://127.0.0.1:9000}"
SONAR_ADMIN_NEW_PASSWORD="${SONAR_ADMIN_NEW_PASSWORD:-TtcAdmin!2026}"
SONAR_TOKEN_NAME="${SONAR_TOKEN_NAME:-jenkins-auto}"

DIFY_ASSETS_DIR="${DIFY_ASSETS_DIR:-/opt/dify-assets}"
STATE_DIR="${STATE_DIR:-/data/.provision}"
mkdir -p "$STATE_DIR"

# ─ 공통: URL-safe name → Jenkins Job name ───────────────────────────────────
urlencode() {
    python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$1"
}

# ─ Jenkins crumb ────────────────────────────────────────────────────────────
jenkins_crumb() {
    curl -sS -u "$JENKINS_USER:$JENKINS_PASSWORD" \
        "$JENKINS_URL/crumbIssuer/api/json" 2>/dev/null \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['crumbRequestField']+':'+d['crumb'])" 2>/dev/null || true
}

# ─ Jenkins Job 존재 여부 ────────────────────────────────────────────────────
jenkins_job_exists() {
    curl -sf -o /dev/null -u "$JENKINS_USER:$JENKINS_PASSWORD" \
        "$JENKINS_URL/job/$(urlencode "$1")/api/json"
}

# ─ Jenkins Inline Pipeline Job 등록 ────────────────────────────────────────
jenkins_create_pipeline_job() {
    local name="$1" jenkinsfile="$2"
    [ ! -f "$jenkinsfile" ] && { warn "Jenkinsfile 없음: $jenkinsfile — $name 건너뜀"; return 0; }
    jenkins_job_exists "$name" && { log "  Job 이미 존재: $name"; return 0; }

    local encoded tmp_xml rc=0
    encoded=$(urlencode "$name")
    tmp_xml=$(mktemp)
    python3 <<PY > "$tmp_xml"
import html
with open(r"$jenkinsfile", encoding="utf-8") as f:
    script = f.read()
print("""<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>TTC All-in-One auto-provisioned: $name</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps">
    <script>{}</script>
    <sandbox>true</sandbox>
  </definition>
  <triggers/>
  <disabled>false</disabled>
</flow-definition>""".format(html.escape(script)))
PY

    local crumb; crumb=$(jenkins_crumb)
    [ -z "$crumb" ] && { warn "  crumb 획득 실패 — $name 등록 스킵"; rm -f "$tmp_xml"; return 1; }

    curl -sS -f -o /dev/null -X POST \
        -u "$JENKINS_USER:$JENKINS_PASSWORD" \
        -H "$crumb" \
        -H "Content-Type: application/xml" \
        --data-binary "@$tmp_xml" \
        "$JENKINS_URL/createItem?name=$encoded" \
        && log "  Job 등록: $name" || rc=$?
    rm -f "$tmp_xml"
    return $rc
}

# ─ Jenkins: Groovy 스크립트를 scriptText 엔드포인트로 실행 ─────────────────
# 주 용도: SonarQube server/tool 등 XML 직접 편집이 번거로운 global config 주입.
jenkins_exec_groovy() {
    local script="$1"
    local crumb; crumb=$(jenkins_crumb)
    [ -z "$crumb" ] && { warn "crumb 없음 — groovy 실행 스킵"; return 1; }
    local tmp; tmp=$(mktemp)
    printf '%s' "$script" > "$tmp"
    local resp; resp=$(curl -sS -X POST \
        -u "$JENKINS_USER:$JENKINS_PASSWORD" -H "$crumb" \
        --data-urlencode "script@$tmp" \
        "$JENKINS_URL/scriptText" 2>/dev/null)
    rm -f "$tmp"
    echo "$resp"
}

# ─ Jenkins: SonarQube server + scanner tool 자동 등록 ──────────────────────
# 02 코드 정적분석.jenkinsPipeline 의 `withSonarQubeEnv('dscore-sonar')` +
# `tool 'SonarScanner-CLI'` 가 의존. sonar Jenkins plugin 설치 후 (download-plugins.sh)
# global config 에 server/tool 을 명시 등록. credentialsId 는 'sonarqube-token'
# (jenkins_upsert_string_credential 이 먼저 주입).
jenkins_configure_sonar_integration() {
    local sonar_url="${1:-http://127.0.0.1:9000}"
    local state="$STATE_DIR/jenkins_sonar_integration.ok"
    [ -f "$state" ] && return 0

    local script; script=$(cat <<'GROOVY'
import jenkins.model.Jenkins
import hudson.plugins.sonar.SonarGlobalConfiguration
import hudson.plugins.sonar.SonarInstallation
import hudson.plugins.sonar.SonarRunnerInstallation

def inst = Jenkins.get()

// Server: name 'dscore-sonar', URL = 인자, credentialsId 'sonarqube-token'.
// 9-arg constructor 로 credentialsId 전달.
def sonarCfg = inst.getDescriptorByType(SonarGlobalConfiguration.class)
def server = new SonarInstallation(
    "dscore-sonar", "__SONAR_URL__", "sonarqube-token",
    null, "", "", "", "", null
)
sonarCfg.setInstallations(server)
sonarCfg.save()

// Scanner tool: name 'SonarScanner-CLI', home '/opt/sonar-scanner' (이미지 번들)
def scannerDesc = inst.getDescriptorByType(SonarRunnerInstallation.DescriptorImpl.class)
def scanner = new SonarRunnerInstallation("SonarScanner-CLI", "/opt/sonar-scanner", new ArrayList())
scannerDesc.setInstallations(scanner)
scannerDesc.save()
inst.save()

println("Registered: Sonar server=" + sonarCfg.getInstallations().collect{it.name} +
        ", scanner=" + scannerDesc.getInstallations().collect{it.name})
GROOVY
)
    # 인자 주입 (placeholder 치환)
    script="${script//__SONAR_URL__/$sonar_url}"

    local resp; resp=$(jenkins_exec_groovy "$script")
    if echo "$resp" | grep -q "Registered:"; then
        touch "$state"
        log "  Jenkins SonarQube server/tool 등록 완료 (dscore-sonar, SonarScanner-CLI)"
    else
        warn "  Jenkins SonarQube 설정 실패 — 응답: $(echo $resp | head -c 300)"
        return 1
    fi
}

# ─ Jenkins Credentials: Secret text 생성/갱신 ──────────────────────────────
jenkins_upsert_string_credential() {
    local id="$1" secret="$2" description="${3:-auto-provisioned}"
    local crumb; crumb=$(jenkins_crumb)
    [ -z "$crumb" ] && { warn "crumb 없음 — credential $id 스킵"; return 1; }

    local payload
    payload=$(python3 <<PY
import json, urllib.parse
obj = {
    "": "0",
    "credentials": {
        "scope": "GLOBAL",
        "id": "$id",
        "secret": """$secret""",
        "description": "$description",
        "\$class": "org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl"
    }
}
print("json=" + urllib.parse.quote(json.dumps(obj)))
PY
)

    # 기존 삭제 후 재생성 (멱등)
    local encoded; encoded=$(urlencode "$id")
    curl -sS -o /dev/null -X POST \
        -u "$JENKINS_USER:$JENKINS_PASSWORD" -H "$crumb" \
        "$JENKINS_URL/credentials/store/system/domain/_/credential/$encoded/doDelete" 2>/dev/null || true

    curl -sS -f -o /dev/null -X POST \
        -u "$JENKINS_USER:$JENKINS_PASSWORD" \
        -H "$crumb" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        --data "$payload" \
        "$JENKINS_URL/credentials/store/system/domain/_/createCredentials" \
        && log "  Credential 주입: $id" \
        || { warn "  Credential 주입 실패: $id"; return 1; }
}

# ─ Dify: 관리자 setup (최초 1회만) ──────────────────────────────────────────
dify_setup_admin() {
    local setup_url="$DIFY_URL/console/api/setup"
    local state; state=$(curl -sS "$setup_url" 2>/dev/null || echo '{}')
    if echo "$state" | grep -q '"step":"finished"'; then
        log "Dify setup 이미 완료됨 — 건너뜀"
        return 0
    fi

    log "Dify 관리자 초기 setup: $DIFY_ADMIN_EMAIL"
    curl -sS -f -X POST "$setup_url" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "import json,os; print(json.dumps({'email':'$DIFY_ADMIN_EMAIL','name':'$DIFY_ADMIN_NAME','password':'$DIFY_ADMIN_PASSWORD'}))")" \
        >/dev/null \
        && log "Dify setup 완료" \
        || { err "Dify setup 실패"; return 1; }
}

# ─ Dify: 로그인 → access_token (Dify 1.13+) ──────────────────────────────
# Dify 1.13.x 의 /console/api/login 은 다음 두 변화를 적용했다:
#   1) password 필드를 base64 로 "obfuscation" (libs/encryption.py = b64decode).
#      plaintext 를 보내면 "Invalid encrypted data" 로 401.
#   2) 응답 body 는 {"result":"success"} 뿐이며 access_token/csrf_token/
#      refresh_token 은 HTTP 쿠키로 전달된다.
# 후속 콘솔 API 호출은:
#   Authorization: Bearer <access_token>  +  X-CSRF-Token: <csrf_token>  +  쿠키 3종
# 을 모두 요구한다. 쿠키는 $DIFY_COOKIE_JAR 에 저장해 재사용.
DIFY_COOKIE_JAR="${DIFY_COOKIE_JAR:-/tmp/dify-provision-cookies.txt}"

dify_login() {
    local pw_b64
    pw_b64=$(printf '%s' "$DIFY_ADMIN_PASSWORD" | base64 | tr -d '\n')
    rm -f "$DIFY_COOKIE_JAR"
    curl -sS -c "$DIFY_COOKIE_JAR" -X POST "$DIFY_URL/console/api/login" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "import json,sys; print(json.dumps({'email':sys.argv[1],'password':sys.argv[2],'language':'ko-KR','remember_me':True}))" "$DIFY_ADMIN_EMAIL" "$pw_b64")" \
        -o /dev/null 2>/dev/null || return 1
    _dify_extract_cookie access_token
}

_dify_extract_cookie() {
    # 쿠키 jar 에서 지정 쿠키값 추출 (netscape 포맷: 이름 컬럼은 탭 구분 6번째)
    local name="$1"
    awk -v name="$name" 'BEGIN{FS="\t"} $6==name {print $7}' "$DIFY_COOKIE_JAR" 2>/dev/null | tail -n1
}

# 모든 console API 호출이 써야 할 인증 인자들. 배열로 받아서 curl 에 전달.
_dify_auth_args() {
    local token csrf
    token=$(_dify_extract_cookie access_token)
    csrf=$(_dify_extract_cookie csrf_token)
    printf '%s\n' "-b" "$DIFY_COOKIE_JAR" "-H" "Authorization: Bearer $token" "-H" "X-CSRF-Token: $csrf"
}

# ─ Dify: Ollama provider 등록 ──────────────────────────────────────────────
# ─ Dify 1.13+: Ollama 모델 provider 는 플러그인으로 설치. 최초 1 회 seed .difypkg
# 를 upload/pkg → install/pkg 로 등록해야 이후 customizable-model API 가 의미를 가진다.
# 멱등: 이미 설치된 provider 는 model-providers 목록으로 확인 후 skip.
dify_install_ollama_plugin() {
    local cached="$STATE_DIR/ollama_plugin.ok"
    [ -f "$cached" ] && { log "  Ollama 플러그인 이미 설치됨 — skip"; return 0; }

    local pkg="/opt/seed/dify-plugins/langgenius-ollama-0.1.3.difypkg"
    [ -f "$pkg" ] || { warn "  Ollama 플러그인 .difypkg 없음 ($pkg)"; return 1; }

    log "Dify Ollama 플러그인 설치 (offline pkg)"
    mapfile -t _auth < <(_dify_auth_args)

    # 이미 등록된 경우 skip
    local existing; existing=$(curl -sS "$DIFY_URL/console/api/workspaces/current/model-providers" \
        "${_auth[@]}" 2>/dev/null \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(any(p.get('provider','').endswith('/ollama') for p in d.get('data',[])))" 2>/dev/null || echo "False")
    if [ "$existing" = "True" ]; then
        log "  Ollama provider 이미 등록됨 — 플러그인 재설치 skip"
        touch "$cached"
        return 0
    fi

    # 1) .difypkg 업로드 → unique_identifier 획득
    local up_resp
    up_resp=$(curl -sS -X POST "$DIFY_URL/console/api/workspaces/current/plugin/upload/pkg" \
        "${_auth[@]}" \
        -F "pkg=@$pkg" 2>/dev/null)
    local uid
    uid=$(echo "$up_resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('unique_identifier',''))" 2>/dev/null)
    [ -z "$uid" ] && { warn "  플러그인 upload 실패 — 응답: $(echo "$up_resp" | head -c 300)"; return 1; }

    # 2) install/pkg → task_id 생성 후 비동기 완료 대기
    local payload; payload=$(python3 -c "import json; print(json.dumps({'plugin_unique_identifiers':['$uid']}))")
    local inst_resp
    inst_resp=$(curl -sS -X POST "$DIFY_URL/console/api/workspaces/current/plugin/install/pkg" \
        "${_auth[@]}" -H "Content-Type: application/json" -d "$payload" 2>/dev/null)
    echo "$inst_resp" | grep -q '"task_id"' || { warn "  플러그인 install 실패 — 응답: $(echo "$inst_resp" | head -c 300)"; return 1; }

    # 3) provider 목록에 나타날 때까지 최대 60s 대기
    local _w=0
    until [ $_w -ge 60 ]; do
        sleep 3; _w=$((_w + 3))
        local ok; ok=$(curl -sS "$DIFY_URL/console/api/workspaces/current/model-providers" \
            "${_auth[@]}" 2>/dev/null \
            | python3 -c "import json,sys; d=json.load(sys.stdin); print(any(p.get('provider','').endswith('/ollama') for p in d.get('data',[])))" 2>/dev/null || echo "False")
        [ "$ok" = "True" ] && { touch "$cached"; log "  Ollama 플러그인 설치 완료 (${_w}s)"; return 0; }
    done
    warn "  Ollama 플러그인 설치 60s 내 미완료"
    return 1
}

dify_register_ollama_provider() {
    log "Dify Ollama provider 등록 ($OLLAMA_BASE_URL, 모델=$OLLAMA_MODEL)"
    local payload
    # Dify 1.13 custom model 등록은 /models/credentials 엔드포인트. /models 는 load-balancing
    # 전용이며 200 을 반환하지만 실제 모델은 등록되지 않음.
    payload=$(python3 <<PY
import json
print(json.dumps({
    "model": "$OLLAMA_MODEL",
    "model_type": "llm",
    "credentials": {
        "base_url": "$OLLAMA_BASE_URL",
        "mode": "chat",
        "context_size": "8192",
        "max_tokens": "4096"
    },
    "name": "$OLLAMA_MODEL-default"
}))
PY
)
    mapfile -t _auth < <(_dify_auth_args)
    curl -sS -X POST "$DIFY_URL/console/api/workspaces/current/model-providers/langgenius/ollama/ollama/models/credentials" \
        "${_auth[@]}" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        -o /tmp/dify-provider.json 2>/dev/null
    if grep -qE '"error"|exception|"code":"[^s]' /tmp/dify-provider.json; then
        warn "  Ollama provider 등록 경고 — 응답: $(head -c 300 /tmp/dify-provider.json)"
    else
        log "  Ollama provider 등록 완료 ($OLLAMA_MODEL)"
    fi
}

# ─ Dify: Ollama embedding provider 등록 (bge-m3) ───────────────────────────
# Dataset high_quality 모드 (벡터 검색) 은 임베딩 모델이 Dify provider 에 등록되어
# 있어야 함. 호스트 Ollama 에 미리 받아둔 bge-m3 를 text-embedding 모델로 등록.
# 멱등: state cache 확인 후 skip.
dify_register_ollama_embedding() {
    local emb_model="${OLLAMA_EMBEDDING_MODEL:-bge-m3}"
    local cached="$STATE_DIR/ollama_embedding.ok"
    [ -f "$cached" ] && { log "  Ollama embedding provider 이미 등록됨 — skip"; return 0; }

    log "Dify Ollama embedding 등록 ($OLLAMA_BASE_URL, 모델=$emb_model)"
    local payload
    payload=$(python3 <<PY
import json
print(json.dumps({
    "model": "$emb_model",
    "model_type": "text-embedding",
    "credentials": {
        "base_url": "$OLLAMA_BASE_URL",
        "context_size": "8192"
    },
    "name": "$emb_model-default"
}))
PY
)
    mapfile -t _auth < <(_dify_auth_args)
    curl -sS -X POST "$DIFY_URL/console/api/workspaces/current/model-providers/langgenius/ollama/ollama/models/credentials" \
        "${_auth[@]}" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        -o /tmp/dify-embedding.json 2>/dev/null
    if grep -qE '"error"|exception|"code":"[^s]' /tmp/dify-embedding.json; then
        warn "  Ollama embedding 등록 경고 — 응답: $(head -c 300 /tmp/dify-embedding.json)"
        return 1
    fi
    touch "$cached"
    log "  Ollama embedding 등록 완료 ($emb_model)"
}

# ─ Dify: workspace default model 설정 (LLM + embedding) ───────────────────
# Dify 1.13 은 Dataset high_quality 생성 시 "workspace 기본 embedding 모델" 을 요구.
# model-provider 등록과 별개로 이 설정이 비어있으면 "Default model not found" 400.
# LLM 기본도 함께 설정해 workflow LLM 노드가 기본값으로 동작하도록 보장.
dify_set_default_models() {
    local cached="$STATE_DIR/default_models.ok"
    [ -f "$cached" ] && { log "  기본 모델 설정 이미 완료됨 — skip"; return 0; }

    local emb_model="${OLLAMA_EMBEDDING_MODEL:-bge-m3}"
    log "Dify workspace 기본 모델 설정 (llm=$OLLAMA_MODEL, embedding=$emb_model)"

    local payload
    payload=$(python3 <<PY
import json
print(json.dumps({
    "model_settings": [
        {"model_type": "llm", "provider": "langgenius/ollama/ollama", "model": "$OLLAMA_MODEL"},
        {"model_type": "text-embedding", "provider": "langgenius/ollama/ollama", "model": "$emb_model"}
    ]
}))
PY
)
    mapfile -t _auth < <(_dify_auth_args)
    local resp
    resp=$(curl -sS -X POST "$DIFY_URL/console/api/workspaces/current/default-model" \
        "${_auth[@]}" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null)
    if echo "$resp" | grep -qE '"error"|exception|"code":"'; then
        warn "  기본 모델 설정 경고 — 응답: $(echo "$resp" | head -c 300)"
        return 1
    fi
    touch "$cached"
    log "  기본 모델 설정 완료"
}

# ─ Dify: Knowledge Dataset 생성 (또는 기존 재사용) → dataset_id ────────────
dify_create_dataset() {
    local cached="$STATE_DIR/dataset_id"
    [ -f "$cached" ] && { cat "$cached"; return 0; }

    local ds_name; ds_name=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['name'])" "$DIFY_ASSETS_DIR/code-context-dataset.json")

    # 이름 중복이면 동일 이름의 기존 Dataset id 를 재사용 (provision 이 과거에
    # 생성한 레코드를 state cache 유실 후에도 재사용 가능해야 함).
    mapfile -t _auth < <(_dify_auth_args)
    local existing; existing=$(curl -sS -G "$DIFY_URL/console/api/datasets" \
        --data-urlencode "page=1" --data-urlencode "limit=100" \
        "${_auth[@]}" 2>/dev/null \
        | python3 -c "import json,sys; d=json.load(sys.stdin);
ds=[x for x in d.get('data',[]) if x.get('name')==sys.argv[1]];
print(ds[0]['id'] if ds else '')" "$ds_name" 2>/dev/null || true)
    if [ -n "$existing" ]; then
        echo "$existing" > "$cached"
        log "  Dataset 기존 재사용: $existing ($ds_name)"
        echo "$existing"
        return 0
    fi

    local payload; payload=$(cat "$DIFY_ASSETS_DIR/code-context-dataset.json" | python3 -c "import json,sys; d=json.load(sys.stdin); d.pop('_comment',None); print(json.dumps(d))")
    local resp; resp=$(curl -sS -X POST "$DIFY_URL/console/api/datasets" \
        "${_auth[@]}" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null)
    local id; id=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || true)
    if [ -n "$id" ]; then
        echo "$id" > "$cached"
        log "  Dataset 생성: $id"
        echo "$id"
    else
        warn "  Dataset 생성 실패 — 응답: $resp"
        return 1
    fi
}

# ─ Dify: Dataset API key 발급 (workspace-level, Dify 1.13+) ───────────────
# Dify 1.13 은 Dataset API key 를 workspace 단위로 관리하며 dataset_id 가 경로에
# 포함되지 않는다. /console/api/datasets/api-keys POST 로 생성, GET 으로 조회.
# 기존 key 가 있으면 재사용 (workspace 당 max 10개 제한 존재).
dify_issue_dataset_api_key() {
    local _unused_dataset_id="${1:-}"   # 시그니처 호환 유지 (이전 per-dataset 엔드포인트 흔적)
    local cached="$STATE_DIR/dataset_api_key"
    [ -f "$cached" ] && { cat "$cached"; return 0; }

    mapfile -t _auth < <(_dify_auth_args)

    # 기존 workspace-level key 재사용
    local existing; existing=$(curl -sS "$DIFY_URL/console/api/datasets/api-keys" \
        "${_auth[@]}" 2>/dev/null \
        | python3 -c "import json,sys; d=json.load(sys.stdin);
items=d.get('items',[]);
print(items[0]['token'] if items else '')" 2>/dev/null || true)
    if [ -n "$existing" ]; then
        echo "$existing" > "$cached"
        log "  Dataset API key 기존 재사용"
        echo "$existing"
        return 0
    fi

    # 신규 발급 (Dify 1.13: POST /console/api/datasets/api-keys — dataset_id 불필요)
    local resp; resp=$(curl -sS -X POST "$DIFY_URL/console/api/datasets/api-keys" \
        "${_auth[@]}" 2>/dev/null)
    local key; key=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null || true)
    if [ -n "$key" ]; then
        echo "$key" > "$cached"
        log "  Dataset API key 발급"
        echo "$key"
    else
        warn "  Dataset API key 발급 실패 — 응답: $resp"
        return 1
    fi
}

# ─ Dify: Sonar Analyzer Workflow import → app_id ──────────────────────────
# ─ Dify: 기존 workflow draft 의 knowledge-retrieval 노드에 dataset_id 주입 ─────
# provision 재실행 race: workflow_app_id 캐시는 있는데 과거 실패로 dataset_ids 가
# 비어 있는 상태 → GET draft → patch nodes → hash 포함 POST. import 함수와 분리해
# 안전망으로 동작.
dify_patch_workflow_dataset_id() {
    local app_id="$1" dataset_id="$2"
    [ -z "$app_id" ] || [ -z "$dataset_id" ] && return 0

    mapfile -t _auth < <(_dify_auth_args)
    local draft_file; draft_file=$(mktemp --suffix=.json)
    curl -sS "$DIFY_URL/console/api/apps/$app_id/workflows/draft" "${_auth[@]}" -o "$draft_file" 2>/dev/null
    [ ! -s "$draft_file" ] && { rm -f "$draft_file"; return 1; }

    local needs_patch; needs_patch=$(DRAFT_FILE="$draft_file" python3 <<'PY' 2>/dev/null
import json, os, sys
d = json.load(open(os.environ["DRAFT_FILE"]))
g = d.get("graph", {})
for n in g.get("nodes", []):
    dd = n.get("data", {})
    if dd.get("type") == "knowledge-retrieval" and not dd.get("dataset_ids"):
        print("yes"); sys.exit(0)
print("no")
PY
)
    if [ "$needs_patch" != "yes" ]; then
        rm -f "$draft_file"
        log "  Workflow dataset_ids 이미 주입됨 — skip"
        return 0
    fi

    log "  Workflow draft 에 dataset_id fallback 주입 ($dataset_id)"
    local payload_file; payload_file=$(mktemp --suffix=.json)
    DRAFT_FILE="$draft_file" DATASET_ID="$dataset_id" python3 <<'PY' > "$payload_file"
import json, os
d = json.load(open(os.environ["DRAFT_FILE"]))
g = d.get("graph", {})
for n in g.get("nodes", []):
    if n.get("data", {}).get("type") == "knowledge-retrieval":
        n["data"]["dataset_ids"] = [os.environ["DATASET_ID"]]
out = {
    "graph": g,
    "features": d.get("features", {}),
    "environment_variables": d.get("environment_variables", []),
    "conversation_variables": d.get("conversation_variables", []),
    "hash": d.get("hash") or d.get("unique_hash"),
}
print(json.dumps(out))
PY
    rm -f "$draft_file"
    local resp; resp=$(curl -sS -X POST "$DIFY_URL/console/api/apps/$app_id/workflows/draft" \
        "${_auth[@]}" -H "Content-Type: application/json" -d @"$payload_file" 2>/dev/null)
    rm -f "$payload_file"
    if echo "$resp" | grep -q '"result":"success"'; then
        log "  Workflow dataset_id fallback 주입 완료"
    else
        warn "  Workflow fallback 주입 실패 — 응답: $(echo "$resp" | head -c 200)"
        return 1
    fi
}

dify_import_workflow() {
    local dataset_id="${1:-}"  # Phase 1: knowledge-retrieval 노드의 dataset_ids 채우기
    local cached="$STATE_DIR/workflow_app_id"
    if [ -f "$cached" ]; then
        local existing_id; existing_id=$(cat "$cached")
        # 캐시 히트 — 기존 draft 의 dataset_ids 비었으면 fallback 패치
        [ -n "$dataset_id" ] && dify_patch_workflow_dataset_id "$existing_id" "$dataset_id" || true
        echo "$existing_id"
        return 0
    fi

    # Phase 1: YAML 의 `dataset_ids: []` 를 실제 dataset_id 로 치환하여 RAG 노드가
    # 현재 워크스페이스의 code-context-kb 를 조회하도록 보장.
    local tmp_yaml; tmp_yaml=$(mktemp --suffix=.yaml)
    cp "$DIFY_ASSETS_DIR/sonar-analyzer-workflow.yaml" "$tmp_yaml"
    if [ -n "$dataset_id" ]; then
        # sed 로 'dataset_ids: []' → 'dataset_ids: ["<id>"]' 치환.
        # yaml 의 inline list 문법 사용 (Dify 가 array of string 파싱).
        sed -i.bak "s|dataset_ids: \[\]|dataset_ids: ['$dataset_id']|g" "$tmp_yaml"
        rm -f "${tmp_yaml}.bak"
        log "  Workflow yaml 에 dataset_id 주입: $dataset_id"
    fi

    local payload; payload=$(python3 <<PY
import json
with open("$tmp_yaml", encoding="utf-8") as f:
    yc = f.read()
print(json.dumps({"mode": "yaml-content", "yaml_content": yc}))
PY
)
    mapfile -t _auth < <(_dify_auth_args)
    local resp; resp=$(curl -sS -X POST "$DIFY_URL/console/api/apps/imports" \
        "${_auth[@]}" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null)
    rm -f "$tmp_yaml"
    local id; id=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('app_id') or d.get('id') or '')" 2>/dev/null || true)
    if [ -n "$id" ]; then
        echo "$id" > "$cached"
        log "  Workflow import: $id"
        echo "$id"
    else
        warn "  Workflow import 실패 — 응답: $resp"
        return 1
    fi
}

# ─ Dify: Workflow publish (draft → published) ────────────────────────────
# import 만 하면 workflow 는 draft 상태. `/v1/workflows/run` 엔드포인트로 호출
# 하려면 publish 필수 — 그렇지 않으면 "Workflow not published" 400 반환.
# (발견 경위: 첫 실 파이프라인 실행에서 03 Job 의 Dify 호출이 400 으로 실패)
dify_publish_workflow() {
    local app_id="$1"
    [ -z "$app_id" ] && return 0
    local state="$STATE_DIR/workflow_published.ok"
    [ -f "$state" ] && return 0

    mapfile -t _auth < <(_dify_auth_args)
    local resp; resp=$(curl -sS -X POST "$DIFY_URL/console/api/apps/$app_id/workflows/publish" \
        "${_auth[@]}" -H "Content-Type: application/json" -d '{}' 2>/dev/null)
    local ok; ok=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('result','') == 'success')" 2>/dev/null || echo "False")
    if [ "$ok" = "True" ]; then
        touch "$state"
        log "  Workflow publish 완료 ($app_id)"
    else
        warn "  Workflow publish 실패 — 응답: $resp"
        return 1
    fi
}

# ─ Dify: App API key 발급 ──────────────────────────────────────────────────
dify_issue_app_api_key() {
    local app_id="$1"
    local cached="$STATE_DIR/workflow_api_key"
    [ -f "$cached" ] && { cat "$cached"; return 0; }

    mapfile -t _auth < <(_dify_auth_args)
    local resp; resp=$(curl -sS -X POST "$DIFY_URL/console/api/apps/$app_id/api-keys" \
        "${_auth[@]}" 2>/dev/null)
    local key; key=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null || true)
    if [ -n "$key" ]; then
        echo "$key" > "$cached"
        log "  App API key 발급"
        echo "$key"
    else
        warn "  App API key 발급 실패 — 응답: $resp"
        return 1
    fi
}

# ─ GitLab: 대기 + root PAT 발급 ────────────────────────────────────────────
gitlab_wait_ready() {
    log "GitLab 헬스 대기 (최대 15분)..."
    local w=0 limit=900
    until curl -sf -o /dev/null "$GITLAB_URL/users/sign_in"; do
        sleep 10; w=$((w + 10))
        if [ $w -ge $limit ]; then
            err "GitLab 15분 내 준비되지 않음. 컨테이너 로그 확인: docker logs ttc-gitlab"
            return 1
        fi
        [ $((w % 60)) -eq 0 ] && log "  GitLab 대기 중... (${w}s)"
    done
    log "GitLab ready (${w}s)"
}

gitlab_issue_root_pat() {
    local cached="$STATE_DIR/gitlab_root_pat"
    [ -f "$cached" ] && { cat "$cached"; return 0; }

    # 1. oauth password grant 로 access_token 획득
    local oauth_resp; oauth_resp=$(curl -sS -X POST "$GITLAB_URL/oauth/token" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "import json; print(json.dumps({'grant_type':'password','username':'root','password':'$GITLAB_ROOT_PASSWORD'}))")" \
        2>/dev/null)
    local access_token; access_token=$(echo "$oauth_resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || true)
    if [ -z "$access_token" ]; then
        warn "GitLab oauth 실패 — 응답: $oauth_resp"
        return 1
    fi

    # 2. root (id=1) 에 대해 personal_access_token 발급 (admin API)
    # GitLab 17.x 는 PAT 만료일을 최대 365일 이내로 강제 (기본 정책). 오늘 + 364일
    # 로 계산해 정책 상한에 걸리지 않도록 한다.
    local expires_at; expires_at=$(date -u -d '+364 days' '+%Y-%m-%d' 2>/dev/null || date -u -v+364d '+%Y-%m-%d')
    local pat_resp; pat_resp=$(curl -sS -X POST "$GITLAB_URL/api/v4/users/1/personal_access_tokens" \
        -H "Authorization: Bearer $access_token" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "import json,sys; print(json.dumps({'name':'ttc-auto','scopes':['api','read_repository','write_repository'],'expires_at':sys.argv[1]}))" "$expires_at")" \
        2>/dev/null)
    local pat; pat=$(echo "$pat_resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null || true)
    if [ -n "$pat" ]; then
        echo "$pat" > "$cached"
        log "GitLab root PAT 발급"
        echo "$pat"
    else
        warn "GitLab PAT 발급 실패 — 응답: $pat_resp"
        return 1
    fi
}

# ─ SonarQube: ready 대기 ────────────────────────────────────────────────────
# 첫 기동은 ES 인덱스 bootstrap 때문에 3-8분 소요. 여유롭게 10분 timeout.
sonar_wait_ready() {
    log "SonarQube 헬스 대기 (최대 10분)..."
    local w=0 limit=600 status=""
    while [ $w -lt $limit ]; do
        status=$(curl -sS --max-time 5 "$SONAR_URL/api/system/status" 2>/dev/null \
            | python3 -c "import json,sys;
try: print(json.load(sys.stdin).get('status',''))
except Exception: print('')" 2>/dev/null || true)
        [ "$status" = "UP" ] && { log "SonarQube ready (${w}s)"; return 0; }
        sleep 5; w=$((w + 5))
        [ $((w % 30)) -eq 0 ] && log "  SonarQube 대기 중... (${w}s, status=${status:-init})"
    done
    err "SonarQube 10분 내 준비되지 않음. 로그: /data/sonarqube/logs/{sonar,es}.log"
    return 1
}

# ─ SonarQube: 초기 비밀번호 변경 (멱등) ───────────────────────────────────
# 주의: /api/authentication/validate 는 잘못된 비번에도 HTTP 200 + {"valid":false}
#       를 반환하므로 HTTP 코드로 판정 불가. admin 권한 endpoint /api/users/search
#       로 판정한다 (잘못된 비번이면 401).
sonar_change_initial_password() {
    # 이미 새 비번으로 변경된 상태인지 검증
    if curl -sf -u "admin:$SONAR_ADMIN_NEW_PASSWORD" -o /dev/null \
        "$SONAR_URL/api/users/search"; then
        log "  SonarQube admin 비번 이미 변경됨 — 건너뜀"
        return 0
    fi
    # 기본 비번(admin/admin)으로 변경 시도
    local code
    code=$(curl -sS -u "admin:admin" -X POST "$SONAR_URL/api/users/change_password" \
        --data-urlencode "login=admin" \
        --data-urlencode "previousPassword=admin" \
        --data-urlencode "password=$SONAR_ADMIN_NEW_PASSWORD" \
        -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")
    if [ "$code" = "204" ] || [ "$code" = "200" ]; then
        # 변경 직후 재검증 (새 비번으로 admin endpoint 호출)
        if curl -sf -u "admin:$SONAR_ADMIN_NEW_PASSWORD" -o /dev/null \
            "$SONAR_URL/api/users/search"; then
            log "  SonarQube admin 비밀번호 변경 완료"
            return 0
        fi
        warn "  SonarQube 비번 변경 API 는 성공(status=$code) 이나 재검증 실패"
        return 1
    fi
    warn "  SonarQube 비번 변경 실패 (status=$code)"
    return 1
}

# ─ SonarQube: 토큰 발급 (멱등: 동일 이름 revoke 후 재발급) ──────────────────
sonar_generate_token() {
    local cached="$STATE_DIR/sonar_token"
    [ -f "$cached" ] && { cat "$cached"; return 0; }

    # 기존 동일 이름 토큰 revoke (첫 실행 시엔 404/skip)
    curl -sS -u "admin:$SONAR_ADMIN_NEW_PASSWORD" -X POST \
        "$SONAR_URL/api/user_tokens/revoke" \
        --data-urlencode "login=admin" \
        --data-urlencode "name=$SONAR_TOKEN_NAME" \
        -o /dev/null 2>/dev/null || true

    local resp; resp=$(curl -sS -u "admin:$SONAR_ADMIN_NEW_PASSWORD" -X POST \
        "$SONAR_URL/api/user_tokens/generate" \
        --data-urlencode "name=$SONAR_TOKEN_NAME" 2>/dev/null)
    local token; token=$(echo "$resp" | python3 -c "import json,sys;
try: print(json.load(sys.stdin).get('token',''))
except Exception: print('')" 2>/dev/null || true)
    if [ -n "$token" ]; then
        echo "$token" > "$cached"
        log "  SonarQube 토큰 발급: $SONAR_TOKEN_NAME"
        echo "$token"
    else
        warn "  SonarQube 토큰 발급 실패 — 응답: $resp"
        return 1
    fi
}

# ─ Jenkinsfile 사본에 credentials('gitlab-pat') 참조 삽입 ──────────────────
patch_jenkinsfile_gitlab_credentials() {
    log "Jenkinsfile 사본에 credentials('gitlab-pat') 참조 삽입"
    # Dockerfile 빌드 시점에 이미 `GITLAB_PAT = ''` / `GITLAB_TOKEN=""` 로 치환됨.
    # 런타임에 이를 credentials('gitlab-pat') 참조로 재치환.
    cd "$JENKINSFILE_DIR"
    sed -i "s|GITLAB_PAT = ''|GITLAB_PAT = credentials('gitlab-pat')|g" *.jenkinsPipeline
    sed -i 's|GITLAB_TOKEN=""|GITLAB_TOKEN="${GITLAB_PAT}"|g' *.jenkinsPipeline
    log "  치환 완료"
}

# ═════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════
log "=========================================="
log "TTC 4-Pipeline All-in-One 자동 프로비저닝 시작"
log "=========================================="

# A. Jenkins 대기
log "Jenkins 응답 확인..."
_w=0
until curl -sf -o /dev/null -u "$JENKINS_USER:$JENKINS_PASSWORD" "$JENKINS_URL/api/json"; do
    sleep 3; _w=$((_w + 3))
    [ $_w -ge 180 ] && { err "Jenkins 3분 내 응답 없음"; exit 1; }
done
log "  Jenkins OK (${_w}s)"

# B+C+D+E+F. Dify 전체 자동화
log "Dify 자동화 시작..."
if dify_setup_admin; then
    DIFY_TOKEN=$(dify_login)
    if [ -n "$DIFY_TOKEN" ]; then
        log "  Dify 로그인 성공 (cookie jar: $DIFY_COOKIE_JAR)"
        # Phase 1: Dify 1.13 plugin-model 체계 — 먼저 ollama 플러그인 설치
        dify_install_ollama_plugin || true
        dify_register_ollama_provider || true
        # high_quality Dataset 생성 전 반드시 embedding provider 등록 + 기본 모델 지정
        dify_register_ollama_embedding || true
        dify_set_default_models || true
        DATASET_ID=$(dify_create_dataset || echo "")
        [ -n "$DATASET_ID" ] && KNOWLEDGE_KEY=$(dify_issue_dataset_api_key "$DATASET_ID" || echo "")
        # Phase 1: workflow import 시 dataset_id 를 yaml 에 주입해 knowledge-retrieval 노드 활성화
        WORKFLOW_APP_ID=$(dify_import_workflow "$DATASET_ID" || echo "")
        if [ -n "$WORKFLOW_APP_ID" ]; then
            WORKFLOW_KEY=$(dify_issue_app_api_key "$WORKFLOW_APP_ID" || echo "")
            # Phase 1 런타임 버그 fix: import 한 draft 를 publish 해야 /v1/workflows/run 호출 가능
            dify_publish_workflow "$WORKFLOW_APP_ID" || true
        fi
    else
        warn "Dify 로그인 실패 — 수동 확인 필요"
    fi
else
    warn "Dify setup 실패 — 후속 단계 건너뜀"
fi

# G. GitLab
GITLAB_PAT=""
if gitlab_wait_ready; then
    GITLAB_PAT=$(gitlab_issue_root_pat || echo "")
fi

# J. SonarQube 초기 비번 변경 + 토큰 발급
SONAR_TOKEN=""
if sonar_wait_ready; then
    if sonar_change_initial_password; then
        SONAR_TOKEN=$(sonar_generate_token || echo "")
    fi
fi

# H. Jenkins Credentials 주입
log "Jenkins Credentials 주입..."
[ -n "${DATASET_ID:-}" ]     && jenkins_upsert_string_credential "dify-dataset-id"   "$DATASET_ID"     "Dify Code Context Dataset ID"
[ -n "${KNOWLEDGE_KEY:-}" ]  && jenkins_upsert_string_credential "dify-knowledge-key" "$KNOWLEDGE_KEY" "Dify Knowledge API Key"
[ -n "${WORKFLOW_KEY:-}" ]   && jenkins_upsert_string_credential "dify-workflow-key" "$WORKFLOW_KEY"  "Dify Sonar Analyzer Workflow API Key"
[ -n "$GITLAB_PAT" ]         && jenkins_upsert_string_credential "gitlab-pat"        "$GITLAB_PAT"    "GitLab root PAT (auto-issued)"
[ -n "$SONAR_TOKEN" ]        && jenkins_upsert_string_credential "sonarqube-token"   "$SONAR_TOKEN"   "SonarQube User Token (auto-issued)"

# H-1. Jenkins SonarQube server/tool 등록 (sonar plugin + sonarqube-token credential 의존)
# 02 Jenkinsfile 의 `withSonarQubeEnv('dscore-sonar')` + `tool 'SonarScanner-CLI'` 바인딩.
[ -n "$SONAR_TOKEN" ] && jenkins_configure_sonar_integration "$SONAR_URL" || true

# I. Jenkinsfile credentials 참조 치환 (Credentials 주입 후)
patch_jenkinsfile_gitlab_credentials

# A (재실행). Jenkins 4개 Job 등록
log "Jenkins 4개 Pipeline Job 등록..."
jenkins_create_pipeline_job "01-코드-사전학습"                   "$JENKINSFILE_DIR/01 코드 사전학습.jenkinsPipeline" || true
jenkins_create_pipeline_job "02-코드-정적분석"                   "$JENKINSFILE_DIR/02 코드 정적분석.jenkinsPipeline" || true
jenkins_create_pipeline_job "03-정적분석-결과분석-이슈등록"      "$JENKINSFILE_DIR/03 코드 정적분석 결과분석 및 이슈등록.jenkinsPipeline" || true
jenkins_create_pipeline_job "04-AI평가"                          "$JENKINSFILE_DIR/04 AI평가.jenkinsPipeline" || true

log "=========================================="
log "자동 프로비저닝 완료."
log "  Jenkins    : $JENKINS_URL ($JENKINS_USER / $JENKINS_PASSWORD)"
log "  Dify       : $DIFY_URL ($DIFY_ADMIN_EMAIL / $DIFY_ADMIN_PASSWORD)"
log "  SonarQube  : http://localhost:29000 (admin / $SONAR_ADMIN_NEW_PASSWORD)"
log "  GitLab     : http://localhost:28090 (root / $GITLAB_ROOT_PASSWORD)"
log "  Ollama     : $OLLAMA_BASE_URL (호스트)"
if [ -z "$SONAR_TOKEN" ]; then
    log ""
    log "수동 확인: SonarQube 토큰 자동 발급 실패 — Jenkins 'sonarqube-token' Credential 수동 등록 필요"
fi
log "=========================================="
