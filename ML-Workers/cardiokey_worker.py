"""
Single-instance S3 processing worker. Run as its own background service.
Processes raw_sessions/*.csv as enrollment and auth_sessions/*.csv as authentication.
"""
import os, io, json, time, logging
import boto3, pandas as pd
from supabase import create_client
from cardiokey_ml import enroll_person, authenticate

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cardiokey-worker")
BUCKET = os.getenv("S3_BUCKET", "cardiokey-biometric-dataset")
REGION = os.getenv("AWS_REGION", "ap-south-1")
POLL = int(os.getenv("WORKER_POLL_SECONDS", "3"))
RESULT_PREFIX = os.getenv("RESULT_PREFIX", "results/live_auth/")

AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")

if AWS_ACCESS_KEY and AWS_SECRET_KEY:
    s3 = boto3.client("s3", aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY, region_name=REGION)
else:
    s3 = boto3.client("s3", region_name=REGION)

RAW_SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
if RAW_SUPABASE_URL.endswith("/rest/v1"):
    SUPABASE_URL = RAW_SUPABASE_URL[:-8]
else:
    SUPABASE_URL = RAW_SUPABASE_URL

SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

def session_row(session_id):
    rows = sb.table("auth_sessions").select("*").eq("session_id", session_id).limit(1).execute().data or []
    return rows[0] if rows else None

def user_profile(user_id):
    if not user_id:
        return {}
    rows = sb.table("users").select("id,first_name,last_name,age,sex").eq("id", user_id).limit(1).execute().data or []
    return rows[0] if rows else {}

def resolve_candidate_user_id(candidate):
    if not candidate:
        return None
    # 1. Newly enrolled identities use their Supabase UUID as gallery key.
    try:
        import uuid
        return str(uuid.UUID(str(candidate)))
    except Exception:
        pass
    # 2. Historical C001 templates use normalized participant-name slugs.
    users = sb.table("users").select("id,first_name,last_name").execute().data or []
    for u in users:
        slug = "_".join(x for x in [u.get("first_name", ""), u.get("last_name", "")] if x).lower()
        slug = "".join(ch if ch.isalnum() or ch in "_-" else "" for ch in slug)
        if slug == str(candidate).lower():
            return u.get("id")
    return None

def publish(session_id, result):
    key = f"{RESULT_PREFIX}{session_id}.json"
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(result, indent=2, allow_nan=False).encode(),
        ContentType="application/json"
    )
    return key

def process_object(key, session_type):
    session_id = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    sess = session_row(session_id)
    if not sess:
        log.warning("No auth_sessions row for %s; leaving object for later retry", session_id)
        return False
    if sess.get("status") == "complete":
        return True

    user_id = sess.get("user_id")
    profile = user_profile(user_id) if user_id else {}
    
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    df = pd.read_csv(io.BytesIO(obj["Body"].read()))
    
    try:
        if session_type == "enroll":
            if not user_id:
                raise ValueError("Enrollment session has no user_id; refusing to create an unlinked template.")
            
            result = enroll_person(df, user_id, age_years=profile.get("age"), sex=profile.get("sex"))
            result["session_id"] = session_id
            result["session_type"] = "enroll"
            result["processed_at_utc"] = pd.Timestamp.utcnow().isoformat()
            
            # Mark user enrolled in Supabase
            sb.table("users").update({
                "registration_status": "enrolled",
                "has_active_template": True,
                "enrolled_at": pd.Timestamp.utcnow().isoformat()
            }).eq("id", user_id).execute()
            
            update = {
                "status": "complete",
                "updated_at": pd.Timestamp.utcnow().isoformat(),
                "health_scan": result.get("health_baseline", {})
            }
        else:
            result = authenticate(df, age_years=profile.get("age"), sex=profile.get("sex"))
            result["session_id"] = session_id
            result["session_type"] = "auth"
            result["processed_at_utc"] = pd.Timestamp.utcnow().isoformat()
            
            predicted_id = resolve_candidate_user_id(result.get("candidate"))
            result["predicted_user_id"] = predicted_id
            
            # Lookup predicted user display name
            predicted_display_name = result.get("user", "Unknown")
            if predicted_id:
                pred_profile = user_profile(predicted_id)
                if pred_profile:
                    predicted_display_name = f"{pred_profile.get('first_name', '')} {pred_profile.get('last_name', '')}".strip()
            
            is_match = (result.get("status") == "PROVISIONAL_MATCH")
            sim_score = float(result.get("aggregate_cosine_similarity") or 0.0)
            threshold_val = float(result.get("development_threshold") or 0.366)
            margin_val = round(sim_score - threshold_val, 3)

            update = {
                "status": "complete",
                "updated_at": pd.Timestamp.utcnow().isoformat(),
                "authenticated": is_match,
                "predicted_user": predicted_display_name,
                "predicted_user_id": predicted_id,
                "similarity": sim_score,
                "threshold": threshold_val,
                "margin": margin_val,
                "health_scan": result.get("health_status", {})
            }

        output_key = publish(session_id, result)
        sb.table("auth_sessions").update(update).eq("session_id", session_id).execute()
        log.info("Processed %s (%s) -> %s (sim=%.3f)", session_id, session_type, result.get("status"), update.get("similarity", 0.0))
        return True

    except Exception as exc:
        log.exception("Processing failed for %s", session_id)
        error = {
            "status": "PROCESSING_ERROR",
            "session_id": session_id,
            "session_type": session_type,
            "error": str(exc),
            "processed_at_utc": pd.Timestamp.utcnow().isoformat()
        }
        try:
            publish(session_id, error)
            sb.table("auth_sessions").update({
                "status": "failed",
                "error_message": str(exc),
                "updated_at": pd.Timestamp.utcnow().isoformat(),
                "health_scan": {"status": "NOT_AVAILABLE", "reason": str(exc)}
            }).eq("session_id", session_id).execute()
        except Exception:
            log.exception("Could not publish processing error")
        return True

def scan_prefix(prefix, session_type, seen):
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if not key.lower().endswith(".csv") or key in seen:
                continue
            done = process_object(key, session_type)
            if done:
                seen.add(key)

def main():
    log.info("CardioKey worker online. Bucket=%s; polling every %ss", BUCKET, POLL)
    seen = set()
    while True:
        try:
            scan_prefix("raw_sessions/", "enroll", seen)
            scan_prefix("auth_sessions/", "auth", seen)
        except Exception:
            log.exception("S3 polling iteration failed")
        time.sleep(POLL)

if __name__ == "__main__":
    main()
