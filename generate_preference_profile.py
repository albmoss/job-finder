"""
Pre-Flight Preference Profile Generator v3
============================================
Generates a granular "Candidate Preference Profile" using 5-tier rating analysis.

KEY IMPROVEMENTS OVER V2:
- v2: 5-tier system, job descriptions, smart sampling, deal breakers/makers
- v3: Longer descriptions so AI sees actual skill requirements
- v3: Contrastive pair analysis (compares adjacent tiers for precision)
- v3: Weighted scoring categories for downstream AI analysis
- v3: Negative pattern extraction with explicit examples
- v3: Location and salary preference detection

Run this BEFORE waterfall_analysis.py to get the best results.
"""

from google import genai
from google.genai import types
import json
import os
import sys
import random
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from config import GEMINI_API_KEYS
from utils.links import canonical_link
from utils.safe_io import load_json_safe

# Configuration
# UWAGA: tutaj celowo INNA kolejność niż w waterfall_analysis.py.
# Tam mamy tysiące wywołań klasyfikujących wg gotowej rubryki - wygrywają modele lite.
# Tutaj jest JEDNO wywołanie robiące otwartą syntezę preferencji z przykładów,
# a jego wynik (profil) wpływa potem na każdą ocenę. Mocniejszy model się opłaca:
# jeden wolniejszy call raz na jakiś czas kosztuje nic, a lepszy profil zyskuje wszystko.
MODELS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]
API_KEYS = GEMINI_API_KEYS
INPUT_DB = "jobs_database.json"
DECISIONS_FILE = "user_decisions.json"
RATED_ARCHIVE = "rated_archive.json"
CV_FILE = "final_cv_text.txt"
OUTPUT_FILE = "preference_profile.json"

# Tier configuration — v3: increased desc_len for better AI pattern recognition
TIERS = {
    "ideal":      {"range": (9, 10), "label": "🟢 IDEALNIE (9-10/10)",     "max_samples": 999, "desc_len": 800},
    "very_good":  {"range": (7, 8),  "label": "🟢 BARDZO DOBRZE (7-8/10)", "max_samples": 999, "desc_len": 600},
    "maybe":      {"range": (5, 6),  "label": "🟡 MOŻE BYĆ (5-6/10)",     "max_samples": 30,  "desc_len": 500},
    "rather_not": {"range": (3, 4),  "label": "🟠 RACZEJ NIE (3-4/10)",    "max_samples": 25,  "desc_len": 400},
    "hard_no":    {"range": (1, 2),  "label": "🔴 ZDECYDOWANIE NIE (1-2/10)", "max_samples": 30, "desc_len": 300},
}


def load_jobs():
    """
    Oferty z bazy PLUS z archiwum ocenionych.

    Bez archiwum profil budował się tylko z ocen, których oferty wciąż są w bazie -
    a oferty starsze niż 14 dni są usuwane. Twoje najstarsze (i często najlepiej
    przemyślane) oceny po prostu wypadały z profilu.
    """
    jobs = load_json_safe(INPUT_DB, default=[])

    archive = load_json_safe(RATED_ARCHIVE, default=[])
    known = {canonical_link(j.get("link", "")) for j in jobs}
    recovered = 0
    for entry in archive:
        job = entry.get("job") or {}
        link = canonical_link(job.get("link", ""))
        if link and link not in known:
            jobs.append(job)
            known.add(link)
            recovered += 1

    if recovered:
        print(f"    Recovered {recovered} rated offers from {RATED_ARCHIVE}")

    return jobs


def load_user_decisions():
    return load_json_safe(DECISIONS_FILE, default={})


