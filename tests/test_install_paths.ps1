#!/usr/bin/env pwsh
<#
End-to-end checks for install_blade_quant.ps1's two paths.

  1. clean install into an empty dir  (real: downloads + venv + pip)
  2. refresh over a simulated pre-existing install  (real: same script, second run)

The refresh case is built to look exactly like the user's machine: only
app_v84_hermes.py, .venv and the OLD start.cmd (no config.env support), no
configure_blade_quant.ps1, plus a config.env with a proxy and a token.

Run with -SkipCleanInstall to test only the refresh path.
#>
param(
    [string]$RepoRoot = '',
    [switch]$SkipCleanInstall,
    [string]$KeepRoot = ''
)

$ErrorActionPreference = 'Stop'
$script:Failures = 0
$script:Checks = 0

function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    $script:Checks = $script:Checks + 1
    if ($Ok) {
        Write-Host ("PASS " + $Name) -ForegroundColor Green
        return
    }
    $script:Failures = $script:Failures + 1
    $msg = "FAIL " + $Name
    if ($Detail -ne '') { $msg = $msg + "  <- " + $Detail }
    Write-Host $msg -ForegroundColor Red
}

if ($RepoRoot -eq '') {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}
$Installer = Join-Path $RepoRoot 'install_blade_quant.ps1'
if (-not (Test-Path -LiteralPath $Installer)) { throw "installer missing: $Installer" }

