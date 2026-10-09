"""
Token Diet - skrócenie opisów przed wysłaniem do modelu.

Usuwa sekcje bezwartościowe dla oceny dopasowania (benefity, RODO, opis procesu
rekrutacji) oraz tagi HTML. Zachowuje wymagania, zadania i stack technologiczny.
"""

import logging
from pathlib import Path

from utils import telemetry
from utils.safe_io import load_json_safe, save_json_atomic
from utils.text_cleaner import clean_job_description

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("TokenDiet")

def reduce_file(filepath):
    path = Path(filepath)
    if not path.exists():
        logger.warning(f"File not found: {path}")
        return

    logger.info(f"Implementing Token Diet for {path}...")

    data = load_json_safe(path, default=[])
    if not data:
        logger.info(f"{path.name} is empty - skipping.")
        return

    initial_chars = 0
    zmienione = 0
    final_chars = 0
    
    for item in data:
        orig_desc = item.get('description', '') or ''
        initial_chars += len(orig_desc)

        cleaned = clean_job_description(orig_desc)
        if cleaned != orig_desc:
            zmienione += 1
        item['description'] = cleaned

        final_chars += len(cleaned)

    # Zapis tylko wtedy, gdy cokolwiek faktycznie sie zmienilo. Przy juz
    # przyciętej bazie ten etap przepisywał 64 MB i robił do tego kopię
    # zapasową, żeby odtworzyć plik bajt w bajt - a rotacja kopii i tak
    # kasowała ją w tym samym przebiegu.
    if zmienione:
        save_json_atomic(path, data, backup=True)
    else:
        logger.info(f"   {path.name}: nic do przyciecia, plik bez zmian")

    reduction = 0
    if initial_chars > 0:
        reduction = ((initial_chars - final_chars) / initial_chars) * 100
        
    logger.info(f"Diet Complete for {path.name}")
    logger.info(f"   Before: {initial_chars:,} chars")
    logger.info(f"   After:  {final_chars:,} chars")
    logger.info(f"   Reduction: {reduction:.1f}%")
    telemetry.emit("stage_stats", id="phase2_5", chars_before=initial_chars, chars_after=final_chars)

def run():
    logger.info("Starting Token Diet Procedure...")
    reduce_file("jobs_database.json")
    logger.info("Token Diet Complete. Ready for efficient analysis.")

if __name__ == "__main__":
    run()
