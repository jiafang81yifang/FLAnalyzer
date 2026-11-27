<#
FLAnalyzer - blade-quant 配置向导（Windows PowerShell 5.1 兼容）

作用：
  一次一个问题地配置可选项，不需要 setx，不需要手工编辑文件，不需要管理员权限：
    * Telegram 机器人 token（会真的调用 getMe 校验）
    * DeepSeek API key（可选在线校验）
    * 本机代理（自动探测 127.0.0.1 的 7890 / 7897 / 10809 / 1080）
  配置写入安装目录下的 config.env（严格 UTF-8 无 BOM，每行一个 KEY=VALUE）。

用法（复制整行到 PowerShell，回车即可）：
  powershell -NoProfile -ExecutionPolicy Bypass -File "C:\DevProjects\blade-quant\configure_blade_quant.ps1"

只看状态、不提问：
  powershell -NoProfile -ExecutionPolicy Bypass -File "C:\DevProjects\blade-quant\configure_blade_quant.ps1" --check

指定安装目录：
  powershell -NoProfile -ExecutionPolicy Bypass -File .\configure_blade_quant.ps1 -InstallDir "D:\blade-quant"

安全约定：
  * 密钥只写入 config.env（已被 .gitignore 忽略），不回显、不写日志、不写其它文件。
  * 绝不打印请求 URL（Telegram 的 URL 里带着 token）。
  * 本文件必须以 UTF-8 + BOM 保存：Windows PowerShell 5.1 读取无 BOM 的 UTF-8
    时会按 ANSI/GBK 解码，中文会乱码。脚本正文（代码与注释）全部是 ASCII，
    只有 Write-Host / Read-Host 的提示文字是中文。
    #>

