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
#       [-Python <path>] [-NoRegisterStartup] [-NoStart]

[CmdletBinding()]
param(
    [switch]$RegisterStartup,
    [switch]$RegisterTask,
    [switch]$NoRegisterStartup,
    [switch]$NoStart,
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
function Get-PythonMinor {
    param([Parameter(Mandatory)][string]$PythonExe)
    $ver = & $PythonExe -c "import sys; print('{}.{}'.format(sys.version_info[0], sys.version_info[1]))" 2>&1
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($ver | Select-Object -First 1).ToString().Trim()
}
function Test-Python311 {
    param([Parameter(Mandatory)][string]$PythonExe)
    return ((Get-PythonMinor $PythonExe) -eq "3.11")
}
function Resolve-Python311 {
    param(
        [Parameter(Mandatory)][string]$RequestedPython,
        [Parameter(Mandatory)][string]$InstallRoot,
        [Parameter(Mandatory)][string]$ScriptDir
    )

    $bundledPython = Join-Path $InstallRoot "python311\python.exe"
    $candidates = @()
    if ($RequestedPython -and $RequestedPython -ne "python") {
        $candidates += $RequestedPython
    }
    $candidates += $bundledPython
    $candidates += "py -3.11"
    $candidates += "python"

    foreach ($candidate in $candidates) {
        try {
            if ($candidate -eq "py -3.11") {
                $resolved = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null)
                if ($LASTEXITCODE -eq 0 -and $resolved -and (Test-Python311 $resolved.Trim())) {
                    return $resolved.Trim()
                }
            } elseif (Test-Path $candidate) {
                if (Test-Python311 $candidate) { return $candidate }
            } elseif ($candidate -eq "python") {
                if (Test-Python311 $candidate) { return $candidate }
            }
        } catch {
            # Try the next candidate.
        }
    }

    if ($RequestedPython -and $RequestedPython -ne "python") {
        throw "지정한 Python 이 3.11.x 가 아닙니다: $RequestedPython"
    }

    $installerDir = Join-Path $ScriptDir "python\win64"
    $installer = Get-ChildItem -Path $installerDir -Filter "python-3.11.*-amd64.exe" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        Select-Object -First 1
    if (-not $installer) {
        throw "Python 3.11.x 를 찾지 못했고, 동봉 Python installer 도 없습니다: $installerDir"
    }

    $targetDir = Split-Path -Parent $bundledPython
    if (-not (Test-Path $targetDir)) { New-Item -ItemType Directory -Path $targetDir -Force | Out-Null }
    Write-Host "[install-monitor] Python 3.11 미탐지 — 동봉 installer 로 로컬 설치: $targetDir"
    $args = @(
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=0",
        "Include_launcher=0",
        "Include_pip=1",
        "Include_tcltk=0",
        "Include_test=0",
        "Shortcuts=0",
        "TargetDir=$targetDir"
    )
    $proc = Start-Process -FilePath $installer.FullName -ArgumentList $args -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "동봉 Python 설치 실패 (exit $($proc.ExitCode))"
    }
    if (-not (Test-Python311 $bundledPython)) {
        throw "동봉 Python 설치 후 검증 실패: $bundledPython"
    }
    return $bundledPython
}
function Test-ReplayUI {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:18094/" -TimeoutSec 2
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
    } catch {
        return $false
    }
}
function Test-ChromiumPayload {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    $chrome = Get-ChildItem -Path $Path -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.FullName -like "*\chrome-win64\chrome.exe" -or
            $_.FullName -like "*\chrome-win\chrome.exe"
        } |
        Select-Object -First 1
    return [bool]$chrome
}

$InstallRoot = if ($env:MONITOR_HOME) { $env:MONITOR_HOME } else { "$env:USERPROFILE\.dscore.ttc.monitor" }
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$OsTag = "win64"
if (-not (Test-Path $InstallRoot)) { New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null }

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

# Python 검증/부트스트랩. monitor-runtime wheels are built with
# --python-version 3.11, so the interpreter must be Python 3.11.x.
try {
    $Python = Resolve-Python311 -RequestedPython $Python -InstallRoot $InstallRoot -ScriptDir $ScriptDir
    $PyVer = Get-PythonMinor $Python
    Write-Host "[install-monitor] Python $PyVer OK"
} catch {
    Write-Error "Python 준비 실패: $_  (-Python <python.exe path> 옵션으로 명시 가능)"
    exit 1
}

