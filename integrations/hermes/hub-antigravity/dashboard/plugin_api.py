"""Status-only Dashboard backend, credentials stay server-side."""
import json
import os
import urllib.request
from fastapi import APIRouter

router = APIRouter()

@router.get("/status")
def status():
    token = os.environ.get("HUB_AGY_TOKEN")
    if not token:
        return {"error_code": "missing_host_token"}
    try:
        req = urllib.request.Request(os.environ.get("HUB_HOST_URL", "http://host.docker.internal:8084") + "/host/hermes/agy/status",
            headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    except Exception:
        return {"error_code": "host_unreachable"}