def load_cv():
    try:
        with open(CV_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "CV not available."


def smart_sample(items, max_count):
    """Representative sampling: evenly spaced across the list to preserve variety."""
    if len(items) <= max_count:
        return items
    
    # Always include first and last, then evenly space the rest
    step = len(items) / max_count
    indices = [int(i * step) for i in range(max_count)]
    # Ensure uniqueness
    indices = sorted(set(indices))
    return [items[i] for i in indices if i < len(items)]


def categorize_decisions_granular(all_jobs):
    """Categorize user decisions into 5 granular tiers + aspirational."""
    decisions = load_user_decisions()
    if not decisions:
        print("No user decisions found. Cannot generate profile.")
        return None

    # Porównanie po postaci kanonicznej - inaczej oceny nie trafiają na oferty
    link_to_job = {canonical_link(j.get('link', '')): j for j in all_jobs}

    # Initialize tier buckets
    tiers = {name: [] for name in TIERS}
    aspirational = []
    legacy_rejects = []  # Legacy string-format decisions with no rating
    orphaned = 0

    for link, d in decisions.items():
        job = link_to_job.get(canonical_link(link))
        if not job:
            orphaned += 1
            continue

        title = job.get('title', '?')
        company = job.get('company', '?')
        description = job.get('description', '')
        location = job.get('location', '')
        source = job.get('source', '')

        if isinstance(d, dict):
            status = d.get('status', 'rated')
            rating = d.get('rating')

            if status == 'aspirational':
                aspirational.append({
                    "title": title, "company": company,
                    "description": description, "rating": rating,
                    "location": location, "source": source
                })
                continue

            if rating is None:
                # Status-only decisions (no numeric rating)
                if status in ('save', 'apply'):
                    # Treat as 9/10
                    rating = 9
                elif status == 'reject':
                    # Treat as 1/10
                    rating = 1
                else:
                    continue

            # Place in correct tier
            for tier_name, tier_cfg in TIERS.items():
                lo, hi = tier_cfg["range"]
                if lo <= rating <= hi:
                    tiers[tier_name].append({
                        "title": title, "company": company,
                        "description": description, "rating": rating,
                        "location": location, "source": source
                    })
                    break

        elif isinstance(d, str):
            # Legacy string-format decisions
            if d in ('apply', 'save'):
                tiers["ideal"].append({
                    "title": title, "company": company,
                    "description": description, "rating": 9,
                    "location": location, "source": source
                })
            elif d == 'reject':
                legacy_rejects.append({
                    "title": title, "company": company,
                    "description": description, "rating": 1,
                    "location": location, "source": source
                })
            elif d == 'aspirational':
                aspirational.append({
                    "title": title, "company": company,
                    "description": description, "rating": None,
                    "location": location, "source": source
                })

    # Merge legacy rejects into hard_no
    tiers["hard_no"].extend(legacy_rejects)

    total = sum(len(v) for v in tiers.values()) + len(aspirational)
    if total == 0:
        print("No categorizable decisions found.")
        return None

    if orphaned:
        print(f"{orphaned} decisions skipped - no matching offer "
              f"(neither in the database nor in {RATED_ARCHIVE}).")

    print(f"Categorized {total} decisions (granular 5-tier):")
    for name, cfg in TIERS.items():
        count = len(tiers[name])
        print(f"   {cfg['label']}: {count}")
    print(f"   ASPIRACYJNE: {len(aspirational)}")
    if legacy_rejects:
        print(f"   (including {len(legacy_rejects)} legacy rejects merged into hard_no)")

    return {"tiers": tiers, "aspirational": aspirational}


def format_tier_text(items, tier_name, tier_cfg):
    """Format a tier's items for the prompt, with descriptions and metadata."""
    sampled = smart_sample(items, tier_cfg["max_samples"])
    desc_len = tier_cfg["desc_len"]
    
    lines = []
    for item in sampled:
        desc = item["description"][:desc_len].replace('\n', ' ').strip() if item["description"] else ""
        rating_str = f"{item['rating']}/10" if item['rating'] else "?"
        location = item.get('location', '')
        
        entry = f"[{rating_str}] {item['title']} — {item['company']}"
        if location:
            entry += f" | Lokalizacja: {location}"
        if desc and len(desc) > 20:
            entry += f"\n   Opis: {desc}..."
        lines.append(entry)
    
    skipped = len(items) - len(sampled)
    header = f"{tier_cfg['label']} — {len(items)} ofert"
    if skipped > 0:
        header += f" (pokazuję {len(sampled)}, pominięto {skipped} podobnych)"
    
    return header + "\n" + "\n".join(lines)


def build_contrastive_pairs(tiers):
    """Build contrastive pairs comparing jobs from adjacent tiers."""
    adjacent = [
        ("ideal", "very_good", "9-10 vs 7-8"),
        ("very_good", "maybe", "7-8 vs 5-6"),
        ("maybe", "rather_not", "5-6 vs 3-4"),
    ]
    
    pairs_text = []
    for high_tier, low_tier, label in adjacent:
        high_items = tiers.get(high_tier, [])
        low_items = tiers.get(low_tier, [])
        if not high_items or not low_items:
            continue
        
        # Pick up to 2 representative pairs
        num_pairs = min(2, len(high_items), len(low_items))
        for i in range(num_pairs):
            h = high_items[i]
            l = low_items[i % len(low_items)]
            h_desc = h['description'][:200].replace('\n', ' ').strip() if h.get('description') else ''
            l_desc = l['description'][:200].replace('\n', ' ').strip() if l.get('description') else ''
            
            pair = f"""--- PARA {label} (#{i+1}) ---
WYŻSZA OCENA [{h['rating']}/10]: {h['title']} — {h['company']}
   Opis: {h_desc}...
NIŻSZA OCENA [{l['rating']}/10]: {l['title']} — {l['company']}
   Opis: {l_desc}...
PYTANIE: Co KONKRETNIE sprawia, że pierwsza oferta jest wyżej oceniona?"""
            pairs_text.append(pair)
    
    return "\n\n".join(pairs_text) if pairs_text else "Brak wystarczających danych do porównań."


def build_meta_prompt(categories, cv_text):
    """Build the granular meta-analysis prompt for Gemini (v3 with contrastive pairs & weighted scoring)."""
    
    tiers = categories["tiers"]
    aspirational = categories["aspirational"]
    
    # Build tier sections
    tier_sections = []
    for tier_name, tier_cfg in TIERS.items():
        items = tiers[tier_name]
        if items:
            tier_sections.append(format_tier_text(items, tier_name, tier_cfg))
    
    tiers_text = "\n\n".join(tier_sections)
    
    # Build contrastive pairs
    contrastive_text = build_contrastive_pairs(tiers)
    
    # Aspirational section
    asp_lines = []
    for item in aspirational:
        desc = item["description"][:300].replace('\n', ' ').strip() if item["description"] else ""
        rating_str = f"{item['rating']}/10" if item['rating'] else "brak oceny"
        entry = f"[{rating_str}] {item['title']} — {item['company']}"
        if desc and len(desc) > 20:
            entry += f"\n   Opis: {desc}..."
        asp_lines.append(entry)
    
    asp_text = "\n".join(asp_lines) if asp_lines else "Brak danych"

    prompt = f"""Jesteś zaawansowanym systemem analizy preferencji zawodowych. Twoim zadaniem jest przeanalizowanie historii ocen kandydata, ale z ABSOLUTNYM PRIORYTETEM dla poniższych WYTYCZNYCH KANDYDATA.

WYTYCZNE ABSOLUTNE (CORE DIRECTIVE):
1. **Główny Cel**: Kandydat docelowo dąży do bycia Software/Web/Frontend Developerem (najlepiej w startupie bez "sufitu" rozwoju). Obecnie szuka ról Junior/Trainee/Staż w tym obszarze. To jest szczyt góry.
2. **Otwarte horyzonty**: NIE zamykaj profilu tylko na IT/Marketing. Kandydat jest bardzo otwarty na "zwykłe" prace biurowe, grafikę w lokalnych firmach, a nawet role w tradingu/ekonomii, jeśli oferta jest atrakcyjna. Celem jest pełne spektrum możliwości.
3. **Kluczowe Atuty**: Niezwykła ambicja, samozaparcie, chęć rywalizacji i dążenie do bycia najlepszym (potwierdzone latami rywalizacji na najwyższym poziomie w e-sporcie w Europie). Twarde skille: biegłość w HTML/CSS/JavaScript/Figma, podstawy WordPress, mistrzostwo w narzędziach AI, Adobe Premiere, Social Media, dobry angielski.
4. **Deal Breakers (ODRZUTY)**: Magazyn, Call Center, wymagane ponad 2 lata doświadczenia w czymkolwiek, ochroniarz/ochrona, budowa, język wymagany inny niż Polski/Angielski. Sama praca fizyczna czy nocki są do zaakceptowania jeśli reszta oferty jest świetna, ale w.w. słowa to odrzut.
5. **Deal Makers (100% MATCH)**: Staż/Junior w obszarze Web/Software/Frontend Dev, oferty nie wymagające doświadczenia, praca od zaraz.
6. **Wymarzone Summary (skopiuj ten vibe/tekst)**: "Młody ambitny, szybko uczący się człowiek, który chętnie podejmie się nauki w danym obszarze, w celu stania się w nim najlepszym. Liczy się dla niego rozwój i rywalizacja. Ma predyspozycje i chęci do pracy kreatywnej, czy to w obszarze szeroko pojętego frontend dev, czy innych kategorii. Interesuje się także architekturą, ekonomią i geopolityką. Zna podstawy HTML/CSS/JS. Kilka lat rywalizował na najwyższym poziomie w Europie w e-sporcie, co daje mu i jest dowodem na konsekwentność i dążenie do celu."

Użyj poniższego CV i historii ocen GŁÓWNIE PO TO, by uzupełnić ten profil o wagi i dodatkowe detale, ALE NIE NADAJUJ ICH WBREW POWYŻSZEMU CORE DIRECTIVE.

CV KANDYDATA (dla uzupełnienia):
{cv_text[:3000]}

HISTORIA OCEN KANDYDATA (pogrupowana wg ocen, z opisami stanowisk):

{tiers_text}

🌟 ASPIRACYJNE (branża/typ atrakcyjny, ale kandydat jeszcze nie ma kwalifikacji):
{asp_text}

🔬 ANALIZA KONTRASTOWA — PORÓWNAJ PARY OFERT Z SĄSIEDNICH TIERÓW:
(Zwróć uwagę na KONKRETNE różnice w opisach, wymaganiach i warunkach pracy)

{contrastive_text}

INSTRUKCJA ANALIZY:

1. ANALIZA GRANULARNA OCEN:
   - Porównaj oferty 9-10/10 vs 7-8/10: co sprawia, że jedne są "idealnym trafieniem" a drugie "tylko dobrym"?
   - Porównaj oferty 5-6/10 vs 3-4/10: co sprawia, że jedne są "może" a drugie "raczej nie"?
   - Analizuj OPISY STANOWISK, nie tylko tytuły! Szukaj wzorców w wymaganiach, obowiązkach, warunkach.
   - Zwróć uwagę na GRADACJĘ — 6/10 to nie 7/10 z konkretnego powodu!
   - Wykorzystaj PARY KONTRASTOWE powyżej do precyzyjnego określenia granic między tierami.

2. WZORCE DECYZYJNE:
   - Co KONKRETNIE decyduje o przeskoku z 5-6 do 7-8? (np. konkretne słowa, branża, warunki)
   - Co KONKRETNIE powoduje spadek z 5-6 do 3-4? (np. wymagania, lokalizacja, branża)
   - Jakie cechy ofert ZAWSZE dają wysokie oceny? (deal makers)
   - Jakie cechy ofert ZAWSZE dają niskie oceny? (deal breakers)

3. KLUCZOWE: Analizuj OPISY ofert w każdym tierze. Tytuł to za mało — szukaj wzorców w:
   - Wymaganych umiejętnościach/narzędziach
   - Warunkach pracy (zdalnie, biuro, fizycznie)
   - Obowiązkach (kreatywne vs rutynowe vs fizyczne)
   - Branży i typie firmy
   - Lokalizacji i warunkach dojazdu

4. USTAL WAGI SCORINGU — przydziel procentowe wagi do kategorii oceny (muszą sumować się do 100%):
   - role_fit: jak bardzo typ stanowiska pasuje do preferencji
   - industry_match: dopasowanie branży
   - skills_alignment: pokrycie wymaganych umiejętności z CV
   - work_conditions: dopasowanie warunków pracy
   - growth_potential: potencjał rozwoju i nauki
   - company_culture: profil firmy vs preferencje

Odpowiedz WYŁĄCZNIE w formacie JSON (bez markdown, bez komentarzy):
{{
    "preferred_role_types": ["lista preferowanych typów stanowisk"],
    "preferred_industries": ["lista preferowanych branż/sektorów"],
    "preferred_companies_profile": "opis profilu preferowanych firm",
    "attractive_keywords": ["słowa kluczowe/umiejętności przyciągające kandydata"],
    "red_flags": ["cechy/wymagania regularnie odrzucane"],
    "growth_directions": ["kierunki rozwoju wynikające z aspiracyjnych"],
    "work_conditions_preference": "preferowane warunki pracy",
    "location_preferences": "preferowana lokalizacja i stosunek do dojazdów/pracy zdalnej na podstawie wzorców z ocen",
    "salary_preferences": "wszelkie wzorce dotyczące oczekiwań finansowych wynikające z typów akceptowanych vs odrzucanych ofert",
    
    "deal_breakers": ["lista absolutnych elementów wykluczających — jeśli oferta MA tę cechę, ocena musi spaść do 0-20%"],
    "deal_makers": ["lista elementów natychmiastowo podnoszących ocenę — jeśli oferta MA tę cechę, dodaj +15-25% do match_percentage"],
    "negative_patterns": [
        {{"pattern": "konkretny wzorzec z opisu oferty", "example_title": "tytuł przykładowej oferty", "auto_score_max": 15}}
    ],
    "borderline_signals": {{
        "pushes_up": ["co przesuwa ofertę z 5-6 do 7-8 — konkretne cechy, które 'domykają' pozytywną decyzję"],
        "pushes_down": ["co przesuwa ofertę z 5-6 do 3-4 — konkretne cechy, które 'psują' potencjalnie OK ofertę"]
    }},
    "scoring_weights": {{
        "role_fit": 0.30,
        "industry_match": 0.15,
        "skills_alignment": 0.20,
        "work_conditions": 0.15,
        "growth_potential": 0.10,
        "company_culture": 0.10
    }},
    "rating_calibration": {{
        "what_9_10_means": "KONKRETNY opis co łączy oferty ocenione na 9-10 — jakie stanowiska, branże, wymagania?",
        "what_7_8_means": "KONKRETNY opis co łączy oferty 7-8 i DLACZEGO nie dostały 9-10",
        "what_5_6_means": "KONKRETNY opis ofert 5-6: co jest OK ale co przeszkadza?",
        "what_3_4_means": "KONKRETNY opis co czyni oferty 3-4 — dlaczego nie są zupełnym odrzuceniem?",
        "what_1_2_means": "KONKRETNY opis dlaczego te oferty dostały 1-2"
    }},
    
    "summary": "Skopiuj dokładnie Wymarzone Summary z CORE DIRECTIVE (Punkt 6) dodając na końcu 1-2 zdania z wytycznymi kalibracji ocen (kiedy dawać 100%, a kiedy 0%)"
}}
"""
    return prompt


def generate_profile(categories, cv_text):
    """Send meta-analysis request to Gemini and parse the result."""

    prompt = build_meta_prompt(categories, cv_text)

    for model_name in MODELS:
        for key_idx, api_key in enumerate(API_KEYS):
            try:
                masked = api_key[:4] + "..." + api_key[-4:]
                print(f"   Trying Model: {model_name} | Key[{key_idx}] {masked}")

                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        max_output_tokens=8192  # Increased for richer output
                    )
                )

                text = response.text.replace("```json", "").replace("```", "").strip()
                profile = json.loads(text)

                # Validate expected fields (core + new)
                required = ["preferred_role_types", "preferred_industries", "red_flags", "summary"]
                if all(k in profile for k in required):
                    print(f"   Profile generated successfully!")
                    
                    # Check for new enriched fields (v2 + v3)
                    new_fields = ["deal_breakers", "deal_makers", "borderline_signals", "rating_calibration",
                                  "scoring_weights", "negative_patterns", "location_preferences", "salary_preferences"]
                    found_new = [f for f in new_fields if f in profile]
                    print(f"   Enriched fields present: {len(found_new)}/{len(new_fields)} ({', '.join(found_new)})")
                    
                    # Validate scoring_weights sum to ~1.0 if present
                    if "scoring_weights" in profile:
                        weights_sum = sum(profile["scoring_weights"].values())
                        if abs(weights_sum - 1.0) > 0.05:
                            print(f"   scoring_weights sum to {weights_sum:.2f}, normalizing to 1.0...")
                            factor = 1.0 / weights_sum
                            profile["scoring_weights"] = {k: round(v * factor, 2) for k, v in profile["scoring_weights"].items()}
                    
                    return profile
                else:
                    missing = [k for k in required if k not in profile]
                    print(f"   Missing required fields: {missing}. Retrying...")

            except Exception as e:
                err = str(e)
                print(f"   Error: {err[:120]}")
                if "429" in err or "ResourceExhausted" in err:
                    import time
                    print("   ⏳ Rate limited. Waiting 30s...")
                    time.sleep(30)

    print("CRITICAL: Failed to generate profile with all models/keys.")
    return None