# 1. 디렉토리.
foreach ($d in @("venv", "chromium", "auth-profiles", "scenarios", "runs")) {
    $p = Join-Path $InstallRoot $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

# 2. venv.
$VenvPy = Join-Path $InstallRoot "venv\Scripts\python.exe"
if (Test-Path $VenvPy) {
    $VenvVer = & $VenvPy -c "import sys; print('{}.{}'.format(sys.version_info[0], sys.version_info[1]))" 2>&1
    if ($LASTEXITCODE -ne 0 -or $VenvVer -ne "3.11") {
        Write-Host "[install-monitor] 기존 venv Python $VenvVer 는 cp311 wheels 와 불일치 — venv 재생성"
        Remove-Item -Path (Join-Path $InstallRoot "venv") -Recurse -Force
    }
}
if (-not (Test-Path $VenvPy)) {
    & $Python -m venv (Join-Path $InstallRoot "venv")
    Write-Host "[install-monitor] venv 생성"
} else {
    Write-Host "[install-monitor] 기존 venv 재사용 (SKIP)"
}

# 3. Python 패키지 설치 (이미 설치된 건 pip 가 자동 SKIP).
$Packages = @("fastapi", "uvicorn", "pydantic", "playwright", "python-multipart", "portalocker", "requests", "pyotp", "pillow", "pywin32")
$WheelsDir = Join-Path $ScriptDir "wheels\$OsTag"
if (Test-Path $WheelsDir) {
    Write-Host "[install-monitor] 오프라인 wheels 사용: $WheelsDir"
    # pip / wheel 자체 업그레이드는 best-effort — wheels 디렉토리에 wheel 패키지가
    # 없는 빌드 (pip download 의 기본 REQS 에서 빠진 경우) 에서도 본 설치는 진행돼야
    # 함. Mac/Linux install-monitor.sh 의 `|| echo skip` 대칭 (2026-05-11).
    try {
        Invoke-Pip $VenvPy install --no-index --find-links $WheelsDir --upgrade pip wheel
    } catch {
        Write-Host "[install-monitor] pip/wheel 업그레이드 skip — $($_.Exception.Message)"
    }
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
if ((Test-Path $ChromiumSrc) -and (Test-ChromiumPayload $ChromiumSrc)) {
    Copy-Item -Path "$ChromiumSrc\*" -Destination $ChromiumDst -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[install-monitor] Chromium 오프라인 배치 완료"
} else {
    if (Test-Path $ChromiumSrc) {
        Write-Host "[install-monitor] 동봉 Chromium 이 Windows 형식이 아님 — Windows 에서 Chromium 설치 진행"
    } else {
        Write-Host "[install-monitor] 동봉 Chromium 없음 — Windows 에서 Chromium 설치 진행"
    }
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

# 6. Replay UI launcher — startup task 와 즉시 실행이 같은 진입점을 사용한다.
$LauncherPs1 = Join-Path $InstallRoot "start-replay-ui.ps1"
@"
`$ErrorActionPreference = "Continue"
`$env:PLAYWRIGHT_BROWSERS_PATH = "$ChromiumDst"
`$env:AUTH_PROFILES_DIR = "$InstallRoot\auth-profiles"
`$env:MONITOR_HOME = "$InstallRoot"
Set-Location "$InstallRoot"
& "$VenvPy" -m uvicorn replay_service.server:app --host 127.0.0.1 --port 18094 1>> "$InstallRoot\replay-ui.stdout.log" 2>> "$InstallRoot\replay-ui.stderr.log"
"@ | Set-Content -Path $LauncherPs1 -Encoding UTF8
Write-Host "[install-monitor] Replay UI launcher 생성: $LauncherPs1"

# 사용자는 PowerShell 실행 정책을 신경 쓰지 않고 이 파일만 더블클릭하면 된다.
$OpenPs1 = Join-Path $InstallRoot "open-replay-ui.ps1"
@"
`$ErrorActionPreference = "Continue"
`$root = "$InstallRoot"
`$url = "http://127.0.0.1:18094"
function Test-ReplayUI {
    try {
        `$resp = Invoke-WebRequest -UseBasicParsing -Uri `$url -TimeoutSec 2
        return (`$resp.StatusCode -ge 200 -and `$resp.StatusCode -lt 500)
    } catch {
        return `$false
    }
}
if (-not (Test-ReplayUI)) {
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path `$root "start-replay-ui.ps1")) `
        -WorkingDirectory `$root `
        -WindowStyle Hidden
    foreach (`$i in 1..10) {
        Start-Sleep -Seconds 1
        if (Test-ReplayUI) { break }
    }
}
Start-Process `$url
"@ | Set-Content -Path $OpenPs1 -Encoding UTF8

$OpenCmd = Join-Path $InstallRoot "open-replay-ui.cmd"
@"
@echo off
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%open-replay-ui.ps1"
"@ | Set-Content -Path $OpenCmd -Encoding ASCII
Write-Host "[install-monitor] Replay UI 더블클릭 실행 파일 생성: $OpenCmd"

try {
    $DesktopDir = [Environment]::GetFolderPath("Desktop")
    if ($DesktopDir) {
        $ShortcutPath = Join-Path $DesktopDir "DSCORE Replay UI.lnk"
        $Wsh = New-Object -ComObject WScript.Shell
        $Shortcut = $Wsh.CreateShortcut($ShortcutPath)
        $Shortcut.TargetPath = $OpenCmd
        $Shortcut.WorkingDirectory = $InstallRoot
        $Shortcut.IconLocation = "shell32.dll,220"
        $Shortcut.Save()
        Write-Host "[install-monitor] 바탕화면 바로가기 생성: $ShortcutPath"
    }
} catch {
    Write-Warning "바탕화면 바로가기 생성 실패: $($_.Exception.Message)"
}

# 7. startup task (기본 등록, 사용자 권한, UAC 승격 없이).
$ShouldRegisterStartup = (-not $NoRegisterStartup) -or $RegisterStartup
if ($ShouldRegisterStartup) {
    $TaskName = "DSCORE Replay UI"
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$LauncherPs1`"" `
        -WorkingDirectory $InstallRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "[install-monitor] 작업 스케줄러 등록 (At log on): $TaskName"
}

# 8. Replay UI 즉시 실행.
if (-not $NoStart) {
    if (Test-ReplayUI) {
        Write-Host "[install-monitor] Replay UI 이미 실행 중: http://127.0.0.1:18094"
    } else {
        Start-Process -FilePath "powershell.exe" `
            -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $LauncherPs1) `
            -WorkingDirectory $InstallRoot `
            -WindowStyle Hidden
        Write-Host "[install-monitor] Replay UI 시작 중: http://127.0.0.1:18094"
        Start-Sleep -Seconds 3
        if (Test-ReplayUI) {
            Write-Host "[install-monitor] Replay UI 실행 확인 OK"
        } else {
            Write-Warning "Replay UI 응답 대기 중입니다. 잠시 후 http://127.0.0.1:18094 를 열어 보세요. 실패 시 $InstallRoot\replay-ui.stderr.log 확인"
        }
    }
}

# 9. 30분 스케줄러 안내 (D17 — 단일 .py 흐름).
if ($RegisterTask) {
    Write-Host @"
[install-monitor] 30분 주기 스케줄러 등록 안내 - 시나리오 .py 별로 한 번씩 만들어 주세요:
schtasks /create /sc minute /mo 30 /tn "Monitor Replay [시나리오이름]" /tr "$InstallRoot\venv\Scripts\python.exe -m monitor replay-script $InstallRoot\scripts\[시나리오이름.py] --out $InstallRoot\runs\auto --profile [프로파일이름]"
"@
}

Write-Host @"

[install-monitor] 셋업 완료 (D17 — .py 일원화).
  Replay UI:   http://127.0.0.1:18094
  launcher:    $LauncherPs1
  double-click: $OpenCmd
  logs:        $InstallRoot\replay-ui.stdout.log
               $InstallRoot\replay-ui.stderr.log
  CLI 실행:
    & '$InstallRoot\venv\Scripts\python.exe' -m monitor replay-script [시나리오.py] --out [결과폴더] [--profile <alias>] [--verify-url <URL>]
  CLI 로그인 등록:
    & '$InstallRoot\venv\Scripts\python.exe' -m monitor profile seed [프로파일이름] --target [사이트 URL]
"@
