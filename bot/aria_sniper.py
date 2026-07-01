import os
import time
import logging
from datetime import datetime
from dotenv import load_dotenv
import requests

load_dotenv()

DRY_RUN = True
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("AriaSniper")

tokens_analyzed = 0

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
    except: pass

def main():
    send_telegram("🚀 Aria Sniper - Compteur de tokens activé")
    logger.info("Aria démarrée avec compteur")

    while True:
        try:
            global tokens_analyzed
            tokens_analyzed += 1
            logger.info(f"Tokens analysés : {tokens_analyzed}")
            send_telegram(f"📊 Tokens analysés : {tokens_analyzed}")
            time.sleep(15)
        except KeyboardInterrupt:
            send_telegram("🛑 Arrêt manuel")
            break
        except Exception as e:
            logger.error(str(e))
            time.sleep(30)

if __name__ == "__main__":
    main()
