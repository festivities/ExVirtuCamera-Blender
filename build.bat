@echo off
rem Builds exvirtucamera_blender-<version>.zip in this folder.
rem Needs Windows x64 and the py launcher with pip. Blender 5.2 is used for the build;
rem set BLENDER to its full path if it is not in the default location.
setlocal
cd /d "%~dp0"
if "%BLENDER%"=="" set "BLENDER=C:\Apps\blender\blender.exe"

if not exist wheels mkdir wheels
rem "call" because py may be a .cmd wrapper, which would otherwise end this script.
call py -m pip download av==18.1.0 qrcode==8.2 --only-binary=:all: --no-deps --platform win_amd64 --python-version 3.11 -d wheels
if errorlevel 1 exit /b 1

"%BLENDER%" --factory-startup --command extension build --source-dir "%~dp0." --output-dir "%~dp0."
if errorlevel 1 exit /b 1
