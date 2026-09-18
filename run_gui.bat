@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\wasd1\Desktop\Python\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Python not found:
    echo %PYTHON_EXE%
    pause
    exit /b 1
)

"%PYTHON_EXE%" main.py

if errorlevel 1 (
    echo.
    echo Application exited with an error.
    pause
)