param(
    [switch]$Check,
    [string]$InstallDir = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$ScriptSelf = ''
try { $ScriptSelf = [string]$PSCommandPath } catch { $ScriptSelf = '' }
if ($ScriptSelf -eq '') {
    try { $ScriptSelf = [string]$MyInvocation.MyCommand.Path } catch { $ScriptSelf = '' }
}

# Accept "--check" / "-check" / "/check": a bare double-dash flag is not bound
# to the switch when the script is invoked through "iex" or "&".
foreach ($unbound in $args) {
    $token = [string]$unbound
    if ($token -eq '--check' -or $token -eq '-check' -or $token -eq '/check') { $Check = $true }
}
if ($InstallDir -eq '--check' -or $InstallDir -eq '-check' -or $InstallDir -eq '/check') {
    $Check = $true
    $InstallDir = ''
}

# Windows PowerShell 5.1 does not enable TLS 1.2 by default, so a direct
# https call to api.telegram.org fails before the request is even sent.
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch { }

$RelPython   = '.venv\Scripts\python.exe'
$ConfigName  = 'config.env'
$ProxyPorts  = @(7890, 7897, 10809, 1080)
$KeyTelegram = 'TELEGRAM_BOT_TOKEN'
$KeyDeepSeek = 'DEEPSEEK_API_KEY'
$KeyProxy    = 'OKOOO_PROXY'
$KeyOrder    = @($KeyTelegram, $KeyDeepSeek, $KeyProxy)
$MaskTail    = '********'

# Test-only overrides; leave these alone in normal use.
$TelegramBase = 'https://api.telegram.org'
$DeepSeekBase = 'https://api.deepseek.com'
if ($env:BLADE_CONFIG_TG_BASE) { $TelegramBase = ([string]$env:BLADE_CONFIG_TG_BASE).TrimEnd('/') }
if ($env:BLADE_CONFIG_DS_BASE) { $DeepSeekBase = ([string]$env:BLADE_CONFIG_DS_BASE).TrimEnd('/') }

# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #
function Write-Step([string]$msg) { Write-Host ''; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg) { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "    !   $msg" -ForegroundColor Yellow }
function Write-Note([string]$msg) { Write-Host "    $msg" -ForegroundColor Gray }

function Format-MaskedValue([string]$Value) {
    if ([string]::IsNullOrEmpty($Value)) { return '(未设置)' }
    if ($Value.Length -le 4) { return $MaskTail }
    return ($Value.Substring(0, 4) + $MaskTail)
}

function Join-PathSafe([string]$Dir, [string]$Child) {
    # Join-Path throws on paths whose "drive" is unknown (and on malformed input
    # typed by the user). Fall back to plain concatenation so a bad path can
    # never crash the wizard with an unhandled error.
    if ([string]::IsNullOrWhiteSpace($Dir)) { return $Child }
    try {
        return (Join-Path $Dir $Child)
    } catch {
        $trimmed = $Dir.TrimEnd('\', '/')
        return ($trimmed + '\' + $Child)
    }
}

function Test-LocalPort([int]$Port, [int]$TimeoutMs = 300) {
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        $ready = $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if ($ready -and $client.Connected) { return $true }
        return $false
    } catch {
        return $false
    } finally {
        if ($null -ne $client) {
            try { $client.Close() } catch { }
        }
    }
}

function Find-LiveProxyPort {
    foreach ($port in $ProxyPorts) {
        if (Test-LocalPort -Port $Port -TimeoutMs 300) { return $Port }
    }
    return 0
}

function Read-SecretInput([string]$Prompt) {
    # Secret input. Never echoes, on any path:
    #   * real console -> Read-Host -AsSecureString (masked)
    #   * redirected stdin -> Console.In.ReadLine(); Read-Host echoes what it read
    #   * no SecureString support (ISE, ...) -> Console.ReadKey, char by char
    $redirected = $false
    try { $redirected = [Console]::IsInputRedirected } catch { $redirected = $false }

    if ($redirected) {
        Write-Host ($Prompt + ': ') -NoNewline
        $line = ''
        try { $line = [Console]::In.ReadLine() } catch { $line = '' }
        Write-Host ''
        if ($null -eq $line) { return '' }
        return [string]$line
    }

    try {
        $secure = Read-Host -Prompt $Prompt -AsSecureString
        if ($null -eq $secure) { return '' }
        $cred = New-Object System.Net.NetworkCredential('', $secure)
        if ($null -eq $cred.Password) { return '' }
        return [string]$cred.Password
    } catch {
        # Fall through to the character-by-character read below.
    }

    Write-Host ($Prompt + ': ') -NoNewline
    $buffer = New-Object System.Text.StringBuilder
    try {
        while ($true) {
            $key = [Console]::ReadKey($true)
            if ($key.Key -eq [ConsoleKey]::Enter) { break }
            if ($key.Key -eq [ConsoleKey]::Escape) { $buffer.Length = 0; break }
            if ($key.Key -eq [ConsoleKey]::Backspace) {
                if ($buffer.Length -gt 0) { [void]$buffer.Remove($buffer.Length - 1, 1) }
                continue
            }
            [void]$buffer.Append($key.KeyChar)
        }
        Write-Host ''
        return $buffer.ToString()
    } catch {
        # No usable console after all: fall back to a plain read so the wizard
        # still works instead of dying on the secret prompt.
        Write-Host ''
        $plain = Read-Host -Prompt $Prompt
        if ($null -eq $plain) { return '' }
        return [string]$plain
    }
}

function Read-Choice([string]$Prompt, [string[]]$Valid) {
    # Input must hit one of $Valid; empty input (including EOF) picks the first
    # entry so automation can never hang here.
    while ($true) {
        $answer = Read-Host -Prompt $Prompt
        if ($null -eq $answer) { return $Valid[0] }
        $answer = ([string]$answer).Trim().ToLower()
        if ($answer -eq '') { return $Valid[0] }
        foreach ($candidate in $Valid) {
            if ($answer -eq $candidate) { return $candidate }
        }
        Write-Note ('请回答：' + ($Valid -join ' / '))
    }
}

function Read-YesNo([string]$Prompt, [bool]$Default = $true) {
    if ($Default) {
        $choice = Read-Choice -Prompt "$Prompt (Y/n)" -Valid @('y', 'n')
    } else {
        $choice = Read-Choice -Prompt "$Prompt (y/N)" -Valid @('n', 'y')
    }
    if ($choice -eq 'y') { return $true }
    return $false
}

# --------------------------------------------------------------------------- #
# config.env reading and writing
# --------------------------------------------------------------------------- #
function Read-ConfigEnv([string]$Path) {
    $state = @{
        Exists = $false
        Path   = $Path
        Values = New-Object System.Collections.Specialized.OrderedDictionary
    }
    if (-not (Test-DirPath -Dir $Path)) { return $state }
    $state.Exists = $true

    $text = ''
    try {
        $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    } catch {
        return $state
    }
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) {
        $text = $text.Substring(1)
    }

    foreach ($line in ($text -split "`r?`n")) {
        $trimmed = ([string]$line).Trim()
        if ($trimmed -eq '') { continue }
        if ($trimmed.StartsWith('#')) { continue }
        $eq = $trimmed.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $trimmed.Substring(0, $eq).Trim()
        $value = $trimmed.Substring($eq + 1).Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        if ($key -match '^[A-Za-z_][A-Za-z0-9_]*$') {
            $state.Values[$key] = $value
        }
    }
    return $state
}

function Save-ConfigEnv([string]$Path, $Values) {
# Strict UTF-8 without BOM, CRLF endings, one KEY=VALUE per line; empty values
# are skipped so the file only ever holds keys the user actually set.
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('# blade-quant configuration - generated by configure_blade_quant.ps1')
    $lines.Add('# This file holds credentials. Keep it private and never commit it.')
    foreach ($key in $KeyOrder) {
        if (-not $Values.Contains($key)) { continue }
        $value = [string]$Values[$key]
        if ([string]::IsNullOrEmpty($value)) { continue }
        $lines.Add($key + '=' + $value)
    }
    foreach ($key in $Values.Keys) {
        if ($KeyOrder -contains $key) { continue }
        $value = [string]$Values[$key]
        if ([string]::IsNullOrEmpty($value)) { continue }
        $lines.Add($key + '=' + $value)
    }

    $text = ($lines -join "`r`n") + "`r`n"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $text, $encoding)
    return $lines.Count
}

