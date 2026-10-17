@echo off
setlocal
set "VLCAPTIONER_UPDATE_PAUSE=1"
if /i "%~1"=="--no-pause" set "VLCAPTIONER_UPDATE_PAUSE="

rem Parse the whole update block before Git can replace this script.
(
    echo Updating VLCaptioner...
    echo Close VLCaptioner and its tool windows before running this updater.
    echo.

    where git >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Git was not found. Install Git and reopen this updater.
        if defined VLCAPTIONER_UPDATE_PAUSE pause
        exit /b 1
    )

    if not exist "%~dp0.git" (
        echo ERROR: This folder is not a Git checkout. Clone the repository first.
        if defined VLCAPTIONER_UPDATE_PAUSE pause
        exit /b 1
    )

    pushd "%~dp0"
    if errorlevel 1 (
        echo ERROR: Could not open the VLCaptioner folder.
        if defined VLCAPTIONER_UPDATE_PAUSE pause
        exit /b 1
    )

    git symbolic-ref --quiet HEAD >nul 2>&1
    if errorlevel 1 (
        echo ERROR: No branch is checked out. Switch to a branch before updating.
        popd
        if defined VLCAPTIONER_UPDATE_PAUSE pause
        exit /b 1
    )

    git rev-parse --verify "@{upstream}" >nul 2>&1
    if errorlevel 1 (
        echo ERROR: This branch has no usable upstream. Configure its tracking branch first.
        popd
        if defined VLCAPTIONER_UPDATE_PAUSE pause
        exit /b 1
    )

    rem Let Git preserve local changes and reject only changes that block the update.
    echo Pulling the current branch from its configured upstream...
    git -c merge.autostash=false -c rebase.autostash=false pull --ff-only --no-rebase
    if errorlevel 1 (
        echo.
        echo ERROR: Update failed. Review the Git message above.
        echo Check network access, local changes to incoming files, conflicting untracked files, or diverged branch history.
        echo This updater does not reset, stash, or resolve conflicts automatically.
        popd
        if defined VLCAPTIONER_UPDATE_PAUSE pause
        exit /b 1
    )

    echo.
    echo Repository update complete.
    echo If dependencies changed, run install.bat before launching GUI.cmd again.
    popd
    if defined VLCAPTIONER_UPDATE_PAUSE pause
    exit /b 0
)
