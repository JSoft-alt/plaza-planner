@echo off
setlocal
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 "%~dp0scripts\run-native.py" %*
goto finished
:use_python
python "%~dp0scripts\run-native.py" %*
:finished
set "planner_exit=%ERRORLEVEL%"
if not "%planner_exit%"=="0" (
  echo Install 64-bit Python 3.12 from python.org if Python is missing.
  pause
)
exit /b %planner_exit%
