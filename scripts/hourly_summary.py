import os
import json
import feedparser
import time
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
                    
                    # HTML temizliği: BeautifulSoup ile tüm gereksiz etiketleri uçuruyoruz
                    clean_desc = BeautifulSoup(raw_desc, "html.parser").get_text(separator=' ', strip=True)
                    # Sadece ilk 300 karakteri alıyoruz (Token tasarrufu ve net bağlam için)
                    clean_desc = clean_desc[:300]
                    
                    today_news_list.append({
                        "source": source['name'], 
                        "title": entry.title,
                        "desc": clean_desc
                    })
        except Exception as e:
            print(f"❌ {source['name']} okunurken hata: {e}")

    return today_news_list

def generate_ai_summary(news_data, use_fallback=False):
    model_name = 'gemini-3.5-flash' if use_fallback else 'gemini-2.5-flash'
    print(f"🤖 Yapay zeka modeli olarak '{model_name}' deneniyor...")
    
    news_text = "\n".join([f"- [{n['source']}] {n['title']} (Detay: {n['desc']})" for n in news_data])
    
    prompt = f"""
    Sen Gezo Gündem uygulamasının Kıdemli Genel Yayın Yönetmenisin. Amacın, yoğun insanlara günün en kritik gelişmeleri hakkında kusursuz bir "Yönetici Özeti (Executive Briefing)" sunmak.
    
    Aşağıda Türkiye'nin en büyük kaynaklarından toplanmış son 24 saatin haber havuzu (Başlık ve Detaylar) bulunuyor:
    {news_text}

    GÖREVİN VE EDİTORYAL KURALLAR:
    1. SEÇİM: Gündemi en çok etkileyen, toplumda, ekonomide veya siyasette en çok yankı uyandıran gelişmeleri seç. Mükerrer haberleri tekilleştir. Gündemin yoğunluğuna göre EN AZ 3, EN FAZLA 6 benzersiz madde çıkar. Sırf 6 maddeyi doldurmak için zorlama veya önemsiz olayları ekleme.
    2. SIRALAMA (ÇOK KRİTİK): Seçtiğin maddeleri yayınlanma saatine göre DEĞİL, Türkiye gündemindeki önem derecesine ve etki gücüne (impact) göre sırala. Gündemi sarsan en kritik olay KESİNLİKLE 1. sırada yer almalı.
    3. ÜSLUP: Objektif, net ve tık tuzağı (clickbait) içermeyen prestijli bir dil kullan. Başlıklar kısa ve vurucu (maksimum 6 kelime), detaylar ise doyurucu olmalı (5N1K kuralına uygun 2-3 cümle).
    4. KESİNLİK VE DOĞRULUK: Haber başlığında veya detayında AÇIKÇA belirtilmeyen HİÇBİR kişi, kurum, takım veya mekan adını ASLA tahmin etme. Bilgiyi sadece sana verilen metinden al.

    Yanıtı SADECE ve EKSİKSİZ aşağıdaki JSON formatında ver. JSON dışında tek bir kelime bile açıklama yazma:
    {{
        "push_title": "📅 Günün Özeti: Gündemde Neler Oluyor?",
        "push_body": "[Buraya seçtiğin o en önemli 1. haberin dikkat çekici ve merak uyandırıcı tek cümlelik özetini yaz]",
        "detailed_summary": [
            {{"title": "1. Haberin Vurucu Kısa Başlığı", "desc": "En önemli haberin detaylı, net ve doyurucu açıklaması."}}
            // (Maddeleri buraya ekle. Toplamda 3 ile 6 madde arasında olsun.)
        ],
        "sources_used": "Kaynak 1 • Kaynak 2 • Kaynak 3"
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
            print(f"⚠️ {model_name} kotası doldu! Beklemeden otomatik olarak gemini-2.5-flash modeline geçiliyor...")
            return generate_ai_summary(news_data, use_fallback=True)
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
        "items": summary_data['detailed_summary'],
        "sources": summary_data['sources_used']
    }

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