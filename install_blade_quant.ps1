<#
FLAnalyzer · blade-quant 一键安装 / 一键刷新（Windows PowerShell）

作用：
  1. 检查 Python / pip，缺失就明确告诉你装什么
  2. 在目标目录建 venv 并装齐依赖
  3. 下载已通过审计的 app_v84_hermes.py 与 configure_blade_quant.ps1（固定 commit）
  4. 写入启动脚本（会自动加载 config.env），并跑一次 --diagnose 自检
  5. 打印配置 Telegram / DeepSeek 的那一条命令

已经装过的目录会自动走「刷新已有安装」快路径：复用 .venv、依赖已满足就不装包、
只更新程序和启动脚本，config.env 一个字节都不碰。

用法（复制整行到 PowerShell，回车即可）：
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/jiafang81yifang/FLAnalyzer/cursor/fix-runtime-issues-84a8/install_blade_quant.ps1 | iex"

可传参数指定安装目录（默认 C:\DevProjects\blade-quant，没有 C:\DevProjects 则用 用户目录\blade-quant）：
  & { $env:BLADE_INSTALL_DIR='D:\blade-quant'; irm <上面的URL> | iex }
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# 老版本 Windows PowerShell 默认不启用 TLS 1.2，会导致下载和 pip 失败。
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11
} catch { }

# 程序和向导都固定到同一个 commit（$PinCommit），受分支后续变动影响为零。
# 注意：这个 commit 必须同时包含 app_v84_hermes.py 和 configure_blade_quant.ps1。
# 改这个值必须同时更新两份文件都来自同一个 commit，否则刷新会装到不一致的组合。
# 057c455 = 统一赔率评估器 unified_odds_evaluator.py（含 CN/INTL 同场按名对齐、
#           走水凯利修正说明、保本概率别名）+ 单文件内嵌同步
# ce23836 = 说明 EV 为何不含走水项（push*0 精确为 0，只有凯利用 1/(1-push_prob)）
$PinCommit = 'ce23836403f20a3f60822fedc4339787e160ff72'
$RepoSlug = 'jiafang81yifang/FLAnalyzer'
$AppName = 'app_v84_hermes.py'
$ConfigName = 'configure_blade_quant.ps1'
$DepsImportList = 'streamlit, pandas, numpy, requests, plotly, schedule, openai, scipy, bs4, httpx, urllib3, tornado'

$RunClock = [System.Diagnostics.Stopwatch]::StartNew()

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    !   $msg" -ForegroundColor Yellow }
function Write-Note($msg) { Write-Host "    $msg" -ForegroundColor Gray }
function Fail($msg) {
    Write-Host ""
    Write-Host "安装未完成：$msg" -ForegroundColor Red
    Write-Host ""
    exit 1
}

# ---------- 1. 目标目录 ----------
# 优先沿用已存在的 C:\DevProjects，避免程序散落在不同位置。
$InstallDir = $env:BLADE_INSTALL_DIR
if (-not $InstallDir) {
    if (Test-Path 'C:\DevProjects') {
        $InstallDir = 'C:\DevProjects\blade-quant'
    } else {
        $InstallDir = Join-Path $HOME 'blade-quant'
    }
}
$DirExisted = Test-Path $InstallDir
Write-Step "安装目录：$InstallDir"

# Windows 的 venv 是 Scripts\python.exe；其它平台是 bin/python。
# 这样同一个脚本在非 Windows 上也能跑（便于自动化验证），Windows 行为不变。
$OnWindows = ($env:OS -eq 'Windows_NT')
$VenvDir = Join-Path $InstallDir '.venv'
if ($OnWindows) {
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VenvPython = Join-Path $VenvDir 'bin/python'
}
$AppFile = Join-Path $InstallDir $AppName
$ConfigScript = Join-Path $InstallDir $ConfigName
$StartBat = Join-Path $InstallDir 'start.cmd'
$ConfigEnv = Join-Path $InstallDir 'config.env'

