# Only a trusted master run may publish tested Windows ZIPs.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$ExpectedSha,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-dev\.[1-9][0-9]*$')]
    [string]$ReleaseVersion
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_REPOSITORY -ne 'hatsu07/pokemon_champions_assistant' -or
    $env:GITHUB_REF -ne 'refs/heads/master') {
    throw 'Release publication is restricted to the trusted master branch.'
}
if (-not $env:GH_TOKEN) { throw 'GH_TOKEN required.' }
$checkedOut = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $checkedOut -ne $ExpectedSha -or $env:GITHUB_SHA -ne $ExpectedSha) {
    throw 'Checkout, event and expected release SHA must match.'
}
$tag = "development-windows-$ReleaseVersion"
$title = "Windows DEVELOPMENT $ReleaseVersion"
# A prerelease number comes from the workflow's monotonically increasing
# run_number; reruns retain their original release identity and SHA.
$names = @(
    "PokemonChampionsAssistant-$ReleaseVersion-windows-x64.zip",
    "PokemonChampionsScreenDataCollector-$ReleaseVersion-windows-x64.zip",
    "PokemonChampionsGPUDiagnostics-$ReleaseVersion-windows-x64.zip"
)
$paths = @()
foreach ($filename in $names) {
    $file = Join-Path 'release-zips' $filename
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing ZIP: $file" }
    $info = Get-Item -LiteralPath $file
    if ($info.Length -le 0 -or $info.Length -ge 2GB) { throw "Invalid release asset size: $file" }
    $paths += $info.FullName
}
$headers = @{
    Authorization = "Bearer $env:GH_TOKEN"
    Accept = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
}
$url = "https://api.github.com/repos/$env:GITHUB_REPOSITORY/releases/tags/$tag"
function Get-DevRelease {
    try {
        return Invoke-RestMethod -Uri $url -Headers $headers -Method Get
    }
    catch {
        $response = $_.Exception.Response
        if ($null -ne $response -and [int]$response.StatusCode -eq 404) { return $null }
        throw
    }
}
function Assert-DevRelease {
    param([object]$Release, [bool]$RequireComplete)
    if ($null -eq $Release) { throw "Release not found: $tag" }
    if ($Release.tag_name -ne $tag -or $Release.name -ne $title -or
        $Release.prerelease -ne $true -or $Release.target_commitish -ne $ExpectedSha) {
        throw "Release metadata does not match trusted DEVELOPMENT master revision."
    }
    if ($RequireComplete) {
        for ($i = 0; $i -lt $names.Count; $i++) {
            $found = @($Release.assets | Where-Object { $_.name -eq $names[$i] })
            if ($found.Count -ne 1 -or [int64]$found[0].size -le 0) {
                throw "Missing or empty release asset: $($names[$i])"
            }
            if ([int64]$found[0].size -ne (Get-Item -LiteralPath $paths[$i]).Length) {
                throw "Release asset size mismatch: $($names[$i])"
            }
        }
    }
}
$release = Get-DevRelease
if ($null -ne $release -and $release.draft -eq $false) {
    Assert-DevRelease -Release $release -RequireComplete $true
    Write-Host "Already published, all assets verified: $tag"
    exit 0
}
if ($null -eq $release) {
    $notesPath = Join-Path $env:RUNNER_TEMP 'windows-development-notes.md'
    @(
        '**DEVELOPMENT_ONLY**, not a production MSI or validated battle assistant.',
        '',
        "version=$ReleaseVersion",
        "commit_sha=$ExpectedSha",
        "workflow_run_id=$env:GITHUB_RUN_ID",
        'release_kind=windows-development-only',
        '',
        'Three ZIPs: desktop GUI, dataset collector, and opt-in GPU crash diagnostics.',
        'Only offline GUI smoke tests were performed.',
        'No raw screenshots, private battle logs or credentials are included.',
        'Trained screen-model accuracy, device capture and live battles are NOT validated.'
    ) | Set-Content -LiteralPath $notesPath -Encoding UTF8

    # gh release create with asset arguments manages the draft lifecycle:
    # internally create draft -> upload ALL assets -> publish prerelease.
    # A draft is not visible through GET /releases/tags/<tag> until published.
    # NEVER create an empty draft and then try to fetch it via the tag API.
    $arguments = @('release', 'create', $tag,
        $paths[0], $paths[1], $paths[2],
        '--repo', $env:GITHUB_REPOSITORY,
        '--target', $ExpectedSha,
        '--title', $title,
        '--notes-file', $notesPath,
        '--prerelease', '--latest=false')
    & gh @arguments
    if ($LASTEXITCODE -ne 0) {
        throw 'GitHub CLI did not complete all asset uploads and publish the prerelease.'
    }
}
# Re-fetch the PUBLISHED prerelease by tag and verify both actual assets.
$release = Get-DevRelease
Assert-DevRelease -Release $release -RequireComplete $true
if ($release.draft -ne $false) { throw 'Release still draft after GitHub CLI publish.' }
Write-Host "Published and verified Windows DEVELOPMENT prerelease: $tag"
