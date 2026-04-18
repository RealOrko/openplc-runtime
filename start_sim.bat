@echo off
echo.
echo  =========================================
echo   Water Plant Simulator - Mode Selection
echo  =========================================
echo.
echo   1. Original  (6 scenarios  - developer version)
echo   2. Extended  (14 scenarios - includes additional faults)
echo.
set /p CHOICE="  Enter 1 or 2: "

if "%CHOICE%"=="1" (
    set SIM_MODE=original
    echo.
    echo  Starting with ORIGINAL mode ^(6 scenarios^)...
) else if "%CHOICE%"=="2" (
    set SIM_MODE=extended
    echo.
    echo  Starting with EXTENDED mode ^(14 scenarios^)...
) else (
    echo.
    echo  Invalid choice. Defaulting to ORIGINAL mode.
    set SIM_MODE=original
)

echo.
docker compose up --build
