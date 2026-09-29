---
title: Vision Daycare API
emoji: 👶
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: Video analysis backend for the Vision Daycare web app
---

# Vision Daycare: analysis API

The backend for the Vision Daycare web app: YOLO pose + phone detection, ByteTrack, and the day-care safety rules.
Source: https://github.com/harsha7123/Vision_daycare · API docs: `/docs`

Settings → Variables and secrets:

| Name | Example | Purpose |
|---|---|---|
| `ALLOWED_ORIGINS` | `https://vision-daycare.vercel.app` | Only your Vercel site may call the API |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | (secrets) | Optional: real phone calls and SMS |
| `ALLOWED_CALL_NUMBERS` | `+919876543210,+919812345678` | Numbers the server is allowed to call |
