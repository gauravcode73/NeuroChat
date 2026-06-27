@echo off
setlocal
title NeuroChat — Offline AI Assistant

echo.
echo  Starting NeuroChat...
echo  ─────────────────────────────────────────────────

:: ── Activate venv ──────────────────────────────────────
if not exist ".venv\Scripts\activate.bat" (
    echo  Virtual environment not found. Please run setup.bat first.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat



:: ── Start the FastAPI server ────────────────────────────
echo  Launching NeuroChat server...
echo  Open your browser at: http://localhost:8000
echo  Press Ctrl+C to stop.
echo.

cd backend
python main.py

pause
