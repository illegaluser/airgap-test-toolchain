# [Mac->Windows 포팅] 신규 파일 (upstream 에 없음).
# windows-recording-setup.ps1 — Windows 호스트에 recording_service 직접 기동
#
# 배경: 원본 toolchain 은 mac-agent-setup.sh / wsl-agent-setup.sh 두 가지로 호스트
# 데몬을 띄우는데, WSL 경로는 Chromium 창이 WSLg 를 거치며 한글 IME 가 작동 안 함.
# 본 스크립트는 wsl-agent-setup.sh 의 §6.5 (recording_service 기동) 만 Windows 네이티브
# Python/Playwright 로 옮긴 등가물. mac-agent-setup.sh 의 같은 §6.5 와 동일한 일을
# PowerShell + Windows 경로 규약으로 수행.
#
# 목적: WSLg 의 한글 IME 한계를 회피하기 위해 Recording UI / Playwright Chromium 을
# Windows 네이티브로 띄움. Jenkins agent + Pipeline 은 그대로 wsl-agent-setup.sh
# 가 관리하는 WSL 측을 사용 (하이브리드 토폴로지).
#
# 토폴로지:
#   Windows 호스트:
#     ├── Docker Desktop → dscore.ttc.playwright (Jenkins, Dify)
#     ├── Python venv (이 스크립트가 만듦) → recording_service :18092
#     │                                    → Playwright Chromium (네이티브 창, 한글 IME OK)
#     └── WSL2 Ubuntu → Jenkins agent (agent.jar) — wsl-agent-setup.sh 관리
#
# 사용법:
#   PowerShell (관리자 권한 불필요) 에서:
#     cd C:\Users\KTDS\airgap-test-toolchain\playwright-allinone
#     .\windows-recording-setup.ps1
#
#   포트 변경: $env:RECORDING_PORT="18093"; .\windows-recording-setup.ps1
#   Python 강제 지정: $env:PY_LAUNCHER="3.11"; .\windows-recording-setup.ps1
#
# 재실행 idempotent — 이미 깔린 venv/패키지/chromium 은 스킵, 데몬만 재기동.

$ErrorActionPreference = "Stop"

# ── 0. 경로/설정 ─────────────────────────────────────────────────────────────
$RootDir       = "C:\Users\KTDS\airgap-test-toolchain\playwright-allinone"
$AgentDir      = "$env:USERPROFILE\.dscore.ttc.playwright-agent"
$VenvDir       = "$AgentDir\venv"
$RecordingsDir = "$AgentDir\recordings"
$LogFile       = "$AgentDir\recording-service.log"
$ErrLogFile    = "$AgentDir\recording-service.err.log"
$PidFile       = "$AgentDir\recording-service.pid"
$Port          = if ($env:RECORDING_PORT) { [int]$env:RECORDING_PORT } else { 18092 }
$PyLauncher    = if ($env:PY_LAUNCHER) { $env:PY_LAUNCHER } else { "3.12" }

function Log($msg) { Write-Host "[win-rec-setup] $msg" }

Log "AGENT_DIR=$AgentDir"
Log "VENV=$VenvDir"
Log "ROOT=$RootDir"
Log "PORT=$Port"

if (-not (Test-Path "$RootDir\recording_service\server.py")) {
    throw "ROOT_DIR 가 잘못됨 — $RootDir\recording_service\server.py 없음"
}

# ── 1. 디렉터리 준비 ────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $AgentDir, $RecordingsDir | Out-Null

# ── 2. 기존 데몬 정지 (Windows + WSL 양쪽) ──────────────────────────────────
Log "[2/7] 기존 데몬 정지"

# 2-A. Windows PID 파일 기반
if (Test-Path $PidFile) {
    $oldPid = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($oldPid -and ($oldPid -match '^\d+$')) {
        Stop-Process -Id ([int]$oldPid) -Force -ErrorAction SilentlyContinue
        Log "  Windows old PID=$oldPid 정지"
    }
    Remove-Item $PidFile -Force
}

# 2-B. 포트 점유 프로세스 정리 (Windows 측)
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $conn | ForEach-Object {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
        Log "  포트 $Port 점유 PID=$($_.OwningProcess) 정지"
    }
}

# 2-C. WSL 측 데몬 정지 — WSL2 의 localhost forwarding 으로 같은 포트가 잡혀있으면
# Windows 측에서 EADDRINUSE 가 안 떠도 forwarding 우선순위가 꼬일 수 있음
& wsl -- bash -c 'pkill -f "uvicorn.*recording_service" 2>/dev/null; rm -f $HOME/.dscore.ttc.playwright-agent/recording-service.pid 2>/dev/null; true' 2>$null
Log "  WSL 측 recording_service 정지 (있으면)"

Start-Sleep -Seconds 1

# ── 3. Python 확인 ──────────────────────────────────────────────────────────
Log "[3/7] Python $PyLauncher 확인"
$pyVersion = & py "-$PyLauncher" --version 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "py -$PyLauncher 실행 실패. Python $PyLauncher 가 설치돼있는지 확인 (`py --list`)"
}
Log "  $pyVersion"

