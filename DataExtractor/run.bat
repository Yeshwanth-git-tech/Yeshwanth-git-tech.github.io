@echo off
echo ============================================================
echo  BindIQ Agent 1 -- Data Extractor
echo ============================================================

REM Activate venv if present, otherwise use system Python
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

REM Pass all args through to run_all.py
REM Usage:
REM   run.bat                          -- full run (all collectors)
REM   run.bat --skip-scraping          -- static data only, no HTTP (fast test)
REM   run.bat --collector naic         -- single collector debug
REM   run.bat --collector moneygeek
REM   run.bat --collector insurify
REM   run.bat --collector carriers
REM   run.bat --collector herald

python run_all.py %*

if errorlevel 1 (
    echo.
    echo ERROR: Run failed. Check raw_data\logs\ for details.
    pause
    exit /b 1
)

echo.
echo Output saved to: raw_data\output\
echo   kg_table_1_carrier_identity.json
echo   kg_table_2_pricing_benchmarks.json
echo   kg_table_3_reliability.json
echo   kg_table_4_appetite.json
echo   kg_table_5_state_presence.json
echo   kg_master_[date].json
pause
