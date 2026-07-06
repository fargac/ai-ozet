import os
import json
import feedparser
import time
import requests
from datetime import datetime, timezone, timedelta
from dateutil import parser as date_parser
from google import genai
from bs4 import BeautifulSoup

# 🛡️ ANTI-BAN (ENGEL ÖNLEYİCİ) KİMLİK
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
feedparser.USER_AGENT = USER_AGENT

SOURCES = [
    {"name": "CNN Türk",     "url": "https://www.cnnturk.com/feed/rss/all/news"},
    {"name": "Hürriyet",     "url": "https://www.hurriyet.com.tr/rss/anasayfa"},
    {"name": "Sözcü",        "url": "https://www.sozcu.com.tr/feeds-son-dakika"},
    {"name": "Sabah",        "url": "https://www.sabah.com.tr/rss/gundem.xml"},
    {"name": "Milliyet",     "url": "https://www.milliyet.com.tr/rss/rssnew/sondakikarss.xml"},
    {"name": "Habertürk",    "url": "https://www.haberturk.com/rss/manset.xml"},
    {"name": "En Son Haber", "url": "https://www.ensonhaber.com/rss/gundem.xml"},
    {"name": "Mynet",        "url": "https://www.mynet.com/haber/rss/sondakika"},
    {"name": "Son Dakika",   "url": "https://rss.sondakika.com/rss_standart.asp"},
    {"name": "NTV Spor",     "url": "https://www.ntvspor.net/rss/anasayfa"},
    {"name": "Fotomaç",      "url": "https://www.fotomac.com.tr/rss/son24saat.xml"},
    {"name": "Ekonomim",     "url": "https://www.ekonomim.com/rss"}
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
                    raw_desc = entry.get('summary', entry.get('description', ''))
                    
                    # 🧹 HTML TEMİZLİĞİ: BeautifulSoup ile gereksiz etiketleri (reklam, görsel vb.) uçuruyoruz.
                    clean_desc = BeautifulSoup(raw_desc, "html.parser").get_text(separator=' ', strip=True)
                    clean_desc = clean_desc[:300] # Token tasarrufu için ilk 300 karakter yeterli
                    
                    today_news_list.append({
                        "source": source['name'], 
                        "title": entry.title,
                        "desc": clean_desc
                    })
        except Exception as e:
            print(f"❌ {source['name']} okunurken hata: {e}")

    return today_news_list

def get_previous_summary():
    """GitHub Actions sanal makinesi her defasında sıfırlandığı için, bir önceki özeti canlı CDN'den okuruz."""
    cdn_url = os.environ.get("CDN_JSON_URL")
    if not cdn_url:
        print("⚠️ CDN_JSON_URL bulunamadı. Sistem hafızasız (sıfırdan) çalışacak.")
        return None
    
    try:
        resp = requests.get(cdn_url, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"⚠️ Önceki özet çekilemedi: {e}")
    return None

def generate_ai_summary(news_data, previous_summary_data=None, use_fallback=False):
    model_name = 'gemini-3.5-flash' if use_fallback else 'gemini-2.5-flash'
    print(f"🤖 Yapay zeka modeli olarak '{model_name}' deneniyor...")
    
    news_text = "\n".join([f"- [{n['source']}] {n['title']} (Detay: {n['desc']})" for n in news_data])
    
    prev_context = ""
    if previous_summary_data and "items" in previous_summary_data:
        prev_text = json.dumps(previous_summary_data["items"], ensure_ascii=False, indent=2)
        prev_context = f"\nBİR ÖNCEKİ SAATİN ÖZETİ (HAFIZA YARDIMI):\n{prev_text}\n"

    prompt = f"""
    Sen Gezo Gündem uygulamasının Kıdemli Genel Yayın Yönetmenisin.
    
    Aşağıda Türkiye'nin en büyük kaynaklarından toplanmış YENİ HABER HAVUZU bulunuyor:
    {news_text}
    {prev_context}

    GÖREVİN VE EDİTORYAL KURALLAR:
    1. KIYASLAMA VE HAFIZA: Sana verilen 'Bir Önceki Saatin Özeti' ile 'Yeni Haber Havuzu'nu kıyasla. Gündemi sarsacak, mevcut sıralamayı değiştirecek veya listeye girmeyi hak edecek kadar ÖNEMLİ YENİ BİR GELİŞME YOKSA, JSON'da sadece {{"has_changes": false}} döndür ve işlemi bitir.
    2. YENİ GELİŞME VARSA: Eğer listeye girecek kadar önemli yeni bir gelişme varsa {{"has_changes": true}} döndür. Yeni gelişmeyi listeye ekle, yer açmak için en önemsiz eski maddeyi listeden çıkar.
    3. YENİ ETİKETİ (ÇOK KRİTİK): SADECE yeni eklediğin ve bir önceki özette ASLA olmayan o yeni maddenin içine `"is_new": true` ekle. Eski maddelerde bu alan `false` olsun.
    4. SEÇİM: Gündemin yoğunluğuna göre EN AZ 3, EN FAZLA 6 benzersiz madde çıkar. Zorlama madde üretme.
    5. SIRALAMA: Yayınlanma saatine göre DEĞİL, Türkiye gündemindeki etki gücüne (impact) göre sırala.
    6. ÜSLUP: Başlıklar maksimum 6 kelime, detaylar 2-3 cümle (5N1K kuralına uygun). Asla metinde olmayan bir şeyi uydurma.

    Yanıtı SADECE ve EKSİKSİZ aşağıdaki JSON formatında ver:
    {{
        "has_changes": true veya false,
        "detailed_summary": [
            {{
                "title": "Vurucu Kısa Başlık", 
                "desc": "Detaylı açıklama.",
                "is_new": true veya false
            }}
        ],
        "sources_used": "Kaynak 1 • Kaynak 2"
    }}
    """

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=genai.types.GenerateContentConfig(response_mime_type="application/json")
        )
        return json.loads(response.text)
    except Exception as e:
        error_str = str(e)
        if not use_fallback and ("429" in error_str or "RESOURCE_EXHAUSTED" in error_str):
            print(f"⚠️ {model_name} kotası doldu! gemini-2.5-flash modeline geçiliyor...")
            return generate_ai_summary(news_data, previous_summary_data, use_fallback=True)
        raise e

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
        "items": summary_data.get('detailed_summary', []),
        "sources": summary_data.get('sources_used', '')
    }

    latest_path = os.path.join(output_dir, "hourly_latest.json")
    with open(latest_path, 'w', encoding='utf-8') as f:
        json.dump(cdn_payload, f, ensure_ascii=False, separators=(',', ':'))

    print(f"📦 CDN dosyası güncellendi: {latest_path}")
    
    # Değişiklik olduğunu GitHub Action'a bildirmek için ufak bir bayrak (flag) dosyası oluşturuyoruz
    with open(os.path.join(output_dir, ".upload_ready"), 'w') as f:
        f.write("ready")

