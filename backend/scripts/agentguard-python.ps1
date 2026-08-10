[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$projectPython = $env:AGENTGUARD_PYTHON
if (-not $projectPython) {
    $projectPython = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $projectPython -or -not (Test-Path -LiteralPath $projectPython)) {
    throw "Project Python was not found. Set AGENTGUARD_PYTHON or install Python 3.12+."
}

& $projectPython @Arguments
exit $LASTEXITCODE
