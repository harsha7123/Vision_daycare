# Vision Daycare: Caretaker Attention Monitor

Watches a day-care play area through CCTV/IP cameras and raises a real-time alert when supervision breaks down: a caretaker absorbed in a phone, children left unattended, a child who fell. It runs on a small on-premise edge box, and **only events** (a face-blurred snapshot, a short clip, metadata) leave the building.

Stack: **Ultralytics YOLO** (pose + phone detection) · **ByteTrack** · **Supervision** · rule engine · **FastAPI** dashboard · Telegram / FCM / webhook alerts · optional keypoint **GRU** action model.

---

## 1. Run the client demo (2 minutes, no camera or GPU needed)

```bash
pip install -r requirements.txt
python run.py --open            # Windows: double-click run_demo.bat
```

Open <http://localhost:8000>. The default source is a **simulated day-care room**: scripted actors produce the same tracked boxes and 17-point skeletons YOLO would. Everything after detection runs for real: role voting, phone association, zones, timers, rules, snapshots, clips and alerts. One 150 s loop fires every rule (demo thresholds):

| Time | Rule | What happens |
|---|---|---|
| ~20 s | **R1** Caretaker on phone | Priya texts, head down, phone in hand |
| ~37 s | **R4** Ratio exceeded | Ravi goes to the kitchen: 5 children, 1 adult (demo max 4) |
| ~44 s | **R2** Left child zone | Ravi still outside the play zone |
| ~49 s | **R3** Children unattended | Priya leaves through the door: 0 adults in the zone |
| ~104 s | **R5** Child fall | A child falls and stays down (the napping child on the mat never alerts) |
| ~117 s | **R6** Caretaker idle | Ravi sits head-down, motionless |

A phone lying on the table is a deliberate hard negative and never alerts.

Then switch the source from the dashboard:

* **Webcam 0**: real YOLO11-pose + ByteTrack + phone detection on your laptop camera. Hold your phone up to your face or look down at it to trigger R1. Step into the right-hand *staff only* strip to trigger R2. Use **Set role → child** on a track to show R3 / R4.
* **Upload video…**: any recorded footage (looped in real time).
* **RTSP / HTTP URL…**: a real IP camera, e.g. `rtsp://user:pass@192.168.1.20:554/stream1`.

On the first real-camera run, Ultralytics downloads `yolo11n-pose.pt` and `yolo11s.pt` into `models/` (~26 MB).

```bash
python run.py --source 0                          # webcam
python run.py --source recordings/room1.mp4       # file
python run.py --source rtsp://... --profile production --host 0.0.0.0
```

`--profile demo` (default) uses the short thresholds in `configs/rules.demo.yaml`. `--profile production` uses the blueprint values in `configs/rules.yaml`: 120 s phone, 180 s out of zone, 60 s unattended, and so on.

### Web app: upload any video (Vercel + free backend)

`web/` is a React app you can host on **Vercel**, backed by `run_cloud.py`, which runs free on **Hugging Face Spaces**. See **[DEPLOY.md](DEPLOY.md)** for step-by-step hosting and optional real phone calls (Twilio).

* Drag in a video, optionally draw the play / nap / exit / staff-only areas on the first frame, and pick a sensitivity (short clips, demo, or real centre).
* The server runs YOLO pose + phone detection + ByteTrack, auto-calibrates adult height from the video, and evaluates R1–R6.
* The results play back on the original video with live overlays: boxes, skeletons, zones, phone timers, and in-browser face blur. A timeline shows alert markers and children/adults counts.
* As playback reaches an alert, a banner and toast pop up. **Critical alerts call the parent**: a ringing call screen that reads the alert aloud (simulated), or a real phone call and SMS through Twilio.
* You can fix who is a child, redraw zones, or change thresholds, then **Re-analyze** in seconds; YOLO doesn't need to run again.
* **Live camera:** real-time detection from the laptop camera (or a video file played as a camera), streamed to the server over a WebSocket. It shows round-trip and AI time, and a phone-use meter, and critical alerts call the parent. Host it on a GPU for 8–12 fps: [deploy/ec2/README.md](deploy/ec2/README.md).
* **Watch a sample analysis** works offline, so the Vercel demo never depends on the backend being awake.

