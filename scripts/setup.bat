@echo off
rem Nhay dup de cai dat LezioneLive (chay mot lan). Them tham so -Gpu neu may co GPU NVIDIA.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
pause
