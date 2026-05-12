<#
.SYNOPSIS
  Replay UI 휴대용 자산을 `playwright-allinone/replay-ui/` 폴더 안에 채워넣는다.

.DESCRIPTION
  반출 모델 — replay-ui/ 폴더 자체가 portable. 본 스크립트는 그 폴더 안에
  embedded Python · 외부 의존 패키지 · Chromium · 공용 코드 · 실행파일을
  직접 채워넣는다. 채워넣은 후 사용자는 `zip -r replay-ui.zip replay-ui/`
  한 줄로 반출하거나, 본 스크립트의 -MakeZip 옵션으로 자동 zip 생성.

.PARAMETER PythonVersion
  embeddable Python 의 버전 (기본 3.11.9).

.PARAMETER ReuseCache
  cache 의 wheels/chromium/python-embed zip 을 재사용.

.PARAMETER MakeZip
  마지막에 replay-ui/ 폴더를 zip 으로 압축 (산출: replay-ui-portable-build/build-out/).

.NOTES
  사전 조건 — `.monitor-runtime-cache/monitor-runtime-build-cache/` 안에
    wheels/win64/*.whl, chromium/win64/ms-playwright/... 채워져 있어야 함.
  비어있으면 먼저 (Git Bash 또는 WSL2 에서):
    bash playwright-allinone/monitor-build/build-monitor-runtime.sh --target win64 --no-package
#>

param(
    [string]$PythonVersion = "3.11.9",
    [switch]$ReuseCache,
    [switch]$MakeZip
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# --- 경로 -------------------------------------------------------------
$BuildDir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$AllInOneDir  = Split-Path -Parent $BuildDir
$RepoRoot     = Split-Path -Parent $AllInOneDir
$ReplayUiDir  = Join-Path $AllInOneDir "replay-ui"
$SharedDir    = Join-Path $AllInOneDir "shared"

$CacheRoot       = Join-Path $RepoRoot ".monitor-runtime-cache"
$CacheBuildDir   = Join-Path $CacheRoot "monitor-runtime-build-cache"
$WheelsDir       = Join-Path $CacheBuildDir "wheels\win64"
$ChromiumSrcDir  = Join-Path $CacheBuildDir "chromium\win64"
$PythonEmbedZip  = Join-Path $CacheRoot "python-$PythonVersion-embed-amd64.zip"
$PythonEmbedUrl  = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"

$TemplatesDir = Join-Path $BuildDir "templates"
$BuildOutDir  = Join-Path $BuildDir "build-out"

Write-Host "[pack-windows] ReplayUiDir = $ReplayUiDir"
Write-Host "[pack-windows] CacheBuildDir = $CacheBuildDir"

# --- 사전 조건 --------------------------------------------------------
$WheelFiles = if (Test-Path $WheelsDir) { Get-ChildItem -Path $WheelsDir -Filter "*.whl" -ErrorAction SilentlyContinue } else { @() }
if (-not $WheelFiles -or $WheelFiles.Count -eq 0) {
    throw @"
wheels 캐시가 비어있습니다 — $WheelsDir
먼저 Git Bash 또는 WSL2 에서:
    bash playwright-allinone/monitor-build/build-monitor-runtime.sh --target win64 --no-package
"@
}
Write-Host "[pack-windows] wheels 캐시 OK ($($WheelFiles.Count) files)"

if (-not (Test-Path $ChromiumSrcDir)) {
    Write-Warning "chromium 캐시 디렉토리 없음 — $ChromiumSrcDir"
    Write-Warning "Chromium 미동봉으로 진행. 받는 사람의 첫 실행 시 별도 설치 필요."
}

# --- embeddable Python tarball 준비 -----------------------------------
if ((-not $ReuseCache) -or (-not (Test-Path $PythonEmbedZip))) {
    if (-not (Test-Path $CacheRoot)) { New-Item -ItemType Directory -Path $CacheRoot -Force | Out-Null }
    Write-Host "[pack-windows] Downloading embeddable Python -> $PythonEmbedZip"
    try {
        Invoke-WebRequest -Uri $PythonEmbedUrl -OutFile $PythonEmbedZip -UseBasicParsing
    } catch {
        throw "embeddable Python 다운로드 실패 ($PythonEmbedUrl): $($_.Exception.Message)"
    }
}
if (-not (Test-Path $PythonEmbedZip) -or (Get-Item $PythonEmbedZip).Length -lt 1MB) {
    throw "embeddable Python zip 이 비어있거나 손상됨: $PythonEmbedZip"
}

# --- replay-ui/ 폴더 안의 기존 자산 비우기 (재실행 안전) --------------
$ToClean = @("embedded-python", "site-packages", "chromium", "recording_shared", "zero_touch_qa")
foreach ($name in $ToClean) {
    $p = Join-Path $ReplayUiDir $name
    if (Test-Path $p) {
        Write-Host "[pack-windows] Clean previous -> $p"
        # in-use 파일(IDE indexer, 떠있는 embedded python 등) 만나도 throw 하지 말고
        # 가능한 만큼만 비우고 진행. 후속 단계가 -Force 로 덮어쓴다 — 자산이 일부
        # 잔존해도 최종 상태는 동일.
        Remove-Item -Path $p -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $p) {
            Write-Warning "[pack-windows] 일부 파일이 잠겨 있어 부분 잔존: $p (덮어쓰기 모드로 진행)"
        }
    }
}
foreach ($f in @("Launch-ReplayUI.bat", "Stop-ReplayUI.bat", "README.txt")) {
    $p = Join-Path $ReplayUiDir $f
    if (Test-Path $p) { Remove-Item -Path $p -Force }
}

# 1. embedded-python/ 풀기.
$EmbeddedPyDir = Join-Path $ReplayUiDir "embedded-python"
New-Item -ItemType Directory -Path $EmbeddedPyDir -Force | Out-Null
Write-Host "[pack-windows] Extracting embedded Python -> embedded-python/"
Expand-Archive -Path $PythonEmbedZip -DestinationPath $EmbeddedPyDir -Force

# 2. python311._pth — replay-ui/ 자체 + site-packages 검색 경로 + import site.
# pywin32 의 win32/win32\lib 도 명시 추가 — embed python 에서는 .pth 파일이
# 자동 처리되지 않아 pywin32.pth 의 효과가 살지 않음 (E2E 회귀 검출, plan G.3).
$PthFile = Join-Path $EmbeddedPyDir "python311._pth"
@"
python311.zip
.
..\
..\site-packages
..\site-packages\win32
..\site-packages\win32\lib
import site
"@ | Set-Content -Path $PthFile -Encoding ASCII

# 3. site-packages/ 채우기 (외부 의존성만 — 우리 코드는 5단계에서 폴더 루트로).
$SitePkgDir = Join-Path $ReplayUiDir "site-packages"
New-Item -ItemType Directory -Path $SitePkgDir -Force | Out-Null
$Packages = @(
    "fastapi", "uvicorn", "pydantic", "playwright", "python-multipart",
    "portalocker", "requests", "pyotp", "pillow", "pywin32", "colorama"
)
Write-Host "[pack-windows] pip install --target site-packages (offline wheels)"
# PowerShell 5.1 의 ErrorActionPreference=Stop 이 native command 의 stderr 한 줄을
# NativeCommandError 로 throw 함. pip 의 dependency-resolver 경고는 stderr 라 정상
# 진행도 throw 됨 — 호출 동안 잠시 Continue 로 낮추고 LASTEXITCODE 로만 판정.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
python -m pip install `
    --target $SitePkgDir `
    --no-index --find-links $WheelsDir `
    --platform win_amd64 `
    --python-version 3.11 `
    --only-binary :all: `
    --implementation cp --abi cp311 `
    @Packages
$pipExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($pipExit -ne 0) { throw "pip install --target 실패 (exit $pipExit)" }

# 3b. pywin32 portable fix — pywin32_postinstall.py 가 embed python 에서 자동
# 실행되지 않아 pywintypes311.dll/pythoncom311.dll 이 sys.path 의 DLL search
# 경로에 안 잡힘. pywin32_system32/ 의 DLL 을 embedded-python/ 으로 카피해
# 표준 postinstall 결과를 재현 (E2E 회귀 검출, plan G.3).
$Pywin32Sys32 = Join-Path $SitePkgDir "pywin32_system32"
if (Test-Path $Pywin32Sys32) {
    Get-ChildItem -Path $Pywin32Sys32 -Filter "*.dll" -ErrorAction SilentlyContinue |
        ForEach-Object {
            Copy-Item -Path $_.FullName -Destination $EmbeddedPyDir -Force
            Write-Host "[pack-windows] pywin32 DLL -> embedded-python/$($_.Name)"
        }
}

# 4. chromium/ 복사.
if (Test-Path $ChromiumSrcDir) {
    $ChromiumDstDir = Join-Path $ReplayUiDir "chromium"
    New-Item -ItemType Directory -Path $ChromiumDstDir -Force | Out-Null
    Write-Host "[pack-windows] Copying Chromium -> chromium/"
    Copy-Item -Path (Join-Path $ChromiumSrcDir "*") -Destination $ChromiumDstDir -Recurse -Force
}

# 5. 공용 코드 패키지 카피 (shared/ → replay-ui/ 루트로).
foreach ($pkg in @("recording_shared", "zero_touch_qa")) {
    $src = Join-Path $SharedDir $pkg
    if (-not (Test-Path $src)) { throw "공용 패키지 소스 없음: $src" }
    $dst = Join-Path $ReplayUiDir $pkg
    Copy-Item -Path $src -Destination $dst -Recurse -Force
    Get-ChildItem -Path $dst -Recurse -Force -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path $dst -Recurse -Force -Filter "*.pyc" | Remove-Item -Force -ErrorAction SilentlyContinue
    Write-Host "[pack-windows] $pkg <- $src"
}

# 6. 실행파일 / 안내 카피.
Copy-Item -Path (Join-Path $TemplatesDir "Launch-ReplayUI.bat") -Destination $ReplayUiDir -Force
Copy-Item -Path (Join-Path $TemplatesDir "Stop-ReplayUI.bat")   -Destination $ReplayUiDir -Force
Copy-Item -Path (Join-Path $TemplatesDir "README.txt")          -Destination $ReplayUiDir -Force

# 7. data/ 빈 디렉토리.
$DataDir = Join-Path $ReplayUiDir "data"
foreach ($d in @("auth-profiles", "scenarios", "scripts", "runs")) {
    New-Item -ItemType Directory -Path (Join-Path $DataDir $d) -Force | Out-Null
}

# 8. Smoke — embedded python 으로 핵심 모듈 import.
$EmbeddedPy = Join-Path $EmbeddedPyDir "python.exe"
Write-Host "[pack-windows] Smoke: import 핵심 모듈"
& $EmbeddedPy -c "import replay_service.server, recording_shared.trace_parser, recording_shared.report_export, zero_touch_qa.auth_profiles, monitor.replay_cmd, playwright"
if ($LASTEXITCODE -ne 0) { throw "smoke import 실패 (exit $LASTEXITCODE)" }
Write-Host "[pack-windows] Smoke OK"

Write-Host ""
Write-Host "[pack-windows] 자산 채우기 완료 — $ReplayUiDir"
Write-Host "  반출 zip 은 'zip -r replay-ui.zip replay-ui' 한 줄로 만들거나 -MakeZip 옵션을 쓰세요."

# 자산 source SHA 를 stamp 로 기록 — pre-push hook 의 stale 검출 기준.
# OS 무관 일관성 위해 git rev-parse 의 tree object id 들을 LF 없이 concat 후 SHA256.
try {
    Push-Location $RepoRoot
    $ids = & git rev-parse `
        "HEAD:playwright-allinone/shared" `
        "HEAD:playwright-allinone/replay-ui/replay_service" `
        "HEAD:playwright-allinone/replay-ui/monitor" `
        "HEAD:playwright-allinone/replay-ui-portable-build/templates"
    $concat = ($ids -join "")
    $sha = [System.BitConverter]::ToString(
        [System.Security.Cryptography.SHA256]::Create().ComputeHash(
            [System.Text.Encoding]::UTF8.GetBytes($concat)
        )
    ).Replace("-", "").ToLower()
    Set-Content -Path (Join-Path $ReplayUiDir ".pack-stamp") -Value $sha -NoNewline
    Write-Host "[pack-windows] .pack-stamp = $($sha.Substring(0, 12))..."
} finally {
    Pop-Location
}

# 9. (옵션) zip 압축. replay-ui/ 폴더를 통째로 zip 으로.
if ($MakeZip) {
    if (-not (Test-Path $BuildOutDir)) { New-Item -ItemType Directory -Path $BuildOutDir -Force | Out-Null }
    $Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $ZipPath = Join-Path $BuildOutDir "DSCORE-ReplayUI-portable-win64-$Timestamp.zip"
    Write-Host "[pack-windows] Compressing replay-ui/ -> $ZipPath"
    if (Test-Path $ZipPath) { Remove-Item -Path $ZipPath -Force }
    Compress-Archive -Path $ReplayUiDir -DestinationPath $ZipPath -CompressionLevel Optimal
    $Info   = Get-Item $ZipPath
    $Hash   = (Get-FileHash -Algorithm SHA256 -Path $ZipPath).Hash
    $SizeMB = [math]::Round($Info.Length / 1MB, 1)
    Write-Host ""
    Write-Host "[pack-windows] zip 산출: $ZipPath"
    Write-Host "  size : ${SizeMB} MB"
    Write-Host "  sha256: $Hash"
}
