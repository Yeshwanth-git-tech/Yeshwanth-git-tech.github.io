@echo off
echo ============================================================
echo  BindIQ Agent 1 -- Setup
echo ============================================================

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.11+ from python.org
    pause
    exit /b 1
)

REM Create virtual environment if it doesn't exist
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

REM Activate and install
echo Activating virtual environment...
call .venv\Scripts\activate.bat

echo Installing dependencies...
pip install --upgrade pip

REM lxml must use a pre-built binary wheel on Windows — compiling from source
REM requires libxml2/libxslt headers which are not available by default.
pip install "lxml>=5.1.0" --only-binary :all:
if errorlevel 1 (
    echo ERROR: Could not install lxml binary wheel.
    echo Try: pip install lxml --only-binary :all:
    pause
    exit /b 1
)

REM Install remaining packages (none require C compilation)
pip install -r requirements.txt

echo.
echo ============================================================
echo  Setup complete.
echo  To run the data extractor:   run.bat
echo  To run in fast/static mode:  run.bat --skip-scraping
echo  To run a single collector:   run.bat --collector naic
echo ============================================================
pause
