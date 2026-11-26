"""Nova guard: when today's Firestore reads get close to the free limit, switch Nova off (Maintenance + Block) until the limit resets.
It reads the usage from Google Monitoring (this is NOT a Firestore read) and touches config/app only when something has to change."""
import json, os, sys, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

PROJECT = "nova-ai-80225"
LIMIT_ON = int(os.environ.get("LIMIT_ON", "40000"))   # switch Nova off at this many reads today
LIMIT_OFF = 5000                                       # a new day has started when reads fall below this
MESSAGE = ("আজকের সার্ভার সীমা প্রায় শেষ। সিস্টেম ঠিক রাখতে Nova কিছুক্ষণের জন্য বন্ধ রাখা হলো। "
           "বাংলাদেশ সময় বিকেল ২টার পর আবার চালু হবে। অসুবিধার জন্য দুঃখিত 🙏")

info = json.loads(os.environ["GCP_KEY"])
creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
s = AuthorizedSession(creds)

pac = ZoneInfo("America/Los_Angeles")
now = datetime.now(pac)
day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
next_reset = day_start + timedelta(days=1)
secs = max(120, int((now - day_start).total_seconds()))


def reads_today():
    params = {
        "filter": 'metric.type="firestore.googleapis.com/document/read_count"',
        "interval.startTime": day_start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval.endTime": now.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggregation.alignmentPeriod": f"{secs}s",
        "aggregation.perSeriesAligner": "ALIGN_SUM",
        "aggregation.crossSeriesReducer": "REDUCE_SUM",
    }
    r = s.get(f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/timeSeries", params=params, timeout=60)
    if r.status_code != 200:
        print("Monitoring error", r.status_code, r.text[:300]); sys.exit(1)
    total = 0
    for ts in r.json().get("timeSeries", []):
        for p in ts.get("points", []):
            v = p.get("value", {})
            total += int(v.get("int64Value", 0)) + int(float(v.get("doubleValue", 0)))
    return total


DOC = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents/config/app"


def get_cfg():
    r = s.get(DOC, timeout=60)
    if r.status_code == 404:
        return {}
    if r.status_code != 200:
        print("Config read error", r.status_code, r.text[:300]); sys.exit(1)
    return r.json().get("fields", {})


def patch(fields):
    q = "&".join("updateMask.fieldPaths=" + k for k in fields)
    r = s.patch(DOC + "?" + q, json={"fields": fields}, timeout=60)
    if r.status_code != 200:
        print("Config write error", r.status_code, r.text[:300]); sys.exit(1)


reads = reads_today()
print("Reads today:", reads, "limit on:", LIMIT_ON)
now_ms = int(time.time() * 1000)

if reads >= LIMIT_ON:
    cfg = get_cfg()
    if cfg.get("maintenance", {}).get("booleanValue") is True:
        print("Maintenance is already on. Nothing to do.")
    else:
        end_ms = int(next_reset.timestamp() * 1000) + 120000
        patch({
            "maintenance": {"booleanValue": True},
            "maintenance_block": {"booleanValue": True},
            "maintenance_message": {"stringValue": MESSAGE},
            "maintenance_start": {"integerValue": "0"},
            "maintenance_end": {"integerValue": str(end_ms)},
            "auto_maint": {"booleanValue": True},
        })
        print("Maintenance switched ON until", next_reset.isoformat())
elif reads < LIMIT_OFF:
    # A new day has started: only now look at the settings (to switch off what this guard switched on).
    cfg = get_cfg()
    if cfg.get("auto_maint", {}).get("booleanValue") is True:
        end = int(cfg.get("maintenance_end", {}).get("integerValue", "0") or 0)
        if True:
            patch({"maintenance": {"booleanValue": False}, "maintenance_block": {"booleanValue": False}, "auto_maint": {"booleanValue": False}})
            print("Maintenance switched OFF (new day).")
    else:
        print("All fine.")
else:
    print("All fine.")