```bash
python run_cloud.py                  # API on http://localhost:7860/docs
cd web && npm install && npm run dev # UI on http://localhost:5173
```

---

## 2. Architecture

```mermaid
flowchart LR
  subgraph EDGE["Edge box (on-premise)"]
    CAM[IP cameras<br/>RTSP 1080p] --> ING[Ingest<br/>OpenCV, 10-15 fps<br/>15 s ring buffer]
    ING --> DET[YOLO pose + detect<br/>person, keypoints, phone]
    DET --> TRK[ByteTrack<br/>stable IDs]
    TRK --> ROLE[Role resolver<br/>adult / child vote]
    ROLE --> RULES[Rule engine<br/>zones, timers, EMA<br/>hysteresis, cooldown]
    ACT[Pose GRU<br/>optional] -.-> RULES
  end
  subgraph EVT["Event & alert layer"]
    RULES --> STORE[Event service<br/>FastAPI + SQLite<br/>snapshot + clip]
    STORE --> DISP[Dispatcher<br/>Telegram / FCM / webhook<br/>escalation]
  end
  subgraph CLI["Clients"]
    DISP --> PAR[Parents<br/>critical only, blurred]
    STORE --> DASH[Admin dashboard<br/>live view, zones, review]
    DASH -->|true / false feedback| TRAIN[Training loop<br/>CVAT → fine-tune → export]
  end
```

Per processed frame (see [`src/daycare/pipeline.py`](src/daycare/pipeline.py)):

1. **Grab**: RTSP/webcam/file, sampled to 10–15 fps; annotated JPEGs go to a 15 s ring buffer for clips.
2. **Detect**: YOLO11-pose (persons + 17 keypoints, conf ≥ 0.35) and YOLO11s (COCO class 67 *cell phone*, conf ≥ 0.25).
3. **Track**: ByteTrack via `model.track(persist=True)`, `track_buffer` ≈ 3 s.
4. **Role**: p(child) from the optional crop classifier, height against the calibrated ground-plane scale, and head-to-torso proportion. A rolling 20-frame vote with hysteresis decides the role. Staff enrolment from the dashboard always wins.
5. **Phone ↔ adult**: the phone centre must be within 0.6 × shoulder width of a wrist, and the face test (call posture or head down) boosts the score. A phone on a table doesn't count. If no phone box is visible, "wrists together + head down" gives a weak 0.4 signal.
6. **State**: zone membership from the box **bottom-centre** (feet), EMA-smoothed phone score, per-track timers.
7. **Rules**: R1–R6 with enter/exit hysteresis (0.6 / 0.4), sliding windows and per-rule cooldowns.
8. **Emit**: a face-blurred snapshot, a clip from 10 s before to 3 s after, SQLite, then the dispatcher. Critical alerts go to admins and parents; high and medium go to admins and escalate to the owner if nobody acknowledges within 120 s.

### Rules

| ID | Rule | Trigger (production defaults) | Priority |
|---|---|---|---|
| R1 | Caretaker on phone | adult phone score > 0.6 for 120 s cumulative within 150 s | high |
| R2 | Left the child zone | children present, a caretaker outside the play zone > 180 s | medium |
| R3 | Children unattended | ≥ 1 child in the play zone, 0 adults, > 60 s | critical |
| R4 | Adult : child ratio | children ÷ adults > 8 for > 120 s | medium |
| R5 | Child fall / lying | child lying (pose, or action model) outside the nap zone > 30 s | critical |
| R6 | Caretaker idle | adult seated, head down, near-zero motion > 5 min | low (off in production by default) |