# ── 4. venv 생성 + 패키지 설치 ──────────────────────────────────────────────
Log "[4/7] venv 준비"
$VenvPy = "$VenvDir\Scripts\python.exe"

if (-not (Test-Path $VenvPy)) {
    Log "  venv 생성: $VenvDir"
    & py "-$PyLauncher" -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "venv 생성 실패" }
} else {
    Log "  venv 이미 존재 — 스킵"
}

Log "  pip upgrade"
& $VenvPy -m pip install --upgrade pip --quiet --disable-pip-version-check

$Pkgs = @(
    "requests", "playwright", "pillow", "pymupdf",
    "pytest", "pytest-xdist", "pytest-playwright",
    "fastapi", "uvicorn", "httpx", "pyotp", "python-multipart"
)
Log "  pip install: $($Pkgs -join ', ')"
& $VenvPy -m pip install --quiet --disable-pip-version-check @Pkgs
if ($LASTEXITCODE -ne 0) { throw "pip install 실패" }

# ── 5. Playwright Chromium 설치 (Windows 네이티브 바이너리) ─────────────────
Log "[5/7] Playwright Chromium 설치 (이미 있으면 스킵)"
& $VenvPy -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "playwright install chromium 실패" }

# ── 6. 데몬 기동 ────────────────────────────────────────────────────────────
Log "[6/7] uvicorn 기동 (별도 콘솔 창)"

# 기존 로그 백업 (디버깅용 마지막 1회분 보존)
if (Test-Path $LogFile) { Move-Item $LogFile "$LogFile.prev" -Force }
if (Test-Path $ErrLogFile) { Move-Item $ErrLogFile "$ErrLogFile.prev" -Force }

# .bat wrapper 로 별도 콘솔 창에서 uvicorn 기동.
# 핵심: -WindowStyle Hidden 미사용 — 자식 chromium 이 부모의 interactive console /
# window station 을 상속받아야 sandbox / GPU init 가 정상 동작. 백그라운드 hidden
# 컨텍스트에선 fallback path 로 가서 BSOD / 즉시 종료 위험.
# .bat 안에서 환경변수 set + stdio 파일 redirect → 콘솔 창에 진행 표시 + 로그 파일도 보존.
$RunBat = "$AgentDir\run-recording-service.bat"
@"
@echo off
title recording_service :$Port
set PYTHONPATH=$RootDir
set RECORDING_HOST_ROOT=$RecordingsDir
set PYTHONUNBUFFERED=1
set PATH=$VenvDir\Scripts;%PATH%
echo ============================================================
echo recording_service — port $Port
echo 이 창이 닫히면 데몬 종료됨. 자식 chromium 도 이 창의 컨텍스트 상속.
echo ============================================================
echo.
"$VenvPy" -m uvicorn recording_service.server:app --host 127.0.0.1 --port $Port --workers 1 --log-level info
echo.
echo [run-recording-service] 종료. 아무 키나 눌러 창 닫기...
pause > nul
"@ | Set-Content -Path $RunBat -Encoding ASCII

$proc = Start-Process -FilePath $RunBat `
    -WorkingDirectory $RootDir `
    -PassThru

$proc.Id | Out-File -FilePath $PidFile -Encoding ascii -NoNewline
Log "  cmd.exe wrapper PID=$($proc.Id) → $PidFile"
Log "  (별도 콘솔 창이 떴을 거 — 그 창이 곧 recording_service. 닫으면 데몬 종료)"

# ── 7. 헬스체크 ─────────────────────────────────────────────────────────────
Log "[7/7] 헬스체크 (최대 15초)"
$healthOK = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2 -ErrorAction Stop
        if ($resp.codegen_available) {
            Log "  /healthz: ok=$($resp.ok) version=$($resp.version) codegen_available=$($resp.codegen_available)"
            Log "  ✅ codegen_available=true"
            $healthOK = $true
            break
        } else {
            Log "  ⚠ codegen_available=false — Chromium PATH 문제 의심. 계속 대기..."
        }
    } catch {
        # 아직 부팅 중일 수 있음 — 다음 루프
    }
}

if (-not $healthOK) {
    Log "❌ 헬스체크 실패 — 로그 마지막 30줄:"
    if (Test-Path $LogFile) { Get-Content $LogFile -Tail 30 }
    if (Test-Path $ErrLogFile) {
        Log "--- stderr ---"
        Get-Content $ErrLogFile -Tail 30
    }
    exit 1
}

Write-Host ""
Log "완료. http://localhost:$Port/ 접속 가능."
Log "녹화 클릭 시 Windows 네이티브 Chromium 창이 뜸 — 한글 IME 정상 동작."
Log "데몬 정지: 떠있는 'recording_service :$Port' 콘솔 창을 닫거나, 본 setup 을 다시 실행."
Log "로그는 콘솔 창에 실시간 표시 (파일 저장 안 함 — 영구 보존하려면 콘솔에서 복사)."