# --------------------------------------------------------------------------- #
# HTTP probing. No URL is ever printed: the Telegram URL embeds the token.
# --------------------------------------------------------------------------- #
function Invoke-JsonGet([string]$Url, [string]$ProxyUrl = '', [string]$Bearer = '', [int]$TimeoutSec = 12) {
    $result = @{ Outcome = 'network'; HttpStatus = 0; Body = ''; Reason = 'unknown' }

    $request = $null
    try {
        $request = [System.Net.WebRequest]::Create($Url)
    } catch {
        $result.Reason = 'bad_url'
        return $result
    }

    try {
        $request.Method = 'GET'
        $request.Timeout = $TimeoutSec * 1000
        $request.ReadWriteTimeout = $TimeoutSec * 1000
        $request.UserAgent = 'blade-quant-config/1.0'
        if ($ProxyUrl -ne '') {
            $request.Proxy = New-Object System.Net.WebProxy($ProxyUrl)
        } else {
            $request.Proxy = $null
        }
        if ($Bearer -ne '') {
            $request.Headers.Add('Authorization', 'Bearer ' + $Bearer)
        }
    } catch {
        $result.Reason = 'request_setup'
        return $result
    }

    $response = $null
    $reader = $null
    try {
        $response = $request.GetResponse()
        $result.HttpStatus = [int]$response.StatusCode
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        $result.Body = $reader.ReadToEnd()
        $result.Outcome = 'http'
        return $result
    } catch [System.Net.WebException] {
        $web = $_.Exception
        if ($null -ne $web.Response) {
            try { $result.HttpStatus = [int]$web.Response.StatusCode } catch { $result.HttpStatus = 0 }
            try {
                $reader = New-Object System.IO.StreamReader($web.Response.GetResponseStream())
                $result.Body = $reader.ReadToEnd()
            } catch { }
            $result.Outcome = 'http'
            return $result
        }
        $status = ''
        try { $status = [string]$web.Status } catch { $status = '' }
        if ($status -eq 'Timeout') { $result.Reason = 'timeout' }
        elseif ($status -eq 'ConnectFailure') { $result.Reason = 'connect_failed' }
        elseif ($status -eq 'NameResolutionFailure') { $result.Reason = 'dns_failed' }
        elseif ($status -eq 'ProxyNameResolutionFailure') { $result.Reason = 'proxy_dns_failed' }
        elseif ($status -eq 'TrustFailure') { $result.Reason = 'tls_failed' }
        elseif ($status -eq 'SecureChannelFailure') { $result.Reason = 'tls_failed' }
        else { $result.Reason = 'network' }
        $result.Outcome = 'network'
        return $result
    } catch {
        $result.Outcome = 'network'
        $result.Reason = 'network'
        return $result
    } finally {
        if ($null -ne $reader) { try { $reader.Close() } catch { } }
        if ($null -ne $response) { try { $response.Close() } catch { } }
    }
}

