@echo off
chcp 65001 >nul
title Aion Chat
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0aion-chat\restart-home-qq.ps1" -StartOnly %*
if not errorlevel 1 exit /b 0
pause
exit /b 1
