@echo off
setlocal
set "PYTHONUNBUFFERED=1"
if not defined GUI_DIST_DIR set "GUI_DIST_DIR=%~dp0dist"

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

"%PYTHON_EXE%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo PyInstaller is missing; installing...
    "%PYTHON_EXE%" -m pip --disable-pip-version-check install --timeout 15 --retries 1 pyinstaller
    if errorlevel 1 (
        echo Failed to install PyInstaller.
        pause
        popd
        exit /b 1
    )
)

"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean --distpath "%GUI_DIST_DIR%" AnalogwinPiezoGUI.spec

if errorlevel 1 (
    echo.
    echo Build failed.
    echo If the EXE is in use, close it before building again.
    pause
    popd
    exit /b 1
)

echo.
echo Build complete: "%GUI_DIST_DIR%\AnalogwinPiezoGUI.exe"
pause
popd
exit /b 0

:path_error
echo Cannot open the application folder. Extract the complete ZIP first.
pause
exit /b 1