# ---------- 2. 判断是全新安装还是刷新 ----------
# 只看 .venv\Scripts\python.exe 在不在；在就当已有安装。
# 但 venv 本身可能坏了（Python 被升级/删除），所以再让它跑一句，跑不起来就退回全量安装。
$IsRefresh = $false
if ($DirExisted -and (Test-Path $VenvPython)) {
    $venvProbe = & $VenvPython -c "import sys;print(sys.version_info[0])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $venvProbe -eq '3') {
        $IsRefresh = $true
    }
}

if ($IsRefresh) {
    Write-Host ""
    Write-Host "=====================================================" -ForegroundColor DarkCyan
    Write-Host "  刷新已有安装（复用 .venv，不动 config.env）" -ForegroundColor Cyan
    Write-Host "=====================================================" -ForegroundColor DarkCyan
    Write-Ok "复用 .venv，跳过创建虚拟环境"
} else {
    Write-Host ""
    Write-Host "=====================================================" -ForegroundColor DarkCyan
    Write-Host "  全新安装" -ForegroundColor Cyan
    Write-Host "=====================================================" -ForegroundColor DarkCyan
}

if (-not $DirExisted) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}
Set-Location $InstallDir

# ---------- 3. 检查 Python（只有要建 venv 时才需要） ----------
if (-not $IsRefresh) {
    Write-Step "检查 Python"
    $python = $null
    foreach ($cand in @('python', 'python3', 'py')) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if ($cand -eq 'py') {
            $ver = & py -3 -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
            $exe = 'py'
            $argsBase = @('-3')
        } else {
            $ver = & $cand -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
            $exe = $cand
            $argsBase = @()
        }
        if ($LASTEXITCODE -eq 0 -and $ver) {
            $major, $minor = $ver.Split('.')
            if ([int]$major -eq 3 -and [int]$minor -ge 10) {
                $python = $exe
                $pyArgs = $argsBase
                Write-Ok "Python $ver ($exe)"
                break
            }
            Write-Warn "$cand 是 Python $ver，需要 3.10 或更高"
        }
    }

    if (-not $python) {
        Write-Host ""
        Write-Host "这台机器没有可用的 Python 3.10+。" -ForegroundColor Yellow
        Write-Host "请先装 Python（勾选 Add python.exe to PATH）：" -ForegroundColor Yellow
        Write-Host "    https://www.python.org/downloads/windows/" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "装好后重新运行这条命令即可，脚本会自动接管剩下所有步骤。" -ForegroundColor Yellow
        Write-Host ""
        try {
            Start-Process 'https://www.python.org/downloads/windows/'
        } catch { }
        exit 1
    }

    # ---------- 4. 建 venv ----------
    if (-not (Test-Path $VenvPython)) {
        Write-Step "创建虚拟环境"
        & $python @pyArgs -m venv $VenvDir
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPython)) {
            Fail "虚拟环境创建失败。可能是 Python 安装缺少 venv 组件，重装 Python 时保留默认选项即可。"
        }
        Write-Ok ".venv"
    } else {
        Write-Ok ".venv 已存在，复用"
    }
}

# ---------- 5. 探测本机代理 ----------
# pip 和下载都会继承系统代理。Clash 没开时代理会拒绝连接（WinError 10061），
# 所以只在代理端口真的监听时才用它，否则显式绕过系统代理直连。
$proxyAlive = $false
$aliveProxy = ''
foreach ($port in @(7890, 7897, 10809, 1080)) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $ar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
        if ($ar.AsyncWaitHandle.WaitOne(300, $false) -and $c.Connected) {
            $proxyAlive = $true
            $aliveProxy = "http://127.0.0.1:$port"
            $c.Close()
            break
        }
        $c.Close()
    } catch { }
}
if ($proxyAlive) {
    Write-Ok "代理可用：$aliveProxy"
    $env:HTTP_PROXY = $aliveProxy
    $env:HTTPS_PROXY = $aliveProxy
} else {
    Write-Warn "本机代理未运行，本次安装直连（已忽略系统代理设置）"
    $env:HTTP_PROXY = ''
    $env:HTTPS_PROXY = ''
    $env:ALL_PROXY = ''
}

