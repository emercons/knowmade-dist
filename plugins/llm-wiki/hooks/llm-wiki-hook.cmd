@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SCRIPT=%~dp0llm_wiki_session.py"

where py >nul 2>nul && goto run_py

where python >nul 2>nul && goto run_python

where python3 >nul 2>nul && goto run_python3

rem Session capture is optional. Missing Python must not block Codex itself.
exit /b 0

:run_py
py -3 "%SCRIPT%" %*
exit /b %ERRORLEVEL%

:run_python
python "%SCRIPT%" %*
exit /b %ERRORLEVEL%

:run_python3
python3 "%SCRIPT%" %*
exit /b %ERRORLEVEL%
