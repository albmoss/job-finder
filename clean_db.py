"""
Token Diet - skrócenie opisów przed wysłaniem do modelu.

Usuwa sekcje bezwartościowe dla oceny dopasowania (benefity, RODO, opis procesu
rekrutacji) oraz tagi HTML. Zachowuje wymagania, zadania i stack technologiczny.
"""

import logging
from pathlib import Path

from utils.safe_io import load_json_safe, save_json_atomic
from utils.text_cleaner import clean_job_description

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("TokenDiet")

def reduce_file(filepath, is_analyzed=False):
    path = Path(filepath)
    if not path.exists():
        logger.warning(f"File not found: {path}")
        return

    logger.info(f"✂️ Implementing Token Diet for {path}...")

    data = load_json_safe(path, default=[])
    if not data:
        logger.info(f"{path.name} jest pusty - pomijam.")
        return

    initial_chars = 0
    final_chars = 0
    
    if is_analyzed:
        for item in data:
            orig_desc = item['job'].get('description', '') or ''
            initial_chars += len(orig_desc)
            
            cleaned = clean_job_description(orig_desc)
            item['job']['description'] = cleaned
            
            final_chars += len(cleaned)
    else:
        for item in data:
            orig_desc = item.get('description', '') or ''
            initial_chars += len(orig_desc)
            
            cleaned = clean_job_description(orig_desc)
            item['description'] = cleaned
            
            final_chars += len(cleaned)

    save_json_atomic(path, data, backup=True)

    reduction = 0
    if initial_chars > 0:
        reduction = ((initial_chars - final_chars) / initial_chars) * 100
        
    logger.info(f"✅ Diet Complete for {path.name}")
    logger.info(f"   Before: {initial_chars:,} chars")
    logger.info(f"   After:  {final_chars:,} chars")
    logger.info(f"   Reduction: {reduction:.1f}%")

def run():
    logger.info("Starting Token Diet Procedure... 📉")
    
    # 1. Clean Analyzed Jobs
    reduce_file("analyzed_jobs_waterfall.json", is_analyzed=True)
    
    # 2. Clean Raw Database
    reduce_file("jobs_database.json", is_analyzed=False)
    
    logger.info("Token Diet Complete. Ready for efficient analysis.")

if __name__ == "__main__":
    run()