# ---------- 6. 安装依赖 ----------
$packages = @(
    'streamlit', 'pandas', 'numpy', 'requests', 'plotly', 'schedule',
    'openai', 'scipy', 'beautifulsoup4', 'httpx', 'urllib3', 'tornado',
    'scikit-learn'
)
$pipCommon = @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input')
$mirror = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$proxyFlag = if ($proxyAlive) { @('--proxy', $aliveProxy) } else { @('--proxy', '') }

function Invoke-Pip($pipArgs) {
    & $VenvPython @pipArgs 2>&1 | ForEach-Object {
        if ($_ -match '^\s*(WARNING|ERROR)') { Write-Host "    $_" -ForegroundColor DarkYellow }
    }
    return $LASTEXITCODE
}

# 真的能 import 才算装好，避免 pip 报成功但包不完整。
# 刷新时先跑这一句：已经满足就完全跳过 pip，省掉最慢的一步。
& $VenvPython -c "import $DepsImportList; print('deps ok')" 2>$null | Out-Null
$depsReady = ($LASTEXITCODE -eq 0)

if ($depsReady) {
    Write-Step "依赖"
    Write-Ok "依赖已满足，跳过安装"
} else {
    Write-Step "安装依赖（首次约 2-5 分钟）"

    Write-Host "    升级 pip..."
    $code = Invoke-Pip (@($pipCommon[0], $pipCommon[1], 'install', '--upgrade', 'pip', '--quiet') + $proxyFlag)
    if ($code -ne 0) {
        Write-Warn "pip 升级跳过，继续安装依赖"
    }

    Write-Host "    安装依赖包..."
    $code = Invoke-Pip (@($pipCommon) + $proxyFlag + $packages)
    if ($code -ne 0) {
        Write-Warn "官方源失败，改用清华镜像"
        $code = Invoke-Pip (@($pipCommon) + $proxyFlag + @('-i', $mirror) + $packages)
    }
    if ($code -ne 0) {
        Write-Warn "带代理再试一次"
        $code = Invoke-Pip (@($pipCommon) + @('-i', $mirror) + @('--proxy', 'http://127.0.0.1:7890') + $packages)
    }
    if ($code -ne 0) {
        Fail "依赖安装失败。请打开 Clash 后重跑这条命令，或检查网络。"
    }

    & $VenvPython -c "import $DepsImportList; print('deps ok')"
    if ($LASTEXITCODE -ne 0) {
        Fail "依赖装上了但导入失败，请把上面的错误发给我。"
    }
    Write-Ok "依赖就绪"
}

# ---------- 7. 下载程序与配置向导 ----------
function Save-RemoteFile {
    # 用 HttpWebRequest 而不是 Invoke-WebRequest：Windows PowerShell 5.1 的
    # Invoke-WebRequest 没有 -NoProxy 参数，传了就报参数绑定错误，直连分支必然失败。
    # 这里显式设置 Proxy（$null = 绕过系统代理），5.1 和 7 行为一致。
    param([string]$Url, [string]$OutFile, [string]$ProxyUrl = '', [int]$TimeoutSec = 60)

    $request = [System.Net.WebRequest]::Create($Url)
    $request.Method = 'GET'
    $request.Timeout = $TimeoutSec * 1000
    $request.ReadWriteTimeout = $TimeoutSec * 1000
    $request.UserAgent = 'blade-quant-installer/1.0'
    if ($ProxyUrl -ne '') {
        $request.Proxy = New-Object System.Net.WebProxy($ProxyUrl)
    } else {
        $request.Proxy = $null
    }

    $response = $null
    $inStream = $null
    $outStream = $null
    try {
        $response = $request.GetResponse()
        $inStream = $response.GetResponseStream()
        $outStream = [System.IO.File]::Create($OutFile)
        $inStream.CopyTo($outStream)
    } finally {
        if ($null -ne $outStream) { try { $outStream.Close() } catch { } }
        if ($null -ne $inStream) { try { $inStream.Close() } catch { } }
        if ($null -ne $response) { try { $response.Close() } catch { } }
    }
}