function ConvertFrom-JsonSafe([string]$Text) {
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    try { return ($Text | ConvertFrom-Json) } catch { return $null }
}

function Get-ProxyCandidates([int]$Port) {
# Try the detected local proxy first, then a direct connection, so a dead
# Clash port cannot stall the check.
    $list = New-Object System.Collections.Generic.List[string]
    if ($Port -gt 0) { $list.Add('http://127.0.0.1:' + [string]$Port) }
    $list.Add('')
    return $list
}

function Test-TelegramToken([string]$Token, [int]$ProxyPort = 0) {
    $result = @{ Status = 'network'; Username = ''; HttpStatus = 0; Reason = 'unknown' }
    if ($Token -eq '') { $result.Reason = 'empty'; return $result }
    $url = $TelegramBase + '/bot' + $Token + '/getMe'

    foreach ($proxy in (Get-ProxyCandidates -Port $ProxyPort)) {
        $raw = Invoke-JsonGet -Url $url -ProxyUrl $proxy -TimeoutSec 12
        if ($raw.Outcome -eq 'http') {
            $result.HttpStatus = $raw.HttpStatus
            if ($raw.HttpStatus -eq 200) {
                $payload = ConvertFrom-JsonSafe -Text $raw.Body
                $isOk = $false
                $username = ''
                if ($null -ne $payload) {
                    try { $isOk = [bool]$payload.ok } catch { $isOk = $false }
                    try {
                        if ($null -ne $payload.result -and $null -ne $payload.result.username) {
                            $username = [string]$payload.result.username
                        }
                    } catch { $username = '' }
                }
                if ($isOk) {
                    $result.Status = 'ok'
                    $result.Username = $username
                    return $result
                }
                $result.Status = 'other'
                $result.Reason = 'api_not_ok'
                return $result
            }
            if ($raw.HttpStatus -eq 401 -or $raw.HttpStatus -eq 404) {
                $result.Status = 'invalid'
                return $result
            }
            $result.Status = 'other'
            $result.Reason = 'http_status'
            return $result
        }
        $result.Reason = $raw.Reason
    }
    $result.Status = 'network'
    return $result
}

function Test-DeepSeekKey([string]$Key, [int]$ProxyPort = 0) {
    $result = @{ Status = 'network'; HttpStatus = 0; Reason = 'unknown' }
    if ($Key -eq '') { $result.Reason = 'empty'; return $result }
    $url = $DeepSeekBase + '/user/balance'

    foreach ($proxy in (Get-ProxyCandidates -Port $ProxyPort)) {
        $raw = Invoke-JsonGet -Url $url -ProxyUrl $proxy -Bearer $Key -TimeoutSec 12
        if ($raw.Outcome -eq 'http') {
            $result.HttpStatus = $raw.HttpStatus
            if ($raw.HttpStatus -eq 200) {
                $result.Status = 'ok'
                return $result
            }
            if ($raw.HttpStatus -eq 401 -or $raw.HttpStatus -eq 403) {
                $result.Status = 'invalid'
                return $result
            }
            $result.Status = 'other'
            $result.Reason = 'http_status'
            return $result
        }
        $result.Reason = $raw.Reason
    }
    $result.Status = 'network'
    return $result
}

function Format-NetworkReason([string]$Reason) {
    if ($Reason -eq 'timeout') { return '连接超时' }
    if ($Reason -eq 'connect_failed') { return '连不上服务器' }
    if ($Reason -eq 'dns_failed') { return '域名解析失败' }
    if ($Reason -eq 'proxy_dns_failed') { return '代理域名解析失败' }
    if ($Reason -eq 'tls_failed') { return 'TLS 握手失败' }
    if ($Reason -eq 'bad_url') { return '这个值无法用于请求' }
    if ($Reason -eq 'empty') { return '值为空' }
    return '网络不可用'
}

