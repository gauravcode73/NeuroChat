@echo off
setlocal enabledelayedexpansion
title NeuroChat — Setup

echo.
echo  ███╗   ██╗███████╗██╗   ██╗██████╗  ██████╗
echo  ████╗  ██║██╔════╝██║   ██║██╔══██╗██╔═══██╗
echo  ██╔██╗ ██║█████╗  ██║   ██║██████╔╝██║   ██║
echo  ██║╚██╗██║██╔══╝  ██║   ██║██╔══██╗██║   ██║
echo  ██║ ╚████║███████╗╚██████╔╝██║  ██║╚██████╔╝
echo  ╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝
echo.
echo  NeuroChat — Offline AI Assistant  ^|  Setup Script
echo  ─────────────────────────────────────────────────
echo.

:: ────────────────────────────────────────────────────────
:: Step 1: Check Python
:: ────────────────────────────────────────────────────────
echo [1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found.
    echo  Please install Python 3.10+ from https://python.org
    echo  Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  Found: %PYVER%

:: ────────────────────────────────────────────────────────
:: Step 2: Create virtual environment
:: ────────────────────────────────────────────────────────
echo.
echo [2/5] Creating virtual environment...
if exist ".venv" (
    echo  .venv already exists, skipping.
) else (
    python -m venv .venv
    if errorlevel 1 (
        echo  ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo  Created .venv
)

:: ────────────────────────────────────────────────────────
:: Step 3: Install dependencies
:: ────────────────────────────────────────────────────────
echo.
echo [3/5] Installing Python dependencies...
call .venv\Scripts\activate.bat
pip install --upgrade pip --quiet
pip install -r backend\requirements.txt
if errorlevel 1 (
    echo  ERROR: Failed to install dependencies.
    pause
    exit /b 1
)
echo  Dependencies installed successfully.



:: ────────────────────────────────────────────────────────
:: Create conversations directory
:: ────────────────────────────────────────────────────────
if not exist "conversations" mkdir conversations

:: ────────────────────────────────────────────────────────
:: Done
:: ────────────────────────────────────────────────────────
echo.
echo  ══════════════════════════════════════════════════
echo   ✅  Setup complete!
echo.
echo   To start NeuroChat, run:  start.bat
echo   Then open:  http://localhost:8000
echo  ══════════════════════════════════════════════════
echo.
pause
