from google import genai
from google.genai import types
from pydantic import BaseModel
import hashlib
import json
import time
import os
import sys
import typing
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from config import GEMINI_API_KEYS, GEMINI_MODEL
from utils.links import canonical_link
from utils.safe_io import save_json_atomic
from utils.console import force_utf8

# Struktury pod tryb Structured Outputs
class JobEval(BaseModel):
    id: int
    match_percentage: int
    reason: str
    is_entry_level: bool
    missing_skills: list[str]
    learnable_in_month: bool
    industry: str

BATCH_SIZE = 75

# Kolejność ustalona empirycznie (benchmark_models.py na 53 ofertach ocenionych
# ręcznie, 3 przebiegi). Korelacja Spearmana z ocenami użytkownika / czas na batch:
#   gemini-3.5-flash-lite   +0.840   15s   <- najlepsza korelacja, najszybszy
#   gemini-3.1-flash-lite   +0.833   16s   <- najlepszy MAE (0.91), remis w korelacji
#   gemini-3.6-flash        +0.802   40s   <- gorszy MIMO że nowszy i większy
#   gemini-2.5-flash        +0.664  100s   <- ostatnia deska ratunku
#
# Wniosek wbrew intuicji: modele "lite" wygrywają. To zadanie to klasyfikacja
# wg jawnej rubryki (profil preferencji), a nie otwarte rozumowanie - większy
# model nie ma tu czego dołożyć, a na darmowym planie kosztuje czas i limity.
# Zanim zmienisz tę listę, uruchom benchmark_models.py.
MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash", "gemini-2.5-flash"]
API_KEYS = GEMINI_API_KEYS
INPUT_DB = "jobs_database.json"
OUTPUT_FILE = "analyzed_jobs_waterfall.json"
CV_FILE = "final_cv_text.txt"
STATE_FILE = "waterfall_state.json"

def load_api_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                return state.get("current_key_val", 0), state.get("current_model_idx", 0)
        except Exception:
            pass
    return 0, 0

def save_api_state(key_val, model_idx):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"current_key_val": key_val, "current_model_idx": model_idx}, f)
    except Exception as e:
        print(f"Error saving state: {e}")

