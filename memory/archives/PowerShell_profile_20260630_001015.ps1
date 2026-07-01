# ===============================================================================
# CONFIGURATION ARIA v1.0.2 - MOTEUR MULTI-AGENT (Ollama / Grok / Groq)
# ===============================================================================
# 🛑 DIRECTIVES POUR LES IA - NE PAS SUPPRIMER
# Ce script tourne dans un profil Windows PowerShell classique.
#
# RÈGLES DE SÉCURITÉ STRICTES :
# 1. Les clés API sont maintenant chargées via variables d'environnement Windows
#    (jamais en clair dans le code).
# 2. Modèle Grok corrigé → "grok-3"
# 3. PAS DE STREAMING API (stream = $false obligatoire)
# 4. Détection Mode Agent largement améliorée (v1.0.2)
# ===============================================================================

# === CLÉS API via variables d'environnement (SÉCURISÉ) ===
$GrokApiKey = $env:GROK_API_KEY
$GroqApiKey = $env:GROQ_API_KEY
$ThirdApiKey = $env:THIRD_API_KEY   # Optionnel

if ([string]::IsNullOrWhiteSpace($GrokApiKey)) {
    Write-Host "⚠️  GROK_API_KEY non définie dans les variables d'environnement !" -ForegroundColor Red
}
if ([string]::IsNullOrWhiteSpace($GroqApiKey)) {
    Write-Host "⚠️  GROQ_API_KEY non définie dans les variables d'environnement !" -ForegroundColor Red
}

# Modèle local principal
$OllamaModel = "qwen2.5:14b"

# Chemin vers la structure
$BaseRepoPath = "C:\Users\Studi\GitHub-Repos\ARIA"
$MemoryPath   = "$BaseRepoPath\memory"
if (-not (Test-Path $MemoryPath)) { New-Item -ItemType Directory -Path $MemoryPath -Force | Out-Null }

# Variables de session
$Global:ActiveContextText = ""
$Global:ActiveContextName = "Aucun"

# ===============================================================================
# BASE DE PERSONNALITÉ
# ===============================================================================
$GlobalSystemPrompt = @"
Tu es ARIA, une IA autonome professionnelle de haut niveau.

OBJECTIF : Aider efficacement tout en participant activement au perfectionnement des processus.

RÈGLES :
- Réponds de manière structurée et professionnelle.
- Sois directe, va à l'essentiel.
- Propose tes améliorations directement.
- Adopte un ton calme et collaboratif.
"@

# ===============================================================================
# Vérification du modèle au démarrage
# ===============================================================================
Write-Host "🦙 Vérification du modèle Ollama : $OllamaModel ..." -ForegroundColor Yellow
try {
    $models = ollama list | Out-String
    if ($models -notmatch [regex]::Escape($OllamaModel)) {
        Write-Host "⚠️  Le modèle '$OllamaModel' n'est pas installé !" -ForegroundColor Red
    } else {
        Write-Host "✅ Modèle '$OllamaModel' détecté." -ForegroundColor Green
    }
} catch {
    Write-Host "⚠️ Impossible de vérifier Ollama (assure-toi qu'il est lancé)" -ForegroundColor Yellow
}

