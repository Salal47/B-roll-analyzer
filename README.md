# B-Roll Analyzer — deployable version

Converted from the Colab notebook: no `google.colab.drive` mount, no
hardcoded API keys, works as a script, a container, or a web app.

## ⚠️ Pehle ye kro
Aap ke original notebook me 3 real Gemini API keys hardcoded thay
(plaintext, output cells me bhi print ho rahi thi). Wo keys ab leaked
mani jayengi. **Turant** https://aistudio.google.com/apikey pe jao,
inhe delete/regenerate kro, aur naye keys kabhi bhi code me paste na
kro — hamesha environment variable / secret manager use kro (jaisa
neeche diya hai).

## Files
- `core.py` — saari logic (key rotation, Gemini call, CSV/metadata write). Import karke kahin se bhi use ho sakta hai.
- `cli.py` — terminal se run karne ke liye: `python cli.py /path/to/clips`
- `app.py` — Gradio web UI (upload clips → CSV/metadata download)
- `Dockerfile` — container platforms ke liye
- `requirements.txt`

## Environment variable
```
GEMINI_API_KEYS=key1,key2,key3
```
(comma se separate, spaces ki zaroorat nahi)

## Free deploy — best option: Hugging Face Spaces
Ye tool ek batch/one-off job hai (24/7 server ki zaroorat nahi), is liye
Spaces sabse aasan free option hai — no credit card, free CPU forever:

1. https://huggingface.co/new-space → SDK = **Gradio**.
2. Repo ke ye files upload kro: `app.py`, `core.py`, `requirements.txt`.
3. Space Settings → **Repository secrets** → add `GEMINI_API_KEYS`.
4. Space khud build ho kar live ho jayegi — clips upload kro, "Analyze"
   dabao, CSV/metadata download kro.

## Alternative — Docker par koi bhi free host (Render / Fly.io / Cloud Run)
`Dockerfile` already banaya hai. Render.com free web service pe:
1. Repo GitHub pe push kro.
2. Render → New → Web Service → repo select, "Docker" environment.
3. Environment tab me `GEMINI_API_KEYS` secret add kro.
4. Free tier idle hone par so jata hai (cold start ~30-60s) — batch tool ke
   liye ye acceptable hai.

## GitHub Actions + Google Drive se run karna

Workflow file already bana hai: `.github/workflows/analyze.yml`. Ye:
1. `rclone` se aapke Drive folder se clips (+ purana `des/` agar hai) runner
   me download karta hai,
2. `cli.py` chala kar analyze karta hai,
3. results ko **artifact zip** ke taur pe deta hai AUR **wapas Drive me**
   `des/` subfolder me push karta hai (taaki resume-by-CSV next run pe kaam kare).

Repo ko git se track nahi karna padta clips ke liye — Drive hi source of
truth rehta hai, jaisa aapke original Colab notebook me tha.

### Step 1 — Google service account bano (ek baar ka setup)
Ye GitHub Actions ko bina interactive login ke aapke Drive folder tak
access deta hai:
1. https://console.cloud.google.com → naya project (ya existing use kro).
2. **APIs & Services → Library** → "Google Drive API" search kro → Enable.
3. **APIs & Services → Credentials → Create Credentials → Service account**
   → naam de kro (e.g. `broll-actions`) → Create.
4. Service account bane ke baad → **Keys** tab → **Add Key → Create new
   key → JSON** → file download hogi.
5. Us JSON file me `client_email` field ka email copy kro
   (e.g. `broll-actions@your-project.iam.gserviceaccount.com`).
6. Apne Google Drive pe wo clips ka folder **share** kro us email ke saath,
   **Editor** access (Editor chahiye kyunki results wapas likhne hain).

### Step 2 — GitHub secrets add kro
Repo → **Settings → Secrets and variables → Actions → New repository secret**:
- `GEMINI_API_KEYS` → `key1,key2,key3`
- `GDRIVE_SA_KEY` → step 1 ki poori JSON file ka **content** paste kro (poora JSON, as-is)

### Step 3 — Drive folder ID nikaalo
Apne clips folder ko Drive me browser me kholo — URL me
`.../folders/<THIS_PART>` — yahi folder ID hai.

### Step 4 — Run kro
Repo → **Actions** tab → "Analyze B-Roll Clips" → **Run workflow** →
`drive_folder_id` field me wo ID paste kro → Run.

### Resume behavior
CSV-driven resume same hai — agla run purana `des/broll_auto.csv` Drive se
khud download karega, jo clips usme pehle se hain unhe skip karega, aur
naye results wapas Drive me push kar dega.
