# Strategia Skalowania i Limity API

## 🎯 Odpowiedzi na Twoje Pytania

### 1. Czy pobierzemy więcej niż 50 ofert?

**TAK!** Scraper może pobrać **200-500 ofert** z wszystkich 5 portali:

**Main Scraper (`main_scraper.py`):**
```python
# Pobiera z 5 portali jednocześnie:
- Pracuj.pl:    ~50-100 ofert (do 5 stron)
- OLX Praca:    ~40-80 ofert
- RocketJobs:   ~30-60 ofert
- LinkedIn:     ~40-80 ofert  
- Indeed:       ~40-80 ofert
-------------------------------------------
RAZEM:          ~200-400 ofert
```

**Jak uruchomić:**
```bash
python main_scraper.py
```
Zajmie 10-20 minut, ale zbierze wszystkie portale.

---

### 2. Problem Limitów API Gemini

**Twoje obecne limity (z screenshot):**

| Model | RPM (Requests/min) | TPM (Tokens/min) | RPD (Requests/day) |
|-------|-------------------|------------------|-------------------|
| Gemini 3 Flash | 5 | 250K | 20 |
| Gemini 2.5 Flash Lite | 10 | 250K | 20 |

**Problem:** 
- 300 ofert × 1 request = 300 requestów
- Limit: 5 req/min = tylko 5 ofert/minutę
- Czas: 300 ÷ 5 = **60 minut!** (za długo)

---

## ✅ Rozwiązania

### **Rozwiązanie 1: Rate Limiting z Progress** (ZALECANE)

Dodamy inteligentne zarządzanie limitami:

```python
# W utils/gemini_client.py - dodamy:
import time
from datetime import datetime

class GeminiClient:
    def __init__(self, api_key, model_name, rpm_limit=5):
        self.rpm_limit = rpm_limit  # 5 req/min dla Gemini 3 Flash
        self.request_times = []
    
    def batch_analyze_with_limits(self, cv_text, jobs, show_progress=True):
        matches = []
        total = len(jobs)
        
        for idx, job in enumerate(jobs, 1):
            # Czekaj jeśli przekroczono limit
            self._wait_if_needed()
            
            # Analizuj
            result = self.analyze_job_match(cv_text, job.description, job.title)
            
            if result:
                matches.append(JobMatch(...))
            
            # Progress
            if show_progress:
                elapsed = (datetime.now() - start_time).seconds
                remaining = (total - idx) * (elapsed / idx)
                print(f"{idx}/{total} | ETA: {remaining/60:.1f} min")
        
        return matches
    
    def _wait_if_needed(self):
        now = time.time()
        # Wyczyść stare requesty (starsze niż 60s)
        self.request_times = [t for t in self.request_times if now - t < 60]
        
        # Jeśli osiągnięto limit, czekaj
        if len(self.request_times) >= self.rpm_limit:
            sleep_time = 60 - (now - self.request_times[0])
            time.sleep(sleep_time + 1)
            self.request_times = []
        
        # Zapisz czas requestu
        self.request_times.append(now)
```

**Efekt:**
- Automatycznie przestrzega limitu 5 req/min
- Pokazuje progress (np. "50/300 | ETA: 45 min")
- Działa w tle, nie trzeba pilnować

**Czas dla 300 ofert:**
- 300 ofert ÷ 5 req/min = **60 minut**
- Można uruchomić i zostawić na noc

---

### **Rozwiązanie 2: Batch Processing z Checkpoint**

Podziel analizę na sesje:

```python
# Dodamy w Streamlit:
- "Analyze Next 50 Jobs" button
- Zapisuje checkpoint po każdych 50 jobów
- Można wznowić później
```

**Przykład:**
1. Sesja 1: Analizuj jobs 0-50 (10 min)
2. **Zapisz checkpoint**
3. Wróć za godzinę 
4. Sesja 2: Analizuj jobs 51-100 (10 min)
5. itd.

---

### **Rozwiązanie 3: Pre-filtering** (NAJBARDZIEJ EFEKTYWNE)

Przed wysłaniem do AI, filtruj oferty lokalnie:

```python
def quick_filter(job):
    """Szybki lokalny filtr bez AI"""
    
    # Odrzuć jeśli w tytule są słowa:
    bad_keywords = ['senior', 'lead', 'manager', 'architect', 
                    'principal', 'head of', '5+ lat', 'specjalista']
    
    title_lower = job.title.lower()
    if any(keyword in title_lower for keyword in bad_keywords):
        return False
    
    # Akceptuj jeśli w tytule:
    good_keywords = ['junior', 'asystent', 'praktyk', 'staż', 'trainee',
                     'entry', 'młodszy', 'bez doświadczenia']
    
    if any(keyword in title_lower for keyword in good_keywords):
        return True
    
    return True  # Dalej analizuj z AI

# Użycie:
jobs_to_analyze = [j for j in all_jobs if quick_filter(j)]
# 300 jobs → ~150 jobs (50% redukcja)
# Czas: 150 ÷ 5 = 30 minut zamiast 60!
```

---

## 📊 Rekomendowana Strategia

**Krok 1: Zbierz dużą bazę**
```bash
python main_scraper.py
# Wynik: 200-400 ofert w jobs_database.json
```

**Krok 2: Smart Analysis**
```
1. Pre-filter lokalnie (title-based) → ~150 ofert
2. Batch analyze z rate limiting → ~30 minut
3. Zapisuj checkpoint co 50 ofert
4. Możesz przerwać i wznowić później
```

**Krok 3: Rezultaty**
```
- 150 ofert przeanalizowanych
- Top 20-30 z >70% match
- Gotowe do aplikowania!
```

---

## 🔧 Co Muszę Dodać

1. **Rate Limiter** w `gemini_client.py`
2. **Quick Filter** w `streamlit_app.py`
3. **Progress Bar** w Streamlit
4. **Checkpoint System** (opcjonalnie)

**Czy chcesz żebym to teraz dodał?**

---

## 💡 Alternatywa: Gemini 2.5 Flash Lite

Zamiast Gemini 3 Flash (5 req/min), użyj **Gemini 2.5 Flash Lite**:
- **10 req/min** (2x szybciej!)
- Te same 250K TPM
- 300 ofert ÷ 10 = **30 minut** zamiast 60

Zmiana w `config.py`:
```python
GEMINI_MODEL = "gemini-2.5-flash-lite"  # 10 req/min
```

---

## 📈 Podsumowanie

| Metoda | Czas dla 300 ofert | Wady |
|--------|-------------------|------|
| Gemini 3 Flash (5 RPM) | 60 min | Powolne |
| **Gemini 2.5 Flash Lite (10 RPM)** | **30 min** | ✓ Najlepsze |
| Pre-filter → 150 ofert | 15-30 min | Może przegapić oferty |
| Batch sessions (3×100) | 3× 20 min | Trzeba wracać |

**Moja rekomendacja:**
1. Użyj **Gemini 2.5 Flash Lite** (10 req/min)
2. Dodaj **Pre-filter** (tytuł jobs)
3. Dodaj **Progress Bar**
4. Uruchom i zostaw na 30-40 minut

**Result:** ~150-200 przeanalizowanych ofert, top 30-50 z najlepszym matchem!