# ===============================================================================
# ORCHESTRATEUR (routage)
# ===============================================================================
function Invoke-Grok($Prompt) {
    Write-Host "`n🤖 [ORCHESTRATEUR] : Routage vers GROK..." -ForegroundColor Magenta
    $Url = "https://api.x.ai/v1/chat/completions"

    $Body = @{
        model    = "grok-3"
        messages = @(
            @{ role = "system"; content = $GlobalSystemPrompt },
            @{ role = "user"; content = $Prompt }
        )
    } | ConvertTo-Json -Depth 10

    $Headers = @{
        "Authorization" = "Bearer $GrokApiKey"
        "Content-Type"  = "application/json"
    }

    try {
        $Response = Invoke-RestMethod -Uri $Url -Method Post -Body $Body -Headers $Headers
        Write-Host ""
        Write-Host " ┌────────────────────────────────────────────────────────────┐" -ForegroundColor Cyan
        Write-Host " │ ARIA v1.0.2 | GROK-3                                       │" -ForegroundColor Cyan
        Write-Host " ├────────────────────────────────────────────────────────────┤" -ForegroundColor Cyan
        Write-Host ""
        Write-Host $Response.choices[0].message.content -ForegroundColor White
        Write-Host ""
        Write-Host " └────────────────────────────────────────────────────────────┘" -ForegroundColor Cyan
    }
    catch { Write-Host "`n❌ Erreur Grok : $($_.Exception.Message)" -ForegroundColor Red }
}

function Invoke-Groq($Prompt) {
    Write-Host "`n⚡ [ORCHESTRATEUR] : Routage vers GROQ..." -ForegroundColor Yellow
    $Url = "https://api.groq.com/openai/v1/chat/completions"

    $Body = @{
        model    = "llama3-70b-8192"
        messages = @(
            @{ role = "system"; content = $GlobalSystemPrompt },
            @{ role = "user"; content = $Prompt }
        )
    } | ConvertTo-Json -Depth 10

    $Headers = @{
        "Authorization" = "Bearer $GroqApiKey"
        "Content-Type"  = "application/json"
    }

    try {
        $Response = Invoke-RestMethod -Uri $Url -Method Post -Body $Body -Headers $Headers
        Write-Host ""
        Write-Host " ┌────────────────────────────────────────────────────────────┐" -ForegroundColor Cyan
        Write-Host " │ ARIA v1.0.2 | GROQ (LLaMA-3 70B)                           │" -ForegroundColor Cyan
        Write-Host " ├────────────────────────────────────────────────────────────┤" -ForegroundColor Cyan
        Write-Host ""
        Write-Host $Response.choices[0].message.content -ForegroundColor White
        Write-Host ""
        Write-Host " └────────────────────────────────────────────────────────────┘" -ForegroundColor Cyan
    }
    catch { Write-Host "`n❌ Erreur Groq : $($_.Exception.Message)" -ForegroundColor Red }
}

function Invoke-Ollama($Prompt) {
    Write-Host "`n🦙 [ORCHESTRATEUR] : Routage vers OLLAMA ($OllamaModel)..." -ForegroundColor Green
    $Url = "http://localhost:11434/api/generate"

    $FinalPrompt = $Prompt
    if (-not [string]::IsNullOrWhiteSpace($Global:ActiveContextText)) {
        $FinalPrompt = "CONTEXTE:`n$($Global:ActiveContextText)`n`nREQUÊTE:`n$Prompt"
    }

    $Body = @{
        model   = $OllamaModel
        system  = $GlobalSystemPrompt
        prompt  = $FinalPrompt
        stream  = $false
        options = @{ num_ctx = 8192; temperature = 0.7; top_p = 0.9 }
    } | ConvertTo-Json -Depth 10

    try {
        Write-Host "⏳ Génération en cours..." -ForegroundColor DarkGray
        $Response = Invoke-RestMethod -Uri $Url -Method Post -Body $Body -ContentType "application/json" -TimeoutSec 300
        Write-Host ""
        Write-Host " ┌────────────────────────────────────────────────────────────┐" -ForegroundColor Cyan
        Write-Host " │ ARIA v1.0.2 | $($OllamaModel.PadRight(40))│" -ForegroundColor Cyan
        Write-Host " ├────────────────────────────────────────────────────────────┤" -ForegroundColor Cyan
        Write-Host ""
        Write-Host $Response.response -ForegroundColor White
        Write-Host ""
        Write-Host " └────────────────────────────────────────────────────────────┘" -ForegroundColor Cyan
    }
    catch { Write-Host "`n❌ Erreur Ollama : $($_.Exception.Message)" -ForegroundColor Red }
}

