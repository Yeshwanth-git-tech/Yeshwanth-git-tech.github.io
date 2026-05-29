@echo off
echo BindIQ UI - Starting Streamlit...
echo.

cd /d "%~dp0"

:: Install dependencies if needed
pip install -r requirements.txt -q

:: Start Streamlit
streamlit run app.py --server.port 8501 --server.headless false

pause