def load_cv():
    try:
        with open(CV_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print("ERROR: CV file not found at", CV_FILE)
        return "ERROR: CV File not found."

def load_jobs():
    with open(INPUT_DB, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Tylko oferty z sensownym opisem - zaślepki OLX mają poniżej 50 znaków
    valid = [j for j in data if j.get("description") and j["description"] != "Brak opisu" and len(j["description"].strip()) > 20]
    return valid

def load_existing_results():
    if not os.path.exists(OUTPUT_FILE):
        return []
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def description_fingerprint(job: dict) -> str:
    """
    Skrót opisu oferty. Pozwala wykryć, że oferta została wzbogacona po analizie
    (np. OLX dostał prawdziwy opis zamiast zaślepki) i wymaga przeliczenia.
    """
    text = (job.get("description") or "") + "||" + (job.get("title") or "")
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def profile_version() -> str:
    """Wersja profilu preferencji - jego zmiana unieważnia wcześniejsze oceny."""
    try:
        with open("preference_profile.json", "r", encoding="utf-8") as f:
            meta = json.load(f).get("_metadata", {})
        return f"{meta.get('generator_version', 'v?')}@{meta.get('generated_at', '')[:19]}"
    except Exception:
        return "brak"


def is_stale(entry: dict, job: dict, current_profile: str) -> bool:
    """
    Czy wynik analizy jest nieaktualny?
    Nieaktualny = opis oferty się zmienił albo profil preferencji jest nowszy.
    Stare wpisy bez stempli traktujemy jako aktualne, żeby nie przeliczać
    całej bazy przy pierwszym uruchomieniu po aktualizacji.
    """
    stamped_desc = entry.get("_description_hash")
    if stamped_desc and stamped_desc != description_fingerprint(job):
        return True
    stamped_profile = entry.get("_profile_version")
    if stamped_profile and stamped_profile != current_profile:
        return True
    return False

def load_user_decisions():
    try:
        if os.path.exists("user_decisions.json"):
            with open("user_decisions.json", "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception: pass
    return {}

def build_active_learning_context(all_jobs):
    # Priorytet: gotowy profil preferencji, jeśli został wygenerowany
    if os.path.exists("preference_profile.json"):
        try:
            with open("preference_profile.json", "r", encoding="utf-8") as f:
                profile = json.load(f)
            
            # Podstawowe pola profilu
            preferred_roles = ", ".join(profile.get("preferred_role_types", []))
            preferred_industries = ", ".join(profile.get("preferred_industries", []))
            red_flags = ", ".join(profile.get("red_flags", []))
            growth_dirs = ", ".join(profile.get("growth_directions", []))
            attractive_kw = ", ".join(profile.get("attractive_keywords", []))
            company_profile = profile.get("preferred_companies_profile", "Brak danych")
            work_conds = profile.get("work_conditions_preference", "Brak danych")
            summary = profile.get("summary", "Brak podsumowania")
            
            # Pola wzbogacone (v2)
            deal_breakers = ", ".join(profile.get("deal_breakers", []))
            deal_makers = ", ".join(profile.get("deal_makers", []))
            
            borderline = profile.get("borderline_signals", {})
            pushes_up = ", ".join(borderline.get("pushes_up", []))
            pushes_down = ", ".join(borderline.get("pushes_down", []))
            
            calibration = profile.get("rating_calibration", {})
            
            # Sekcja kalibracji ocen
            cal_section = ""
            if calibration:
                cal_section = """
📐 KALIBRACJA OCEN (tak ten konkretny kandydat rozumie poszczególne oceny):
"""
                cal_map = {
                    "what_9_10_means": "   9-10/10 = ",
                    "what_7_8_means":  "   7-8/10  = ",
                    "what_5_6_means":  "   5-6/10  = ",
                    "what_3_4_means":  "   3-4/10  = ",
                    "what_1_2_means":  "   1-2/10  = ",
                }
                for key, prefix in cal_map.items():
                    if key in calibration:
                        cal_section += f"{prefix}{calibration[key]}\n"
            
            # Kontekst dla modelu
            version = profile.get("_metadata", {}).get("generator_version", "v1")
            total_analyzed = profile.get("_metadata", {}).get("total_decisions_analyzed", "?")
            
            # v3: wagi punktacji
            scoring_section = ""
            scoring_weights = profile.get("scoring_weights", {})
            if scoring_weights:
                scoring_section = "\n⚖️ WAGI SCORINGU (użyj do obliczenia match_percentage jako średniej ważonej!):\n"
                for cat, weight in scoring_weights.items():
                    pct = int(weight * 100)
                    scoring_section += f"   {cat}: {pct}%\n"
                scoring_section += "   INSTRUKCJA: Oceń każdą kategorię 0-100, potem oblicz: match_percentage = Σ(ocena_kategorii × waga)\n"
            
            # v3: wzorce negatywne
            neg_patterns = profile.get("negative_patterns", [])
            neg_section = ""
            if neg_patterns:
                neg_section = "\n🚫 AUTOMATYCZNE WZORCE NEGATYWNE (jeśli opis oferty ZAWIERA te wzorce, ustaw max score):\n"
                for np_item in neg_patterns:
                    if isinstance(np_item, dict):
                        neg_section += f"   - Wzorzec: '{np_item.get('pattern', '')}' → max score: {np_item.get('auto_score_max', 15)}%\n"
            
            # v3: preferencje lokalizacji i wynagrodzenia
            loc_prefs = profile.get("location_preferences", "")
            salary_prefs = profile.get("salary_preferences", "")
            extra_prefs = ""
            if loc_prefs:
                extra_prefs += f"\n📍 PREFERENCJE LOKALIZACJI: {loc_prefs}"
            if salary_prefs:
                extra_prefs += f"\n💰 PREFERENCJE WYNAGRODZENIA: {salary_prefs}"
            
            context = f"""
==============
🧬 PROFIL PREFERENCJI KANDYDATA ({version}, wygenerowany z {total_analyzed} ocen) 🧬

📋 INSTRUKCJA KALIBRACJI: {summary}

🎯 PREFEROWANE TYPY STANOWISK: {preferred_roles}
🏢 PREFEROWANE BRANŻE: {preferred_industries}
🔑 SŁOWA KLUCZOWE PRZYCIĄGAJĄCE: {attractive_kw}
🏗️ PROFIL PREFEROWANYCH FIRM: {company_profile}
🏠 WARUNKI PRACY: {work_conds}
{extra_prefs}

🌱 KIERUNKI ROZWOJU (aspiracje - faworyzuj entry-level w tych branżach, NIE dopasowuj do seniorskich!): {growth_dirs}

⛔ DEAL BREAKERS (automatycznie ustaw match_percentage 0-20% dla ofert z tymi cechami!): {deal_breakers if deal_breakers else red_flags}

✨ DEAL MAKERS (automatycznie PODWYŻSZ match_percentage o +15-25% dla ofert z tymi cechami!): {deal_makers if deal_makers else "Brak danych"}

🚩 RED FLAGS (zaniżaj match_percentage): {red_flags}

📊 SYGNAŁY GRANICZNE (decydują o ocenie 5-6 vs 7-8 vs 3-4):
   ⬆️ Podnoszą ocenę: {pushes_up if pushes_up else "Brak danych"}
   ⬇️ Obniżają ocenę: {pushes_down if pushes_down else "Brak danych"}
{scoring_section}{neg_section}{cal_section}

INSTRUKCJA OCENIANIA:
1. NAJPIERW sprawdź deal breakers → jeśli present, match_percentage = 0-20%
2. POTEM sprawdź deal makers → jeśli present, dodaj +15-25% do bazowej oceny
3. Użyj KALIBRACJI OCEN aby dopasować skalę do preferencji kandydata
4. Użyj SYGNAŁÓW GRANICZNYCH aby rozstrzygnąć przypadki "na pograniczu"
5. Oferty zgodne z preferowanymi branżami/typami = WYŻSZY match_percentage
6. Oferty w kierunkach rozwoju (entry-level!) = traktuj łagodniej
==============
"""
            print("   Using pre-generated Preference Profile (v3 enriched) for Active Learning.")
            return context
        except Exception as e:
            print(f"   Error loading preference profile: {e}. Falling back to raw decisions.")
    
    # Zapas: surowa lista trafień i odrzuceń prosto z decyzji
    decisions = load_user_decisions()
    if not decisions: return ""
    
    link_to_job = {j['link']: j for j in all_jobs}
    
    favorites = []
    rejected = []
    
    for link, d in decisions.items():
        job = link_to_job.get(link)
        if not job: continue
        title = job['title']
        company = job['company']
        info = f"- {title} w firmie {company}"
        
        if isinstance(d, dict) and 'rating' in d:
            if d['rating'] >= 8: favorites.append(f"{info} (Ocena: {d['rating']}/10)")
            elif d['rating'] <= 3: rejected.append(f"{info} (Ocena: {d['rating']}/10)")
        elif isinstance(d, str):
            if d == 'apply' or d == 'save': favorites.append(f"{info} (Zapisane w ulubionych)")
            elif d == 'reject': rejected.append(f"{info} (Odrzucone stanowczo)")
            
    favorites_text = "\n".join(favorites[-30:]) if favorites else "Brak"
    rejected_text = "\n".join(rejected[-30:]) if rejected else "Brak"
    
    context = f"""
==============
🎯 ACTIVE LEARNING / FEEDBACK KANDYDATA 🎯
Przeanalizuj poniższe, najświeższe oceny wystawione przez kandydata, by dopasować swój algorytm myślenia.
Przestań sugerować stanowiska podobne do tych odrzuconych (dawaj im bardzo niskie match_percentage)!
Skup się na promowaniu ofert podobnych do tych ulubionych!

HITY (Stanowiska wysoko ocenione - szukaj takich!):
{favorites_text}

KITY (Stanowiska odrzucone / niskie oceny - zaniżaj ich match_percentage!):
{rejected_text}
==============
"""
    return context

MIN_MEANINGFUL_DESC = 150  # poniżej tego opis nie niesie realnej informacji


def create_prompt(cv_text, jobs_batch, active_learning_context=""):
    jobs_text = ""
    thin_count = 0
    for i, job in enumerate(jobs_batch):
        desc = (job.get("description") or "")[:4000]
        # Oznacz oferty bez realnego opisu. Bez tego model oceniał je po samym
        # tytule i wystawiał 95%+ ofertom, o których nie wiedział nic.
        if len(desc.strip()) < MIN_MEANINGFUL_DESC:
            thin_count += 1
            jobs_text += (
                f"ID: {i}\nTitle: {job['title']}\nCompany: {job['company']}\n"
                f"Desc: [BRAK PEŁNEGO OPISU - dostępny tylko tytuł]\n\n"
            )
        else:
            jobs_text += f"ID: {i}\nTitle: {job['title']}\nCompany: {job['company']}\nDesc: {desc}\n\n"

    prompt = f"""Jesteś wszechstronnym Doradcą Kariery (nie tylko IT). Twoim celem jest PRECYZYJNE ocenienie dopasowania ofert pracy do kandydata na podstawie jego CV i profilu preferencji.

{active_learning_context}

DANE WEJŚCIOWE:
CV KANDYDATA:
{cv_text[:3000]}

LISTA OFERT PRACY (Dokładnie {len(jobs_batch)} ofert):
{jobs_text}

INSTRUKCJA OCENY (Algorytm myślenia):

1. 🛑 KRYTERIA WYKLUCZAJĄCE (Auto-Reject):
   Natychmiast ustaw `match_percentage: 0-15`, jeśli oferta wymaga: języka innego niż Polski/Angielski, >3 lat doświadczenia, lub twardych uprawnień niemożliwych do zdobycia w miesiąc.
   
2. 🧠 ZASADA "1 MIESIĄCA" (Learning Curve):
   Jeśli brak skilla można nadrobić w ~160h (Excel, CMS, podstawy SQL) -> TRAKTUJ JAKO DO NADROBIENIA. Jeśli wymaga lat (C++, Pełna Księgowość) -> obniż ocenę.
   
3. 🌍 TRANSFER SKILLS:
   Szukaj punktów styku CV kandydata i oferty.

4. 🏆 BONUS JUNIOR/ENTRY-LEVEL (+10-15% do match_percentage):
   Jeśli tytuł stanowiska zawiera słowa takie jak: "Junior", "Młodszy", "Stażysta", "Praktykant", "Entry-Level", "Trainee", "Asystent" — PREMIUJ tę ofertę dodając +10-15% do match_percentage.

5. 📊 SCORING BREAKDOWN — oceń każdą kategorię osobno (0-100), potem oblicz średnią ważoną:
   - role_fit: Jak bardzo typ stanowiska pasuje do preferencji kandydata?
   - industry_match: Czy branża jest preferowana/neutralna/odrzucona?
   - skills_alignment: Pokrycie wymaganych umiejętności z CV
   - work_conditions: Warunki pracy vs preferencje (zdalnie/hybrydowo/biuro)
   - growth_potential: Czy oferta oferuje rozwój w preferowanych kierunkach?
   - company_culture: Czy profil firmy pasuje do preferencji?

🛑 ANTI-INFLATION GUARD:
   - WIĘKSZOŚĆ ofert powinna mieć match_percentage 15-45%
   - Tylko WYBITNIE dopasowane oferty (z deal_makers + preferred_roles + preferred_industries) powinny dostać >70%

⚠️ OFERTY BEZ PEŁNEGO OPISU (oznaczone jako "[BRAK PEŁNEGO OPISU - dostępny tylko tytuł]"):
   Masz do dyspozycji WYŁĄCZNIE tytuł - nie znasz wymagań, technologii, warunków ani firmy.
   ZASADY (bezwzględne):
   - match_percentage MAKSYMALNIE 55, nawet jeśli tytuł wygląda idealnie.
     Sam tytuł "Junior Frontend Developer" NIE jest dowodem dopasowania.
   - Nie zgaduj `missing_skills` - zwróć pustą listę [].
   - W polu `reason` zacznij od "[tylko tytuł]".
   - Deal breakers nadal obowiązują - jeśli tytuł je zawiera, obniż ocenę normalnie.

🔥!!! BARDZO WAŻNE - ZAKAZ POMIJANIA !!!🔥
Otrzymałeś listę zawierającą dokładnie {len(jobs_batch)} ofert pracy (od ID 0 do ID {len(jobs_batch)-1}).
Twoim krytycznym zadaniem jest zwrócenie w tablicy JSON dokładnie {len(jobs_batch)} obiektów. 
Zabrania się pominięcia choćby jednej oferty. Nawet jeśli uzasadnienie ma być takie samo, wypisz je. Niedokończenie generowania uszkodzi cały system. Musisz przetworzyć 100% ID.

Zdefiniuj następujące pola dla każdej oferty:
- `is_entry_level`: Czy oferta jest przeznaczona dla osoby początkującej/junior/stazysty? (True/False)
- `missing_skills`: Lista kluczowych umiejętności z oferty, których kandydat NIE posiada w swoim CV.
- `learnable_in_month`: Czy brakujące umiejętności można opanować w ciągu jednego miesiąca nauki (~160 godzin)? (True/False)
- `industry`: Branża, do której należy oferta (np. IT, Sales, Marketing, Customer Service, Finance, Engineering, Other).

FORMAT ODPOWIEDZI (Tylko zwięzły JSON array, bez komentarzy):
[
  {{
    "id": 0,
    "match_percentage": 85,
    "reason": "Max 1 zdanie uzasadnienia dlaczego pasuje lub odrzucone.",
    "is_entry_level": true,
    "missing_skills": ["SQL", "Tableau"],
    "learnable_in_month": true,
    "industry": "IT"
  }}
]

UWAGA: 
- `reason` musi być bardzo krótkie (max 1 zdanie). Nie rozpisuj się, aby oszczędzić limit znaków w API.
- match_percentage oblicz w pamięci (z uwzględnieniem deal_breakers, deal_makers i kalibracji).
"""
    return prompt



def main():
    print("Starting WATERFALL Analysis (RESUME MODE)...")
    
    cv_text = load_cv()
    all_jobs = load_jobs()
    print(f"Loaded {len(all_jobs)} valid jobs form DB.")
    
    # Wznawianie przerwanego przebiegu
    existing_results = load_existing_results()
    print(f"Loaded {len(existing_results)} existing matched jobs.")

    current_profile_version = profile_version()
    print(f"Preference profile version: {current_profile_version}")

    # Mapa link -> istniejący wynik (na postaci kanonicznej linku)
    jobs_by_link = {canonical_link(j['link']): j for j in all_jobs}
    existing_by_link = {}
    for item in existing_results:
        link = canonical_link((item.get('job') or {}).get('link', ''))
        if link:
            existing_by_link[link] = item

    # Wyniki nieaktualne (zmieniony opis / nowszy profil) trafiają do ponownej analizy,
    # a ich stare wpisy są usuwane - inaczej mielibyśmy dwa wyniki dla jednej oferty.
    stale_links = {
        link for link, item in existing_by_link.items()
        if link in jobs_by_link and is_stale(item, jobs_by_link[link], current_profile_version)
    }
    if stale_links:
        print(f"{len(stale_links)} results are stale (description or profile changed) - rescoring.")
        existing_results = [
            item for item in existing_results
            if canonical_link((item.get('job') or {}).get('link', '')) not in stale_links
        ]

    processed_links = {link for link in existing_by_link if link not in stale_links}

    # Co zostało do przeliczenia
    remaining_jobs = [j for j in all_jobs if canonical_link(j['link']) not in processed_links]

    # Pomijamy też oferty ocenione ręcznie - nie ma po co ich przeliczać
    user_decisions = load_user_decisions()
    decided_links = {canonical_link(k) for k in user_decisions.keys()}
    before_filter = len(remaining_jobs)
    remaining_jobs = [j for j in remaining_jobs if canonical_link(j['link']) not in decided_links]
    skipped = before_filter - len(remaining_jobs)
    if skipped > 0:
        print(f"⏭ Skipped {skipped} already-decided jobs (rejected/saved/aspirational).")
    print(f"Remaining jobs to process: {len(remaining_jobs)}")
    
    if not remaining_jobs:
        print("Nothing left to do! All jobs analyzed.")
        return

    current_key_val, current_model_idx = load_api_state()
    print(f"Loaded API State: Model[{current_model_idx}] Key[{current_key_val}]")
    
    results = existing_results
    
    # Kolejka dynamiczna
    job_queue = remaining_jobs[:]
    b_idx = 0
    current_batch_size = BATCH_SIZE
    print(f"Processing {len(job_queue)} jobs (Target Batch Size: {BATCH_SIZE}).")
    al_context = build_active_learning_context(all_jobs)
    
    while job_queue:
        batch = job_queue[:current_batch_size]
        b_idx += 1
        print(f"\nBatch {b_idx} (Processing {len(batch)} jobs, {len(job_queue)} remaining in queue)...")
        batch_success = False
        fatal_json_error = False
        
        available_models = MODELS[current_model_idx:] + MODELS[:current_model_idx]
        
        for model_name in available_models:
            if batch_success or fatal_json_error: break
            
            available_keys_idx = list(range(current_key_val, len(API_KEYS))) + list(range(0, current_key_val))
            
            for key_idx in available_keys_idx:
                if batch_success or fatal_json_error: break
                
                api_key = API_KEYS[key_idx]
                client = genai.Client(api_key=api_key)
                masked_key = api_key[:4] + "..." + api_key[-4:]
                
                for attempt in range(1, 3): 
                    try:
                        print(f"   Model: {model_name} | Key[{key_idx}] {masked_key} | Try: {attempt}")
                        
                        if attempt > 1: time.sleep(15) 
                        else: time.sleep(5)

                        prompt = create_prompt(cv_text, batch, al_context)
                        
                        response = client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                response_schema=list[JobEval],
                                max_output_tokens=65535,
                                temperature=0.3
                            )
                        )
                        
                        parsed = json.loads(response.text)
                        
                        if parsed:
                            processed_ids = set()
                            for item in parsed:
                                job_idx_local = item.get('id')
                                if job_idx_local is not None and 0 <= job_idx_local < len(batch):
                                    processed_ids.add(job_idx_local)
                                    original_job = batch[job_idx_local]
                                    result_entry = {
                                        "job": original_job,
                                        "match_percentage": item.get('match_percentage', 0),
                                        "reason": item.get('reason', 'N/A'),
                                        "is_entry_level": item.get('is_entry_level', True),
                                        "missing_skills": item.get('missing_skills', []),
                                        "learnable_in_month": item.get('learnable_in_month', True),
                                        "industry": item.get('industry', 'Other'),
                                        # Stemple: pozwalają później wykryć, że wynik jest
                                        # nieaktualny i przeliczyć TYLKO te oferty
                                        "_description_hash": description_fingerprint(original_job),
                                        "_profile_version": current_profile_version,
                                        "_model": model_name,
                                        "_analyzed_at": datetime.now().isoformat(),
                                    }
                                    results.append(result_entry)
                                    
                            if not processed_ids:
                                print("      JSON parsed, but zero matching IDs found. Retrying...")
                                if attempt == 2: raise ValueError("No valid IDs")
                                continue 
                                
                            print(f"      Success! Processed {len(processed_ids)} out of {len(batch)} requested jobs.")
                            
                            unprocessed = [job for i, job in enumerate(batch) if i not in processed_ids]
                            job_queue = unprocessed + job_queue[len(batch):]
                            
                            if len(unprocessed) > 0:
                                print(f"      LLM Laziness detected: {len(unprocessed)} jobs skipped. Re-queuing them...")
                                
                            batch_success = True
                            save_results(results) 
                            
                            current_model_idx = MODELS.index(model_name)
                            current_key_val = key_idx
                            print(f"      Locked into Model '{model_name}' on Key {key_idx}.")
                            save_api_state(current_key_val, current_model_idx)
                            break
                        else:
                            print("      Invalid JSON received.")
                            if attempt == 2: raise ValueError("JSON parsing failed repeatedly")
                            
                    except json.JSONDecodeError as e:
                        print(f"      JSON Syntax Error (Toxic Batch). Failing fast to resize: {e}")
                        fatal_json_error = True
                        break
                    except Exception as e:
                        err = str(e)
                        if "429" in err or "403" in err or "ResourceExhausted" in err:
                            print(f"      API Error: {err[:100]}...")
                            if attempt < 2:
                                print("      ⏳ Rate Limit Hit. Sleeping 70s before final try on this key...")
                                time.sleep(70)
                            else:
                                print("      Key exhaustion. Moving to next key...")
                        elif "Expecting" in err or "Unterminated" in err or "JSON Validate Err" in err:
                            print(f"      JSON Truncation Error (Toxic Batch). Failing fast to resize: {err[:80]}...")
                            fatal_json_error = True
                            break
                        else:
                            print(f"      General Error: {err[:100]}...")
                            pass
                            
            if batch_success or fatal_json_error: break
            if not batch_success and not fatal_json_error:
                 next_model_idx = (MODELS.index(model_name) + 1) % len(MODELS)
                 print(f"   Exhausted ALL keys for model {model_name}. Downgrading to {MODELS[next_model_idx]}...")
                 current_model_idx = next_model_idx
                 current_key_val = 0 
                 save_api_state(current_key_val, current_model_idx)
                  
        if batch_success:
             current_batch_size = BATCH_SIZE 
        else:
             if fatal_json_error:
                  if current_batch_size > 5:
                       new_size = current_batch_size // 2
                       print(f"Halving batch size from {current_batch_size} to {new_size} due to JSON truncation.")
                       current_batch_size = new_size
                  else:
                       print("Micro-batch is fundamentally broken. Discarding 1 job to save pipeline.")
                       job_queue = job_queue[1:]
                       current_batch_size = BATCH_SIZE
             else:
                  print("CRITICAL: Failed to process batch even after trying ALL keys on ALL models.")
                  print("   Waiting 5 minutes before retrying (rate limits may reset)...")
                  time.sleep(300)
                  current_model_idx = 0
                  current_key_val = 0
                  save_api_state(current_key_val, current_model_idx)

    print(f"\nDONE. Queue empty! Processed {b_idx} batches total.")

def save_results(data):
    save_json_atomic(OUTPUT_FILE, data)

if __name__ == "__main__":
    force_utf8()
    main()
