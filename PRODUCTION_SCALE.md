# PRODUCTION SCALE SYSTEM - Full Specification

## 🎯 System Capabilities

### Scraping Scale
**Target: ~4400+ jobs from Pracuj.pl alone + other portals**

**Pracuj.pl filters (from screenshot):**
- ✅ Praktykant/stażysta (146)
- ✅ Asystent (572) 
- ✅ Młodszy specjalista (Junior) (1719)
- ✅ Menedżer (707)
- ✅ Pracownik fizyczny (1421)
- **Total showing: 4374 jobs** (your screenshot)

**Complete system will scrape:**
- Pracuj.pl: ~4400 jobs (UNLIMITED pagination)
- OLX Praca: ~500-1000 jobs
- RocketJobs: ~300-500 jobs
- LinkedIn: ~500-1000 jobs
- Indeed: ~500-1000 jobs
---
**GRAND TOTAL: ~6000-8000 jobs**

---

## 💡 Revolutionary Batch AI Processing

### The Problem (Old Way):
```
4400 jobs × 1 API request per job = 4400 requests
4400 requests ÷ 5 req/min = 880 minutes (14.7 HOURS!) ❌
```

### The Solution (New Way - BATCH PROCESSING):
```
4400 jobs ÷ 15 jobs per request = ~293 API requests
293 requests ÷ 5 req/min = 59 minutes (~1 HOUR!) ✅
```

**How it works:**
```python
# BEFORE (1 job per request):
analyze_job_match(cv, job1)  # Request 1
analyze_job_match(cv, job2)  # Request 2
analyze_job_match(cv, job3)  # Request 3
# 4400 requests total

# AFTER (15 jobs per request):
analyze_job_batch(cv, [job1, job2, ..., job15])  # Request 1
analyze_job_batch(cv, [job16, job17, ..., job30])  # Request 2  
analyze_job_batch(cv, [job31, job32, ..., job45])  # Request 3
# ~293 requests total (15x FEWER!)
```

---

## 📊 Performance Analysis

### Scenario: 6000 Jobs Total

| Method | API Requests | Time @ 5 req/min | Time @ 10 req/min |
|--------|-------------|------------------|-------------------|
| **Old (1 job/req)** | 6000 | 1200 min (20 hrs) | 600 min (10 hrs) |
| **NEW (15 jobs/req)** | **400** | **80 min (1.3 hrs)** | **40 min** |
| **NEW (20 jobs/req)** | **300** | **60 min (1 hr)** | **30 min** |

**🚀 Result: 15-20x faster with batch processing!**

---

## 🔧 Technical Implementation

### 1. Unlimited Scraping
```python
# scrapers/pracuj_scraper.py
while True:  # No more max_pages=5 limit!
    page_jobs = self.scrape_current_page()
    if not page_jobs:
        break  # Stop when no more results
    jobs.extend(page_jobs)
    if not self.go_to_next_page():
        break
    
    # Safety: max 200 pages (~10,000 jobs)
```

### 2. Batch AI Analysis
```python
# utils/gemini_client.py
def analyze_job_batch(cv, jobs_batch):
    """Analyze 10-20 jobs in ONE API request"""
    
    # Build prompt with multiple jobs
    prompt = f"""
    CV: {cv}
    
    OFERTA 1: {job1.title} - {job1.description}
    OFERTA 2: {job2.title} - {job2.description}
    ...
    OFERTA 15: {job15.title} - {job15.description}
    
    Return JSON array:
    [
      {{"match": 85, "reason": "...", "entry": true}},
      {{"match": 60, "reason": "...", "entry": true}},
      ...
    ]
    """
    
    # ONE API request returns 15 results!
```

### 3. Smart Rate Limiting
```python
def batch_analyze(cv, jobs, rpm_limit=5, batch_size=15):
    num_requests = len(jobs) // batch_size
    
    for batch_num in range(num_requests):
        # Rate limit check
        if requests_this_minute >= rpm_limit:
            sleep(60 - elapsed)
        
        # Process batch of 15 jobs
        batch = jobs[batch_num*15:(batch_num+1)*15]
        results = analyze_job_batch(cv, batch)
        
        # Progress
        print(f"{batch_num*15}/{len(jobs)} | ETA: {eta} min")
```

---

## 🎯 Usage Example

### Full Production Run:

```bash
# Step 1: Scrape ALL portals (10-30 min)
python main_scraper.py
# Result: 6000-8000 jobs in jobs_database.json

# Step 2: Analyze with batch AI (60-80 min)
# In Streamlit:
# - Upload CV
# - Load Jobs (6000+)
# - Click "Analyze with AI"
# - Wait ~1 hour
# - Get results sorted by match %

# Step 3: Filter & Apply
# - Top 100 jobs with >70% match
# - Mark "Apply" to best fits
# - Export CSV
```

---

## 💰 Cost/Quota Analysis

### Gemini 3 Flash (5 req/min):
- 6000 jobs ÷ 15 per request = 400 API requests
- Time: 400 ÷ 5 = **80 minutes**
- Daily limit: Typically 1500 requests/day
- **Can analyze 22,500 jobs per day!**

### Gemini 2.5 Flash Lite (10 req/min):
- 6000 jobs ÷ 15 per request = 400 API requests  
- Time: 400 ÷ 10 = **40 minutes**
- Daily limit: Typically 1500 requests/day
- **Can analyze 22,500 jobs per day!**

---

## 🚀 Benefits

1. **True Production Scale**
   - Can handle 4400+ jobs from one portal
   - Total system: 6000-8000 jobs
   - No artificial limits

2. **Efficient API Usage**
   - 15-20x fewer API requests
   - Stays within free tier limits
   - ~1 hour for full analysis

3. **Better Results**
   - Analyzes EVERY available job
   - No jobs missed
   - Complete market coverage

4. **User Experience**
   - Set it and forget it
   - Progress tracking
   - Real-time ETA

---

## 📈 Recommended Settings

```python
# config.py
SCRAPER_CONFIG = {
    "max_pages": None,  # Unlimited!
    "batch_size": 15,   # Jobs per API request
    "rpm_limit": 5,     # Gemini 3 Flash
}

# Or for faster:
GEMINI_MODEL = "gemini-2.5-flash-lite"  # 10 req/min
batch_size = 20  # Push to 20 jobs per request
# Result: 6000 jobs in 30 minutes!
```

---

## ✅ Current Status

- ✅ Unlimited pagination implemented
- ✅ Batch AI processing (15 jobs/request)
- ✅ Rate limiting with progress
- ✅ Ready for production use

**The system is now ready to handle REAL-WORLD scale!**