function Get-RemoteArtifact {
    # 依次尝试：本机代理 → 直连 → 7890，每个代理再试 GitHub raw 与 jsDelivr 镜像。
    # raw.githubusercontent.com 在国内经常连不上，镜像能救回来。
    #
    # 刚 push 完的 commit 偶尔会先在 CDN 上返回 404（缓存了负结果，几十秒后自愈），
    # 所以每个 URL 都重试几轮再放弃，别让用户因为这种瞬时状态重装。
    param([string]$FileName, [string]$OutFile, [double]$MinMb = 0)

    $urls = @(
        "https://raw.githubusercontent.com/$RepoSlug/$PinCommit/$FileName",
        "https://cdn.jsdelivr.net/gh/$RepoSlug@$PinCommit/$FileName"
    )
    $proxyCandidates = @()
    if ($proxyAlive) { $proxyCandidates += $aliveProxy }
    $proxyCandidates += ''
    $proxyCandidates += 'http://127.0.0.1:7890'

    for ($round = 1; $round -le 3; $round++) {
        foreach ($proxy in $proxyCandidates) {
            foreach ($url in $urls) {
                try {
                    Save-RemoteFile -Url $url -OutFile $OutFile -ProxyUrl $proxy -TimeoutSec 60
                    if (-not (Test-Path -LiteralPath $OutFile)) { continue }
                    $bytes = (Get-Item $OutFile).Length
                    if ($bytes -le 0) { continue }
                    if ($MinMb -gt 0) {
                        $mb = [math]::Round($bytes / 1MB, 2)
                        if ($mb -lt $MinMb) {
                            # 明显被截断，换下一条再试。
                            continue
                        }
                    }
                    return $true
                } catch { }
            }
        }
        if ($round -lt 3) {
            Write-Note "第 $round 轮下载失败，等 5 秒重试（CDN 刚发布的 commit 可能要一会儿才可用）"
            Start-Sleep -Seconds 5
        }
    }
    return $false
}

Write-Step "下载程序"
if (-not (Get-RemoteArtifact -FileName $AppName -OutFile $AppFile -MinMb 2.0)) {
    Fail "下载 app_v84_hermes.py 失败。请检查网络，或打开 Clash 后重跑这条命令。"
}
$sizeMb = [math]::Round((Get-Item $AppFile).Length / 1MB, 2)
Write-Ok "$AppName ($sizeMb MB)"

Write-Step "下载配置向导"
if (-not (Get-RemoteArtifact -FileName $ConfigName -OutFile $ConfigScript)) {
    Fail "下载 configure_blade_quant.ps1 失败。请检查网络，或打开 Clash 后重跑这条命令。"
}
$cfgKb = [math]::Round((Get-Item $ConfigScript).Length / 1KB, 1)
Write-Ok "$ConfigName ($cfgKb KB)"

# ---------- 8. 校验程序文件 ----------
Write-Step "校验程序文件"
& $VenvPython -c "import py_compile,sys; py_compile.compile(r'$AppFile', doraise=True); print('compile ok')"
if ($LASTEXITCODE -ne 0) {
    Fail "程序文件校验失败，可能下载时被截断，请重试。"
}
Write-Ok "app_v84_hermes.py 语法校验通过"

