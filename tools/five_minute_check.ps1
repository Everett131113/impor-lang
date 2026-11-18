# 5 分钟陌生人测试（v3.10 第一版）
#
# 目标：一个刚 clone 仓库、没有读文档的人，跑这一条命令就能确认
#  ① 工具链能起来  ② 解释器能跑  ③ 能编出脱离 Python 的 exe
#  ④ 能编出 C ABI 源码包  ⑤ 三后端一致性门禁通过
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File tools/five_minute_check.ps1
#   pwsh -File tools/five_minute_check.ps1
#   powershell -ExecutionPolicy Bypass -File tools/five_minute_check.ps1 -Full
#
# 默认 conformance 等距抽样 30 个用例，给慢机留出 300 s 余量；
# -Full 跑全部用例（作者本机约 180 s）。
#
# 可用环境变量 IMPOR_PYTHON 指定 Python；默认按 python / py -3 顺序找。

param([switch]$Full)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Resolve-Python {
    if ($env:IMPOR_PYTHON -and (Test-Path -LiteralPath $env:IMPOR_PYTHON)) {
        return $env:IMPOR_PYTHON
    }
    foreach ($cand in @("python", "python3")) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    throw "找不到 Python；请安装 Python 3.11+ 或设置 IMPOR_PYTHON"
}

$Python = Resolve-Python
$Steps = [System.Collections.Generic.List[object]]::new()
$Failures = 0

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Body
    )
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Write-Host ""
    Write-Host ("==> " + $Name) -ForegroundColor Cyan
    try {
        & $Body
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        if ($code -ne 0) { throw ("退出码 " + $code) }
        $sw.Stop()
        $Steps.Add([pscustomobject]@{ Name = $Name; Ok = $true; Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 2) })
        Write-Host ("    OK  " + [math]::Round($sw.Elapsed.TotalSeconds, 2) + " s") -ForegroundColor Green
    } catch {
        $sw.Stop()
        $script:Failures++
        $Steps.Add([pscustomobject]@{ Name = $Name; Ok = $false; Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 2) })
        Write-Host ("    FAIL " + $_.Exception.Message) -ForegroundColor Red
    }
}

$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("impor_five_minute_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

try {
    Invoke-Step "① 版本与平台" {
        & $Python Impor.py version
    }

    Invoke-Step "② 解释器跑 hello" {
        & $Python Impor.py run examples/hello.impor
    }

    Invoke-Step "③ 编 exe 并脱离 Python 运行" {
        $exe = Join-Path $Tmp "hello.exe"
        & $Python Impor.py build examples/hello.impor -o $exe
        if ($LASTEXITCODE -ne 0) { throw "build 失败" }
        if (-not (Test-Path -LiteralPath $exe)) { throw "没有生成 $exe" }
        & $exe
        if ($LASTEXITCODE -ne 0) { throw "生成的 exe 运行失败" }
    }

    Invoke-Step "④ C ABI 源码包" {
        $out = Join-Path $Tmp "cabi"
        & $Python Impor.py build --lib --c-source examples/c_abi/ci_lib.impor -o $out
        if ($LASTEXITCODE -ne 0) { throw "C ABI 源码包生成失败" }
        foreach ($f in @("ci_lib.c", "ci_lib.h", "ci_lib.def", "build_msvc.bat", "build_gcc.bat")) {
            if (-not (Test-Path -LiteralPath (Join-Path $out $f))) { throw "缺少 $f" }
        }
    }

    Invoke-Step "⑤ 三后端一致性 conformance" {
        if ($Full) {
            & $Python Impor.py conformance
        } else {
            & $Python Impor.py conformance --sample 30
        }
    }
}
finally {
    Remove-Item -LiteralPath $Tmp -Recurse -Force -ErrorAction SilentlyContinue
}

$Total = ($Steps | Measure-Object -Property Seconds -Sum).Sum
Write-Host ""
Write-Host "---- 5 分钟陌生人测试 ----"
foreach ($s in $Steps) {
    $mark = if ($s.Ok) { "OK  " } else { "FAIL" }
    Write-Host ("  [{0}] {1,-28} {2,8:N2} s" -f $mark, $s.Name, $s.Seconds)
}
Write-Host ("  总计 {0:N2} s / 上限 300 s" -f $Total)
if ($Failures -gt 0) {
    Write-Host ("  结果：{0} 步失败" -f $Failures) -ForegroundColor Red
    exit 1
}
if ($Total -gt 300) {
    Write-Host "  结果：超时（> 300 s）" -ForegroundColor Red
    exit 1
}
Write-Host "  结果：五步全 OK" -ForegroundColor Green
exit 0
