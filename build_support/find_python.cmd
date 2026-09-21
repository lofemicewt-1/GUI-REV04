@echo off
REM Sets PYTHON_EXE for the caller. Do not use setlocal here.

set "REQUESTED_PYTHON=%PYTHON_EXE%"
set "PYTHON_EXE="
if defined REQUESTED_PYTHON (
  call :accept_candidate "%REQUESTED_PYTHON%"
  if defined PYTHON_EXE goto :eof
)

for %%I in ("%~dp0..") do set "ROOT=%%~fI"

if exist "%ROOT%\.venv\Scripts\python.exe" (
  call :accept_candidate "%ROOT%\.venv\Scripts\python.exe"
  if defined PYTHON_EXE goto :eof
)
if exist "%ROOT%\venv\Scripts\python.exe" (
  call :accept_candidate "%ROOT%\venv\Scripts\python.exe"
  if defined PYTHON_EXE goto :eof
)

where py >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
    call :accept_candidate "%%I"
    if defined PYTHON_EXE goto :eof
  )
)

for /f "delims=" %%I in ('where python 2^>nul') do (
  call :accept_candidate "%%I"
  if defined PYTHON_EXE goto :eof
)

if exist "%USERPROFILE%\Desktop\Python\Scripts\python.exe" (
  call :accept_candidate "%USERPROFILE%\Desktop\Python\Scripts\python.exe"
  if defined PYTHON_EXE goto :eof
)

goto :eof

:accept_candidate
set "PYTHON_EXE="
if "%~1"=="" goto :eof
if not exist "%~1" goto :eof
echo "%~1" | find /I "WindowsApps" >nul
if not errorlevel 1 goto :eof
"%~1" -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :eof
set "PYTHON_EXE=%~1"
goto :eof
