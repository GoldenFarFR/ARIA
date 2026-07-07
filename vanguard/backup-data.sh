#!/usr/bin/env bash
# Sauvegarde des DONNÉES ARIA (/opt/aria-data) — la preuve accumulée que GitHub ne contient PAS.
#
# GitHub sauvegarde le CODE. Les DONNÉES (track-record, carnet, prédictions, SQLite) vivent
# dans /opt/aria-data sur le VPS. Si ce disque meurt, la preuve est perdue. Ce script en fait
# un instantané compressé, horodaté, à conserver AILLEURS que sur le VPS.
#
# Lecture seule sur les données. N'écrit que dans le dossier de backup. Aucun secret affiché.
#
# Installation (sur le VPS) :
#   chmod +x /opt/aria/vanguard/backup-data.sh
#   # test manuel :
#   /opt/aria/vanguard/backup-data.sh
#   # automatique tous les jours à 03:30 (crontab -e) :
#   30 3 * * * /opt/aria/vanguard/backup-data.sh >> /var/log/aria-backup.log 2>&1
#
# Hors-site (recommandé, à configurer selon ton hébergeur) : après le dump local, copier
#   $DEST vers un stockage distant (rclone vers un bucket, ou scp vers une autre machine).
#   Exemple (si rclone configuré) :  rclone copy "$ARCHIVE" remote:aria-backups/
set -euo pipefail

DATA_DIR="${ARIA_DATA_DIR:-/opt/aria-data}"
DEST="${ARIA_BACKUP_DIR:-/opt/aria-backups}"
KEEP_DAYS="${ARIA_BACKUP_KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
ARCHIVE="${DEST}/aria-data-${STAMP}.tar.gz"

if [ ! -d "$DATA_DIR" ]; then
  echo "[backup] ERREUR : $DATA_DIR introuvable — rien à sauvegarder." >&2
  exit 1
fi

mkdir -p "$DEST"

# Instantané cohérent : SQLite tolère une copie à chaud, mais on privilégie `.backup` si
# sqlite3 est présent (copie transactionnelle propre), sinon tar direct (best-effort).
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if command -v sqlite3 >/dev/null 2>&1; then
  # Copie transactionnelle de chaque .db (évite un fichier à moitié écrit).
  find "$DATA_DIR" -maxdepth 2 -name '*.db' -print0 | while IFS= read -r -d '' db; do
    rel="${db#"$DATA_DIR"/}"
    mkdir -p "$TMP/$(dirname "$rel")"
    sqlite3 "$db" ".backup '$TMP/$rel'" 2>/dev/null || cp -a "$db" "$TMP/$rel"
  done
  # Fichiers non-.db (json, png du carnet, etc.) : copie directe.
  find "$DATA_DIR" -maxdepth 2 ! -name '*.db' -type f -print0 | while IFS= read -r -d '' f; do
    rel="${f#"$DATA_DIR"/}"
    mkdir -p "$TMP/$(dirname "$rel")"
    cp -a "$f" "$TMP/$rel"
  done
  tar -czf "$ARCHIVE" -C "$TMP" .
else
  tar -czf "$ARCHIVE" -C "$DATA_DIR" .
fi

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
echo "[backup] OK $(date -u +%FT%TZ) -> $ARCHIVE (${SIZE})"

# Rotation : supprime les archives de plus de KEEP_DAYS jours.
find "$DEST" -maxdepth 1 -name 'aria-data-*.tar.gz' -mtime "+${KEEP_DAYS}" -delete 2>/dev/null || true

# Chiffrement optionnel : si ARIA_BACKUP_GPG_RECIPIENT est défini et gpg présent, chiffre
# l'archive et retire la version en clair (pour un envoi hors-site sûr).
if [ -n "${ARIA_BACKUP_GPG_RECIPIENT:-}" ] && command -v gpg >/dev/null 2>&1; then
  gpg --yes --batch --encrypt --recipient "$ARIA_BACKUP_GPG_RECIPIENT" "$ARCHIVE" \
    && rm -f "$ARCHIVE" \
    && echo "[backup] chiffré -> ${ARCHIVE}.gpg (clair supprimé)"
fi
