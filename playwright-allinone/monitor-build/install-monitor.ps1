# install-monitor.ps1 — 모니터링 PC 자동 프로비저닝 (Windows).
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

$InstallRoot = if ($env:MONITOR_HOME) { $env:MONITOR_HOME } else { "$env:USERPROFILE\.dscore.ttc.monitor" }
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$OsTag = "win64"

Write-Host "[install-monitor] OS=Windows  OS_TAG=$OsTag  INSTALL_ROOT=$InstallRoot"

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
    Write-Host "[install-monitor] 기존 venv 재사용"
}
$VenvPip = Join-Path $InstallRoot "venv\Scripts\pip.exe"

# 3. wheels.
$WheelsDir = Join-Path $ScriptDir "wheels\$OsTag"
if (Test-Path $WheelsDir) {
    & $VenvPip install --no-index --find-links $WheelsDir --upgrade pip wheel 2>$null
    & $VenvPip install --no-index --find-links $WheelsDir `
        fastapi uvicorn pydantic playwright python-multipart
    Write-Host "[install-monitor] wheels 설치 완료"
} else {
    Write-Warning "wheels\$OsTag 없음 — 온라인 fallback"
    & $VenvPip install fastapi uvicorn pydantic playwright python-multipart
}

# 4. Chromium.
$ChromiumSrc = Join-Path $ScriptDir "chromium\$OsTag"
if (Test-Path $ChromiumSrc) {
    Copy-Item -Path "$ChromiumSrc\*" -Destination (Join-Path $InstallRoot "chromium") -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[install-monitor] Chromium 배치 완료"
}

# 5. 프로젝트 모듈.
$SitePkg = & $VenvPy -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])'
foreach ($mod in @("replay_service", "monitor", "zero_touch_qa", "recording_service")) {
    $src = Join-Path $ScriptDir "src\$mod"
    if (Test-Path $src) {
        $dst = Join-Path $SitePkg $mod
        if (Test-Path $dst) { Remove-Item -Path $dst -Recurse -Force }
        Copy-Item -Path $src -Destination $dst -Recurse -Force
    }
}
Write-Host "[install-monitor] 프로젝트 모듈 site-packages 에 배치"

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
    $envInfo = "PLAYWRIGHT_BROWSERS_PATH=$InstallRoot\chromium;AUTH_PROFILES_DIR=$InstallRoot\auth-profiles;MONITOR_HOME=$InstallRoot"
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "[install-monitor] 작업 스케줄러 등록 (At log on): $TaskName"
}

# 7. 30분 스케줄러 안내.
if ($RegisterTask) {
    Write-Host @"
[install-monitor] 30분 주기 스케줄러 등록 안내 - bundle 별로 한 번 만들어 주세요:
schtasks /create /sc minute /mo 30 /tn "Monitor Replay [bundle]" /tr "$InstallRoot\venv\Scripts\python.exe -m monitor replay $InstallRoot\scenarios\[bundle.zip] --out $InstallRoot\runs\auto"
"@
}

Write-Host @"

[install-monitor] 셋업 완료.
  Replay UI:   http://127.0.0.1:18094  (RegisterStartup 미사용 시 수동 기동)
  수동 기동:   $InstallRoot\venv\Scripts\python.exe -m uvicorn replay_service.server:app --host 127.0.0.1 --port 18094
  CLI replay:  $InstallRoot\venv\Scripts\python.exe -m monitor replay [bundle.zip] --out [dir]
"@
