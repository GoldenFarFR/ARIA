import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from filelock import FileLock, Timeout

from logging_config import log
from src.config import LEDGER_PATH, settings
from src.core.exceptions import (
    ARIAError,
    LedgerError,
    LedgerFileError,
    ValidationError,
)


class TruthLedger:
    """Truth Ledger robuste, intègre, cohérent et sûr en environnement multi-process"""

    LOCK_TIMEOUT_SECONDS = 5

    def __init__(self):
        self.ledger_path = LEDGER_PATH
        self.ledger_file = self.ledger_path / settings.ledger_file
        self.ledger_path.mkdir(parents=True, exist_ok=True)

        # Un seul fichier de lock partagé par tous les process qui touchent ce ledger.
        # C'est volontairement un fichier .lock à côté du ledger, pas un lock en mémoire :
        # un Lock() Python ne protège que les threads d'un même process, pas plusieurs
        # workers uvicorn ou plusieurs process séparés.
        self._lock = FileLock(str(self.ledger_file) + ".lock")

        self.entries = []
        self._load()

    def _calculate_hash(self, entry_core: dict) -> str:
        return hashlib.sha256(
            json.dumps(entry_core, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _verify_entry_integrity(self, entry: dict) -> bool:
        required = {"timestamp", "source", "data", "entry_id", "hash"}
        if not all(k in entry for k in required):
            return False
        entry_core = {k: entry[k] for k in ["timestamp", "source", "data", "entry_id"]}
        expected_hash = self._calculate_hash(entry_core)
        return entry["hash"] == expected_hash

    def _acquire_lock(self):
        """Acquiert le verrou fichier, avec une erreur explicite en cas de timeout"""
        try:
            self._lock.acquire(timeout=self.LOCK_TIMEOUT_SECONDS)
        except Timeout as e:
            log.bind(path=str(self.ledger_file)).error(
                "Timeout en attente du verrou du ledger (un autre process le détient trop longtemps)"
            )
            raise LedgerFileError(
                "Impossible d'acquérir le verrou du ledger (timeout)",
                str(self.ledger_file),
            ) from e

    def _load(self):
        self.ledger_path.mkdir(parents=True, exist_ok=True)

        if not self.ledger_file.exists():
            self.entries = []
            return

        self._acquire_lock()
        try:
            try:
                with self.ledger_file.open(encoding="utf-8") as f:
                    raw_entries = json.load(f)

                valid_entries = []
                corrupted_count = 0

                for entry in raw_entries:
                    if self._verify_entry_integrity(entry):
                        valid_entries.append(entry)
                    else:
                        corrupted_count += 1
                        log.bind(entry_id=entry.get("entry_id")).warning(
                            "Entrée corrompue ignorée"
                        )

                self.entries = valid_entries

                if corrupted_count > 0:
                    log.warning(
                        "{count} entrées corrompues ont été filtrées",
                        count=corrupted_count,
                    )
                    try:
                        self._save_locked()
                    except Exception as e:
                        log.bind(error=str(e)).error(
                            "Échec de l'auto-nettoyage du ledger corrompu"
                        )
                        raise

                log.info(
                    "Ledger chargé avec succès : {entries} entrées",
                    entries=len(self.entries),
                )

            except ARIAError:
                raise
            except json.JSONDecodeError as e:
                log.bind(path=str(self.ledger_file)).error(
                    "Fichier ledger corrompu (JSON invalide)"
                )
                raise LedgerFileError(
                    "Fichier ledger corrompu", str(self.ledger_file)
                ) from e
            except Exception as e:
                log.bind(error=str(e), path=str(self.ledger_file)).error(
                    "Erreur lors du chargement du ledger"
                )
                raise LedgerFileError(
                    "Impossible de charger le ledger", str(self.ledger_file)
                ) from e
        finally:
            self._lock.release()

    def add_entry(self, data: dict, source: str = "system"):
        if not isinstance(data, dict):
            raise ValidationError("Les données doivent être un dictionnaire", "data")

        self._acquire_lock()
        try:
            try:
                # Sous le verrou : on relit l'état le plus récent du fichier avant de
                # calculer next_id, pour ne pas se baser sur une vue en mémoire périmée
                # si un autre process a écrit entre-temps.
                self._refresh_from_disk_locked()

                next_id = (
                    max((e.get("entry_id", 0) for e in self.entries), default=0) + 1
                )

                entry_core = {
                    "timestamp": datetime.now().isoformat(),
                    "source": source,
                    "data": data,
                    "entry_id": next_id,
                }

                entry = {**entry_core, "hash": self._calculate_hash(entry_core)}

                self.entries.append(entry)

                try:
                    self._save_locked()
                except Exception:
                    self.entries.pop()  # Rollback mémoire
                    raise

                log.bind(entry_id=next_id, source=source).info(
                    "Entrée ajoutée avec succès"
                )
                return entry

            except ARIAError:
                raise
            except Exception as e:
                log.bind(error=str(e)).error(
                    "Erreur inattendue lors de l'ajout d'une entrée"
                )
                raise LedgerError("Impossible d'ajouter l'entrée dans le ledger") from e
        finally:
            self._lock.release()

    def _refresh_from_disk_locked(self):
        """Recharge self.entries depuis le disque. Doit être appelé avec le verrou déjà acquis."""
        if not self.ledger_file.exists():
            self.entries = []
            return

        with self.ledger_file.open(encoding="utf-8") as f:
            raw_entries = json.load(f)

        self.entries = [e for e in raw_entries if self._verify_entry_integrity(e)]

    def _save_locked(self):
        """Écriture atomique. Doit être appelée avec le verrou déjà acquis."""
        self.ledger_path.mkdir(parents=True, exist_ok=True)

        fd, tmp_path_str = tempfile.mkstemp(dir=self.ledger_path, suffix=".tmp")
        tmp_path = Path(tmp_path_str)

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.entries, f, indent=2, ensure_ascii=False)

            tmp_path.replace(self.ledger_file)
            log.debug("Ledger sauvegardé de manière atomique")

        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            raise LedgerFileError(
                "Impossible de sauvegarder le ledger", str(self.ledger_file)
            ) from e

    def verify_integrity(self) -> dict:
        issues = [
            i for i, e in enumerate(self.entries) if not self._verify_entry_integrity(e)
        ]
        return {
            "status": "OK" if not issues else "WARNING",
            "total_entries": len(self.entries),
            "issues_found": len(issues),
        }

    def get_all(self):
        return self.entries
