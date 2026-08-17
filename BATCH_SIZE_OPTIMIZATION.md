# Batch Size Optimization Analysis

## 🎯 Dlaczego 15? (Stara wersja)

**To było ZBYT konserwatywne!** Teraz 30, ale można więcej.

---

## 📊 Token Math - Maximum Batch Size

### Gemini Token Limits:
```
Input tokens:  30,000 (free tier)
Output tokens: 16,384 (default max)
```

### Calculation for Job Batch:

**Input Side:**
```
CV text:                  ~500 tokens
Prompt overhead:          ~500 tokens
Available for jobs:       29,000 tokens

Job description (1000 chars): ~250 tokens each

Maximum jobs = 29,000 ÷ 250 = 116 jobs (theoretical max)
```

**Output Side:**
```
Each result: ~60 tokens
{
  "match_percentage": 85,
  "reason": "Kandydat ma wymagane skill...",
  "is_entry_level": true
}

50 jobs × 60 tokens = 3,000 tokens
100 jobs × 60 tokens = 6,000 tokens
```

---

## ✅ Recommended Batch Sizes

| Batch Size | Input Tokens | Output Tokens | Safety | Time for 6000 jobs @ 5 rpm |
|------------|--------------|---------------|--------|---------------------------|
| **15 jobs** | ~4,750 | ~900 | 🟢 Very Safe | 80 min (400 requests) |
| **30 jobs** ⭐ | ~8,000 | ~1,800 | 🟢 Safe | **40 min (200 requests)** |
| **50 jobs** | ~13,000 | ~3,000 | 🟡 Good | **24 min (120 requests)** |
| **100 jobs** | ~25,500 | ~6,000 | 🔴 Risky | 12 min (60 requests) |

⭐ **Current: 30 jobs** - Best balance of safety and speed

---

## 🚀 Performance Comparison

### For 6000 jobs with 5 req/min:

**OLD (15 jobs/request):**
```
6000 ÷ 15 = 400 API requests
400 ÷ 5 req/min = 80 minutes
```

**NEW (30 jobs/request):**
```
6000 ÷ 30 = 200 API requests
200 ÷ 5 req/min = 40 minutes ⚡ 2X FASTER!
```

**AGGRESSIVE (50 jobs/request):**
```
6000 ÷ 50 = 120 API requests
120 ÷ 5 req/min = 24 minutes ⚡⚡ 3.3X FASTER!
```

---

## ⚙️ How to Optimize Further

### Test with larger batches:

1. **Edit `utils/gemini_client.py`:**
   ```python
   def batch_analyze(..., batch_size: int = 50):  # Change from 30 to 50
   ```

2. **Update output token limit:**
   ```python
   max_output_tokens=10000  # For 100+ job batches
   ```

3. **Monitor logs for errors:**
   - Token limit exceeded → reduce batch_size
   - Quality issues → reduce batch_size
   - Works fine → increase batch_size!

---

## 🧪 Gradual Testing Strategy

**Start small, scale up:**

```python
# Test 1: 30 jobs (current, safe)
batch_size = 30
# Test 10 jobs → verify quality

# Test 2: 50 jobs (if 30 works)  
batch_size = 50
# Test 10 jobs → verify quality

# Test 3: 75 jobs (if 50 works)
batch_size = 75
# Test 10 jobs → verify quality

# Find sweet spot where:
# ✓ No token errors
# ✓ Good AI quality
# ✓ Fast processing
```

---

## 📈 Theoretical Maximum

**IF we're aggressive:**

```python
batch_size = 100
max_output_tokens = 10000

6000 jobs ÷ 100 = 60 API requests
60 ÷ 5 req/min = 12 minutes! 🚀

But risks:
- May exceed token limit on long descriptions
- AI quality may decrease with too many jobs
- Higher chance of API errors
```

---

## ✅ Current Configuration (Optimized)

```python
# utils/gemini_client.py
batch_size = 30 (default)
max_output_tokens = 5000

# Supports up to ~80 jobs per batch if needed
# 30 is safe sweet spot for quality + speed
```

**Result:**
- 6000 jobs in 40 minutes (vs 80 minutes before)
- 2x faster than original
- Still safe token limits
- Good AI quality maintained

---

## 💡 User Can Override

In Streamlit, user could potentially choose:

```python
# Future enhancement in config.py:
BATCH_SIZE = 30  # Conservative
BATCH_SIZE = 50  # Aggressive  
BATCH_SIZE = 75  # Very Aggressive

# Or let user choose in UI:
batch_size = st.slider("Batch Size", 10, 75, 30)
```

---

## 🎯 Bottom Line

**Why 30 (not 15)?**
- 15 was too conservative (only 16% of token capacity)
- 30 uses ~27% of capacity - much better!
- 2x faster processing
- Still very safe margins

**Why not 50 or 100?**
- 30 is proven safe
- Can increase to 50 after testing
- Need to verify AI quality doesn't degrade
- Better to be safe than have errors mid-run

**Recommendation:**
- Start with 30 (current)
- After successful runs, try 50
- Monitor quality and errors
- Find YOUR optimal batch_size
