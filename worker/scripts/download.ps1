param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Url,

    [string]$Proxy,

    [switch]$NoProxy,

    [string]$CookiesBrowser = "firefox",

    [string]$OutputDir = "."
)

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

if ((-not $Proxy) -and (-not $NoProxy)) {
    $configPath = Join-Path (Get-Location) "config.json"
    if (Test-Path $configPath) {
        $config = Get-Content $configPath -Raw | ConvertFrom-Json
        if ($config.'yt-download-proxy') {
            $Proxy = $config.'yt-download-proxy'
        }
    }
}

# ===== Download phase =====
$proxyArgs = @()
if (-not $NoProxy) {
    $proxyArgs = @("--proxy", $Proxy)
}

Write-Output "========================================"
Write-Output "  YouTube Download & Subtitle Fix"
Write-Output "========================================"
Write-Output "URL: $Url"

$dlpArgs = @(
    "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    "-S", "res:480"
    "--embed-metadata"
    "--merge-output-format", "mp4"
    "--write-auto-subs"
    "--sub-langs", "en-orig"
    "--sub-format", "srt"
    "--cookies-from-browser", $CookiesBrowser
    "-o", "%(title)s.mp4"
) + $proxyArgs + @(
    "--js-runtimes", "node"
    $Url
)

$existingSrt = @{}
Get-ChildItem -Path $OutputDir -Filter "*.srt" -File | ForEach-Object { $existingSrt[$_.FullName] = $true }

Write-Output ""
Write-Output "--- Downloading ---"
& yt-dlp @dlpArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "yt-dlp failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

# ===== Subtitle acquisition =====
# YouTube withholds auto captions from the default clients unless a PO token is
# provided, and yt-dlp still exits 0 with no subtitle file. The embedded web
# client serves the same en-orig track without a PO token, so retry with it
# before giving up. Exit code 3 tells the worker "video downloaded, no SRT".
$srtFiles = Get-ChildItem -Path $OutputDir -Filter "*.srt" -File | Where-Object { -not $existingSrt.ContainsKey($_.FullName) }

if ($srtFiles.Count -eq 0) {
    Write-Output ""
    Write-Output "No subtitles downloaded by the default client. Retrying with web_embedded client..."
    $fallbackArgs = @(
        "--skip-download"
        "--write-auto-subs"
        "--sub-langs", "en-orig"
        "--sub-format", "srt"
        "--extractor-args", "youtube:player_client=web_embedded"
        "--cookies-from-browser", $CookiesBrowser
        "-o", "%(title)s.%(ext)s"
    ) + $proxyArgs + @(
        "--js-runtimes", "node"
        $Url
    )
    & yt-dlp @fallbackArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Output "Subtitle fallback exited with code $LASTEXITCODE."
    }
    $srtFiles = Get-ChildItem -Path $OutputDir -Filter "*.srt" -File | Where-Object { -not $existingSrt.ContainsKey($_.FullName) }
}

if ($srtFiles.Count -eq 0) {
    Write-Error "No English subtitles could be downloaded (default and web_embedded clients). YouTube may not have generated captions for this video yet."
    exit 3
}

# ===== Repair phase =====
Write-Output ""
Write-Output "--- Repairing SRT subtitle overlaps ---"

foreach ($srt in $srtFiles) {
    $outPath = Join-Path $OutputDir ($srt.Name -replace '-orig', '')

    Write-Output "  Processing: $($srt.Name) -> $(Split-Path $outPath -Leaf)"

    $lines = [System.IO.File]::ReadAllLines($srt.FullName, $Utf8NoBom)
    $subtitles = @()
    $currentBlock = @()

    foreach ($line in $lines) {
        if ($line.Trim() -eq '') {
            if ($currentBlock.Count -ge 3) {
                $index = $currentBlock[0].Trim()
                $timeLine = $currentBlock[1]
                $timeMatch = $timeLine -match '(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})'
                if ($timeMatch) {
                    $subtitles += [PSCustomObject]@{
                        Index = $index
                        StartTime = $matches[1]
                        EndTime = $matches[2]
                        Text = ($currentBlock[2..($currentBlock.Count-1)] -join "`n")
                    }
                }
            }
            $currentBlock = @()
        } else {
            $currentBlock += $line
        }
    }

    if ($currentBlock.Count -ge 3) {
        $index = $currentBlock[0].Trim()
        $timeLine = $currentBlock[1]
        $timeMatch = $timeLine -match '(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})'
        if ($timeMatch) {
            $subtitles += [PSCustomObject]@{
                Index = $index
                StartTime = $matches[1]
                EndTime = $matches[2]
                Text = ($currentBlock[2..($currentBlock.Count-1)] -join "`n")
            }
        }
    }

    for ($i = 0; $i -lt $subtitles.Count - 1; $i++) {
        $subtitles[$i].EndTime = $subtitles[$i + 1].StartTime
    }

    $outputLines = @()
    for ($i = 0; $i -lt $subtitles.Count; $i++) {
        $outputLines += $subtitles[$i].Index
        $outputLines += "$($subtitles[$i].StartTime) --> $($subtitles[$i].EndTime)"
        $outputLines += $subtitles[$i].Text.Split("`n")
        if ($i -lt $subtitles.Count - 1) {
            $outputLines += ''
        }
    }

    [System.IO.File]::WriteAllLines($outPath, $outputLines, $Utf8NoBom)

    Remove-Item $srt.FullName
}

Write-Output ""
Write-Output "========================================"
Write-Output "  Complete! Processed files:"
$srtFiles | ForEach-Object {
    Write-Output "    $($_.Name -replace '-orig', '')"
}
Write-Output "========================================"