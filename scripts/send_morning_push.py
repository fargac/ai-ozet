import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta

import firebase_admin
from firebase_admin import credentials, messaging, firestore
from google.api_core.exceptions import AlreadyExists

TR_TZ = timezone(timedelta(hours=3))
SUMMARY_PATH = "cdn_data/summaries/hourly_latest.json"
FALLBACK_BODY = "Türkiye ve dünyadan öne çıkan sabah gelişmelerini dinleyin."
LIVE_TOPIC = "daily_summary"


def truncate_text(text: str, limit: int = 100) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def build_push_body(items: list) -> str:
    titles = [
        str(item.get("title", "")).strip()
        for item in items
        if isinstance(item, dict) and str(item.get("title", "")).strip()
    ]

    if not titles:
        return FALLBACK_BODY

    if len(titles) == 1:
        return truncate_text(titles[0], 110)

    remaining = len(titles) - 2
    suffix = f" (+{remaining} gelişme)" if remaining > 0 else ""

    combined_full = f"{titles[0]} • {titles[1]}{suffix}"
    if len(combined_full) <= 115:
        return combined_full

    first = truncate_text(titles[0], 46)
    second = truncate_text(titles[1], 46)
    return f"{first} • {second}{suffix}"


def get_morning_body(now_tr: datetime) -> str | None:
    if not os.path.exists(SUMMARY_PATH):
        print(f"::warning::{SUMMARY_PATH} bulunamadı, bildirim atlandı.")
        return None

    with open(SUMMARY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    generated_at_raw = data.get("generated_at")
    if not generated_at_raw:
        print("::warning::generated_at alanı eksik, bildirim atlandı.")
        return None

    try:
        generated_dt = datetime.fromisoformat(str(generated_at_raw)).astimezone(TR_TZ)
    except ValueError:
        print(f"::warning::Geçersiz generated_at formatı: {generated_at_raw}")
        return None

    if generated_dt.date() != now_tr.date():
        print(f"::warning::Özet bugüne ait değil ({generated_dt.date()}), bildirim atlandı.")
        return None

    items = data.get("items")
    if not isinstance(items, list) or len(items) == 0:
        print("::warning::Özet maddeleri boş, bildirim atlandı.")
        return None

    return build_push_body(items)


def main():
    now_tr = datetime.now(TR_TZ)
    force = os.environ.get("PUSH_FORCE") == "1"
    topic = os.environ.get("PUSH_TOPIC", LIVE_TOPIC)

    # Test modu canlı topic'e asla gönderemez
    if force and topic == LIVE_TOPIC:
        print("❌ PUSH_FORCE canlı topic ile kullanılamaz. PUSH_TOPIC=daily_summary_test verin.")
        sys.exit(1)

    if not force and not (8 <= now_tr.hour < 11):
        print(f"ℹ️ Sabah penceresi dışında ({now_tr.strftime('%H:%M')}). Atlandı.")
        return

    cred_json = os.environ.get("FIREBASE_CREDENTIALS")
    if not cred_json:
        print("❌ FIREBASE_CREDENTIALS bulunamadı.")
        sys.exit(1)

    push_body = get_morning_body(now_tr)
    if not push_body:
        return

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(json.loads(cred_json)))

    db = firestore.client()
    today_id = now_tr.strftime("%Y-%m-%d")

    # Force modunda her denemeye ayrı kayıt: aynı gün tekrar test edilebilir
    log_id = f"{today_id}_{topic}"
    if force:
        log_id += f"_{int(time.time())}"
    doc_ref = db.collection("morning_push_logs").document(log_id)

    try:
        doc_ref.create({"status": "sending", "started_at": firestore.SERVER_TIMESTAMP})
    except AlreadyExists:
        print(f"✅ {log_id} bildirimi zaten gönderilmiş/işleniyor. Atlandı.")
        return

    ttl_seconds = 3 * 60 * 60
    message = messaging.Message(
        notification=messaging.Notification(
            title="Sabah Gündemi Hazır",
            body=push_body,
        ),
        android=messaging.AndroidConfig(
            ttl=timedelta(seconds=ttl_seconds),
            priority="high",
            notification=messaging.AndroidNotification(sound="default"),
        ),
        apns=messaging.APNSConfig(
            headers={"apns-expiration": str(int(time.time()) + ttl_seconds)},
            payload=messaging.APNSPayload(aps=messaging.Aps(sound="default")),
        ),
        data={"type": "daily_summary", "date": today_id},
        topic=topic,
    )

    try:
        resp = messaging.send(message)
    except Exception:
        doc_ref.delete()
        raise

    doc_ref.update({
        "status": "sent",
        "sent_at": firestore.SERVER_TIMESTAMP,
        "message_id": resp,
    })
    print(f"🚀 Bildirim gönderildi ({topic}). ID: {resp}")


if __name__ == "__main__":
    main()