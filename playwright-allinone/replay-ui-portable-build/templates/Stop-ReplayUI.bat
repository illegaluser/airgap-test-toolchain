@echo off
setlocal enabledelayedexpansion

set "FOUND="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":18094 " ^| findstr "LISTENING"') do (
  set "FOUND=%%P"
  taskkill /PID %%P /F >nul 2>&1
  echo [Replay UI] Stopped PID %%P
)

if not defined FOUND (
  echo [Replay UI] No running instance on port 18094.
)

endlocal
