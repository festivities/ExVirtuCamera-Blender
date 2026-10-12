<#
.SYNOPSIS
  GUI end-to-end run of the built extension zip: one GUI Blender plus one fake VirtuCamera 1 client.

.DESCRIPTION
  Starts Blender (--factory-startup, user dirs under <Out>\user, self-watchdog) running
  tests\e2e_blender.py, and tests\e2e_client.py with Blender's bundled Python over 127.0.0.1.
  Only the processes started by this script are killed. Exit 0 only if every client check passed
  and Blender's output has no Traceback (the known av.libs PermissionError tracebacks are ignored).
  Build first (build.bat) so the zip and wheels\av-*.whl exist.

.EXAMPLE
  pwsh tests\run_e2e.ps1 -Exe C:\Apps\blender\blender.exe
  pwsh tests\run_e2e.ps1 -Exe "E:\Program Files\goo-engine-4.4.3\blender.exe" -Port 47242
#>
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [string]$Zip,
    [int]$Port = 47241,
    [string]$Out,
    [string]$Python = 'C:\Apps\blender\5.2\python\bin\python.exe',
    [string]$AvPath
)
$ErrorActionPreference = 'Stop'
$tests = $PSScriptRoot
$repo = Split-Path -Parent $tests
$env:PYTHONDONTWRITEBYTECODE = '1'

function Quote([string]$s) { '"' + $s + '"' }

if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) { throw "Blender not found: $Exe" }
$exeFull = (Resolve-Path -LiteralPath $Exe).Path
$tag = (Split-Path -Leaf (Split-Path -Parent $exeFull)) -replace '[^A-Za-z0-9._-]', '_'
if (-not $Out) { $Out = Join-Path $env:TEMP "exvirtucamera_e2e\$tag" }
$Out = [IO.Path]::GetFullPath($Out)
# -Out is cleared at the start of a run: refuse anything that holds the repo or drive root.
foreach ($keep in @($repo, $tests)) {
    if ($Out.Length -le 3 -or $keep -eq $Out -or $keep.StartsWith($Out.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "-Out must be a scratch folder, not $Out"
    }
}

if (-not $Zip) {
    $z = Get-ChildItem -LiteralPath $repo -Filter 'exvirtucamera_blender-*.zip' -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $z) { throw "no exvirtucamera_blender-*.zip in $repo (run build.bat first)" }
    $Zip = $z.FullName
}
if (-not (Test-Path -LiteralPath $Zip -PathType Leaf)) { throw "zip not found: $Zip" }
$zipFull = (Resolve-Path -LiteralPath $Zip).Path
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python not found: $Python (use -Python)" }
$wheel = Join-Path $repo 'wheels\av-18.1.0-cp311-abi3-win_amd64.whl'
if (-not $AvPath -and -not (Test-Path -LiteralPath $wheel -PathType Leaf)) { throw "PyAV wheel missing: $wheel (run build.bat first)" }

# Fresh run: clear the work dir except the cached av package (stale req/resp/ready files would confuse the client).
New-Item -ItemType Directory -Force -Path $Out | Out-Null
Get-ChildItem -LiteralPath $Out -Force | Where-Object { $_.Name -ne 'avpkg' } | Remove-Item -Recurse -Force
if (-not $AvPath) {
    $AvPath = Join-Path $Out 'avpkg'
    if (-not (Test-Path -LiteralPath (Join-Path $AvPath 'av\__init__.py'))) {
        New-Item -ItemType Directory -Force -Path $AvPath | Out-Null
        & $Python -s -m zipfile -e $wheel $AvPath
        if ($LASTEXITCODE -ne 0) { throw "could not extract $wheel" }
    }
}
$user = Join-Path $Out 'user'
$env:E2E_OUT = $Out
$env:E2E_PORT = "$Port"
$env:E2E_ZIP = $zipFull
$env:E2E_SCRIPTS = Join-Path $tests 'fixtures\scripts'
$env:E2E_AV_PATH = $AvPath
$env:BLENDER_USER_RESOURCES = Join-Path $user 'resources'
$env:BLENDER_USER_CONFIG = Join-Path $user 'config'
$env:BLENDER_USER_SCRIPTS = Join-Path $user 'scripts'
$env:BLENDER_USER_EXTENSIONS = Join-Path $user 'extensions'
$env:BLENDER_USER_DATAFILES = Join-Path $user 'datafiles'

