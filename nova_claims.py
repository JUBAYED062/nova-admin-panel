"""Nova claims: puts role=authenticated on every Firebase user, so Supabase accepts their sign-in token.
Runs on GitHub Actions (free): each run keeps checking every 15 seconds for about 5 hours 40 minutes, then starts the next run itself, so new users get access within seconds. It only changes users that do not have it yet. No Firestore reads."""
import json, os, sys
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

PROJECT = "nova-ai-80225"
info = json.loads(os.environ["GCP_KEY"])
creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
s = AuthorizedSession(creds)
BASE = f"https://identitytoolkit.googleapis.com/v1/projects/{PROJECT}"
WANT = {"role": "authenticated"}

import time

def once():
    todo = []
    total = 0
    token = ""
    while True:
        url = f"{BASE}/accounts:batchGet?maxResults=1000" + (f"&nextPageToken={token}" if token else "")
        r = s.get(url, timeout=60)
        if r.status_code != 200:
            print("List error", r.status_code, r.text[:300]); return 1
        j = r.json()
        for u in j.get("users", []):
            total += 1
            try:
                ca = json.loads(u.get("customAttributes") or "{}")
            except Exception:
                ca = {}
            if ca.get("role") != "authenticated":
                ca.update(WANT)
                todo.append((u["localId"], ca))
        token = j.get("nextPageToken", "")
        if not token:
            break

    print("Users:", total, "to update:", len(todo))
    bad = 0
    for uid, ca in todo:
        r = s.post(f"{BASE}/accounts:update", json={"localId": uid, "customAttributes": json.dumps(ca)}, timeout=60)
        if r.status_code != 200:
            bad += 1
            print("Update error", uid[:6], r.status_code, r.text[:200])
    print("Done. Failed:", bad)
    return bad


END = time.time() + 5*3600 + 40*60
fails = 0
while True:
    try:
        fails = once()
    except Exception as e:
        print("Run error", str(e)[:200])
        fails = 1
    if time.time() > END:
        break
    time.sleep(15)
sys.exit(1 if fails else 0)
