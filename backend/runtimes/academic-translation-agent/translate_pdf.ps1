param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$InputPdf,
    [string]$SourceLang = 'en',
    [string]$TargetLang = 'zh',
    [switch]$NoFigures,
    [ValidateRange(1, 5)] [int]$Parallel = 2,
    [ValidateRange(60, 3600)] [int]$Timeout = 600,
    [string]$GlossaryJson = '{}',
    [switch]$Bilingual
)

$ErrorActionPreference = 'Stop'
$InputPdf = (Resolve-Path -LiteralPath $InputPdf).Path
if ([IO.Path]::GetExtension($InputPdf).ToLowerInvariant() -ne '.pdf') {
    throw 'Input must be a PDF file.'
}

$Root = $PSScriptRoot
$Compose = Join-Path $Root 'agent-system/docker/docker-compose.yml'
$Stem = [IO.Path]::GetFileNameWithoutExtension($InputPdf)
$HostOutputs = Join-Path $Root 'agent-core/outputs'
$Delivery = Join-Path $Root 'output/pdf'
New-Item -ItemType Directory -Force -Path $Delivery | Out-Null

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install and start Docker Desktop, then run this same command again.' }
$TranslationBackend = if ($env:TRANSLATION_LLM_BACKEND) { $env:TRANSLATION_LLM_BACKEND.ToLowerInvariant() } else { 'api' }
if ($TranslationBackend -eq 'ollama') {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { throw 'Install Ollama for TRANSLATION_LLM_BACKEND=ollama.' }
    $OllamaModel = if ($env:OLLAMA_TRANSLATION_MODEL) { $env:OLLAMA_TRANSLATION_MODEL } else { 'translategemma:12b' }
    & ollama show $OllamaModel *> $null
    if ($LASTEXITCODE -ne 0) { & ollama pull $OllamaModel }
} elseif (-not $env:TRANSLATION_API_BASE_URL -or -not $env:TRANSLATION_API_MODEL) {
    throw 'TRANSLATION_API_BASE_URL and TRANSLATION_API_MODEL are required for API translation.'
}
try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 *> $null } catch { Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden }

& docker compose -f $Compose up -d --build | Out-Null
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2 *> $null; break } catch { Start-Sleep -Seconds 1 }
}

$Result = Join-Path $Delivery "$Stem-result.json"
$TranslateFigures = if ($NoFigures) { 'false' } else { 'true' }
$BilingualValue = if ($Bilingual) { 'true' } else { 'false' }
& curl.exe --fail --silent --show-error --max-time 660 -X POST 'http://127.0.0.1:8000/translate/document' `
    -F "file=@$InputPdf" -F "source_lang=$SourceLang" -F "target_lang=$TargetLang" `
    -F preserve_pdf_layout=true -F pdf_only=true -F pdf_layout_mode=batch -F "pdf_bilingual=$BilingualValue" `
    -F "translate_figures=$TranslateFigures" -F "max_parallel_segments=$Parallel" `
    -F max_output_bytes=10000000 -F "pdf_timeout_seconds=$Timeout" `
    --form-string "glossary_json=$GlossaryJson" | Set-Content -Encoding utf8 $Result
if ($LASTEXITCODE -ne 0) { throw "Translation failed; inspect $Result" }

foreach ($Name in @("$Stem-mono-visuals.pdf", "$Stem-mono-figures.pdf", "$Stem-mono.pdf")) {
    $Candidate = Join-Path $HostOutputs $Name
    if ((Test-Path $Candidate) -and ((Get-Item $Candidate).Length -gt 0)) {
        $Final = Join-Path $Delivery "$Stem-zh.pdf"
        Copy-Item -Force $Candidate $Final
        Write-Output $Final
        exit 0
    }
}
throw "Translation finished without a deliverable PDF; inspect $Result"