# --------------------------------------------------------------------------- #
# Install directory discovery
# --------------------------------------------------------------------------- #
function Get-CandidateDirs {
    $list = New-Object System.Collections.Generic.List[string]
    $list.Add('C:\DevProjects\blade-quant')
    if ($env:USERPROFILE) { $list.Add((Join-PathSafe $env:USERPROFILE 'blade-quant')) }
    return $list
}

function Test-DirPath([string]$Dir) {
    # Test-Path throws when the path names an unknown drive; treat that as "no".
    if ([string]::IsNullOrWhiteSpace($Dir)) { return $false }
    try {
        return (Test-Path -LiteralPath $Dir)
    } catch {
        return $false
    }
}

function Test-InstallDir([string]$Dir) {
    if ([string]::IsNullOrWhiteSpace($Dir)) { return $false }
    if (-not (Test-DirPath -Dir $Dir)) { return $false }
    return (Test-DirPath -Dir (Join-PathSafe $Dir $RelPython))
}

function Resolve-InstallDirInteractive {
# Prefer a candidate that already has a working venv; otherwise ask. Returns
# an empty string when the caller gives up.
    $candidates = Get-CandidateDirs
    $existing = ''
    foreach ($candidate in $candidates) {
        if (-not (Test-DirPath -Dir $candidate)) { continue }
        if ($existing -eq '') { $existing = $candidate }
        if (Test-InstallDir -Dir $candidate) { return $candidate }
    }

    $suggest = ''
    if ($existing -ne '') { $suggest = $existing }
    elseif ($candidates.Count -gt 0) { $suggest = $candidates[0] }

    Write-Step '定位安装目录'
    foreach ($candidate in $candidates) {
        if (Test-DirPath -Dir $candidate) { Write-Note ('已存在：' + $candidate) }
        else { Write-Note ('不存在：' + $candidate) }
    }

    $attempts = 0
    while ($attempts -lt 5) {
        $attempts = $attempts + 1
        if ($suggest -ne '') {
            $entered = Read-Host -Prompt ('安装目录（直接回车用 ' + $suggest + '）')
        } else {
            $entered = Read-Host -Prompt '安装目录'
        }
        if ($null -eq $entered) { $entered = '' }
        $entered = ([string]$entered).Trim().Trim('"')
        if ($entered -eq '') { $entered = $suggest }
        if ($entered -eq '') { return '' }

        if (Test-InstallDir -Dir $entered) { return $entered }
        if (Test-DirPath -Dir $entered) {
            Write-Warn ('这个目录里没有虚拟环境：' + (Join-PathSafe $entered $RelPython))
        } else {
            Write-Warn ('目录不存在：' + $entered)
        }
        Write-Warn '请先运行 install_blade_quant.ps1，装好后再运行本向导。'
        $suggest = $entered
    }
    return ''
}

# --------------------------------------------------------------------------- #
# Status report (--check); never prompts.
# --------------------------------------------------------------------------- #
function Show-Status([string]$Dir) {
    $path = Join-PathSafe $Dir $ConfigName
    $state = Read-ConfigEnv -Path $path

    Write-Host ''
    Write-Host '配置状态' -ForegroundColor White
    Write-Note ('安装目录：' + $Dir)
    if ($state.Exists) {
        Write-Note ('配置文件：' + $path)
    } else {
        Write-Note ('配置文件：还没有创建（' + $path + '）')
    }

    $configured = 0
    foreach ($key in $KeyOrder) {
        $value = ''
        if ($state.Values.Contains($key)) { $value = [string]$state.Values[$key] }
        if ($value -ne '') {
            $configured = $configured + 1
            $masked = Format-MaskedValue -Value $value
            if ($key -eq $KeyProxy) { $masked = $value }
            Write-Host ('    ' + $key.PadRight(20) + '已设置  ' + $masked) -ForegroundColor Green
        } else {
            Write-Host ('    ' + $key.PadRight(20) + '未设置') -ForegroundColor DarkGray
        }
    }

    if (-not (Test-InstallDir -Dir $Dir)) {
        Write-Warn ('缺少 ' + (Join-PathSafe $Dir $RelPython) + '，请先运行 install_blade_quant.ps1。')
    }

    Write-Host ''
    if ($configured -eq 0) {
        Write-Note '还没有配置任何密钥。去掉 --check 再运行一次即可进入设置。'
    } else {
        Write-Note ([string]$configured + ' 项已配置。去掉 --check 再运行可修改。')
    }
    return $configured
}

