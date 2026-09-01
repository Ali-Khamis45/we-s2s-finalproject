<#
.SYNOPSIS
    Windows equivalents of the root Makefile. The dev box is Windows; the
    Makefile is kept for CI and for anyone on Linux or macOS.

.EXAMPLE
    .\scripts\make.ps1 types
    .\scripts\make.ps1 dev
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("help", "setup", "dev", "test", "test-backend", "test-frontend",
                 "lint", "schema", "types", "check-types", "bench", "bench-fast", "clean")]
    [string]$Target = "help"
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path $PSScriptRoot -Parent
$Py = Join-Path $Repo "backend\.venv\Scripts\python.exe"
$env:PYTHONPATH = Join-Path $Repo "backend"

function Invoke-Backend([string[]]$Args) {
    Push-Location (Join-Path $Repo "backend")
    try { & $Py @Args; if ($LASTEXITCODE -ne 0) { throw "failed: $($Args -join ' ')" } }
    finally { Pop-Location }
}

function Invoke-Frontend([string[]]$Args) {
    Push-Location (Join-Path $Repo "frontend")
    try { & npx @Args; if ($LASTEXITCODE -ne 0) { throw "failed: npx $($Args -join ' ')" } }
    finally { Pop-Location }
}

switch ($Target) {
    "help" {
        @"
  setup          Create the venv and install both halves
  dev            Run backend and frontend together
  test           Every test
  test-backend   Backend suite (no models needed)
  test-frontend  Frontend suite
  lint           Ruff and TypeScript
  schema         Write docs/openapi.json from the app
  types          Regenerate frontend types from the schema
  check-types    Fail if the committed schema or types are stale (CI)
  bench          Full verification run - NEEDS MODELS, not for CI
  bench-fast     The model-free subset CI can run
  clean          Remove build output and caches
"@
    }

    "setup" {
        & python -m venv (Join-Path $Repo "backend\.venv")
        & $Py -m pip install -r (Join-Path $Repo "backend\requirements.txt")
        Invoke-Frontend @("--yes", "npm", "install")
    }

    "dev" {
        Write-Host "backend  http://127.0.0.1:8000/docs"
        Write-Host "frontend http://localhost:5173"
        $api = Start-Process -FilePath $Py `
            -ArgumentList "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--port", "8000" `
            -WorkingDirectory $Repo -PassThru -NoNewWindow
        try {
            Push-Location (Join-Path $Repo "frontend")
            & npm run dev
        } finally {
            Pop-Location
            Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue
        }
    }

    "test" {
        Invoke-Backend @("-m", "pytest", "-q")
        Invoke-Frontend @("vitest", "run")
    }
    "test-backend"  { Invoke-Backend @("-m", "pytest", "-q") }
    "test-frontend" { Invoke-Frontend @("vitest", "run") }

    "lint" {
        Invoke-Backend @("-m", "ruff", "check", "app", "tests", "scripts")
        Invoke-Frontend @("tsc", "--noEmit")
    }

    "schema" { & $Py (Join-Path $Repo "backend\scripts\dump_openapi.py") }

    "types" {
        & $Py (Join-Path $Repo "backend\scripts\dump_openapi.py")
        Invoke-Frontend @("openapi-typescript", "../docs/openapi.json", "-o", "src/lib/api-types.gen.ts")
        Write-Host "Commit docs/openapi.json and api-types.gen.ts together with the change."
    }

    "check-types" {
        & $Py (Join-Path $Repo "backend\scripts\dump_openapi.py") "--check"
        if ($LASTEXITCODE -ne 0) { throw "committed schema is stale" }
        $tmp = Join-Path $env:TEMP "api-types.check.ts"
        Remove-Item $tmp -ErrorAction SilentlyContinue
        Invoke-Frontend @("openapi-typescript", "../docs/openapi.json", "-o", $tmp)
        $current = Join-Path $Repo "frontend\src\lib\api-types.gen.ts"

        # Compare content, not bytes. git's autocrlf gives a Windows checkout
        # CRLF while the generator always emits LF, so a byte comparison would
        # report every Windows clone as stale.
        $fresh = (Get-Content $tmp -Raw) -replace "`r`n", "`n"
        $committed = (Get-Content $current -Raw) -replace "`r`n", "`n"
        if ($fresh -ne $committed) {
            throw "api-types.gen.ts is stale. Run: .\scripts\make.ps1 types"
        }
        Write-Host "schema and types are current"
    }

    "bench" {
        $words = Join-Path $Repo "backend\scripts\words"
        & $Py (Join-Path $Repo "backend\scripts\verify_acoustic_branch.py") $words
        & $Py (Join-Path $Repo "backend\scripts\verify_retrieval.py")
        & $Py (Join-Path $Repo "backend\scripts\bench_whisper.py") (Join-Path $words "dysfluent_utterance.wav")
    }

    "bench-fast" {
        Invoke-Backend @("-m", "pytest", "-q", "tests/test_acoustic.py", "tests/test_contract.py")
    }

    "clean" {
        Remove-Item -Recurse -Force (Join-Path $Repo "frontend\dist") -ErrorAction SilentlyContinue
        Get-ChildItem $Repo -Recurse -Directory -Filter "__pycache__" |
            Where-Object { $_.FullName -notmatch "\\\.venv\\|node_modules" } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
}
