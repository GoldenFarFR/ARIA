"""Simulation d'attaque du serveur ARIA — red-team automatisé, en-process.

But (demande opérateur) : forcer la sécurité tous les jours avec des milliers de requêtes
malveillantes pour trouver les problèmes AVANT que de vrais auditeurs (ou attaquants) ne les
trouvent. Tourne EN-PROCESS (app FastAPI chargée en mémoire, httpx ASGITransport) : zéro
impact prod, zéro coût LLM, déterministe, rapide.

Composants :
  - corpus.py  : payloads d'attaque (injections, fuzzing, bypass auth…).
  - harness.py : moteur — introspecte TOUTES les routes, tire le corpus sur chaque point
                 d'injection, vérifie les invariants de sécurité, renvoie un rapport.
  - run.py     : entrée CLI (résumé + rapport JSON + code de sortie).
"""
from .harness import Finding, Report, run_attack_simulation  # noqa: F401