if __name__ == "__main__":
    raw_news = get_todays_news()

    if len(raw_news) <= 5:
        print("⚠️ Yeterli haber bulunamadı, işlem durduruldu.")
        exit(0)

    total_scanned = len(raw_news)
    
    # 1. Önceki özeti CDN'den çek
    prev_summary = get_previous_summary()
    last_error = None

    for deneme in range(4):
        try:
            print(f"🔄 Deneme {deneme + 1}/4...")
            summary = generate_ai_summary(raw_news, prev_summary)
            
            # 2. Değişiklik yoksa R2/CDN'e boşuna yazma, React Native tarafını gereksiz tetikleme
            if summary.get("has_changes") is False:
                print("🛑 Gündemde önemli bir değişiklik yok. Yeni json üretilmeyecek ve R2'ye yükleme yapılmayacak.")
                exit(0)
                
            save_to_cdn(summary, total_scanned)
            print("✅ SAATLİK YAPAY ZEKA ÖZETİ (YENİ MADDELERLE) BAŞARIYLA OLUŞTURULDU!")
            break
        except Exception as e:
            last_error = e
            print(f"❌ Deneme {deneme + 1} başarısız: {e}")
            if deneme < 3:
                time.sleep(15 * (deneme + 1))
    else:
        print(f"🚨 4 denemenin tamamı başarısız oldu! Son hata: {last_error}")
        exit(1)