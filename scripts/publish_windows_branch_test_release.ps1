# Explicit manual branch test release. Never replaces the trusted master publisher.
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
    $env:GITHUB_EVENT_NAME -ne 'workflow_dispatch' -or
    $env:GITHUB_ACTOR -ne 'hatsu07' -or
    $env:GITHUB_REF -eq 'refs/heads/master' -or
    $env:GITHUB_REF -ne "refs/heads/$env:GITHUB_REF_NAME" -or
    [string]::IsNullOrWhiteSpace($env:GITHUB_REF_NAME)) {
    throw 'Branch test publication requires owner workflow_dispatch on an explicitly selected non-master branch.'
}
if (-not $env:GH_TOKEN) { throw 'GH_TOKEN required.' }
if ($env:GITHUB_RUN_ID -notmatch '^[1-9][0-9]*$') {
    throw 'Missing valid GitHub workflow run ID.'
}
$checkedOut = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $checkedOut -ne $ExpectedSha -or $env:GITHUB_SHA -ne $ExpectedSha) {
    throw 'Checkout, event and expected branch test SHA must match.'
}
$branch = $env:GITHUB_REF_NAME
$tag = "development-branch-windows-$($ExpectedSha.Substring(0,12))-run$env:GITHUB_RUN_ID"
$title = "Windows BRANCH TEST $branch @ $($ExpectedSha.Substring(0,12)) (run $env:GITHUB_RUN_ID)"
# Different tag namespace from master DEVELOPMENT releases; automatic master
# release cleanup intentionally never deletes branch-test tags or artifacts.
$names = @(
    "PokemonChampionsAssistant-$ReleaseVersion-windows-x64.zip",
    "PokemonChampionsScreenDataCollector-$ReleaseVersion-windows-x64.zip"
)
$paths = @()
foreach ($filename in $names) {
    $file = Join-Path 'release-zips' $filename
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing ZIP: $file" }
    $info = Get-Item -LiteralPath $file
    if ($info.Length -le 0 -or $info.Length -ge 2GB) { throw "Invalid branch-test asset size: $file" }
    $paths += $info.FullName
}
$headers = @{
    Authorization = "Bearer $env:GH_TOKEN"
    Accept = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
}
$url = "https://api.github.com/repos/$env:GITHUB_REPOSITORY/releases/tags/$tag"
function Get-BranchTestRelease {
    try {
        return Invoke-RestMethod -Uri $url -Headers $headers -Method Get
    }
    catch {
        $response = $_.Exception.Response
        if ($null -ne $response -and [int]$response.StatusCode -eq 404) { return $null }
        throw
    }
}
function Assert-BranchTestRelease {
    param([object]$Release)
    if ($null -eq $Release -or
        $Release.tag_name -ne $tag -or
        $Release.name -ne $title -or
        $Release.prerelease -ne $true -or
        $Release.draft -ne $false -or
        $Release.target_commitish -ne $ExpectedSha) {
        throw 'Branch-test release identity or verified commit differs from requested branch.'
    }
    if (@($Release.assets).Count -ne $names.Count) {
        throw 'Branch-test release must contain exactly two ZIP assets.'
    }
    for ($i = 0; $i -lt $names.Count; $i++) {
        $found = @($Release.assets | Where-Object { $_.name -eq $names[$i] })
        if ($found.Count -ne 1 -or [int64]$found[0].size -le 0 -or
            [int64]$found[0].size -ne (Get-Item -LiteralPath $paths[$i]).Length) {
            throw "Branch-test asset size/name mismatch: $($names[$i])"
        }
    }
    # Verify the actual lightweight Git ref points at the event SHA, not just
    # editable release metadata.
    $refUrl = "https://api.github.com/repos/$env:GITHUB_REPOSITORY/git/ref/tags/$tag"
    $tagRef = Invoke-RestMethod -Uri $refUrl -Headers $headers -Method Get
    if ($tagRef.object.type -ne 'commit' -or $tagRef.object.sha -ne $ExpectedSha) {
        throw 'Branch-test Git tag does not point to selected commit.'
    }
}
$release = Get-BranchTestRelease
if ($null -ne $release) {
    Assert-BranchTestRelease -Release $release
    Write-Host "Already published, branch-test identity and both assets verified: $tag"
    exit 0
}
$notesPath = Join-Path $env:RUNNER_TEMP 'windows-branch-test-notes.md'
@(
    '**BRANCH_TEST_ONLY / DEVELOPMENT_ONLY**. Manual non-master build, NOT the master DEVELOPMENT release.',
    '',
    "source_branch=$branch",
    "source_ref=$env:GITHUB_REF",
    "commit_sha=$ExpectedSha",
    "version=$ReleaseVersion",
    "workflow_run_id=$env:GITHUB_RUN_ID",
    "release_kind=windows-manual-branch-test-only",
    '',
    'Two ZIPs: desktop GUI and dataset collector. Run-GPU-Diagnostics.cmd is excluded; developer GPU diagnostic CLI remains opt-in.',
    'Windows desktop and OCR offline smoke were executed; full PR CI parity and real Pokémon Champions play are NOT certified by this branch build.',
    'No real screen captures, private battle logs, credentials or model-validation claims are included.',
    'Do not treat this prerelease as a master release or enable production LOCAL recommendations.'
) | Set-Content -LiteralPath $notesPath -Encoding UTF8
$arguments = @('release', 'create', $tag,
    $paths[0], $paths[1],
    '--repo', $env:GITHUB_REPOSITORY,
    '--target', $ExpectedSha,
    '--title', $title,
    '--notes-file', $notesPath,
    '--prerelease', '--latest=false')
& gh @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'GitHub CLI did not complete branch-test asset uploads and prerelease publication.'
}
$release = Get-BranchTestRelease
Assert-BranchTestRelease -Release $release
Write-Host "Published and verified Windows BRANCH_TEST_ONLY prerelease: $tag"
