Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedVersion = "0.7.2"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root

try {
    $PythonExe = $null
    $PythonArgs = @()

    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $PythonExe = "py.exe"
        $PythonArgs = @("-3")
    }
    elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
        $PythonExe = "python.exe"
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $PythonExe = "python"
    }
    else {
        throw "Python was not found. Install Python 3.10 or newer and enable Add Python to PATH."
    }

    $VersionText = & $PythonExe @PythonArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to run Python."
    }

    $VersionParts = $VersionText.Trim().Split('.')
    $Major = [int]$VersionParts[0]
    $Minor = [int]$VersionParts[1]
    if (($Major -lt 3) -or (($Major -eq 3) -and ($Minor -lt 10))) {
        throw "Python 3.10 or newer is required. Detected: $VersionText"
    }

    Write-Host "Using Python $VersionText"

    $VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        Write-Host "Creating virtual environment..."
        & $PythonExe @PythonArgs -m venv (Join-Path $Root ".venv")
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create the virtual environment."
        }
    }

    Write-Host "Upgrading pip..."
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip. Check your network or Python installation."
    }

    Write-Host "Installing dependencies and upgrading Exchange EWS MCP..."
    & $VenvPython -m pip install --upgrade $Root
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install dependencies or upgrade the project. Check the pip output above."
    }

    Write-Host "Forcing the local v$ExpectedVersion source into this virtual environment..."
    # Reinstall only this local project without re-downloading dependencies. This prevents
    # an older site-packages copy or cached project wheel from surviving the update.
    & $VenvPython -m pip install --force-reinstall --no-deps --no-cache-dir $Root
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to force-reinstall the local project. Check the pip output above."
    }

    $Cli = Join-Path $Root ".venv\Scripts\exchange-ews-mcp.exe"
    if (-not (Test-Path $Cli)) {
        throw "Installation finished, but exchange-ews-mcp.exe was not created."
    }

    $InstalledVersion = (& $VenvPython -c "import exchange_ews_mcp; print(exchange_ews_mcp.__version__)" 2>&1).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Installation finished, but the installed package could not be imported."
    }
    if ($InstalledVersion -ne $ExpectedVersion) {
        throw "Version verification failed. Expected $ExpectedVersion, installed $InstalledVersion."
    }

    $InstalledPath = (& $VenvPython -c "import exchange_ews_mcp; print(exchange_ews_mcp.__file__)" 2>&1).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Could not determine the installed package path."
    }

    Write-Host ""
    Write-Host "Installation completed successfully."
    Write-Host "Installed version: $InstalledVersion"
    Write-Host "Installed package: $InstalledPath"
    Write-Host "Verification command:"
    Write-Host ".\.venv\Scripts\exchange-ews-mcp.exe version"
    Write-Host "Next command:"
    Write-Host ".\.venv\Scripts\exchange-ews-mcp.exe status"
}
finally {
    Pop-Location
}
