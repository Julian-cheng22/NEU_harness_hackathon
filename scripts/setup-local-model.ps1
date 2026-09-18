<#
.SYNOPSIS
    Download llama.cpp (Windows CUDA) + Qwen3.5-9B Q4_K_M, and start llama-server.

.DESCRIPTION
    Reproduces the exact local-inference setup measured on 2026-09-12:

        GPU      RTX 4060 Laptop, 8188 MiB VRAM, compute 8.9, driver 616.92
        model    unsloth/Qwen3.5-9B-GGUF : Qwen3.5-9B-Q4_K_M.gguf (5,680,522,464 bytes)
        server   llama.cpp b10934, win-cuda-12.4-x64
        VRAM     6996 / 8188 MiB with -c 32768 and q8_0 KV cache
        load     ~6 s
        decode   38-39 tok/s sustained

    CUDA 12.4 is chosen over 13.3 for broader driver compatibility; both work
    with driver 616.92.

.EXAMPLE
    .\scripts\setup-local-model.ps1              # download if missing, then start
    .\scripts\setup-local-model.ps1 -StartOnly   # skip downloads
#>
[CmdletBinding()]
param(
    [string]$ModelDir   = "D:\models",
    [string]$LlamaTag   = "b10934",
    [int]   $Port       = 8080,
    [int]   $CtxSize    = 32768,
    [switch]$StartOnly
)

$ErrorActionPreference = "Stop"

$GgufName = "Qwen3.5-9B-Q4_K_M.gguf"
$GgufPath = Join-Path $ModelDir "gguf\$GgufName"
$LlamaDir = Join-Path $ModelDir "llama.cpp"
$Server   = Join-Path $LlamaDir "llama-server.exe"
$HfUrl    = "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/$GgufName`?download=true"

if (-not $StartOnly) {
    New-Item -ItemType Directory -Force -Path (Join-Path $ModelDir "gguf"), $LlamaDir | Out-Null

    if (-not (Test-Path $Server)) {
        Write-Host "Downloading llama.cpp $LlamaTag (win-cuda-12.4-x64)..."
        $tmp  = Join-Path $env:TEMP "llamacpp"
        $base = "https://github.com/ggml-org/llama.cpp/releases/download/$LlamaTag"
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        foreach ($f in @("llama-$LlamaTag-bin-win-cuda-12.4-x64.zip",
                         "cudart-llama-bin-win-cuda-12.4-x64.zip")) {
            & curl.exe -sSL --retry 3 -o (Join-Path $tmp $f) "$base/$f"
            # cudart must land in the same directory as the exe, so both unzip here.
            Expand-Archive -Path (Join-Path $tmp $f) -DestinationPath $LlamaDir -Force
        }
    } else {
        Write-Host "llama.cpp already present at $LlamaDir"
    }

    if (-not (Test-Path $GgufPath)) {
        Write-Host "Downloading $GgufName (~5.7 GB)..."
        # -C - resumes a partial file; a truncated 5.7 GB download is not a
        # thing you want to discover at the venue.
        & curl.exe -L -C - --retry 5 --retry-delay 3 -o $GgufPath $HfUrl
    } else {
        $gb = [math]::Round((Get-Item $GgufPath).Length / 1GB, 2)
        Write-Host "Model already present ($gb GB)"
    }
}

if (-not (Test-Path $Server)) { throw "llama-server.exe not found at $Server" }
if (-not (Test-Path $GgufPath)) { throw "Model not found at $GgufPath" }

# Already listening? Don't start a second copy -- it would fail to bind and the
# error is easy to miss in a hidden window.
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "Port $Port is already in use; assuming llama-server is running."
    return
}

$log = Join-Path $ModelDir "llama-server.log"
$err = Join-Path $ModelDir "llama-server.err.log"

# --jinja is REQUIRED: it enables the model's chat template, which is what makes
# tool calling and the enable_thinking kwarg work at all.
# q8_0 KV cache is what keeps 32K context inside 8 GB alongside the weights.
$serverArgs = @(
    "-m", $GgufPath,
    "-a", "qwen3.5-9b",
    "-ngl", "99",
    "-c", "$CtxSize",
    "--cache-type-k", "q8_0",
    "--cache-type-v", "q8_0",
    "--host", "127.0.0.1",
    "--port", "$Port",
    "--jinja"
)

Write-Host "Starting llama-server on 127.0.0.1:$Port ..."
Start-Process -FilePath $Server -ArgumentList $serverArgs `
    -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden

for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 2
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 3
        Write-Host "Ready after ~$($i * 2)s."
        & "C:\Windows\System32\nvidia-smi.exe" --query-gpu=memory.used,memory.total --format=csv,noheader
        Write-Host ""
        Write-Host "Set HARNESS_LLM=local in .env, then:  python eval/run.py --arm both"
        return
    } catch { }
}

Write-Warning "llama-server did not become ready. Last lines of $err :"
Get-Content $err -Tail 30
