@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT=%~dp0llm_wiki_session.py"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%SCRIPT%" %*
  exit /b !ERRORLEVEL!
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%SCRIPT%" %*
  exit /b !ERRORLEVEL!
)

where python3 >nul 2>nul
if %ERRORLEVEL%==0 (
  python3 "%SCRIPT%" %*
  exit /b !ERRORLEVEL!
)

rem Session capture is optional. Missing Python must not block Codex itself.
exit /b 0
