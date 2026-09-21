@echo off
setlocal
set "PYTHONUNBUFFERED=1"

pushd "%~dp0" || goto :path_error

call "%~dp0build_support\find_python.cmd"

if not defined PYTHON_EXE (
    echo Python 3.10+ with tkinter was not found.
    echo Install Python 3.10+ from python.org and enable tcl/tk and the py launcher.
    pause
    popd
    exit /b 1
)

echo Using: %PYTHON_EXE%
"%PYTHON_EXE%" "%~dp0build_support\ensure_runtime.py"
if errorlevel 1 (
    pause
    popd
    exit /b 1
)

"%PYTHON_EXE%" "%~dp0main.py"

if errorlevel 1 (
    echo.
    echo Application exited with an error.
    echo Check AnalogwinPiezoGUI.error.log beside main.py or in:
    echo "%LOCALAPPDATA%\AnalogwinPiezoGUI"
    pause
    popd
    exit /b 1
)
popd
exit /b 0

:path_error
echo Cannot open the application folder. Extract the complete ZIP first.
pause
exit /b 1
