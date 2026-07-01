# ============================================================
# ARIA ORCHESTRATOR v3.2 - Self-Review Mode + Proactive
# ============================================================
param(
    [string]$Model = "qwen2.5:14b",
    [string]$TaskPrompt = "",
    [string]$MemoryPath = "C:\Users\Studi\GitHub-Repos\ARIA\memory"
)

$JournalPath = Join-Path $MemoryPath "journal_aria.txt"
$BaseRepoPath = "C:\Users\Studi\GitHub-Repos\ARIA"
$isSelfReview = $TaskPrompt.StartsWith("SELF_REVIEW_MODE:")

if ($isSelfReview) {
    $TaskPrompt = $TaskPrompt -replace "SELF_REVIEW_MODE:\s*", ""
}

Write-Host "=== ARIA Orchestrator v3.2 $(if ($isSelfReview) {'[SELF-REVIEW MODE]'}) ===" -ForegroundColor Cyan

$maxIterations = 8
$iteration = 0
$actionHistory = [System.Collections.Generic.List[string]]::new()

# === PROMPT SPÉCIALISÉ ===
if ($isSelfReview) {
    $systemPrompt = @"
Tu es ARIA en mode Self-Review.
Tu dois analyser ton propre code (Microsoft.PowerShell_profile.ps1 et aria_orchestrator.ps1).
OBJECTIF :
- Identifier les problèmes, répétitions, faiblesses et risques
- Proposer des améliorations concrètes et actionnables
- Utiliser les tools (read_file, write_file, edit_file) pour lire et modifier ton code
RÈGLES :
- Sois honnête, précise et constructive
- Priorise les problèmes qui impactent la stabilité et l'autonomie
- À la fin, donne une synthèse claire + les modifications recommandées
"@
} else {
    $systemPrompt = @"
Tu es ARIA, un agent autonome professionnel.
Tu peux utiliser les tools : read_file, write_file, edit_file, list_files, run_powershell.
"@
}

# ====================== OUTILS DE BASE ======================
function Read-AriaFile {
    param([string]$Path)
    if (Test-Path $Path) { Get-Content $Path -Raw } else { "Fichier non trouvé : $Path" }
}

function Write-AriaFile {
    param([string]$Path, [string]$Content)
    $Content | Out-File -FilePath $Path -Encoding utf8 -Force
    Write-Host "✅ Fichier écrit : $Path" -ForegroundColor Green
}

# ====================== BOUCLE PRINCIPALE ======================
Write-Host "🚀 Démarrage de la tâche : $TaskPrompt" -ForegroundColor Yellow

while ($iteration -lt $maxIterations) {
    $iteration++
    Write-Host "`n--- Itération $iteration/$maxIterations ---" -ForegroundColor DarkGray

    # Appel à Ollama (ou autre modèle)
    $Body = @{
        model   = $Model
        system  = $systemPrompt
        prompt  = $TaskPrompt + "`n`nHistorique des actions : $($actionHistory -join ' | ')"
        stream  = $false
        options = @{ num_ctx = 8192; temperature = 0.6 }
    } | ConvertTo-Json -Depth 10

    try {
        $Response = Invoke-RestMethod -Uri "http://localhost:11434/api/generate" -Method Post -Body $Body -ContentType "application/json" -TimeoutSec 180
        $Answer = $Response.response

        Write-Host $Answer -ForegroundColor White

        # Détection d'actions (très basique pour l'instant)
        if ($Answer -match "write_file|edit_file|read_file") {
            $actionHistory.Add("Action détectée à l'itération $iteration")
        }

    } catch {
        Write-Host "❌ Erreur lors de l'appel Ollama : $($_.Exception.Message)" -ForegroundColor Red
        break
    }

    # Condition de sortie
    if ($Answer -match "FIN|TERMINÉ|SELF-REVIEW COMPLET") { break }
}

# Log final
"$(Get-Date) | SelfReview: $isSelfReview | Task: $TaskPrompt" | Out-File $JournalPath -Append -Encoding utf8

if (-not $isSelfReview -and $actionHistory.Count -ge 3) {
    Write-Host "`n💡 Proposition : Tape /self-review pour que j'analyse mon propre code." -ForegroundColor Yellow
}

Write-Host "=== Orchestrateur terminé ===" -ForegroundColor Cyan