# ===============================================================================
# APPEL ORCHESTRATEUR EXTERNE (Agent)
# ===============================================================================
function Invoke-AriaAgent($TaskPrompt) {
    $orchestratorPath = "C:\Users\Studi\GitHub-Repos\ARIA\scripts\PowerShell\aria_orchestrator.ps1"
    if (Test-Path $orchestratorPath) {
        & $orchestratorPath -Model $OllamaModel -TaskPrompt $TaskPrompt -MemoryPath $MemoryPath
    } else {
        Write-Host "`n❌ Orchestrateur non trouvé : $orchestratorPath" -ForegroundColor Red
    }
}

# ===============================================================================
# GESTION DES ENTRÉES
# ===============================================================================
function Read-AriaInput {
    Write-Host "`nARIA > " -NoNewline -ForegroundColor Cyan
    $Line = [Console]::ReadLine()
    if ([string]::IsNullOrWhiteSpace($Line)) { return "" }
    return $Line.Trim()
}

# ===============================================================================
# TABLEAU DE BORD
# ===============================================================================
function dash {
    Clear-Host
    $consoleWidth = $Host.UI.RawUI.WindowSize.Width
    $InnerWidth = [math]::Max(80, $consoleWidth - 4)

    $TopBorder    = "┌" + ("─" * $InnerWidth) + "┐"
    $MiddleBorder = "├" + ("─" * $InnerWidth) + "┤"
    $BottomBorder = "└" + ("─" * $InnerWidth) + "┘"

    $mod = if ($OllamaModel.Length -gt 16) { $OllamaModel.Substring(0,13) + "..." } else { $OllamaModel.PadRight(16) }
    $ctx = if ($Global:ActiveContextName.Length -gt 16) { $Global:ActiveContextName.Substring(0,13) + "..." } else { $Global:ActiveContextName.PadRight(16) }

    Write-Host $TopBorder -ForegroundColor Cyan
    $title = "✨ SUPER ARIA KART v1.0.2 ✨"
    $titlePad = " " * [math]::Floor(($InnerWidth - $title.Length) / 2)
    $titleLine = ($titlePad + $title + $titlePad).PadRight($InnerWidth)
    Write-Host ("│" + $titleLine + "│") -ForegroundColor Magenta

    Write-Host $MiddleBorder -ForegroundColor Cyan
    Write-Host "│ " -NoNewline -ForegroundColor Cyan
    Write-Host "[MOTEUR]" -NoNewline -ForegroundColor DarkYellow
    Write-Host " Hybride Multi-Agent │ " -NoNewline -ForegroundColor White
    Write-Host "[MODÈLE]" -NoNewline -ForegroundColor White
    Write-Host " $mod" -NoNewline -ForegroundColor Green
    Write-Host " │ " -NoNewline -ForegroundColor White
    Write-Host "[CONTEXTE]" -NoNewline -ForegroundColor White
    Write-Host " $ctx" -NoNewline -ForegroundColor Yellow
    Write-Host "│" -ForegroundColor Cyan

    Write-Host $MiddleBorder -ForegroundColor Cyan
    $subtitle = "⚔ ITEM BOX : COMMANDES ⚔"
    $subPad = " " * [math]::Floor(($InnerWidth - $subtitle.Length) / 2)
    $subLine = ($subPad + $subtitle + $subPad).PadRight($InnerWidth)
    Write-Host ("│" + $subLine + "│") -ForegroundColor Yellow

    Write-Host $MiddleBorder -ForegroundColor Cyan
    $col1 = [math]::Floor($InnerWidth * 0.32)
    $col2 = [math]::Floor($InnerWidth * 0.33)
    $col3 = $InnerWidth - $col1 - $col2 - 6

    Write-Host ("│ " + ("/use    : Menu Contextes").PadRight($col1) + "│ " + ("/paste  : Coller Presse").PadRight($col2) + "│ " + ("/grok   : Mode Grok").PadRight($col3) + "│") -ForegroundColor White
    Write-Host ("│ " + ("/add    : Src Web (Scrap)").PadRight($col1) + "│ " + ("/clear  : Vider Contexte").PadRight($col2) + "│ " + ("/groq   : Mode Groq").PadRight($col3) + "│") -ForegroundColor White
    Write-Host ("│ " + ("/list   : Lister dossiers").PadRight($col1) + "│ " + ("/ollama : Mode Ollama").PadRight($col2) + "│ " + ("exit    : Fermer KART").PadRight($col3) + "│") -ForegroundColor White
    Write-Host $BottomBorder -ForegroundColor Cyan
}