$started = @()
$alive = 0
$clock = [Diagnostics.Stopwatch]::StartNew()
try {
    $bp = Start-Process -FilePath $exeFull -ArgumentList @('--factory-startup', '--python', (Quote (Join-Path $tests 'e2e_blender.py'))) -PassThru -RedirectStandardOutput (Join-Path $Out 'blender.stdout.txt') -RedirectStandardError (Join-Path $Out 'blender.stderr.txt')
    $started += $bp
    Write-Output "blender pid $($bp.Id) started: $exeFull"
    $cp = Start-Process -FilePath $Python -ArgumentList @('-s', (Quote (Join-Path $tests 'e2e_client.py')), (Quote $Out), "$Port") -PassThru -RedirectStandardOutput (Join-Path $Out 'client.stdout.txt') -RedirectStandardError (Join-Path $Out 'client.stderr.txt')
    $started += $cp
    Write-Output "client pid $($cp.Id)"

    # Watchdogs: client 115 s, Blender 120 s after launch (Blender also self-quits at 110 s).
    if (-not $cp.WaitForExit([Math]::Max(1000, 115000 - $clock.ElapsedMilliseconds))) {
        Stop-Process -Id $cp.Id -Force -ErrorAction SilentlyContinue
        Write-Output 'WATCHDOG: client killed (timeout)'
    }
    if (-not $bp.WaitForExit([Math]::Max(1000, 120000 - $clock.ElapsedMilliseconds))) {
        Stop-Process -Id $bp.Id -Force -ErrorAction SilentlyContinue
        Write-Output 'WATCHDOG: blender killed after 120 s'
    }
}
finally {
    foreach ($p in $started) {
        if (-not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            $p.WaitForExit(5000) | Out-Null
        }
    }
    $alive = @($started | Where-Object { -not $_.HasExited }).Count
}

$clientExit = $cp.ExitCode
$resFile = Join-Path $Out 'client_results.json'
$res = if (Test-Path -LiteralPath $resFile) { Get-Content -LiteralPath $resFile -Raw | ConvertFrom-Json } else { $null }
$ok = if ($res) { [int]$res.passed } else { 0 }
$total = if ($res) { [int]$res.total } else { 0 }

# Blender output: count Traceback blocks, minus those that are the known av.libs DLL-lock PermissionError.
$logText = (Get-Content -LiteralPath (Join-Path $Out 'blender.stdout.txt') -Raw -ErrorAction SilentlyContinue) + "`n" +
    (Get-Content -LiteralPath (Join-Path $Out 'blender.stderr.txt') -Raw -ErrorAction SilentlyContinue)
$headers = ([regex]'(?m)^Traceback').Matches($logText).Count
$blocks = [regex]'(?m)^Traceback \(most recent call last\):\r?\n(?:[ \t]+.*\r?\n)*.*$'
$benign = @($blocks.Matches($logText) | Where-Object { $_.Value -match 'PermissionError' -and $_.Value -match 'av[\\.]libs' }).Count
$tracebacks = $headers - $benign

Select-String -LiteralPath (Join-Path $Out 'client.stdout.txt') -Pattern '^FAIL ' -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Output $_.Line }
Write-Output "client exit code $clientExit; blender exit code $($bp.ExitCode)"
Write-Output "Traceback in Blender output: $tracebacks (known av.libs PermissionError ignored: $benign)"
Write-Output "processes still alive from this run: $alive"
$pass = ($clientExit -eq 0) -and ($total -gt 0) -and ($ok -eq $total) -and ($tracebacks -eq 0)
Write-Output ("E2E {0}: {1}/{2} PASS{3}" -f $tag, $ok, $total, $(if ($pass) { '' } else { ' - FAILED' }))
exit ([int](-not $pass))
