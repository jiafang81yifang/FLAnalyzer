param(
    [string]$RepoRoot = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$script:Failures = 0
$script:Checks = 0

function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    $script:Checks = $script:Checks + 1
    if ($Ok) {
        Write-Host ("PASS " + $Name) -ForegroundColor Green
    } else {
        $script:Failures = $script:Failures + 1
        $msg = "FAIL " + $Name
        if ($Detail -ne '') { $msg = $msg + "  <- " + $Detail }
        Write-Host $msg -ForegroundColor Red
    }
}

function New-TempInstall {
    $root = Join-Path ([System.IO.Path]::GetTempPath()) ("bqtest-" + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $scripts = Join-Path $root '.venv\Scripts'
    New-Item -ItemType Directory -Path $scripts -Force | Out-Null
    # Stand-in for the venv interpreter: the wizard only checks that it exists.
    Set-Content -LiteralPath (Join-Path $scripts 'python.exe') -Value '' -Encoding ASCII
    return $root
}

function Invoke-Wizard {
    # Feeds $Lines on stdin; returns @{ Output; ExitCode }
    param([string]$WizardPath, [string[]]$Lines, [string]$Dir, [string]$Stub)
    $savedTg = $env:BLADE_CONFIG_TG_BASE
    $savedDs = $env:BLADE_CONFIG_DS_BASE
    if ($Stub -ne '') {
        $env:BLADE_CONFIG_TG_BASE = $Stub
        $env:BLADE_CONFIG_DS_BASE = $Stub
    }
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = 'pwsh'
        $argList = New-Object System.Collections.Generic.List[string]
        $argList.Add('-NoProfile')
        $argList.Add('-File')
        $argList.Add($WizardPath)
        if ($Dir -ne '') { $argList.Add('-InstallDir'); $argList.Add($Dir) }
        $psi.Arguments = ($argList | ForEach-Object { '"' + $_ + '"' }) -join ' '
        $psi.RedirectStandardInput = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.UseShellExecute = $false
        $psi.StandardInputEncoding = New-Object System.Text.UTF8Encoding($false)
        $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
        $psi.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
        $proc = New-Object System.Diagnostics.Process
        $proc.StartInfo = $psi
        [void]$proc.Start()
        $inputText = ($Lines -join "`n") + "`n"
        $proc.StandardInput.Write($inputText)
        $proc.StandardInput.Close()
        $stdout = $proc.StandardOutput.ReadToEnd()
        $stderr = $proc.StandardError.ReadToEnd()
        $proc.WaitForExit()
        return @{ Output = ($stdout + $stderr); ExitCode = $proc.ExitCode }
    } finally {
        $env:BLADE_CONFIG_TG_BASE = $savedTg
        $env:BLADE_CONFIG_DS_BASE = $savedDs
    }
}

function Invoke-WizardCheck {
    # Runs the wizard with --check (no stdin) and returns its output.
    param([string]$WizardPath, [string]$Dir)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'pwsh'
    $psi.Arguments = ('-NoProfile -File "' + $WizardPath + '" -InstallDir "' + $Dir + '" --check')
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $psi.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $proc.StandardInput.Close()
    $out = $proc.StandardOutput.ReadToEnd()
    $err = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    return @{ Output = ($out + $err); ExitCode = $proc.ExitCode }
}

function Get-ConfigBytes([string]$Path) { return [System.IO.File]::ReadAllBytes($Path) }

function Read-ConfigMap([string]$Path) {
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $map }
    $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    foreach ($line in ($text -split "`r?`n")) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $i = $t.IndexOf('=')
        if ($i -lt 1) { continue }
        $map[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
    }
    return $map
}

if ($RepoRoot -eq '') {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}
$Wizard = Join-Path $RepoRoot 'configure_blade_quant.ps1'
$StubPort = 8799
$Stub = 'http://127.0.0.1:' + [string]$StubPort

