@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHONHOME="
set "PYTHONPATH=%ROOT%;%ROOT%site-packages"
set "PLAYWRIGHT_BROWSERS_PATH=%ROOT%chromium"
set "MONITOR_HOME=%ROOT%data"
set "AUTH_PROFILES_DIR=%ROOT%data\auth-profiles"
set "PYTHONIOENCODING=utf-8"

REM 데이터 디렉토리 사전 생성.
if not exist "%ROOT%data" mkdir "%ROOT%data" >nul 2>&1
if not exist "%ROOT%data\auth-profiles" mkdir "%ROOT%data\auth-profiles" >nul 2>&1
if not exist "%ROOT%data\scenarios"     mkdir "%ROOT%data\scenarios" >nul 2>&1
if not exist "%ROOT%data\scripts"       mkdir "%ROOT%data\scripts" >nul 2>&1
if not exist "%ROOT%data\runs"          mkdir "%ROOT%data\runs" >nul 2>&1

REM E 그룹 receiving-PC selftest — 첫 실행 1회 (../docs/PLAN_E2E_REWRITE.md §5 E).
REM 통과 시 .selftest_done 마커 -> 다음 실행 skip. 실패해도 launcher 는 계속.
if not exist "%ROOT%.selftest_done" if exist "%ROOT%selftest-receive.py" (
  echo [Replay UI] First-run selftest...
  "%ROOT%embedded-python\python.exe" "%ROOT%selftest-receive.py"
  if not errorlevel 1 (
    type nul > "%ROOT%.selftest_done"
  ) else (
    echo [Replay UI] Selftest reported issues - see output above. Continuing.
  )
)

REM 포트 충돌 — 기존 인스턴스가 이미 18099 를 잡고 있으면 그 쪽으로 연결.
netstat -ano | findstr ":18099 " | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo [Replay UI] Port 18099 is already in use - opening existing instance.
  start "" "http://127.0.0.1:18099/"
  exit /b 0
)

REM Replay UI 백그라운드 기동. stdout/stderr 를 data\runs\replay-ui.*.log 로 redirect.
REM macOS .command 와 동등한 진단성 — UI 가 안 뜨면 그 파일을 본다.
REM start 자체는 redirect 지원이 까다로워 cmd /c 로 한 번 감싼다.
start "ReplayUI" /min cmd /c ""%ROOT%embedded-python\python.exe" -m uvicorn replay_service.server:app --host 127.0.0.1 --port 18099 > "%ROOT%data\runs\replay-ui.stdout.log" 2> "%ROOT%data\runs\replay-ui.stderr.log""

REM 준비될 때까지 폴링 후 브라우저 오픈.
for /l %%i in (1,1,15) do (
  >nul 2>&1 powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:18099/ -TimeoutSec 1).StatusCode } catch { exit 1 }"
  if not errorlevel 1 goto :open
  >nul timeout /t 1 /nobreak
)

echo [Replay UI] Service did not come up within 15s. See "%ROOT%data\runs" for logs.
exit /b 1

:open
start "" "http://127.0.0.1:18099/"
endlocal