# --------------------------------------------------------------------------- #
# Interactive sections
# --------------------------------------------------------------------------- #
function Read-TelegramToken([string]$Current, [int]$ProxyPort) {
# Returns the token to store; returning $Current means "keep what we had".
    Write-Step 'Telegram 机器人 token（可选）'
    Write-Note '在 Telegram 里找 @BotFather，/newbot，然后把那一串 token 复制过来。'

    if ($Current -ne '') {
        Write-Note ('当前值：' + (Format-MaskedValue -Value $Current))
        Write-Note '直接回车 = 保持不变；粘贴新 token = 替换。'
    } else {
        Write-Note '直接回车 = 这次先跳过 Telegram。'
    }

    $attempts = 0
    while ($attempts -lt 12) {
        $attempts = $attempts + 1
        $answer = ([string](Read-SecretInput -Prompt 'Token')).Trim()
        if ($answer -eq '') { return $Current }
        if ($answer -match '[\s"' + [char]39 + '<>|&]') {
            Write-Warn '这不像一个 token（里面有空格或引号），没有保存。'
            continue
        }

        Write-Note '正在向 Telegram 校验……'
        $check = Test-TelegramToken -Token $answer -ProxyPort $ProxyPort
        if ($check.Status -eq 'ok') {
            if ($check.Username -ne '') {
                Write-Ok ('token 有效，机器人用户名：@' + $check.Username)
            } else {
                Write-Ok 'token 有效。'
            }
            return $answer
        }
        if ($check.Status -eq 'invalid') {
            $code = ''
            if ($check.HttpStatus -gt 0) { $code = '（HTTP ' + [string]$check.HttpStatus + '）' }
            Write-Warn ('token 无效或已被吊销' + $code + '，没有保存。')
            Write-Note '请到 @BotFather 核对；直接回车可跳过。'
            continue
        }

        $detail = Format-NetworkReason -Reason $check.Reason
        if ($check.HttpStatus -gt 0) { $detail = 'HTTP ' + [string]$check.HttpStatus }
        Write-Warn ('没法完成校验：' + $detail + '。')
        Write-Note 'Clash 没开时 telegram 常常连不上，这不代表 token 有问题。'
        $choice = Read-Choice -Prompt '仍然保存 / 重试 / 跳过 (s/r/k)' -Valid @('s', 'r', 'k')
        if ($choice -eq 's') {
            Write-Warn '已保存未经校验的 token。'
            return $answer
        }
        if ($choice -eq 'k') {
            Write-Note '已跳过，保留原来的设置。'
            return $Current
        }
    }
    Write-Warn '尝试次数太多，Telegram token 保持原样。'
    return $Current
}

function Read-DeepSeekKey([string]$Current, [int]$ProxyPort) {
    Write-Step 'DeepSeek API key（可选）'
    Write-Note '在 platform.deepseek.com 的 API keys 里创建，用来做真实 AI 分析。'

    if ($Current -ne '') {
        Write-Note ('当前值：' + (Format-MaskedValue -Value $Current))
        Write-Note '直接回车 = 保持不变；粘贴新 key = 替换。'
    } else {
        Write-Note '直接回车 = 这次先跳过 DeepSeek（分析会走本地兜底，功能正常）。'
    }

    $attempts = 0
    while ($attempts -lt 12) {
        $attempts = $attempts + 1
        $answer = ([string](Read-SecretInput -Prompt 'API key')).Trim()
        if ($answer -eq '') { return $Current }
        if ($answer -match '[\s"' + [char]39 + '<>|&]') {
            Write-Warn '这不像一个 API key（里面有空格或引号），没有保存。'
            continue
        }

        if (-not (Read-YesNo -Prompt '现在联网校验这个 key 吗？' -Default $true)) {
            Write-Note '已跳过校验，直接保存。'
            return $answer
        }

        Write-Note '正在向 DeepSeek 校验……'
        $check = Test-DeepSeekKey -Key $answer -ProxyPort $ProxyPort
        if ($check.Status -eq 'ok') {
            Write-Ok 'key 有效。'
            return $answer
        }
        if ($check.Status -eq 'invalid') {
            $code = ''
            if ($check.HttpStatus -gt 0) { $code = '（HTTP ' + [string]$check.HttpStatus + '）' }
            Write-Warn ('DeepSeek 拒绝了这个 key' + $code + '，没有保存。')
            Write-Note '请到 platform.deepseek.com 核对；直接回车可跳过。'
            continue
        }

        $detail = Format-NetworkReason -Reason $check.Reason
        if ($check.HttpStatus -gt 0) { $detail = 'HTTP ' + [string]$check.HttpStatus }
        Write-Warn ('没法完成校验：' + $detail + '。')
        $choice = Read-Choice -Prompt '仍然保存 / 重试 / 跳过 (s/r/k)' -Valid @('s', 'r', 'k')
        if ($choice -eq 's') {
            Write-Warn '已保存未经校验的 key。'
            return $answer
        }
        if ($choice -eq 'k') {
            Write-Note '已跳过，保留原来的设置。'
            return $Current
        }
    }
    Write-Warn '尝试次数太多，DeepSeek key 保持原样。'
    return $Current
}

