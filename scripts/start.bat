@echo off
rem Nhay dup de khoi dong LezioneLive (http://127.0.0.1:8765). Dong cua so nay de dung.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