# 向导用 PowerShell 自己的解析器校验：5.1 和 7 都有这个类型，不需要额外进程。
# 解析通过还不够：下载被截断时可能刚好停在某个完整语句上，语法依然合法，
# 所以再检查体积下限和几个必须存在的锚点，双重保险。
$cfgBytes = (Get-Item $ConfigScript).Length
if ($cfgBytes -lt 8192) {
    Fail "configure_blade_quant.ps1 只有 $cfgBytes 字节，明显不完整，请重试。"
}
$cfgText = [System.IO.File]::ReadAllText($ConfigScript, [System.Text.Encoding]::UTF8)
foreach ($anchor in @('Save-ConfigEnv', 'Test-TelegramToken', '/user/balance', '--check')) {
    if ($cfgText.IndexOf($anchor) -lt 0) {
        Fail "configure_blade_quant.ps1 缺少必要内容（$anchor），可能下载不完整，请重试。"
    }
}
$parseErrors = $null
$parseTokens = $null
try {
    [void][System.Management.Automation.Language.Parser]::ParseFile($ConfigScript, [ref]$parseTokens, [ref]$parseErrors)
} catch {
    Fail "向导文件无法解析：$($_.Exception.Message)"
}
if ($null -ne $parseErrors -and $parseErrors.Count -gt 0) {
    $first = $parseErrors[0]
    Fail "向导文件第 $($first.Extent.StartLineNumber) 行有语法错误：$($first.Message)"
}
Write-Ok "configure_blade_quant.ps1 解析通过"

# ---------- 8b. 知识库 ----------
# 技能匹配引擎需要 knowledge_base\statistical_skills.json 与 strategy_skills.json。
# 没有就只是「本场不激活技能」，但正常安装不该是这个状态，所以在这里补种。
# 已存在且能解析、有内容的 JSON 一个字节都不动（用户可能自己改过技能）。
Write-Step "知识库"
$KbDir = Join-Path $InstallDir 'knowledge_base'
if (-not (Test-Path $KbDir)) {
    New-Item -ItemType Directory -Path $KbDir -Force | Out-Null
}
$KbHelper = Join-Path $InstallDir '.kb_seed_helper.py'
$KbScript = @'
"""Seed knowledge_base from the app's built-in skills without importing the app.

Reading `_SEED_SKILLS` with ast keeps this offline: importing app_v84_hermes
would boot Streamlit and print its warnings into the installer output.
"""
import ast
import json
import os
import sys

install_dir, kb_dir = sys.argv[1], sys.argv[2]
app_path = os.path.join(install_dir, 'app_v84_hermes.py')

