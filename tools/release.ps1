# Publie une nouvelle version de Lotus Desk.
#   .\tools\release.ps1 1.0.1 "Ce qui change"
# Le script met le numero dans app.py, envoie le code et une etiquette vX sur GitHub.
# C'est GitHub qui construit LotusDesk.exe (sur Windows) et cree la release :
# voir .github/workflows/publier.yml. Les apps installees la voient et se mettent a jour.
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Notes = "",
    [switch]$Exe   # accepte pour compatibilite
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

# 2) commit + etiquette (le texte de l'etiquette = notes de version), puis envoi
# notes passees par un fichier UTF-8 : en argument, PowerShell 5 abime les accents
$notesFile = Join-Path $env:TEMP "lotusdesk-notes.md"
[IO.File]::WriteAllText($notesFile, $(if ($Notes) { $Notes } else { "Version $Version" }), (New-Object Text.UTF8Encoding $false))
git add -A
git commit -m "Version $Version" | Out-Null
git tag -a "v$Version" -F $notesFile
git push origin HEAD
git push origin "v$Version"
Write-Host "Version $Version envoyee. GitHub construit l'exe (~5 min) :"
Write-Host "  https://github.com/$Repo/actions"
Write-Host "  puis https://github.com/$Repo/releases/tag/v$Version"
