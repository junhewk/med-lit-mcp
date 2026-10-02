@echo off
setlocal
set "MED_LIT_INSTALL_DEV="
if "%~1"=="" goto run
if /I not "%~1"=="--dev" goto usage
if "%~2"=="" goto usage
if not "%~3"=="" goto usage
set "MED_LIT_INSTALL_DEV=%~2"
:run
powershell.exe -NoLogo -NoProfile -Command "@BOOTSTRAP@"
set "MED_LIT_INSTALL_RESULT=%ERRORLEVEL%"
if "%MED_LIT_INSTALL_RESULT%"=="0" exit /b 0
echo med-lit setup did not finish. Check the message above, then reopen this file to retry.
pause
exit /b %MED_LIT_INSTALL_RESULT%
:usage
echo Developer usage: "Install med-lit.bat" --dev "C:\path\to\checkout"
pause
exit /b 1