Write-Host ''
Write-Host '=== configure_blade_quant.ps1 offline tests ===' -ForegroundColor Cyan

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '-- 1. bogus token is rejected, nothing written, token never echoed' -ForegroundColor Cyan
$dir1 = New-TempInstall
$bogus = '123456789:AAbadTokenThatMustBeRejectedByStub'
$r = Invoke-Wizard -WizardPath $Wizard -Dir $dir1 -Stub $Stub -Lines @(
    $bogus,     # telegram: rejected by stub (401)
    '',         # telegram: empty -> skip
    '',         # deepseek: skip
    'y'         # proxy: leave unset
)
Check 'bogus-token: exit 0' ($r.ExitCode -eq 0) ("exit=" + $r.ExitCode)
Check 'bogus-token: reports invalid' ($r.Output -match 'token 无效或已被吊销') $r.Output
$cfg1 = Join-Path $dir1 'config.env'
Check 'bogus-token: config.env exists' (Test-Path -LiteralPath $cfg1)
$map1 = Read-ConfigMap $cfg1
Check 'bogus-token: token not saved' (-not $map1.ContainsKey('TELEGRAM_BOT_TOKEN')) ($map1.Keys -join ',')
Check 'bogus-token: token absent from console' (-not $r.Output.Contains($bogus))
Check 'bogus-token: no api.telegram.org URL printed' (-not ($r.Output -match 'api\.telegram\.org')) $r.Output

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '-- 2. network failure offers save-anyway and writes config.env' -ForegroundColor Cyan
$dir2 = New-TempInstall
$unverified = '987654321:AAFakeUnverifiedTokenValueForSaveAnywayPath'
$r = Invoke-Wizard -WizardPath $Wizard -Dir $dir2 -Stub 'http://127.0.0.1:9' -Lines @(
    $unverified,  # telegram: connection refused -> network path
    's',          # save anyway
    '',           # deepseek: skip
    'y'           # proxy: leave unset
)
Check 'save-anyway: exit 0' ($r.ExitCode -eq 0) ("exit=" + $r.ExitCode)
Check 'save-anyway: offered the s/r/k choice' ($r.Output -match 's/r/k') $r.Output
Check 'save-anyway: warned it is unverified' ($r.Output -match '未经校验') $r.Output
$map2 = Read-ConfigMap (Join-Path $dir2 'config.env')
Check 'save-anyway: token saved' ($map2['TELEGRAM_BOT_TOKEN'] -eq $unverified) ([string]$map2['TELEGRAM_BOT_TOKEN'])

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '-- 3. valid token prints username; deepseek accepted; proxy detected' -ForegroundColor Cyan
$dir3 = New-TempInstall
$goodTg = '555000111:AAFakeGoodTokenValueAcceptedByStub'
$goodDs = 'sk-good-test-key-abcdefghijklmnop'
$r = Invoke-Wizard -WizardPath $Wizard -Dir $dir3 -Stub $Stub -Lines @(
    $goodTg,
    $goodDs,
    'y',        # validate deepseek
    'y'         # proxy prompt (no live proxy -> leave unset)
)
Check 'good-token: exit 0' ($r.ExitCode -eq 0) ("exit=" + $r.ExitCode)
Check 'good-token: username printed' ($r.Output -match '@blade_test_bot') $r.Output
Check 'good-token: deepseek accepted' ($r.Output -match 'key 有效') $r.Output
$cfg3 = Join-Path $dir3 'config.env'
$map3 = Read-ConfigMap $cfg3
Check 'good-token: telegram saved' ($map3['TELEGRAM_BOT_TOKEN'] -eq $goodTg)
Check 'good-token: deepseek saved' ($map3['DEEPSEEK_API_KEY'] -eq $goodDs)
Check 'good-token: proxy key absent' (-not $map3.ContainsKey('OKOOO_PROXY')) ($map3.Keys -join ',')
Check 'good-token: secrets not echoed' (-not $r.Output.Contains($goodTg)) $r.Output
Check 'good-token: deepseek not echoed' (-not $r.Output.Contains($goodDs)) $r.Output
$bytes3 = Get-ConfigBytes $cfg3
$hasBom = $false
if ($bytes3.Length -ge 3 -and $bytes3[0] -eq 0xEF -and $bytes3[1] -eq 0xBB -and $bytes3[2] -eq 0xBF) { $hasBom = $true }
Check 'config.env: no BOM' (-not $hasBom)
$text3 = [System.IO.File]::ReadAllText($cfg3, [System.Text.Encoding]::UTF8)
Check 'config.env: CRLF line endings' ($text3.Contains("`r`n")) 
Check 'config.env: one KEY=VALUE per line' (($text3 -split "`r`n" | Where-Object { $_ -match '^[A-Z_]+=' }).Count -eq 2) $text3

