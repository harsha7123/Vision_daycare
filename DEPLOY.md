# Deploying the web app for free

```
 Browser ──► Vercel (web/, static React app, free) ──► Hugging Face Space (analysis API, free CPU)
                                                         └─► Twilio (optional real calls / SMS)
```

| Part | Host | Why |
|---|---|---|
| Frontend `web/` | **Vercel** Hobby (free) | Static Vite build with global CDN |
| Backend `run_cloud.py` | **Hugging Face Spaces** Docker, CPU Basic (free: 2 vCPU, 16 GB RAM) | Enough RAM for PyTorch + YOLO, no credit card, supports long-running jobs |

Vercel can't host the backend: serverless functions are capped at 250 MB and a short execution time, and PyTorch + YOLO alone is over 700 MB.

Other free backend options, if you ever need one:

| Host | Notes |
|---|---|
| Render free | 512 MB RAM: too small for YOLO |
| Koyeb free | 512 MB RAM: same problem |
| Google Cloud Run | Free tier and 2+ GB RAM, but needs a billing card |
| Oracle Cloud Always Free | 4 ARM cores, 24 GB, the strongest option, but more setup (a plain VM running `docker compose`) |

---

## 1. Backend on Hugging Face Spaces (about 10 minutes)

1. Create a free account at https://huggingface.co, then **New → Space**.
   * SDK: **Docker** → *Blank*
   * Hardware: **CPU basic (free)**
   * Name: e.g. `vision-daycare`
2. In the Space's **Files** tab, upload the two files from [`deploy/huggingface/`](deploy/huggingface): `Dockerfile` and `README.md`. The README must replace the Space's default one because its header holds the Docker settings.
3. The Space builds (about 5–8 min) and clones this GitHub repo. When it shows **Running**, open `https://<user>-vision-daycare.hf.space/api/health`. It should return `{"ok": true, ...}`.
4. **Settings → Variables and secrets**:
   * `ALLOWED_ORIGINS` = your Vercel URL, e.g. `https://vision-daycare.vercel.app` (add after step 2 below; until then it allows every origin).
   * Optional, for real calls, see section 3.

Notes:
* Free Spaces **sleep after 48 h without traffic**. The first request wakes the Space, which takes about 1 minute; the web app shows "Server offline" until then.
* After pushing new backend code to GitHub, run **Settings → Factory rebuild** so the Space pulls it.
* Uploaded videos and results are deleted after 1 hour (`JOB_TTL_S`). The limits are 150 MB / 5 min per video and 8 videos per hour per IP (`MAX_UPLOAD_MB`, `MAX_VIDEO_S`, `JOBS_PER_HOUR`).
* Speed on the free CPU: roughly 3–5 analysed frames per second, so a 1-minute clip (sampled at 6 fps) takes about 1–2 minutes.

## 2. Frontend on Vercel (about 3 minutes)

1. At https://vercel.com, click **Add New → Project** and import `harsha7123/Vision_daycare` from GitHub.
2. **Root Directory: `web`**. The framework (Vite) is detected automatically.
3. **Environment Variables**: `VITE_API_URL` = `https://<user>-vision-daycare.hf.space`
4. **Deploy**. Every push to `main` redeploys automatically.

The sample analysis (`web/public/sample/`) is bundled with the site, so the demo works even while the Space is asleep. Users can also point the site at another backend under **Contacts & calling → Analysis server**.

## 3. Real phone calls to parents (optional, Twilio)

Out of the box, calls are **simulated**. The call screen rings in the browser, "connects" and reads the alert aloud with the computer's voice. That's enough for a client demo.

For real calls:

1. Sign up at https://www.twilio.com (the trial includes free credit), then get a phone number: **Phone Numbers → Buy a number**. The trial number is free.
2. On a trial account you can only call **verified** numbers. Add the parent phones under **Verified Caller IDs**.
3. In the Space's **Settings → Variables and secrets**, add these as **secrets**:
   * `TWILIO_ACCOUNT_SID` = `ACxxxxxxxx…`
   * `TWILIO_AUTH_TOKEN` = `…`
   * `TWILIO_FROM_NUMBER` = your Twilio number, e.g. `+15551234567`
   * `ALLOWED_CALL_NUMBERS` = `+919876543210,+919812345678`. The server refuses every other number, which stops anyone who finds your API from using it to call strangers.
4. In the web app, open **Contacts & calling**, choose **Real phone call**, and add the parent with the same number in `+91…` format.

When a critical alert is reached during playback (or you press **Call parent**), Twilio rings the phone and reads the message twice. **Send as SMS** texts the same message.

For India: Twilio calls to Indian mobiles work from a US number on a paid account, but they can be blocked or need DLT registration for SMS. For production in India, a local provider such as Exotel or Plivo is the usual choice. Only `twilio_post()` in `src/daycare/cloud_api.py` would change.

## 4. Local development

```bash
python run_cloud.py                         # backend  → http://localhost:7860/docs
cd web && npm install && npm run dev        # frontend → http://localhost:5173 (uses VITE_API_URL or localhost:7860)
python tools/make_web_sample.py             # regenerate the offline sample
```