function Read-ProxySetting([string]$Current, [int]$LivePort) {
    # Returns @{ Action = keep|set|unset; Value = url }. keep also covers "there
    # was nothing configured before", so a re-run never drops a proxy by accident.
    Write-Step '本机代理（可选）'
    $suggested = ''
    if ($LivePort -gt 0) {
        $suggested = 'http://127.0.0.1:' + [string]$LivePort
        Write-Ok ('检测到代理：127.0.0.1:' + [string]$LivePort)
        if ($Current -eq $suggested) {
            Write-Note ('当前已经是这个值：' + $Current)
        } elseif ($Current -ne '') {
            Write-Note ('当前值：' + $Current + '，本次探测到：' + $suggested)
        }
        if (Read-YesNo -Prompt ('让澳客抓取走 ' + $suggested + ' 吗？') -Default $true) {
            return @{ Action = 'set'; Value = $suggested }
        }
        if ($Current -ne '') {
            if (Read-YesNo -Prompt ('保留原来的 ' + $Current + ' 吗？') -Default $true) {
                return @{ Action = 'keep'; Value = $Current }
            }
        }
    } else {
        Write-Note '7890 / 7897 / 10809 / 1080 都没有代理在监听。'
        Write-Note 'Clash 没开时这样很正常，不设代理也能直连。'
        if ($Current -ne '') {
            if (Read-YesNo -Prompt ('保留原来的 ' + $Current + ' 吗？') -Default $true) {
                return @{ Action = 'keep'; Value = $Current }
            }
        } elseif (Read-YesNo -Prompt '保持不设置 OKOOO_PROXY？' -Default $true) {
            return @{ Action = 'keep'; Value = '' }
        }
    }

    $entered = Read-Host -Prompt '代理地址（直接回车 = 不设置）'
    if ($null -eq $entered) { return @{ Action = 'keep'; Value = $Current } }
    $entered = ([string]$entered).Trim().Trim('"')
    if ($entered -eq '') { return @{ Action = 'unset'; Value = '' } }
    if ($entered -notmatch '^https?://') {
        Write-Warn '代理地址通常形如 http://127.0.0.1:7890，这个值已忽略，保留原来的设置。'
        return @{ Action = 'keep'; Value = $Current }
    }
    return @{ Action = 'set'; Value = $entered }
}

# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
Write-Host ''
Write-Host '=====================================================' -ForegroundColor DarkCyan
Write-Host '  blade-quant  配置向导' -ForegroundColor Cyan
Write-Host '=====================================================' -ForegroundColor DarkCyan

$dir = ''
if ($InstallDir -ne '') {
    $dir = ([string]$InstallDir).Trim().Trim('"')
    if (-not (Test-DirPath -Dir $dir)) {
        Write-Host ''
        Write-Host ('目录不存在：' + $dir) -ForegroundColor Red
        exit 2
    }
} else {
    foreach ($candidate in (Get-CandidateDirs)) {
        if (Test-InstallDir -Dir $candidate) { $dir = $candidate; break }
    }
    if ($dir -eq '') {
        if ($Check) {
            # Never prompt in --check mode: report the first existing candidate.
            foreach ($candidate in (Get-CandidateDirs)) {
                if (Test-DirPath -Dir $candidate) { $dir = $candidate; break }
            }
        } else {
            $dir = Resolve-InstallDirInteractive
        }
    }
}

