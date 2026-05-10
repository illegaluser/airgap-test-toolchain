# install-monitor.ps1 — 모니터링 PC 자동 프로비저닝 (Windows 네이티브).
#
# 이 PC 에 Replay UI 가 동작할 모든 것을 한 번에 설치한다. 이미 설치된 항목은 SKIP.
#
# 두 가지 레이아웃을 자동 감지:
#   (A) monitor-runtime-<ts>.zip 을 푼 폴더 — wheels/win64/, chromium/win64/, src/<모듈>/
#   (B) 소스 트리 (playwright-allinone/monitor-build/) — wheels 없음, ../<모듈>/ 직접 사용 + 온라인 PyPI fallback
#
# 사용:
#   powershell -ExecutionPolicy Bypass -File install-monitor.ps1 `
#       [-RegisterStartup] [-RegisterTask] [-Python <path>]

[CmdletBinding()]
param(
    [switch]$RegisterStartup,
    [switch]$RegisterTask,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

# pip / playwright 의 stderr 는 경고/진행상황을 stderr 로 흘리는데
# PowerShell 5.1 의 strict 모드는 이를 native 에러로 잡아 정상 종료도 실패로 취급한다.
# 호출 직전 ErrorActionPreference 를 Continue 로 낮춰 두고, exit code 만 검사한다.
function Invoke-Pip {
    param([Parameter(Mandatory)][string]$PythonExe,
          [Parameter(ValueFromRemainingArguments)]$PipArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $PythonExe -m pip @PipArgs
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($code -ne 0) {
        throw "pip $PipArgs 실패 (exit $code)"
    }
}
function Invoke-Playwright {
    param([Parameter(Mandatory)][string]$PythonExe,
          [Parameter(ValueFromRemainingArguments)]$PwArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $PythonExe -m playwright @PwArgs
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($code -ne 0) {
        throw "playwright $PwArgs 실패 (exit $code)"
    }
}

$InstallRoot = if ($env:MONITOR_HOME) { $env:MONITOR_HOME } else { "$env:USERPROFILE\.dscore.ttc.monitor" }
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$OsTag = "win64"

# 소스 트리 레이아웃 감지: ScriptDir 가 .../playwright-allinone/monitor-build/ 인 경우
# 부모 디렉토리에서 모듈을 직접 가져온다 (zip 빌드 없이도 동작).
$ParentDir = Split-Path -Parent $ScriptDir
$IsSourceTreeLayout = (
    (Test-Path (Join-Path $ParentDir "replay_service")) -and
    (Test-Path (Join-Path $ParentDir "monitor")) -and
    (Test-Path (Join-Path $ParentDir "zero_touch_qa"))
)

Write-Host "[install-monitor] OS=Windows  OS_TAG=$OsTag  INSTALL_ROOT=$InstallRoot"
Write-Host "[install-monitor] 레이아웃: $(if ($IsSourceTreeLayout) { '소스 트리 (playwright-allinone 안)' } else { 'monitor-runtime zip' })"

# Python 검증 (3.11+).
try {
    $PyVer = & $Python -c "import sys; print('{}.{}'.format(sys.version_info[0], sys.version_info[1]))" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
    $major, $minor = $PyVer -split '\.'
    if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 11)) {
        throw "Python 3.11+ 필요 — 현재 $PyVer"
    }
    Write-Host "[install-monitor] Python $PyVer OK"
} catch {
    Write-Error "Python 확인 실패: $_  (-Python <python.exe path> 옵션으로 명시 가능)"
    exit 1
}

# 1. 디렉토리.
foreach ($d in @("venv", "chromium", "auth-profiles", "scenarios", "runs")) {
    $p = Join-Path $InstallRoot $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

# 2. venv.
$VenvPy = Join-Path $InstallRoot "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    & $Python -m venv (Join-Path $InstallRoot "venv")
    Write-Host "[install-monitor] venv 생성"
} else {
    Write-Host "[install-monitor] 기존 venv 재사용 (SKIP)"
}

# 3. Python 패키지 설치 (이미 설치된 건 pip 가 자동 SKIP).
$Packages = @("fastapi", "uvicorn", "pydantic", "playwright", "python-multipart", "portalocker")
$WheelsDir = Join-Path $ScriptDir "wheels\$OsTag"
if (Test-Path $WheelsDir) {
    Write-Host "[install-monitor] 오프라인 wheels 사용: $WheelsDir"
    Invoke-Pip $VenvPy install --no-index --find-links $WheelsDir --upgrade pip wheel
    Invoke-Pip $VenvPy install --no-index --find-links $WheelsDir @Packages
} else {
    Write-Host "[install-monitor] 온라인 PyPI 사용 (오프라인 wheels 없음)"
    Invoke-Pip $VenvPy install --upgrade pip
    Invoke-Pip $VenvPy install @Packages
}
Write-Host "[install-monitor] 패키지 설치 OK"

# 4. Chromium — 오프라인 카피 우선, 없으면 playwright install (이미 받은 게 있으면 자동 SKIP).
$ChromiumSrc = Join-Path $ScriptDir "chromium\$OsTag"
$ChromiumDst = Join-Path $InstallRoot "chromium"
if (Test-Path $ChromiumSrc) {
    Copy-Item -Path "$ChromiumSrc\*" -Destination $ChromiumDst -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[install-monitor] Chromium 오프라인 배치 완료"
} else {
    Write-Host "[install-monitor] Chromium 다운로드 (playwright install — 이미 받은 게 있으면 SKIP)"
    $env:PLAYWRIGHT_BROWSERS_PATH = $ChromiumDst
    Invoke-Playwright $VenvPy install chromium
}

# 5. 프로젝트 모듈 — 항상 최신본으로 덮어쓴다 (재실행 시 모듈 갱신 보장).
$SitePkg = (& $VenvPy -c 'import sysconfig; print(sysconfig.get_paths()[\"purelib\"])').Trim()
$ModulesRoot = if ($IsSourceTreeLayout) { $ParentDir } else { (Join-Path $ScriptDir "src") }
foreach ($mod in @("replay_service", "monitor", "zero_touch_qa", "recording_service")) {
    $src = Join-Path $ModulesRoot $mod
    if (Test-Path $src) {
        $dst = Join-Path $SitePkg $mod
        if (Test-Path $dst) { Remove-Item -Path $dst -Recurse -Force }
        Copy-Item -Path $src -Destination $dst -Recurse -Force
        Write-Host "[install-monitor] 모듈 배치: $mod"
    } else {
        Write-Warning "모듈 소스 없음: $src"
    }
}

# 6. startup task (사용자 권한, UAC 승격 없이).
if ($RegisterStartup) {
    $TaskName = "DSCORE Replay UI"
    $action = New-ScheduledTaskAction `
        -Execute (Join-Path $InstallRoot "venv\Scripts\python.exe") `
        -Argument "-m uvicorn replay_service.server:app --host 127.0.0.1 --port 18094" `
        -WorkingDirectory $InstallRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "[install-monitor] 작업 스케줄러 등록 (At log on): $TaskName"
}

# 7. 30분 스케줄러 안내 (D17 — 단일 .py 흐름).
if ($RegisterTask) {
    Write-Host @"
[install-monitor] 30분 주기 스케줄러 등록 안내 - 시나리오 .py 별로 한 번씩 만들어 주세요:
schtasks /create /sc minute /mo 30 /tn "Monitor Replay [시나리오이름]" /tr "$InstallRoot\venv\Scripts\python.exe -m monitor replay-script $InstallRoot\scripts\[시나리오이름.py] --out $InstallRoot\runs\auto --profile [프로파일이름]"
"@
}

Write-Host @"

[install-monitor] 셋업 완료 (D17 — .py 일원화).
  Replay UI:   http://127.0.0.1:18094  (RegisterStartup 미사용 시 수동 기동)
  단일 진입점 launcher (Recording UI 와 동등 패턴, 권장):
    bash <repo>/playwright-allinone/run-replay-ui.sh restart
  CLI 실행:
    & '$InstallRoot\venv\Scripts\python.exe' -m monitor replay-script [시나리오.py] --out [결과폴더] [--profile <alias>] [--verify-url <URL>]
  CLI 로그인 등록:
    & '$InstallRoot\venv\Scripts\python.exe' -m monitor profile seed [프로파일이름] --target [사이트 URL]
"@
