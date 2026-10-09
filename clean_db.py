"""
Token Diet - skrócenie opisów przed wysłaniem do modelu.

Usuwa sekcje bezwartościowe dla oceny dopasowania (benefity, RODO, opis procesu
rekrutacji) oraz tagi HTML. Zachowuje wymagania, zadania i stack technologiczny.
"""

import logging

import config
from utils import telemetry
from utils.data_models import JobDatabase
from utils.text_cleaner import clean_job_description

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("TokenDiet")

def reduce_db(db: JobDatabase):
    logger.info(f"Implementing Token Diet for {db.filepath.name}...")

    data = db.load_records()
    if not data:
        logger.info(f"{db.filepath.name} is empty - skipping.")
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

    if zmienione:
        db.save_records(data)
    else:
        logger.info(f"   {db.filepath.name}: nic do przyciecia, baza bez zmian")

    reduction = 0
    if initial_chars > 0:
        reduction = ((initial_chars - final_chars) / initial_chars) * 100
        
    logger.info(f"Diet Complete for {db.filepath.name}")
    logger.info(f"   Before: {initial_chars:,} chars")
    logger.info(f"   After:  {final_chars:,} chars")
    logger.info(f"   Reduction: {reduction:.1f}%")
    telemetry.emit("stage_stats", id="phase2_5", chars_before=initial_chars, chars_after=final_chars)

def run():
    logger.info("Starting Token Diet Procedure...")
    reduce_db(JobDatabase(config.JOBS_DATABASE_PATH))
    logger.info("Token Diet Complete. Ready for efficient analysis.")

if __name__ == "__main__":
    run()