seeds = {}
try:
    with open(app_path, 'r', encoding='utf-8') as handle:
        tree = ast.parse(handle.read(), filename=app_path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != 'SkillMatchingEngine':
            continue
        for item in node.body:
            targets = []
            if isinstance(item, ast.Assign):
                targets = item.targets
            elif isinstance(item, ast.AnnAssign):
                targets = [item.target]
            if not any(isinstance(t, ast.Name) and t.id == '_SEED_SKILLS' for t in targets):
                continue
            value = item.value
            if isinstance(value, ast.Constant):
                seeds = json.loads(value.value)
            elif isinstance(value, ast.Dict):
                seeds = ast.literal_eval(value)
            break
        break
except (OSError, SyntaxError, ValueError) as exc:
    sys.stderr.write('seed_read_failed:' + type(exc).__name__ + ':' + str(exc)[:160] + '\n')

results = []
for name in ('statistical_skills.json', 'strategy_skills.json'):
    path = os.path.join(kb_dir, name)
    skills = seeds.get(name) or []
    ok = False
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
            ok = isinstance(data, dict) and isinstance(data.get('skills'), list) and bool(data['skills'])
        except (OSError, ValueError):
            ok = False
    if ok:
        continue
    if not skills:
        results.append(name + '(无内置技能)')
        continue
    try:
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump({'schema_version': 1, 'kind': name.replace('.json', ''),
                       'note': '由安装脚本补种；可直接编辑，重启生效。', 'skills': skills},
                      handle, ensure_ascii=False, indent=2)
        results.append(name + '(已补种)')
    except OSError as exc:
        results.append(name + '(写入失败:' + type(exc).__name__ + ')')
print('KB_SEED:' + ('; '.join(results) if results else 'ok'))

'@
[System.IO.File]::WriteAllText($KbHelper, $KbScript, (New-Object System.Text.UTF8Encoding($false)))
$KbOutput = & $VenvPython $KbHelper $InstallDir $KbDir 2>&1
$KbCode = $LASTEXITCODE
Remove-Item $KbHelper -Force -ErrorAction SilentlyContinue
# 辅助脚本只回一行，以 KB_SEED: 开头，避免和程序自身的输出混在一起。
$KbText = ("$KbOutput" -split "`n" | Where-Object { $_ -match 'KB_SEED:' } | Select-Object -Last 1)
if ($KbText) { $KbText = $KbText.Trim() } else { $KbText = '' }
if ($KbCode -eq 0 -and $KbText -eq 'KB_SEED:ok') {
    Write-Ok "knowledge_base 已就绪（statistical_skills.json + strategy_skills.json 已存在且非空）"
} elseif ($KbCode -eq 0 -and $KbText) {
    Write-Ok ("knowledge_base " + $KbText.Substring('KB_SEED:'.Length))
} else {
    Write-Warn "knowledge_base: $KbOutput（程序仍会以「本场未激活技能」继续）"
}

# ---------- 9. 启动脚本 ----------
# 只有检测到本机代理端口真的在监听时才写默认代理，避免 Clash 没开时拖慢抓取。
$proxyLine = ''
foreach ($port in @(7890, 7897, 10809, 1080)) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $connect = $client.BeginConnect('127.0.0.1', $port, $null, $null)
        $ok = $connect.AsyncWaitHandle.WaitOne(300, $false)
        if ($ok -and $client.Connected) {
            $proxyLine = "if not defined OKOOO_PROXY set `"OKOOO_PROXY=http://127.0.0.1:$port`""
            Write-Ok "检测到本机代理 127.0.0.1:$port，已写入启动脚本"
            $client.Close()
            break
        }
        $client.Close()
    } catch { }
}
if (-not $proxyLine) {
    Write-Warn "未检测到本机代理，启动脚本走直连"
}

# .cmd 用 ASCII 写，避免 cmd.exe 被 UTF-8 BOM 干扰。
# config.env（由 configure_blade_quant.ps1 生成）存在时先逐行导入成环境变量；
# 解析失败只是跳过那一行，绝不让启动失败。config.env 里没写 OKOOO_PROXY 时，
# 上面那个按探测结果生成的默认代理值才生效（if not defined），不会覆盖用户设置。
# Batch cannot compare against a bare single quote inside an array literal, so
# that one line is assembled from the quote characters to stay readable.
$DQ = [string][char]34
$SQ = [string][char]39
$sqStripLine = 'if ' + $DQ + '%_bv:~0,1%' + $DQ + '==' + $DQ + $SQ + $DQ +
    ' if ' + $DQ + '%_bv:~-1%' + $DQ + '==' + $DQ + $SQ + $DQ + ' set "_bv=%_bv:~1,-1%"'
$batLines = @(
    '@echo off',
    'chcp 65001 >nul',
    'cd /d "%~dp0"',
    'setlocal EnableExtensions',
    '',
    'rem Load optional user configuration. Any parse problem is ignored.',
    'if exist "config.env" call :blade_load_config "config.env"',
    '',
    $proxyLine,
    '',
    '".venv\Scripts\python.exe" -m streamlit run app_v84_hermes.py',
    'pause',
    'exit /b 0',
    '',
    ':blade_load_config',
    'if not exist "%~1" exit /b 0',
    'for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~1") do call :blade_set_var "%%A" "%%B"',
    'exit /b 0',
    '',
    ':blade_set_var',
    'set "_bk=%~1"',
    'set "_bv=%~2"',
    'if "%_bk%"=="" goto :blade_set_var_done',
    'if not "%_bk%"=="%_bk: =%" goto :blade_set_var_done',
    'if not "%_bk%"=="%_bk:#=%" goto :blade_set_var_done',
    'if "%_bv%"=="" goto :blade_set_var_done',
    'if "%_bv:~0,1%"=="""" if "%_bv:~-1%"=="""" set "_bv=%_bv:~1,-1%"',
    $sqStripLine,
    'set "%_bk%=%_bv%"',
    ':blade_set_var_done',
    'set "_bk="',
    'set "_bv="',
    'goto :eof'
) | Where-Object { $_ -ne '' }
[System.IO.File]::WriteAllLines($StartBat, $batLines, (New-Object System.Text.UTF8Encoding($false)))
Write-Ok "start.cmd 已更新（会加载 config.env）"

