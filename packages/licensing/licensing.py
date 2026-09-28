import hashlib
import hmac
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

LEMONSQUEEZY_API = "https://api.lemonsqueezy.com/v1/licenses"
BUY_URL = "https://your-store.lemonsqueezy.com/buy/your-product-id"

TRIAL_DAYS = 14
REVALIDATE_INTERVAL_SECONDS = 7 * 24 * 3600
OFFLINE_GRACE_SECONDS = 14 * 24 * 3600
REQUEST_TIMEOUT = 10

# This is not a real secret: it's baked into a distributed binary, so anyone who
# decompiles the .app can read it. Its only job is to stop a config.json opened in a
# text editor from trivially resetting the trial clock or writing "status: licensed"
# by hand. Real anti-piracy would need server-side validation on every launch, which
# is more than a solo dev's v1 needs. Generate your own random value before shipping,
# don't reuse this one.
SIGNING_SECRET = b"0d93219618a2155c836b505567e883412e7fbc915e6fd970877d0e39b549831b"


class LicenseError(Exception):
    pass


def _sign(payload: str) -> str:
    return hmac.new(SIGNING_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def _stamp(data: dict) -> dict:
    payload = json.dumps(data, sort_keys=True)
    return {"data": data, "sig": _sign(payload)}


def _unstamp(stamped):
    if not isinstance(stamped, dict) or "data" not in stamped or "sig" not in stamped:
        return None
    payload = json.dumps(stamped["data"], sort_keys=True)
    if not hmac.compare_digest(_sign(payload), stamped.get("sig", "")):
        return None
    return stamped["data"]


def _post(path, fields):
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        f"{LEMONSQUEEZY_API}/{path}",
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
        except Exception:
            detail = {}
        return detail, e.code
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, "offline"


def _instance_name():
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


def ensure_trial_started(config: dict) -> dict:
    """Stamp a trial-start time into config the first time this runs. Idempotent, and
    safe to call every launch -- it only writes when there's no valid stamp yet."""
    if _unstamp(config.get("license")) is None:
        config["license"] = _stamp({
            "trial_started_at": time.time(),
            "key": None,
            "instance_id": None,
            "last_validated_at": None,
            "status": "trial",
        })
    return config


def activate(config: dict, license_key: str) -> dict:
    """Activate a key for this machine against Lemon Squeezy and store the result.
    Raises LicenseError with a message safe to show the user."""
    license_key = license_key.strip()
    if not license_key:
        raise LicenseError("Enter a license key first.")
    result, err = _post("activate", {"license_key": license_key, "instance_name": _instance_name()})
    if err == "offline":
        raise LicenseError("Couldn't reach Lemon Squeezy. Check your connection and try again.")
    if err or not result or not result.get("activated"):
        raise LicenseError((result or {}).get("error") or "That license key isn't valid.")
    data = _unstamp(config.get("license")) or {}
    data.update({
        "key": license_key,
        "instance_id": result["instance"]["id"],
        "status": "licensed",
        "last_validated_at": time.time(),
    })
    config["license"] = _stamp(data)
    return config


def deactivate(config: dict) -> dict:
    """Free up this machine's activation slot, e.g. before the user moves the license
    to another Mac. Best-effort: clears local state even if the API call fails."""
    data = _unstamp(config.get("license")) or {}
    if data.get("key") and data.get("instance_id"):
        _post("deactivate", {"license_key": data["key"], "instance_id": data["instance_id"]})
    config["license"] = _stamp({
        "trial_started_at": data.get("trial_started_at", time.time()),
        "key": None,
        "instance_id": None,
        "last_validated_at": None,
        "status": "trial",
    })
    return config


def _revalidate(data: dict):
    """True/False if the API answered, None if unreachable (caller decides via grace window)."""
    result, err = _post("validate", {"license_key": data["key"], "instance_id": data["instance_id"]})
    if err == "offline":
        return None
    return bool(result and result.get("valid"))


def state(config: dict) -> dict:
    """The single entry point callers need. Returns:
      {"allowed": bool, "reason": str, "trial_days_left": int|None}
    reason is one of: "licensed", "trial", "trial_expired", "revoked", "revalidation_overdue".
    Mutates and may need config re-saved by the caller -- check `state.config_changed`.
    """
    before = json.dumps(config.get("license"))
    config = ensure_trial_started(config)
    data = _unstamp(config["license"])
    now = time.time()

    if data.get("status") == "licensed" and data.get("key"):
        age = now - (data.get("last_validated_at") or 0)
        if age > REVALIDATE_INTERVAL_SECONDS:
            ok = _revalidate(data)
            if ok is True:
                data["last_validated_at"] = now
                config["license"] = _stamp(data)
            elif ok is False:
                data["status"] = "revoked"
                config["license"] = _stamp(data)
                return _result(config, before, False, "revoked", None)
            elif age > REVALIDATE_INTERVAL_SECONDS + OFFLINE_GRACE_SECONDS:
                return _result(config, before, False, "revalidation_overdue", None)
        return _result(config, before, True, "licensed", None)

    elapsed_days = (now - data["trial_started_at"]) / 86400
    days_left = max(0, TRIAL_DAYS - elapsed_days)
    if days_left > 0:
        return _result(config, before, True, "trial", int(days_left) + 1)
    return _result(config, before, False, "trial_expired", 0)


def _result(config, before, allowed, reason, trial_days_left):
    return {
        "allowed": allowed,
        "reason": reason,
        "trial_days_left": trial_days_left,
        "config": config,
        "config_changed": json.dumps(config.get("license")) != before,
    }


if __name__ == "__main__":
    cfg = {}
    print("fresh install:", state(cfg))
    cfg["license"]["data"]["trial_started_at"] = time.time() - (TRIAL_DAYS + 1) * 86400
    cfg["license"] = _stamp(cfg["license"]["data"])
    print("after trial window:", state(cfg))
    cfg["license"]["data"]["trial_started_at"] += 3600  # tamper without re-signing
    print("tampered (should re-start trial, not grant time back):", state(cfg))
