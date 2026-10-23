@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows.ps1" %*
set "planner_exit=%ERRORLEVEL%"
if not "%planner_exit%"=="0" pause
exit /b %planner_exit%