# ---------- 10. 自检 ----------
Write-Step "运行自检"
# Python 输出中文时按 UTF-8 解码，避免 PowerShell 用 OEM 代码页读成乱码。
$env:PYTHONIOENCODING = 'utf-8'
$diagnoseOutput = & $VenvPython $AppFile --diagnose 2>&1
$diagnoseOutput | ForEach-Object { Write-Host "    $_" }
if ($LASTEXITCODE -ne 0) {
    Fail "自检未通过，请把上面的输出发给我。"
}
$dbLine = $diagnoseOutput | Where-Object { $_ -match 'DB|数据库' } | Select-Object -First 1
if ($dbLine) { Write-Ok "数据库已初始化：$dbLine" }

# ---------- 11. 配置向导可用性 ----------
# --check 只读不写，跑一次证明向导在这个目录里真的能执行。
$psHostExe = 'powershell'
$cfgCmd = Get-Command 'powershell' -ErrorAction SilentlyContinue
if (-not $cfgCmd) { $cfgCmd = Get-Command 'pwsh' -ErrorAction SilentlyContinue }
if ($cfgCmd) { $psHostExe = $cfgCmd.Source }

Write-Step "检查配置向导"
$checkOutput = & $psHostExe -NoProfile -ExecutionPolicy Bypass -File $ConfigScript -InstallDir $InstallDir --check 2>&1
$checkCode = $LASTEXITCODE
$checkOutput | ForEach-Object { Write-Host "    $_" }
if ($checkCode -ne 0) {
    Fail "配置向导无法运行（退出码 $checkCode）。请把上面的输出发给我。"
}
Write-Ok "configure_blade_quant.ps1 可以正常执行"

# ---------- 12. 汇总 ----------
$RunClock.Stop()
$elapsed = [math]::Round($RunClock.Elapsed.TotalSeconds, 1)

Write-Host ""
if ($IsRefresh) {
    Write-Host "刷新完成（耗时 $elapsed 秒）。" -ForegroundColor Green
} else {
    Write-Host "安装完成（耗时 $elapsed 秒）。" -ForegroundColor Green
}
Write-Host ""
Write-Host "  程序：$AppFile"
Write-Host "  启动：双击 $StartBat"
Write-Host "  体检：$VenvPython `"$AppFile`" --diagnose"
if (Test-Path $ConfigEnv) {
    Write-Host "  配置：$ConfigEnv（已保留，未改动）" -ForegroundColor Gray
}
Write-Host ""

Write-Host "配置 Telegram / DeepSeek 请运行 configure_blade_quant.ps1。" -ForegroundColor Cyan
Write-Host "下一步（复制这一行运行）：" -ForegroundColor White
Write-Host ""
Write-Host "  $psHostExe -NoProfile -ExecutionPolicy Bypass -File `"$ConfigScript`"" -ForegroundColor Yellow
Write-Host ""
Write-Note "只看配置状态：在上面的命令末尾加 --check"
Write-Host ""

$answer = Read-Host "现在就启动吗？(Y/n)"
if ($answer -ne 'n' -and $answer -ne 'N') {
    Set-Location $InstallDir
    & $StartBat
}
