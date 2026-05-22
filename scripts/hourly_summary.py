import os
import json
import feedparser
import time
from datetime import datetime, timezone, timedelta
from dateutil import parser as date_parser
from google import genai

# 🛡️ ANTI-BAN (ENGEL ÖNLEYİCİ) KİMLİK
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
feedparser.USER_AGENT = USER_AGENT

SOURCES = [
    {"name": "Habertürk", "url": "https://www.haberturk.com/rss/manset.xml"},
    {"name": "Sözcü",     "url": "https://www.sozcu.com.tr/feeds-son-dakika"},
    {"name": "Hürriyet",  "url": "https://www.hurriyet.com.tr/rss/anasayfa"},
    {"name": "Ekonomim",  "url": "https://www.ekonomim.com/rss"},
    {"name": "NTV Spor",  "url": "https://www.ntvspor.net/rss/anasayfa"},
    {"name": "Son Dakika","url": "https://rss.sondakika.com/rss_standart.asp"}
]

def get_todays_news():
    today_news_list = []
    tr_tz = timezone(timedelta(hours=3))
    now_tr = datetime.now(tr_tz)
    today_start = now_tr - timedelta(hours=24)

    print(f"🔎 {now_tr.strftime('%d.%m.%Y %H:%M')} itibarıyla son 24 saatin haberleri taranıyor...")

    for source in SOURCES:
        try:
            feed = feedparser.parse(source['url'])
            for entry in feed.entries:
                pub_date = date_parser.parse(entry.get('published', entry.get('pubDate', '')))
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=timezone.utc)

                if pub_date >= today_start:
                    today_news_list.append({"source": source['name'], "title": entry.title})
        except Exception as e:
            print(f"❌ {source['name']} okunurken hata: {e}")

    return today_news_list

def generate_ai_summary(news_data):
    news_text = "\n".join([f"- [{n['source']}] {n['title']}" for n in news_data])
    prompt = f"""
    Sen Gezo Gündem uygulamasının baş editörüsün. Türkiye'nin en büyük 6 kaynağından son 24 saatin haberleri:
    {news_text}
    GÖREVİN: BUGÜNÜN gündemine damga vuran en hayati 6 maddeyi seç. Yanıtı SADECE aşağıdaki JSON formatında ver:
    {{
        "push_title": "📅 Günün Özeti: Neler Oldu?",
        "push_body": "Kısa özet...",
        "detailed_summary": [
            {{"title": "Madde 1 Kısa Başlık", "desc": "Detay"}},
            {{"title": "Madde 2 Kısa Başlık", "desc": "Detay"}}
        ],
        "sources_used": "Kaynak 1 • Kaynak 2"
    }}
    """

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=genai.types.GenerateContentConfig(response_mime_type="application/json")
    )
    return json.loads(response.text)

def save_to_cdn(summary_data, scanned_count):
    output_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'cdn_data', 'summaries'
    )
    os.makedirs(output_dir, exist_ok=True)
    
    tr_tz = timezone(timedelta(hours=3))
    now_tr = datetime.now(tr_tz)
    yesterday_tr = now_tr - timedelta(hours=24)
    doc_id = now_tr.strftime("%Y-%m-%d_%H-%M")

    cdn_payload = {
        "date": doc_id,
        "generated_at": now_tr.isoformat(timespec='seconds'),
        "range": {
            "start": yesterday_tr.isoformat(timespec='seconds'),
            "end": now_tr.isoformat(timespec='seconds')
        },
        "scanned_count": scanned_count,
        "items": summary_data['detailed_summary'],
        "sources": summary_data['sources_used']
    }

    # Doğrudan hourly_latest.json olarak kaydediyoruz (İşi çok basitleştiriyor)
    latest_path = os.path.join(output_dir, "hourly_latest.json")
    with open(latest_path, 'w', encoding='utf-8') as f:
        json.dump(cdn_payload, f, ensure_ascii=False, separators=(',', ':'))

    print(f"📦 CDN dosyası güncellendi: {latest_path}")

if __name__ == "__main__":
    raw_news = get_todays_news()

    if len(raw_news) <= 5:
        print("⚠️ Yeterli haber bulunamadı, işlem durduruldu.")
        exit(0)

    total_scanned = len(raw_news)
    last_error = None

    for deneme in range(4):
        try:
            print(f"🔄 Deneme {deneme + 1}/4...")
            summary = generate_ai_summary(raw_news)
            save_to_cdn(summary, total_scanned)
            print("✅ SAATLİK YAPAY ZEKA ÖZETİ BAŞARIYLA OLUŞTURULDU!")
            break
        except Exception as e:
            last_error = e
            print(f"❌ Deneme {deneme + 1} başarısız: {e}")
            if deneme < 3:
                time.sleep(15 * (deneme + 1))
    else:
        print(f"🚨 4 denemenin tamamı başarısız oldu! Son hata: {last_error}")
        exit(1)