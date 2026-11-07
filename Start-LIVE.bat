@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title BTC Quant Console 1.0 - LIVE
"%~dp0BTCQuantConsole.exe" web --port 18787
if errorlevel 1 pause
