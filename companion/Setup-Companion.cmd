@echo off
setlocal
rem Avoid inheriting incompatible PowerShell 7 modules into Windows PowerShell.
set "PSModulePath="
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-Companion.ps1"
pause