# mask format: first 4 chars then asterisks
Check 'summary: masks token as first 4 chars' ($r.Output -match ([regex]::Escape($goodTg.Substring(0, 4)) + '\*+')) $r.Output

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '-- 4. idempotent re-run keeps values on Enter' -ForegroundColor Cyan
$r = Invoke-Wizard -WizardPath $Wizard -Dir $dir3 -Stub $Stub -Lines @(
    '',    # telegram: keep
    '',    # deepseek: keep
    'y'    # proxy: leave unset
)
Check 'rerun: exit 0' ($r.ExitCode -eq 0) ("exit=" + $r.ExitCode)
$map4 = Read-ConfigMap $cfg3
Check 'rerun: telegram preserved' ($map4['TELEGRAM_BOT_TOKEN'] -eq $goodTg)
Check 'rerun: deepseek preserved' ($map4['DEEPSEEK_API_KEY'] -eq $goodDs)
Check 'rerun: shows masked current value' ($r.Output -match '当前值') $r.Output

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '-- 5. --check is non-interactive and reports keys' -ForegroundColor Cyan
$checkRun = Invoke-WizardCheck -WizardPath $Wizard -Dir $dir3
$checkOut = $checkRun.Output
Check 'check: exit 0' ($checkRun.ExitCode -eq 0) ("exit=" + $checkRun.ExitCode)
Check 'check: reports telegram configured' ($checkOut -match 'TELEGRAM_BOT_TOKEN\s+已设置') $checkOut
Check 'check: reports deepseek configured' ($checkOut -match 'DEEPSEEK_API_KEY\s+已设置') $checkOut
Check 'check: reports proxy not set' ($checkOut -match 'OKOOO_PROXY\s+未设置') $checkOut
Check 'check: does not print secrets' (-not $checkOut.Contains($goodTg) -and -not $checkOut.Contains($goodDs)) $checkOut

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '-- 6. missing venv is refused with installer hint' -ForegroundColor Cyan
$dir6 = Join-Path ([System.IO.Path]::GetTempPath()) ("bqtest-" + [Guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $dir6 -Force | Out-Null
$r = Invoke-Wizard -WizardPath $Wizard -Dir $dir6 -Stub $Stub -Lines @('') 
Check 'missing-venv: non-zero exit' ($r.ExitCode -ne 0) ("exit=" + $r.ExitCode)
Check 'missing-venv: mentions installer' ($r.Output -match 'install_blade_quant') $r.Output
Check 'missing-venv: no config written' (-not (Test-Path -LiteralPath (Join-Path $dir6 'config.env')))

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '-- 7. --check on a dir with no config.env' -ForegroundColor Cyan
$dir7 = New-TempInstall
$run7 = Invoke-WizardCheck -WizardPath $Wizard -Dir $dir7
$out7 = $run7.Output
Check 'check-empty: exit 0' ($run7.ExitCode -eq 0) ("exit=" + $run7.ExitCode)
Check 'check-empty: says nothing configured' ($out7 -match '还没有配置|not created yet') $out7

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '-- 8. parsing tolerates CRLF, blank lines, comments, quoted values' -ForegroundColor Cyan
$dir8 = New-TempInstall
$cfg8 = Join-Path $dir8 'config.env'
$crs = @(
    '# leading comment',
    '',
    '   ',
    'TELEGRAM_BOT_TOKEN="888777666:QuotedTokenValueFromFixtures"',
    '',
    'DEEPSEEK_API_KEY=''sk-good-quoted-single''',
    '# trailing comment',
    'OKOOO_PROXY="http://127.0.0.1:7890"',
    ''
)
[System.IO.File]::WriteAllText($cfg8, ($crs -join "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
$run8 = Invoke-WizardCheck -WizardPath $Wizard -Dir $dir8
$out8 = $run8.Output
Check 'parse-quirks: telegram read' ($out8 -match 'TELEGRAM_BOT_TOKEN\s+已设置') $out8
Check 'parse-quirks: deepseek read' ($out8 -match 'DEEPSEEK_API_KEY\s+已设置') $out8
Check 'parse-quirks: proxy read verbatim' ($out8 -match 'OKOOO_PROXY.*http://127\.0\.0\.1:7890') $out8
$r = Invoke-Wizard -WizardPath $Wizard -Dir $dir8 -Stub $Stub -Lines @('', '', 'y')
Check 'parse-quirks: rewrite exit 0' ($r.ExitCode -eq 0) ("exit=" + $r.ExitCode)
$map8 = Read-ConfigMap $cfg8
Check 'parse-quirks: quotes stripped on read' ($map8['TELEGRAM_BOT_TOKEN'] -eq '888777666:QuotedTokenValueFromFixtures') ([string]$map8['TELEGRAM_BOT_TOKEN'])
Check 'parse-quirks: single quotes stripped' ($map8['DEEPSEEK_API_KEY'] -eq 'sk-good-quoted-single') ([string]$map8['DEEPSEEK_API_KEY'])
Check 'parse-quirks: proxy preserved' ($map8['OKOOO_PROXY'] -eq 'http://127.0.0.1:7890') ([string]$map8['OKOOO_PROXY'])

# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '-- 9. invalid deepseek key re-prompts and is not saved' -ForegroundColor Cyan
$dir9 = New-TempInstall
$r = Invoke-Wizard -WizardPath $Wizard -Dir $dir9 -Stub $Stub -Lines @(
    '',                  # telegram skip
    'sk-bad-rejected',   # deepseek rejected
    'y',                 # validate
    '',                  # key prompt again -> empty = skip
    'y'                  # proxy unset
)
Check 'bad-deepseek: exit 0' ($r.ExitCode -eq 0) ("exit=" + $r.ExitCode)
Check 'bad-deepseek: reported rejection' ($r.Output -match 'DeepSeek 拒绝了这个 key') $r.Output
$map9 = Read-ConfigMap (Join-Path $dir9 'config.env')
Check 'bad-deepseek: not saved' (-not $map9.ContainsKey('DEEPSEEK_API_KEY')) ($map9.Keys -join ',')

Write-Host ''
Write-Host ("TOTAL " + $script:Checks + "  FAIL " + $script:Failures) -ForegroundColor Cyan
if ($script:Failures -gt 0) { exit 1 }
exit 0