# ===============================================================================
# MENU RAPIDE
# ===============================================================================
function Select-AriaContext {
    if (-not (Test-Path $MemoryPath)) { New-Item -ItemType Directory -Path $MemoryPath -Force | Out-Null }
    $folders = @(Get-ChildItem -Path $MemoryPath -Directory)
    if ($folders.Count -eq 0) { Write-Host "⚠️ Aucun dossier trouvé." -ForegroundColor Yellow; return }

    Write-Host "`n📂 SELECTION DU CONTEXTE :" -ForegroundColor Cyan
    for ($i = 0; $i -lt $folders.Count; $i++) { Write-Host " [$($i + 1)] - $($folders[$i].Name)" -ForegroundColor Yellow }
    Write-Host " [q] - Annuler" -ForegroundColor Gray

    $Selection = (Read-Host "`nNuméro du dossier").Trim()
    if ($Selection -eq 'q') { dash; return }

    if ($Selection -match "^\d+$" -and [int]$Selection -ge 1 -and [int]$Selection -le $folders.Count) {
        $targetFolder = $folders[[int]$Selection - 1]
        $files = Get-ChildItem -Path $targetFolder.FullName -File -Include *.md, *.txt, *.py, *.ps1, *.json -Recurse
        if ($files.Count -gt 0) {
            $Global:ActiveContextText = $files | Get-Content -Raw | Out-String
            $Global:ActiveContextName = $targetFolder.Name
            dash
            Write-Host "✅ Contexte chargé : $($targetFolder.Name) ($($files.Count) fichiers)" -ForegroundColor Green
        } else { Write-Host "⚠️ Aucun fichier compatible." -ForegroundColor Yellow }
    } else { Write-Host "❌ Sélection invalide." -ForegroundColor Red }
}

function Add-AriaDoc {
    if (-not (Test-Path $MemoryPath)) { New-Item -ItemType Directory -Path $MemoryPath -Force | Out-Null }
    $folders = @(Get-ChildItem -Path $MemoryPath -Directory)
    Write-Host "`n📁 SÉLECTION DU DOSSIER" -ForegroundColor Cyan
    if ($folders.Count -gt 0) {
        for ($i = 0; $i -lt $folders.Count; $i++) { Write-Host " [$($i + 1)] - $($folders[$i].Name)" -ForegroundColor Yellow }
    }
    $InputSelection = Read-Host "Numéro du dossier (ou nom nouveau)"
    $Selection = $InputSelection.Trim()
    if ($Selection -match "^\d+$" -and [int]$Selection -ge 1 -and [int]$Selection -le $folders.Count) {
        $Sujet = $folders[[int]$Selection - 1].Name
    } else { $Sujet = $Selection }
    if ([string]::IsNullOrWhiteSpace($Sujet)) { return }
    $TargetDir = Join-Path $MemoryPath $Sujet
    if (-not (Test-Path $TargetDir)) { New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null }
    while ($true) {
        $Url = Read-Host "URL (ou 'q' pour quitter)"
        if ($Url.Trim() -eq "q") { break }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
            $txt = $response.Content -replace '(?s)<script[^>]*>.*?</script>', '' -replace '(?s)<style[^>]*>.*?</style>', '' -replace '<[^>]*>', ''
            $txt = [System.Net.WebUtility]::HtmlDecode($txt) -replace '\s+', ' '
            $fileName = "$(Get-Date -Format 'yyyyMMdd_HHmm')_source.md"
            $txt | Out-File (Join-Path $TargetDir $fileName) -Encoding utf8
            Write-Host "✅ Sauvegardé !" -ForegroundColor Green
        } catch { Write-Host "❌ Erreur : $($_.Exception.Message)" -ForegroundColor Red }
    }
}