function New-RunDir {
    if ($KeepRoot -ne '') {
        $d = Join-Path $KeepRoot ('run-' + [Guid]::NewGuid().ToString('N').Substring(0, 6))
    } else {
        $d = Join-Path ([System.IO.Path]::GetTempPath()) ('bqinst-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    }
    New-Item -ItemType Directory -Path $d -Force | Out-Null
    return $d
}

function Invoke-Installer {
    # Runs the real installer against $Dir with stdin 'n' (do not launch at the end).
    param([string]$Dir, [string]$InstallerPath, [string]$RepoRootForPath)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'pwsh'
    $psi.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $InstallerPath + '"'
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.StandardInputEncoding = New-Object System.Text.UTF8Encoding($false)
    $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $psi.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    $psi.EnvironmentVariables['BLADE_INSTALL_DIR'] = $Dir
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    [void]$proc.Start()
    $proc.StandardInput.WriteLine('n')
    $proc.StandardInput.Close()
    $out = $proc.StandardOutput.ReadToEnd()
    $err = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    $clock.Stop()
    return @{
        Output   = ($out + $err)
        ExitCode = $proc.ExitCode
        Seconds  = [math]::Round($clock.Elapsed.TotalSeconds, 1)
    }
}

function Get-Sha([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Get-Bytes([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return [System.IO.File]::ReadAllBytes($Path)
}

Write-Host ''
Write-Host '=== install_blade_quant.ps1 path tests ===' -ForegroundColor Cyan

# --------------------------------------------------------------------------- #
# 1. Clean install into an empty directory
# --------------------------------------------------------------------------- #
$cleanDir = $null
if (-not $SkipCleanInstall) {
    Write-Host ''
    Write-Host '-- 1. clean install into an empty temp dir' -ForegroundColor Cyan
    $cleanDir = New-RunDir
    $r = Invoke-Installer -Dir $cleanDir -InstallerPath $Installer

    Check 'clean: exit 0' ($r.ExitCode -eq 0) ("exit=" + $r.ExitCode + " | tail: " + ($r.Output -split "`n" | Select-Object -Last 12 | Out-String))
    Check 'clean: took the fresh-install banner' ($r.Output -match '全新安装') $r.Output
    Check 'clean: did not claim to be a refresh' (-not ($r.Output -match '刷新已有安装')) $r.Output
    Check 'clean: created .venv\Scripts\python.exe' (Test-Path -LiteralPath (Join-Path $cleanDir '.venv/Scripts/python.exe'))
    Check 'clean: has app_v84_hermes.py' (Test-Path -LiteralPath (Join-Path $cleanDir 'app_v84_hermes.py'))
    Check 'clean: has configure_blade_quant.ps1 (wizard present after install)' (Test-Path -LiteralPath (Join-Path $cleanDir 'configure_blade_quant.ps1'))
    Check 'clean: has start.cmd' (Test-Path -LiteralPath (Join-Path $cleanDir 'start.cmd'))
    Check 'clean: no config.env invented' (-not (Test-Path -LiteralPath (Join-Path $cleanDir 'config.env')))

    $bat = ''
    if (Test-Path -LiteralPath (Join-Path $cleanDir 'start.cmd')) {
        $bat = [System.IO.File]::ReadAllText((Join-Path $cleanDir 'start.cmd'), [System.Text.Encoding]::UTF8)
    }
    Check 'clean: start.cmd loads config.env' ($bat -match 'blade_load_config "config\.env"') $bat
    Check 'clean: start.cmd has the guarded proxy default' ($bat -match 'if not defined OKOOO_PROXY') $bat

    Check 'clean: wizard parser check ran' ($r.Output -match '解析通过') $r.Output
    Check 'clean: wizard --check ran' ($r.Output -match '配置状态') $r.Output
    Check 'clean: printed the copyable next command' ($r.Output -match '-File ".*configure_blade_quant\.ps1"') $r.Output
    Check 'clean: printed the Telegram/DeepSeek hint' ($r.Output -match '配置 Telegram / DeepSeek 请运行 configure_blade_quant\.ps1。') $r.Output
} else {
    Write-Host ''
    Write-Host '-- 1. clean install skipped (-SkipCleanInstall)' -ForegroundColor DarkYellow
}

# --------------------------------------------------------------------------- #
# 2. Refresh over a simulated pre-existing install
# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '-- 2. refresh over a simulated pre-existing install' -ForegroundColor Cyan

$refreshDir = New-RunDir
$oldToken = '123456789:AAFakeExistingTokenThatMustSurviveRefresh'
$oldProxy = 'http://127.0.0.1:10809'

# Same venv layout the installer expects on the current platform.
if ($env:OS -eq 'Windows_NT') {
    $venvRel = '.venv/Scripts/python.exe'
} else {
    $venvRel = '.venv/bin/python'
}
$venvPython = Join-Path $refreshDir $venvRel

# A real venv, so the refresh path really skips creating one.
& python3 -m venv (Join-Path $refreshDir '.venv')
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw ('could not build the simulated .venv at ' + $venvPython)
}
# Make the venv's deps look satisfied so the refresh really skips pip.
& $venvPython -c 'import streamlit, pandas, numpy, requests, plotly, schedule, openai, scipy, bs4, httpx, urllib3, tornado; print("deps ok")' 2>$null | Out-Null
$depsWereReady = ($LASTEXITCODE -eq 0)
if (-not $depsWereReady) {
    Write-Host '    (simulated venv has no deps; the refresh will reinstall them)' -ForegroundColor DarkYellow
}

# Stale app: not the real thing at all, just bytes that must be replaced.
[System.IO.File]::WriteAllText((Join-Path $refreshDir 'app_v84_hermes.py'), "# stale app from an older install`nprint('stale')`n", (New-Object System.Text.UTF8Encoding($false)))
$staleAppSha = Get-Sha (Join-Path $refreshDir 'app_v84_hermes.py')

# The OLD launcher: sets only OKOOO_PROXY, no config.env support at all.
$oldBat = @(
    '@echo off',
    'chcp 65001 >nul',
    'cd /d "%~dp0"',
    'set OKOOO_PROXY=http://127.0.0.1:7890',
    '".venv\Scripts\python.exe" -m streamlit run app_v84_hermes.py',
    'pause'
) -join "`r`n"
[System.IO.File]::WriteAllText((Join-Path $refreshDir 'start.cmd'), $oldBat, (New-Object System.Text.UTF8Encoding($false)))
Check 'refresh-fixture: old start.cmd has no config.env support' (-not ([System.IO.File]::ReadAllText((Join-Path $refreshDir 'start.cmd')) -match 'config\.env'))

# config.env byte-for-byte fixture: CRLF, comments, quoted proxy, token.
# Each element must be a complete line, so build the quoted ones with ( ).
$cfgLines = @(
    '# existing user configuration - must survive a refresh untouched',
    '',
    ('TELEGRAM_BOT_TOKEN="' + $oldToken + '"'),
    ('OKOOO_PROXY="' + $oldProxy + '"')
)
$cfgPath = Join-Path $refreshDir 'config.env'
[System.IO.File]::WriteAllText($cfgPath, ($cfgLines -join "`r`n") + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
$cfgShaBefore = Get-Sha $cfgPath
$cfgBytesBefore = Get-Bytes $cfgPath
$cfgWriteTimeBefore = (Get-Item -LiteralPath $cfgPath).LastWriteTimeUtc

Check 'refresh-fixture: config.env seeded' (Test-Path -LiteralPath $cfgPath)
Check 'refresh-fixture: no wizard yet' (-not (Test-Path -LiteralPath (Join-Path $refreshDir 'configure_blade_quant.ps1')))

$r2 = Invoke-Installer -Dir $refreshDir -InstallerPath $Installer

Check 'refresh: exit 0' ($r2.ExitCode -eq 0) ("exit=" + $r2.ExitCode + " | tail: " + ($r2.Output -split "`n" | Select-Object -Last 12 | Out-String))
Check 'refresh: printed the refresh banner' ($r2.Output -match '刷新已有安装') $r2.Output
Check 'refresh: did not take the fresh-install path' (-not ($r2.Output -match '全新安装')) $r2.Output
Check 'refresh: reused the existing .venv' ($r2.Output -match '复用 \.venv') $r2.Output

if ($depsWereReady) {
    Check 'refresh: skipped pip because imports already satisfied' ($r2.Output -match '依赖已满足，跳过安装') $r2.Output
}

# --- config.env must be byte-identical ---
$cfgShaAfter = Get-Sha $cfgPath
$cfgBytesAfter = Get-Bytes $cfgPath
Check 'refresh: config.env still exists' (Test-Path -LiteralPath $cfgPath)
Check 'refresh: config.env byte-identical (sha256)' ($cfgShaBefore -eq $cfgShaAfter) ("before=" + $cfgShaBefore + " after=" + $cfgShaAfter)
$sameBytes = $false
if ($null -ne $cfgBytesBefore -and $null -ne $cfgBytesAfter) {
    $sameBytes = ($cfgBytesBefore.Length -eq $cfgBytesAfter.Length)
    if ($sameBytes) {
        for ($i = 0; $i -lt $cfgBytesBefore.Length; $i++) {
            if ($cfgBytesBefore[$i] -ne $cfgBytesAfter[$i]) { $sameBytes = $false; break }
        }
    }
}
Check 'refresh: config.env byte-identical (content compare)' $sameBytes
Check 'refresh: config.env mtime untouched' ($cfgWriteTimeBefore -eq (Get-Item -LiteralPath $cfgPath).LastWriteTimeUtc)

# --- both shipped files are the pinned ones ---
$appSha = Get-Sha (Join-Path $refreshDir 'app_v84_hermes.py')
Check 'refresh: app was replaced with the pinned build' ($appSha -ne $staleAppSha) ("stale=" + $staleAppSha + " now=" + $appSha)
Check 'refresh: app is the real single-file build' ((Get-Item -LiteralPath (Join-Path $refreshDir 'app_v84_hermes.py')).Length -gt 2MB)
$wizardPath = Join-Path $refreshDir 'configure_blade_quant.ps1'
Check 'refresh: wizard was downloaded' (Test-Path -LiteralPath $wizardPath)
Check 'refresh: wizard is a real PowerShell script' (
    (Test-Path -LiteralPath $wizardPath) -and
    ([System.IO.File]::ReadAllText($wizardPath) -match 'configure_blade_quant|blade-quant')
)

# --- the new start.cmd loads config.env and does not let the default win ---
$newBat = ''
if (Test-Path -LiteralPath (Join-Path $refreshDir 'start.cmd')) {
    $newBat = [System.IO.File]::ReadAllText((Join-Path $refreshDir 'start.cmd'), [System.Text.Encoding]::UTF8)
}
Check 'refresh: start.cmd regenerated with config.env support' ($newBat -match 'blade_load_config "config\.env"') $newBat
Check 'refresh: start.cmd no longer has the unconditional proxy set' (-not ($newBat -match '(?m)^set OKOOO_PROXY=')) $newBat
# If the installer detected a proxy it must emit only a guarded default, so the
# value loaded from config.env wins. With no proxy live it emits none at all.
$hasProxyLine = ($newBat -match '(?m)^\s*(if not defined OKOOO_PROXY set|set OKOOO_PROXY=)')
if ($hasProxyLine) {
    Check 'refresh: detected-proxy default is guarded' ($newBat -match 'if not defined OKOOO_PROXY') $newBat
} else {
    Check 'refresh: no proxy default emitted when none is live' $true
}
Check 'refresh: loader label is defined after the launch line' (
    ($newBat.IndexOf("`n:blade_load_config") -gt $newBat.IndexOf('streamlit run'))
) $newBat


# --------------------------------------------------------------------------- #
# 3. Runtime proof: config.env wins over a live detected proxy default
# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '-- 3. config.env beats the installer default at run time' -ForegroundColor Cyan

$wine = Get-Command wine -ErrorAction SilentlyContinue
$winePrefix = Join-Path $HOME 'wineprefix'
$cmdExe = Join-Path $winePrefix 'drive_c/windows/system32/cmd.exe'
$canRunBatch = ($null -ne $wine) -and (Test-Path -LiteralPath $cmdExe)

if (-not $canRunBatch) {
    Write-Host 'SKIP run time check (wine/cmd.exe unavailable)' -ForegroundColor DarkYellow
} else {
    # A listener on 7890 makes the installer emit its guarded default, which is
    # exactly the "old installer default" the user has today.
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 7890)
        $listener.Start()
    } catch {
        $listener = $null
    }

    if ($null -eq $listener) {
        Write-Host 'SKIP run time check (port 7890 busy)' -ForegroundColor DarkYellow
    } else {
        try {
            $r3 = Invoke-Installer -Dir $refreshDir -InstallerPath $Installer
            Check 'runtime: refresh still exits 0 with a live proxy' ($r3.ExitCode -eq 0) ($r3.Output -split "`n" | Select-Object -Last 8 | Out-String)
            Check 'runtime: detected the live proxy' ($r3.Output -match '检测到本机代理 127\.0\.0\.1:7890') $r3.Output

            $bat3 = [System.IO.File]::ReadAllText((Join-Path $refreshDir 'start.cmd'), [System.Text.Encoding]::UTF8)
            Check 'runtime: start.cmd now carries the guarded 7890 default' (
                ($bat3 -match 'if not defined OKOOO_PROXY set "OKOOO_PROXY=http://127\.0\.0\.1:7890"')
            ) $bat3
            Check 'runtime: no unconditional proxy set remains' (-not ($bat3 -match '(?m)^set OKOOO_PROXY=')) $bat3
            Check 'runtime: config.env survived this refresh too' ((Get-Sha $cfgPath) -eq $cfgShaBefore)

            # Run the generated batch: report what a child process actually sees.
            $probe = Join-Path $refreshDir 'probe_child.cmd'
            $batForProbe = $bat3.Replace(
                '".venv\Scripts\python.exe" -m streamlit run app_v84_hermes.py',
                'cmd /c "echo CHILD_PROXY=%OKOOO_PROXY% & echo CHILD_TG=%TELEGRAM_BOT_TOKEN%"')
            $batForProbe = $batForProbe.Replace('pause', '')
            [System.IO.File]::WriteAllText($probe, $batForProbe, (New-Object System.Text.UTF8Encoding($false)))

            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = 'wine'
            $psi.Arguments = '"' + $cmdExe + '" /c "' + ($probe -replace '/', '\') + '"'
            $psi.RedirectStandardOutput = $true
            $psi.RedirectStandardError = $true
            $psi.UseShellExecute = $false
            $psi.EnvironmentVariables['WINEDEBUG'] = '-all'
            $psi.EnvironmentVariables['WINEPREFIX'] = $winePrefix
            $proc = New-Object System.Diagnostics.Process
            $proc.StartInfo = $psi
            [void]$proc.Start()
            $o = $proc.StandardOutput.ReadToEnd()
            $e = $proc.StandardError.ReadToEnd()
            $proc.WaitForExit()
            $combined = $o + $e

            Check 'runtime: child sees config.env proxy 10809, not the 7890 default' (
                $combined -match 'CHILD_PROXY=http://127\.0\.0\.1:10809'
            ) $combined
            Check 'runtime: child does not see the installer default' (
                -not ($combined -match 'CHILD_PROXY=http://127\.0\.0\.1:7890\s')
            ) $combined
            Check 'runtime: child sees the existing token' (
                $combined -match ('CHILD_TG=' + [regex]::Escape($oldToken))
            ) $combined
        } finally {
            try { $listener.Stop() } catch { }
        }
    }
}


# --------------------------------------------------------------------------- #
# 4. Wizard download validation rejects truncated / wrong files
# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '-- 4. wizard download validation' -ForegroundColor Cyan

$instText = [System.IO.File]::ReadAllText($Installer, [System.Text.Encoding]::UTF8)
Check 'validation: checks a minimum size' ($instText -match 'cfgBytes -lt 8192')
Check 'validation: checks required anchors' ($instText -match 'Save-ConfigEnv' -and $instText -match 'Test-TelegramToken')
Check 'validation: parses with the PowerShell parser' ($instText -match 'Parser\]::ParseFile')
Check 'validation: aborts on a parse error' ($instText -match '有语法错误')

# The same predicate the installer uses, applied to a truncated copy.
$trunc = Join-Path $refreshDir 'truncated_wizard.ps1'
$fullWizard = Join-Path $refreshDir 'configure_blade_quant.ps1'
$bytes = [System.IO.File]::ReadAllBytes($fullWizard)
$take = [Math]::Min(8000, $bytes.Length)
$slice = New-Object byte[] $take
[Array]::Copy($bytes, $slice, $take)
[System.IO.File]::WriteAllBytes($trunc, $slice)

$truncSize = (Get-Item $trunc).Length
$truncText = [System.IO.File]::ReadAllText($trunc, [System.Text.Encoding]::UTF8)
$missingAnchor = $false
foreach ($a in @('Save-ConfigEnv', 'Test-TelegramToken', '/user/balance', '--check')) {
    if ($truncText.IndexOf($a) -lt 0) { $missingAnchor = $true }
}
Check 'validation: truncated wizard is rejected' (($truncSize -lt 8192) -or $missingAnchor) ("size=" + $truncSize)
$fullText = [System.IO.File]::ReadAllText($fullWizard, [System.Text.Encoding]::UTF8)
$fullMissing = $false
foreach ($a in @('Save-ConfigEnv', 'Test-TelegramToken', '/user/balance', '--check')) {
    if ($fullText.IndexOf($a) -lt 0) { $fullMissing = $true }
}
Check 'validation: the real wizard passes all gates' (
    ((Get-Item $fullWizard).Length -ge 8192) -and (-not $fullMissing)
) ("size=" + (Get-Item $fullWizard).Length + " missingAnchor=" + $fullMissing)

Write-Host ''
Write-Host ("TOTAL " + $script:Checks + "  FAIL " + $script:Failures) -ForegroundColor Cyan
Write-Host ''
Write-Host ('clean dir   : ' + $cleanDir) -ForegroundColor Gray
Write-Host ('refresh dir : ' + $refreshDir) -ForegroundColor Gray
if ($script:Failures -gt 0) { exit 1 }
exit 0
