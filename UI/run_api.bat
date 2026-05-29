@echo off
REM BindIQ FastAPI — starts the API server on port 8000
REM Then expose via ngrok: ngrok http 8000

cd /d "%~dp0"
echo.
echo BindIQ FastAPI starting on http://localhost:8000
echo Docs: http://localhost:8000/docs
echo.
echo To expose via ngrok (in a separate terminal):
echo   ngrok http 8000
echo.

uvicorn api:app --reload --port 8000 --host 0.0.0.0
