[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$projectPython = $env:AGENTGUARD_PYTHON
if (-not $projectPython) {
    $projectPython = "D:\codexdata\agentguard-venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw "Project Python was not found: $projectPython. Set AGENTGUARD_PYTHON or create it with: python -m venv D:\codexdata\agentguard-venv"
}

& $projectPython @Arguments
exit $LASTEXITCODE