Multi-camera rooms: a caretaker counts as present if **any** camera in the same room sees them in its play zone (`RoomPresence`).

---

## 3. Repository layout

```
configs/            cameras.yaml, rules.yaml (+ rules.demo.yaml), alerts.yaml, bytetrack.yaml, zones/*.json
src/daycare/
  ingest/stream.py        sources (sim, webcam, RTSP, file, image) + ring buffer
  perception/detector.py  YOLO pose + phone detector + ByteTrack
  perception/role.py      adult/child resolver, crop classifier, track vote, overrides
  perception/phone_assoc.py  phone <-> wrist/face scoring
  perception/zones.py     normalised polygons, feet-anchor membership
  perception/posture.py   head-down, lying, seated, motion
  rules/state.py          per-track state machine, timers, EMA
  rules/engine.py         R1..R6, cooldowns, schedule, multi-camera presence
  actions/pose_gru.py     optional keypoint-sequence GRU
  events/store.py         SQLite, snapshots, clips, retention, SSE pub/sub
  events/dispatch.py      console / Telegram (+Acknowledge button) / webhook (HMAC) / FCM, escalation
  events/api.py           FastAPI: REST, MJPEG live view, SSE, zones, roles, rule tuning, uploads
  analyzer.py             steps 4-7 for one camera (pure, no I/O)
  pipeline.py             per-camera thread tying it all together
  sim.py                  synthetic day-care scene used by the demo and replay tests
dashboard/          single-page admin UI (vanilla JS)
training/           train_detector.py, train_role_cls.py, train_actions.py, data/daycare.yaml
tests/              unit tests + replay test of the full scenario
src/daycare/batch.py, cloud_api.py   uploaded-video analysis + cloud API for the web app
web/                React web app (Vercel); web/public/sample = offline demo
deploy/huggingface/ free backend hosting (Docker Space)
docker/             Dockerfile.edge, compose.yaml (edge + optional MediaMTX relay)
```

---

## 4. Configuration

* **Cameras**: `configs/cameras.yaml`. Set `source`, `fps`, `room`, the zone file, and the ground-plane `calibration` used by the height heuristic. Calibration pairs are `[feet_y, adult_height]` as fractions of frame height at two floor positions.
* **Zones**: draw them in the dashboard (**Edit zones**). They are saved to `configs/zones/<cam>.json` in normalised coordinates. The types are `play` (the child zone), `nap`, `exit` and `staff_only`. If no play zone is drawn, the whole frame counts as play.
* **Rules**: `configs/rules.yaml`. You can also tune them live on the **Rules** tab (in memory).
* **Alerts**: copy `.env.example` to `.env`.
  * *Telegram*: create a bot with @BotFather and set `TELEGRAM_BOT_TOKEN` plus the chat IDs. Alerts arrive with the snapshot and an **Acknowledge** button; the bot uses long polling, so no public URL is needed.
  * *Webhook*: `WEBHOOK_URL` receives JSON signed with `X-Daycare-Signature: sha256=HMAC(WEBHOOK_SECRET, body)`, with retries and backoff.
  * *FCM*: `pip install firebase-admin` and set `GOOGLE_APPLICATION_CREDENTIALS`. Messages go to topic `centre_<room>`.

### API (all JSON; interactive docs at `/docs`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` · `/api/health` | cameras, counts, timers, tracks, channel deliveries |
| GET | `/api/cameras/{id}/stream.mjpg` | annotated live view |
| POST | `/api/cameras/{id}/source` · `/upload` | switch source / upload a video |
| GET/PUT | `/api/cameras/{id}/zones` | zone polygons |
| POST | `/api/cameras/{id}/tracks/{tid}/role` | enrol staff / correct a role (`adult`, `child`, `auto`) |
| GET/PUT | `/api/rules` | read / tune thresholds |
| GET | `/api/events` · `/api/events/{id}?public=true` | history / blueprint payload |
| GET | `/api/events/stream` | Server-Sent Events: new events and acknowledgements |
| POST | `/api/events/{id}/ack` | acknowledge, with `feedback: "true"/"false"` for tuning data |

