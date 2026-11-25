<#
FLAnalyzer · blade-quant 一键安装（Windows PowerShell）

作用：
  1. 检查 Python / pip，缺失就明确告诉你装什么
  2. 在目标目录建 venv 并装齐依赖
  3. 下载已通过 262 项审计的 app_v84_hermes.py（固定 commit，不受分支变动影响）
  4. 写入启动脚本，并跑一次 --diagnose 自检

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

$AppCommit = 'e4c8701b33806bd73e01baf1362cc5b4a47aa7ad'
$AppUrl = "https://raw.githubusercontent.com/jiafang81yifang/FLAnalyzer/$AppCommit/app_v84_hermes.py"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    !   $msg" -ForegroundColor Yellow }
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
Write-Step "安装目录：$InstallDir"
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}
Set-Location $InstallDir

# ---------- 2. 检查 Python ----------
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

# ---------- 3. 建 venv ----------
$VenvDir = Join-Path $InstallDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
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

# ---------- 4. 安装依赖 ----------
Write-Step "安装依赖（首次约 2-5 分钟）"
& $VenvPython -m pip install --upgrade pip --quiet --disable-pip-version-check
$packages = @(
    'streamlit', 'pandas', 'numpy', 'requests', 'plotly', 'schedule',
    'openai', 'scipy', 'beautifulsoup4', 'httpx', 'urllib3', 'tornado',
    'scikit-learn'
)
& $VenvPython -m pip install --quiet --disable-pip-version-check @packages
if ($LASTEXITCODE -ne 0) {
    Write-Warn "直连安装失败，尝试国内镜像"
    & $VenvPython -m pip install --quiet --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple @packages
    if ($LASTEXITCODE -ne 0) {
        Fail "依赖安装失败。请检查网络或代理后重试。"
    }
}
Write-Ok "依赖就绪"

# ---------- 5. 下载程序 ----------
$AppFile = Join-Path $InstallDir 'app_v84_hermes.py'
Write-Step "下载程序"
try {
    Invoke-WebRequest -Uri $AppUrl -OutFile $AppFile -UseBasicParsing
} catch {
    Fail "下载失败：$($_.Exception.Message)。如果开了 Clash，把系统代理打开后重试。"
}
$sizeMb = [math]::Round((Get-Item $AppFile).Length / 1MB, 2)
if ($sizeMb -lt 2.0) {
    Fail "下载的文件只有 $sizeMb MB，明显不完整，请重试。"
}
Write-Ok "app_v84_hermes.py ($sizeMb MB)"

# ---------- 6. 语法校验 ----------
Write-Step "校验程序文件"
& $VenvPython -c "import py_compile,sys; py_compile.compile(r'$AppFile', doraise=True); print('compile ok')"
if ($LASTEXITCODE -ne 0) {
    Fail "程序文件校验失败，可能下载时被截断，请重试。"
}
Write-Ok "语法校验通过"

# ---------- 7. 自检 ----------
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

# ---------- 8. 启动脚本 ----------
# 只有检测到本机代理端口真的在监听时才写代理变量，避免 Clash 没开时拖慢抓取。
$proxyLine = ''
$proxyPorts = @(7890, 7897, 10809, 1080)
foreach ($port in $proxyPorts) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $connect = $client.BeginConnect('127.0.0.1', $port, $null, $null)
        $ok = $connect.AsyncWaitHandle.WaitOne(300, $false)
        if ($ok -and $client.Connected) {
            $proxyLine = "set OKOOO_PROXY=http://127.0.0.1:$port"
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
$StartBat = Join-Path $InstallDir 'start.cmd'
$batLines = @(
    '@echo off',
    'chcp 65001 >nul',
    'cd /d "%~dp0"',
    $proxyLine,
    '".venv\Scripts\python.exe" -m streamlit run app_v84_hermes.py',
    'pause'
) | Where-Object { $_ -ne '' }
[System.IO.File]::WriteAllLines($StartBat, $batLines, (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "安装完成。" -ForegroundColor Green
Write-Host ""
Write-Host "  程序：$AppFile"
Write-Host "  启动：双击 $StartBat"
Write-Host "  体检：$VenvPython `"$AppFile`" --diagnose"
Write-Host ""
Write-Host "可选：设置 DEEPSEEK_API_KEY 和 TELEGRAM_BOT_TOKEN 才会启用真实 API 与机器人指令，" -ForegroundColor Gray
Write-Host "      不设置也能正常运行，分析走本地兜底。" -ForegroundColor Gray
Write-Host ""

$answer = Read-Host "现在就启动吗？(Y/n)"
if ($answer -ne 'n' -and $answer -ne 'N') {
    Set-Location $InstallDir
    & $StartBat
}
