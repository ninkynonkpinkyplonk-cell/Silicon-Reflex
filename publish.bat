@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 exit /b 1
set "publish_step=Repository checks"
set "check_only=0"
if "%~1"=="--check" set "check_only=1"
if not "%~1"=="" if not "%~1"=="--check" goto usage
if not "%~2"=="" goto usage
where git >nul 2>nul
if errorlevel 1 goto failed
where python >nul 2>nul
if errorlevel 1 goto failed
python -c "import pathlib,subprocess; root=pathlib.Path.cwd().resolve(); actual=pathlib.Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip()).resolve(); assert root==actual, 'Run inside the standalone Silicon Reflex repository'"
if errorlevel 1 goto failed
if "%check_only%"=="1" goto verify

set "publish_step=Local Git identity"
echo [1/6] Checking repository-local Git identity...
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $commitName=git config --local --get user.name; if([string]::IsNullOrWhiteSpace($commitName)){ git config --local user.name 'Bloug'; if($LASTEXITCODE -ne 0){throw 'Cannot save user.name'} }; $commitEmail=git config --local --get user.email; if([string]::IsNullOrWhiteSpace($commitEmail)){ $commitEmail=$env:REFLEX_GIT_EMAIL; if([string]::IsNullOrWhiteSpace($commitEmail)){ $commitEmail=Read-Host 'Git commit email (verified GitHub email or exact noreply address)' } }; if([string]::IsNullOrWhiteSpace($commitEmail) -or $commitEmail -notmatch '^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$' -or $commitEmail -match '(?i)@example\.(com|org|net)$'){throw 'Provide a real commit email, not a placeholder'}; git config --local user.email $commitEmail; if($LASTEXITCODE -ne 0){throw 'Cannot save user.email'}"
if errorlevel 1 goto failed

set "publish_step=Remote and branch checks"
echo [2/6] Checking the linked GitHub repository...
git remote get-url origin >nul 2>nul
if errorlevel 1 (
  git remote add origin https://github.com/ninkynonkpinkyplonk-cell/Silicon-Reflex.git
) else (
  git remote set-url origin https://github.com/ninkynonkpinkyplonk-cell/Silicon-Reflex.git
)
if errorlevel 1 goto failed
git config --local --replace-all remote.origin.pushurl https://github.com/ninkynonkpinkyplonk-cell/Silicon-Reflex.git
if errorlevel 1 goto failed
python -c "import subprocess; run=lambda *a:subprocess.check_output(['git',*a],text=True).strip(); expected='https://github.com/ninkynonkpinkyplonk-cell/Silicon-Reflex.git'; assert run('remote','get-url','--all','origin')==expected, 'Unexpected fetch URL or URL rewrite'; assert run('remote','get-url','--push','--all','origin')==expected, 'Unexpected push URL or URL rewrite'; assert run('symbolic-ref','--short','HEAD')=='main', 'Switch to branch main explicitly'"
if errorlevel 1 goto failed

:verify
set "publish_step=Tests"
echo [3/6] Running pytest...
python -m pytest -q
if errorlevel 1 goto failed
set "publish_step=Benchmark update"
echo [4/6] Updating benchmark-results.json...
python benchmark_physics.py --iterations 1000000 --report benchmark-results.json
if errorlevel 1 goto failed
if "%check_only%"=="1" goto checked

set "publish_step=Existing GitHub history"
git rev-parse --verify HEAD >nul 2>nul
if not errorlevel 1 goto stage
rem Preserve an existing remote README/LICENSE when the local branch is unborn.
git ls-remote --exit-code --heads origin main >nul
if errorlevel 3 goto failed
if errorlevel 2 goto stage
if errorlevel 1 goto failed
git fetch origin main
if errorlevel 1 goto failed
python -c "import subprocess; fmt=subprocess.check_output(['git','rev-parse','--show-object-format'],text=True).strip(); subprocess.run(['git','update-ref','refs/heads/main','refs/remotes/origin/main','0'*(64 if fmt=='sha256' else 40)],check=True)"
if errorlevel 1 goto failed

:stage
set "publish_step=Stage and commit"
echo [5/6] Staging all non-ignored changes...
git add --all
if errorlevel 1 goto failed
git diff --cached --quiet
if errorlevel 2 goto failed
if not errorlevel 1 goto push
for /f "delims=" %%T in ('python -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).strftime('%%Y-%%m-%%dT%%H:%%M:%%SZ'))"') do set "publish_stamp=%%T"
if not defined publish_stamp goto failed
git commit -m "release: Silicon Reflex %publish_stamp%"
if errorlevel 1 goto failed

:push
set "publish_step=Push to origin/main"
echo [6/6] Pushing main...
git push --set-upstream origin main
if errorlevel 1 goto failed
echo Publish complete.
popd
exit /b 0

:checked
echo Tests and benchmark passed. Nothing was staged, committed or pushed.
popd
exit /b 0

:usage
echo Usage: publish.bat [--check]
popd
exit /b 2

:failed
echo.
echo ERROR: %publish_step% failed. Publishing stopped.
echo Read the command error above. No force push was attempted.
echo Authentication failures: run gh auth login, then gh auth setup-git.
echo A non-fast-forward rejection needs a reviewed merge or rebase; do not force push.
if not defined REFLEX_NO_PAUSE pause
popd
exit /b 1