---

## 5. Training (phases 2–5 of the plan)

```bash
# detector: adult / child / cell_phone, labelled in CVAT and exported as YOLO
python training/train_detector.py --data training/data/daycare.yaml --imgsz 960
# role classifier from your own cameras' crops -> models/role_cls.pt (picked up automatically)
python training/train_role_cls.py extract --video recordings/cam01.mp4
python training/train_role_cls.py train --data data/crops
# action GRU -> models/pose_gru.pt (picked up automatically); --synthetic is a smoke test only
python training/train_actions.py --synthetic
# TensorRT FP16 on the target device
python training/train_detector.py --export models/best.pt
```

Weights are **not** committed; track them with DVC or a model registry.

## 6. Deployment

```bash
docker compose -f docker/compose.yaml up -d          # GPU edge box (NVIDIA container toolkit)
```

* Small centre (1–4 cameras): Jetson Orin NX 16 GB. Larger (4–12): a mini-PC with an RTX 4060/4070.
* Put cameras on an isolated VLAN. The edge box needs outbound-only internet for alerts, and remote admin goes over VPN only. **The dashboard has no login**, so never expose port 8000 or RTSP to the internet.
* For CUDA on a dev PC, install PyTorch from <https://pytorch.org> (for example `pip install torch --index-url https://download.pytorch.org/whl/cu126`). The device is picked automatically.
* On Windows, keep the project in a short path (for example `C:\vision_daycare`), or set `DAYCARE_DATA_DIR`, to stay under the 260-character path limit for event media.

## 7. Testing

```bash
pip install -r requirements-ci.txt && python -m pytest -q
```

The suite has unit tests for phone association, zones, roles and every rule, plus a **replay test**. The replay runs the full 150 s scenario faster than real time and asserts that each rule fires once, in its time window, on the right track, and that the hard negatives stay silent. CI runs it on every push without torch or a GPU.

## 8. Privacy, consent & legal

This system records children and monitors employees, so privacy is a core requirement. In India, the DPDP Act 2023 requires verifiable parental consent. Get legal advice before deployment.

* Written parental consent and clear staff notice (policy + signage).
* On-premise processing: only events leave the site, faces are blurred on snapshots, and clips are admin-only.
* Automatic retention: events are purged after 30 days (`privacy.retention_days`).
* No facial recognition or identity profiling, only the roles adult / child.
* Alerts support supervision. A human reviews the clip before any action.

**Licence note:** Ultralytics YOLO is AGPL-3.0. Selling this, or running it as a service without releasing your source, needs an Ultralytics Enterprise licence, or a switch to RT-DETR / YOLOX / RTMDet (Apache-2.0).

## 9. Status against the build checklist

- [x] Baseline YOLO + ByteTrack + zones (live, file, RTSP, webcam)
- [x] Phone association using pose keypoints
- [x] Rules R1–R6 with hysteresis, cooldown and schedule
- [x] Event service, snapshots (face-blurred), clips, retention
- [x] Telegram alerts with Acknowledge, webhook (HMAC), FCM, escalation
- [x] Dashboard: live view, zone editor, staff enrolment, event review with feedback, live rule tuning
- [x] Replay test suite in CI
- [x] Training scripts: detector, role classifier, action GRU; TensorRT export
- [ ] Consent and staff notice agreed with the centre
- [ ] Record 5–10 h of footage and label 2–5 k frames (adult / child / phone)
- [ ] Train and evaluate the role classifier on the centre's own cameras
- [ ] TensorRT multi-camera benchmark on the target edge box
- [ ] Two-week shadow-mode pilot and threshold tuning
