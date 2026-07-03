from logging_config import log
from src.config import settings
from src.ledger.ledger import TruthLedger


def main():
    log.info("🚀 Démarrage d'ARIA v{}", settings.version)
    log.info("Environnement : {}", settings.environment)

    try:
        ledger = TruthLedger()
        ledger.add_entry({"action": "startup", "status": "success"})
        integrity = ledger.verify_integrity()
        log.success("Truth Ledger OK - {}", integrity)
    except Exception as e:
        log.error("Erreur au démarrage : {}", str(e))


if __name__ == "__main__":
    main()
