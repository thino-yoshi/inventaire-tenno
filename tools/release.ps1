# Publie une nouvelle version d'Inventaire Tenno sur GitHub.
#   .\tools\release.ps1 1.0.1 "Ce qui change"
#   .\tools\release.ps1 1.0.1 "Ce qui change" -Exe     (ajoute aussi le .exe a la release)
# Les apps installees voient la release au demarrage et se mettent a jour toutes seules.
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Notes = "",
    [switch]$Exe
)
$ErrorActionPreference = "Stop"
$Repo = "thino-yoshi/inventaire-tenno"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version attendue au format 1.2.3" }

# 1) numero de version dans app.py
$app = [IO.File]::ReadAllText("$Root\app.py")
$app = [regex]::Replace($app, 'VERSION = "[^"]*"', "VERSION = `"$Version`"", 1)
[IO.File]::WriteAllText("$Root\app.py", $app, (New-Object Text.UTF8Encoding $false))

# 2) zip du code (jamais les donnees personnelles)
$tmp = Join-Path $env:TEMP "inventaire-tenno-release"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory "$tmp\src\assets" | Out-Null
Copy-Item app.py, index.html, prime_inventory.py, Lancer.bat, LISEZMOI.txt "$tmp\src"
Copy-Item assets\* "$tmp\src\assets"
$zip = "$tmp\inventaire-tenno-src.zip"
Compress-Archive -Path "$tmp\src\*" -DestinationPath $zip
$assets = @($zip)

# 3) .exe (optionnel)
if ($Exe) {
    py -m PyInstaller --onefile --noconsole --name InventaireWarframe --icon "$Root\assets\lotus.ico" `
        --add-data "$Root\index.html;." --add-data "$Root\assets;assets" `
        --distpath "$tmp\dist" --workpath "$tmp\work" --specpath $tmp --noconfirm --log-level WARN app.py
    $assets += "$tmp\dist\InventaireWarframe.exe"
}

# 4) depot GitHub (cree au premier lancement, public pour que les mises a jour soient visibles)
gh repo view $Repo *> $null
if ($LASTEXITCODE -ne 0) {
    gh repo create $Repo --public --description "Inventaire Warframe + prix Warframe Market" --source . --remote origin
}

# 5) commit, tag, push, release
git add -A
git commit -m "Version $Version" | Out-Null
git tag "v$Version"
git push -u origin HEAD --tags
gh release create "v$Version" @assets --repo $Repo --title "Version $Version" --notes $(if ($Notes) { $Notes } else { "Version $Version" })
Write-Host "Version $Version publiee : https://github.com/$Repo/releases/tag/v$Version"
