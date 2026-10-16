@echo off
setlocal
pushd "%~dp0"
where cl >nul 2>nul
if not errorlevel 1 goto msvc
if exist "%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" (
  for /f "usebackq tokens=*" %%I in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do call "%%I\VC\Auxiliary\Build\vcvars64.bat"
)
where cl >nul 2>nul
if not errorlevel 1 goto msvc
where gcc >nul 2>nul
if errorlevel 1 goto missing
gcc -O3 -std=c11 -Wall -Wextra -shared -o decision_core.dll decision_core.c
goto done
:msvc
cl /nologo /O2 /W4 /LD decision_core.c /link /OUT:decision_core.dll
goto done
:missing
echo Install Visual Studio Build Tools with Desktop development with C++, or x64 MinGW GCC.
exit /b 1
:done
set "build_status=%errorlevel%"
popd
exit /b %build_status%
