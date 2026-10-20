# Publie une nouvelle version de Lotus Desk sur GitHub.
#   .\tools\release.ps1 1.0.1 "Ce qui change"
# La release ne contient que l'exe (LotusDesk.exe) : les apps installees le voient au demarrage
# et se mettent a jour toutes seules.
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Notes = "",
    [switch]$Exe   # accepte pour compatibilite : l'exe est toujours construit
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

$tmp = Join-Path $env:TEMP "inventaire-tenno-release"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $tmp | Out-Null

# 2) l'exe
py -m PyInstaller --onefile --noconsole --name LotusDesk --icon "$Root\assets\lotus.ico" `
    --add-data "$Root\index.html;." --add-data "$Root\assets;assets" `
    --hidden-import webview --hidden-import pystray._win32 --hidden-import reliques_overlay --hidden-import chat_jeu --hidden-import tkinter --collect-all webview `
    --distpath "$tmp\dist" --workpath "$tmp\work" --specpath $tmp --noconfirm --log-level WARN app.py
if (-not (Test-Path "$tmp\dist\LotusDesk.exe")) { throw "construction de l'exe impossible" }
$assets = @("$tmp\dist\LotusDesk.exe")
# TRANSITION : les versions <= 1.9.0 cherchent encore l'ancien nom pour se mettre a jour.
# A retirer quand toutes les installations sont en 1.9.1 ou plus.
Copy-Item "$tmp\dist\LotusDesk.exe" "$tmp\dist\InventaireWarframe.exe"
$assets += "$tmp\dist\InventaireWarframe.exe"

# 3) depot GitHub (cree au premier lancement, public pour que les mises a jour soient visibles)
gh repo view $Repo *> $null
if ($LASTEXITCODE -ne 0) {
    gh repo create $Repo --public --description "Lotus Desk : inventaire Warframe + prix Warframe Market" --source . --remote origin
}

# 4) commit, tag, push, release
git add -A
git commit -m "Version $Version" | Out-Null
git tag "v$Version"
git push -u origin HEAD --tags
# notes passees par un fichier UTF-8 : en argument, PowerShell 5 abime les accents
$notesFile = "$tmp\notes.md"
[IO.File]::WriteAllText($notesFile, $(if ($Notes) { $Notes } else { "Version $Version" }), (New-Object Text.UTF8Encoding $false))
gh release create "v$Version" @assets --repo $Repo --title "Version $Version" --notes-file $notesFile
Write-Host "Version $Version publiee : https://github.com/$Repo/releases/tag/v$Version"
