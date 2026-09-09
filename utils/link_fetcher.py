import logging
import urllib.request
import urllib.parse
from bs4 import BeautifulSoup
from typing import Dict

logger = logging.getLogger(__name__)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0'
]

def extract_job_info_from_url(url: str) -> Dict[str, str]:
    """
    Fetches the URL and attempts to extract basic job metadata (Title, Company, Description).
    Returns a dictionary with extracted data.
    """
    import random
    import json
    
    result = {
        "title": "",
        "company": "",
        "location": "Remote / Nieznana",
        "description": "Brak opisu (Opcja dodana ręcznie z linku)",
        "source": "Manual Entry"
    }
    
    # Try to guess source from domain
    domain = ""
    try:
        parsed_url = urllib.parse.urlparse(url)
        domain = parsed_url.netloc.replace("www.", "")
        result["source"] = f"Manual ({domain})"
    except Exception:
        pass

    def try_playwright_fetch(target_url):
        import os
        import sys
        import subprocess
        logger.info(f"Link Fetcher: Falling back to Playwright subprocess for {target_url}")
        
        script = """
import sys
from playwright.sync_api import sync_playwright
import random

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0',
]

target_url = sys.argv[1]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    context = browser.new_context(viewport={'width': 1920, 'height': 1080}, user_agent=random.choice(USER_AGENTS))
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    page = context.new_page()
    page.set_default_timeout(15000)
    page.goto(target_url, wait_until='domcontentloaded')
    page.wait_for_timeout(2000)
    print(page.content())
    browser.close()
"""
        try:
            proc_result = subprocess.run(
                [sys.executable, '-c', script, target_url],
                capture_output=True, text=True, encoding='utf-8', timeout=25,
                # Podproces wypisuje HTML z polskimi znakami do potoku. Bez tego
                # jego stdout ma cp1252 i kazda strona z 'ł' wraca jako blad.
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}
            )
            if proc_result.returncode == 0:
                return proc_result.stdout
            else:
                logger.error(f"Playwright subprocess failed: {proc_result.stderr}")
        except Exception as e:
            logger.error(f"Playwright subprocess execution failed: {e}")
        return ""

    try:
        html = ""
        # 1. Najpierw zwykłe żądanie
        try:
            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': random.choice(USER_AGENTS),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'pl,en-US;q=0.7,en;q=0.3',
                }
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')
                
            if "Just a moment..." in html or "DDoS protection by Cloudflare" in html:
                 logger.warning("Cloudflare block detected on urllib request.")
                 html = ""
        except Exception as e:
            logger.warning(f"urllib request failed: {e}")
            
        # 2. Fallback to Playwright if urllib failed or was blocked
        if not html:
             html = try_playwright_fetch(url)
             
        if not html:
             raise ValueError("Could not retrieve HTML via urllib or Playwright")
             
        soup = BeautifulSoup(html, 'html.parser')

        # --- PARSER POD JUSTJOIN.IT ---
        if 'justjoin.it' in domain:
            next_data_script = soup.find('script', id='__NEXT_DATA__')
            if next_data_script and next_data_script.string:
                try:
                    data = json.loads(next_data_script.string)
                    
                    found_title = None
                    found_company = None
                    found_city = None
                    
                    def find_job_info(d):
                        nonlocal found_title, found_company, found_city
                        if isinstance(d, dict):
                            # JJIT zwykle ma '__typename': 'Offer'
                            if d.get('__typename') == 'Offer':
                                if d.get('title'): found_title = d['title']
                                if d.get('companyName'): found_company = d['companyName']
                                if d.get('city'): found_city = d['city']
                                
                            # zapas: ogólne klucze w stanie
                            if not found_title and 'title' in d and isinstance(d['title'], str) and len(d['title']) > 3:
                                if 'companyName' in d:
                                    found_title = d['title']
                                    found_company = d['companyName']
                                    if 'city' in d: found_city = d['city']
                                    
                            for dict_val in d.values():
                                find_job_info(dict_val)
                        elif isinstance(d, list):
                            for item in d:
                                find_job_info(item)
                                
                    find_job_info(data)
                    
                    if found_title: result["title"] = found_title
                    if found_company: result["company"] = found_company
                    if found_city: result["location"] = found_city
                    
                    if result["title"]:
                        return result  # Jeśli JJIT się udał, kończymy tutaj
                except Exception as ex:
                    logger.warning(f"Failed to parse JustJoin NEXT_DATA: {ex}")
            
        # 1. Try to get title
        title_tag = soup.find('title')
        page_title = title_tag.text.strip() if title_tag else ""
        
        # Tytuł z Open Graph jako zapas
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
             page_title = og_title["content"].strip()
             
        # Heurystyka na tytuł - typowe formaty to „Stanowisko at Firma”
        # albo „Stanowisko - Firma - Lokalizacja”
        if page_title:
            title_set = False
            # JustJoin.it: „Stanowisko (Poziom) - Firma”
            if 'justjoin.it' in domain and " - " in page_title:
                parts = page_title.split(" - ")
                result["title"] = parts[0].strip()
                result["company"] = parts[-1].strip()
                title_set = True
                
            if not title_set:
                separators = [" at ", " w ", " | ", " - ", " – "]
                for sep in separators:
                    if sep in page_title:
                        parts = page_title.split(sep)
                        if len(parts) >= 2:
                            result["title"] = parts[0].strip()
                            result["company"] = parts[-1].strip() if " at " not in sep else parts[1].strip()
                            title_set = True
                            break
                
            if not title_set:
                result["title"] = page_title
                
        if 'justjoin.it' in domain and not result["company"]:
            # JustJoin.it trzyma nazwę firmy w pojedynczym h2
            h2s = soup.find_all('h2')
            for h2 in h2s:
                if len(h2.text.strip()) > 1:
                    result["company"] = h2.text.strip()
                    break
                 
        # 2. Try to get Description from meta
        og_desc = soup.find("meta", property="og:description")
        meta_desc = soup.find("meta", attrs={"name": "description"})
        
        desc = ""
        if og_desc and og_desc.get("content"):
            desc = og_desc["content"].strip()
        elif meta_desc and meta_desc.get("content"):
            desc = meta_desc["content"].strip()
            
        if desc:
            result["description"] = desc
            
        if not result["title"]:
             result["title"] = "Nie udało się pobrać tytułu"
             
    except Exception as e:
        logger.warning(f"Link fetcher failed for {url}: {e}")
        # Zwracamy tyle, ile udało się ustalić
        result["title"] = "Błąd pobierania (sprawdź URL lub blokadę bota)"
        
    return result
