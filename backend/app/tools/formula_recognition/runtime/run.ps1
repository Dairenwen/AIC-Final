param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Image,
    [Parameter(Position = 1)]
    [ValidateSet("auto", "mps", "cuda", "cpu")]
    [string]$Device = "auto"
)

$ErrorActionPreference = "Stop"
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONUTF8 = "1"
$ToolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ToolDir ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$ModelDir = Join-Path $ToolDir "unimernet\models\unimernet_base"
# PyTorch wheels are large and Windows package builds can exceed MAX_PATH when
# pip stages them below this long Chinese project path. Use short directories
# on the same drive as the checkout, while keeping the runtime itself local.
$DriveRoot = [System.IO.Path]::GetPathRoot($ToolDir)
$TempDir = Join-Path $DriveRoot "zhiyan-formula-tmp"
$PipCacheDir = Join-Path $DriveRoot "zhiyan-formula-pip-cache"
New-Item -ItemType Directory -Force -Path $TempDir, $PipCacheDir | Out-Null
$env:TEMP = $TempDir
$env:TMP = $TempDir
$env:PIP_CACHE_DIR = $PipCacheDir

function Invoke-ToolPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

    # Windows PowerShell can promote a native process' stderr to a terminating
    # error when ErrorActionPreference is Stop. Keep stderr visible and judge
    # native commands by their exit code instead.
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Python @Arguments
        $ExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    if ($ExitCode -ne 0) {
        throw "Python command failed with exit code $ExitCode"
    }
}

if (-not (Test-Path $Python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.10 -m venv $VenvDir
    }
    else {
        & python -m venv $VenvDir
    }
}

$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $Python -c "import torch, transformers, unimernet" *> $null
    $DependenciesReady = $LASTEXITCODE -eq 0
}
finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}
if (-not $DependenciesReady) {
    Invoke-ToolPython -m pip install --disable-pip-version-check --upgrade pip
    Invoke-ToolPython -m pip install --disable-pip-version-check --no-cache-dir "setuptools<81" wheel
    if ($Device -ne "cpu" -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
        # Prefer the matching CUDA wheel when an NVIDIA runtime is present.
        Invoke-ToolPython -m pip install --disable-pip-version-check --no-cache-dir --no-deps --retries 5 --timeout 120 `
            torch==2.4.0+cu124 torchvision==0.19.0+cu124 `
            --index-url https://download.pytorch.org/whl/cu124
    }
    Invoke-ToolPython -m pip install --disable-pip-version-check --no-cache-dir --prefer-binary --no-build-isolation --retries 8 --timeout 120 -r (Join-Path $ToolDir "requirements-inference.txt")
    Invoke-ToolPython -m pip install --no-deps --editable (Join-Path $ToolDir "unimernet")
}

if (-not (Test-Path (Join-Path $ModelDir "pytorch_model.pth"))) {
    Invoke-ToolPython (Join-Path $ToolDir "download_model.py") $ModelDir
}

Invoke-ToolPython (Join-Path $ToolDir "recognize.py") $Image --device $Device