# ===============================================================================
# MOTEUR PRINCIPAL
# ===============================================================================
function aria {
    dash
    while ($true) {
        $S = Read-AriaInput
        if ([string]::IsNullOrWhiteSpace($S)) { continue }
        if ($S -eq "exit") { break }

        if ($S -eq "/paste") {
            $clipboard = (Get-Clipboard) -join "`n"
            if ([string]::IsNullOrWhiteSpace($clipboard)) {
                Write-Host "⚠️ Presse-papiers vide." -ForegroundColor Yellow
                continue
            }
            $S = $clipboard
            Write-Host "`n📋 Contenu importé depuis le presse-papiers." -ForegroundColor Green
        }

        if ($S -eq "/add") { Add-AriaDoc; dash }
        elseif ($S -eq "/list") { Get-ChildItem $MemoryPath -Directory | ForEach-Object { Write-Host " ├─ $($_.Name)" -ForegroundColor Green } }
        elseif ($S.StartsWith("/use")) { Select-AriaContext }
        elseif ($S -eq "/clear") { $Global:ActiveContextText = ""; $Global:ActiveContextName = "Aucun"; dash }
        else {
            $Override = $null
            if ($S.StartsWith("/grok")) { $Override = "GROK"; $S = $S.Substring(5).Trim() }
            elseif ($S.StartsWith("/groq")) { $Override = "GROQ"; $S = $S.Substring(5).Trim() }
            elseif ($S.StartsWith("/ollama")) { $Override = "CHAT"; $S = $S.Substring(7).Trim() }

            $Intent = if ($Override) { $Override } else { "CHAT/AGENT" }

            $LogEntry = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | Mode: $Intent | Prompt: $S"
            $LogEntry | Out-File (Join-Path $MemoryPath "conversation_history.log") -Append -Encoding UTF8

            # === DÉTECTION MODE AGENT AMÉLIORÉE v1.0.2 ===
            $agentTriggers = "exécute|crée|modifie|écris|update|lis |lit |affiche|montre|contenu|read|list|liste|supprime|rename|move|copy|fichier|dossier|powershell|commande|agent|ouvre|ferme"
            if ($S.ToLower() -match $agentTriggers) {
                Write-Host "`n⚙️ Mode Agent détecté → Lancement de l'orchestrateur v3..." -ForegroundColor Magenta
                Invoke-AriaAgent -TaskPrompt $S
            }
            elseif ($Intent -eq "GROK") { Invoke-Grok -Prompt $S }
            elseif ($Intent -eq "GROQ") { Invoke-Groq -Prompt $S }
            else { Invoke-Ollama -Prompt $S }
        }
    }
}

Set-Alias -Name ed -Value code
function rc { . $PROFILE; Write-Host "🔄 Profil rechargé !" -ForegroundColor Green }
dash
Write-Host "`n✅ ARIA v1.0.2 prête. Tape 'aria' pour démarrer." -ForegroundColor Green
# === SELF REVIEW COMMAND ===
function self-review {
    Write-Host "
🔄 Lancement du Self Review ARIA..." -ForegroundColor Magenta
    $Prompt = "Effectue un Self Review complet selon le format défini dans ARIA_MEMORY.txt section 18. Sois honnête, structurée et propose des améliorations concrètes."
    Invoke-Ollama -Prompt $Prompt
}