if ($dir -eq '') {
    Write-Host ''
    Write-Host '没有找到 blade-quant 安装目录。' -ForegroundColor Red
    Write-Host '请先运行 install_blade_quant.ps1，装好后再运行本向导。' -ForegroundColor Yellow
    Write-Host ''
    exit 2
}

if ($Check) {
    [void](Show-Status -Dir $dir)
    exit 0
}

if (-not (Test-InstallDir -Dir $dir)) {
    Write-Host ''
    Write-Host ('虚拟环境不完整，缺少：' + (Join-PathSafe $dir $RelPython)) -ForegroundColor Red
    Write-Host '请先运行 install_blade_quant.ps1，装好后再运行本向导。' -ForegroundColor Yellow
    Write-Host ''
    exit 2
}

Write-Note ('安装目录：' + $dir)

$configPath = Join-PathSafe $dir $ConfigName
$state = Read-ConfigEnv -Path $configPath
$values = New-Object System.Collections.Specialized.OrderedDictionary
foreach ($key in $state.Values.Keys) { $values[$key] = [string]$state.Values[$key] }

$currentTelegram = ''
$currentDeepSeek = ''
$currentProxy = ''
if ($values.Contains($KeyTelegram)) { $currentTelegram = [string]$values[$KeyTelegram] }
if ($values.Contains($KeyDeepSeek)) { $currentDeepSeek = [string]$values[$KeyDeepSeek] }
if ($values.Contains($KeyProxy)) { $currentProxy = [string]$values[$KeyProxy] }

Write-Host ''
if ($state.Exists) {
    Write-Note ('已读取现有配置：' + $configPath)
} else {
    Write-Note '还没有配置，只会写入你实际填写的项。'
}

$livePort = Find-LiveProxyPort

$telegram = Read-TelegramToken -Current $currentTelegram -ProxyPort $livePort
if ($telegram -ne '') { $values[$KeyTelegram] = $telegram } else { $values.Remove($KeyTelegram) }

$deepseek = Read-DeepSeekKey -Current $currentDeepSeek -ProxyPort $livePort
if ($deepseek -ne '') { $values[$KeyDeepSeek] = $deepseek } else { $values.Remove($KeyDeepSeek) }

$proxyResult = Read-ProxySetting -Current $currentProxy -LivePort $livePort
if ($proxyResult.Action -eq 'set') { $values[$KeyProxy] = $proxyResult.Value }
elseif ($proxyResult.Action -eq 'unset') { $values.Remove($KeyProxy) }

Write-Step '写入配置'
[void](Save-ConfigEnv -Path $configPath -Values $values)
Write-Ok ('已写入 ' + $configPath)
Write-Note '严格 UTF-8 无 BOM，每行一个 KEY=VALUE。这个文件请自己保管，不要提交。'

Write-Step '本次结果'
foreach ($key in $KeyOrder) {
    $value = ''
    if ($values.Contains($key)) { $value = [string]$values[$key] }
    if ($value -ne '') {
        $masked = Format-MaskedValue -Value $value
        if ($key -eq $KeyProxy) { $masked = $value }
        Write-Host ('    ' + $key.PadRight(20) + '已设置  ' + $masked) -ForegroundColor Green
    } else {
        Write-Host ('    ' + $key.PadRight(20) + '未设置') -ForegroundColor DarkGray
    }
}

$startCmd = Join-PathSafe $dir 'start.cmd'
Write-Host ''
Write-Host '下一步，启动程序：' -ForegroundColor White
Write-Host ''
Write-Host ('  & "' + $startCmd + '"') -ForegroundColor Yellow
Write-Host ''
if ($ScriptSelf -ne '') {
    Write-Note ('以后随时可以再运行本向导：' + $ScriptSelf)
    Write-Note ('只看状态：' + $ScriptSelf + ' --check')
}
Write-Host ''

exit 0