def main():
    print("=" * 60)
    print("PREFERENCE PROFILE GENERATOR v2 (Granular 5-Tier)")
    print("=" * 60)

    all_jobs = load_jobs()
    cv_text = load_cv()
    print(f"Loaded {len(all_jobs)} jobs from database.")

    categories = categorize_decisions_granular(all_jobs)
    if not categories:
        print("Aborting: No decisions to analyze.")
        sys.exit(1)

    print("\nSending granular meta-analysis request to Gemini...")
    profile = generate_profile(categories, cv_text)

    if profile:
        # Add metadata
        tiers = categories["tiers"]
        profile["_metadata"] = {
            "generated_at": datetime.now().isoformat(),
            "generator_version": "v3_contrastive",
            "total_decisions_analyzed": sum(len(v) for v in tiers.values()) + len(categories["aspirational"]),
            "breakdown": {
                "ideal_9_10": len(tiers["ideal"]),
                "very_good_7_8": len(tiers["very_good"]),
                "maybe_5_6": len(tiers["maybe"]),
                "rather_not_3_4": len(tiers["rather_not"]),
                "hard_no_1_2": len(tiers["hard_no"]),
                "aspirational": len(categories["aspirational"])
            }
        }

        temp_file = OUTPUT_FILE + ".tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, OUTPUT_FILE)
        except Exception as e:
            print(f"Error saving profile: {e}")

        print(f"\nProfile saved to {OUTPUT_FILE}")
        print(f"\nSUMMARY:")
        print(f"   {profile.get('summary', 'N/A')}")
        print(f"\nPreferred roles: {', '.join(profile.get('preferred_role_types', []))}")
        print(f"Preferred industries: {', '.join(profile.get('preferred_industries', []))}")
        print(f"Red flags: {', '.join(profile.get('red_flags', []))}")
        print(f"Growth directions: {', '.join(profile.get('growth_directions', []))}")
        
        # Print new fields
        if "deal_breakers" in profile:
            print(f"Deal breakers: {', '.join(profile.get('deal_breakers', []))}")
        if "deal_makers" in profile:
            print(f"Deal makers: {', '.join(profile.get('deal_makers', []))}")
        if "rating_calibration" in profile:
            cal = profile["rating_calibration"]
            print(f"\nRating Calibration:")
            for key in ["what_9_10_means", "what_7_8_means", "what_5_6_means", "what_3_4_means", "what_1_2_means"]:
                if key in cal:
                    print(f"   {key}: {cal[key][:120]}...")
        if "scoring_weights" in profile:
            print(f"\nScoring Weights: {profile['scoring_weights']}")
        if "negative_patterns" in profile:
            print(f"Negative patterns: {len(profile['negative_patterns'])} identified")
    else:
        print("Profile generation failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
