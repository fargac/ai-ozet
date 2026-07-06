import os
import json
import feedparser
import time
import requests
import difflib
from datetime import datetime, timezone, timedelta  # Eski çalışan yönteme geri dönüldü
from dateutil import parser as date_parser
from google import genai
from pydantic import BaseModel, Field  # Tip güvenliği ve kesin şema için
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

# Yapay Zeka Çıktı Şemaları (Type-Safe Yapı)
class NewsItem(BaseModel):
    title: str = Field(description="Vurucu Kısa Başlık. Maksimum 6 kelime.")
    desc: str = Field(description="Detaylı açıklama. 2-3 cümle.")
    source_titles: list[str] = Field(description="Bu maddeyi oluştururken kullanılan ham RSS başlıkları.")

class SummaryResponse(BaseModel):
    has_changes: bool = Field(description="Sana verilen 'Bir Önceki Saatin Özeti' ile yeni gelen haberleri kıyasladığında, gündemi değiştirecek ÖNEMLİ YENİ BİR GELİŞME var mı?")
    detailed_summary: list[NewsItem] = Field(default=[], description="Haber maddelerinin listesi.")
    sources_used: str = Field(default="", description="Kullanılan kaynaklar. Örn: 'CNN Türk • Sözcü'")

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
                    clean_desc = BeautifulSoup(raw_desc, "html.parser").get_text(separator=' ', strip=True)
                    clean_desc = clean_desc[:300]
                    link = entry.get('link', entry.get('id', '')).strip()

                    today_news_list.append({
                        "source": source['name'], 
                        "title": entry.title,
                        "desc": clean_desc,
                        "link": link
                    })
        except Exception as e:
            print(f"❌ {source['name']} okunurken hata: {e}")

    return today_news_list

def get_previous_summary():
    cdn_url = os.environ.get("CDN_JSON_URL")
    if not cdn_url:
        return None
    try:
        resp = requests.get(cdn_url, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"⚠️ Önceki özet çekilemedi: {e}")
    return None

def get_seen_links_cache():
    """R2'den (CDN) daha önce işlenmiş linklerin listesini çeker."""
    cdn_url = os.environ.get("CDN_CACHE_URL")
    if not cdn_url:
        print("⚠️ CDN_CACHE_URL bulunamadı. Cache boş varsayılıyor.")
        return []
    try:
        resp = requests.get(cdn_url, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("seen_links", [])
    except Exception as e:
        print(f"⚠️ Link cache çekilemedi: {e}")
    return []

def generate_ai_summary(new_news_data, previous_summary_data=None, use_fallback=False):
    # Ana model ve Fallback model rolleri yer değiştirildi
    main_model = 'gemini-3.5-flash'
    fallback_model = 'gemini-2.5-flash'
    model_name = fallback_model if use_fallback else main_model
    
    print(f"🤖 Yapay zeka modeli '{model_name}' ile {len(new_news_data)} yeni haber değerlendiriliyor...")
    
    news_text = "\n".join([f"- [{n['source']}] {n['title']} (Detay: {n['desc']})" for n in new_news_data])
    
    prev_context = ""
    if previous_summary_data and "items" in previous_summary_data:
        prev_text = json.dumps(previous_summary_data["items"], ensure_ascii=False, indent=2)
        prev_context = f"\nBİR ÖNCEKİ SAATİN ÖZETİ (MEVCUT GÜNDEM):\n{prev_text}\n"

    prompt = f"""
    Sen Gezo Gündem uygulamasının Kıdemli Genel Yayın Yönetmenisin.
    
    Aşağıda sisteme son 1 saatte düşmüş SADECE YENİ HABERLER (DELTA HAVUZU) bulunuyor:
    {news_text}
    {prev_context}

    GÖREVİN VE EDİTORYAL KURALLAR:
    1. KIYASLAMA VE HAFIZA: Sana verilen 'Bir Önceki Saatin Özeti' (mevcut gündem) ile sisteme yeni düşen 'Delta Havuzu'nu kıyasla. Eğer bu yeni haberler içinde, listeye girmeyi hak edecek veya gündemi değiştirecek ÖNEMLİ YENİ BİR GELİŞME YOKSA, JSON'da has_changes alanını false yap.
    2. YENİ GELİŞME VARSA: Eğer önemli yeni bir gelişme varsa has_changes alanını true yap. Yeni gelişmeyi listeye ekle, yer açmak için en önemsiz eski maddeyi listeden çıkar.
    3. source_titles ALANI (KRİTİK): Seçtiğin her madde için, o maddeyi oluşturmakta kullandığın ham haber başlıklarını BİREBİR kopyalayarak `source_titles` dizisine yaz.
    4. SEÇİM: Gündemin yoğunluğuna göre EN AZ 3, EN FAZLA 6 benzersiz madde çıkar. 
    5. SIRALAMA: Türkiye gündemindeki etki gücüne (impact) göre sırala.
    6. ÜSLUP: Başlıklar maksimum 6 kelime, detaylar 2-3 cümle. 
    """

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SummaryResponse  # Kesin çıktı formatı şeması
            )
        )
        return json.loads(response.text)
    except Exception as e:
        error_str = str(e)
        if not use_fallback and ("429" in error_str or "RESOURCE_EXHAUSTED" in error_str):
            print(f"⚠️ {model_name} kotası doldu! {fallback_model} modeline geçiliyor...")
            return generate_ai_summary(new_news_data, previous_summary_data, use_fallback=True)
        raise e

def resolve_is_new_hybrid(summary_data, raw_news, previous_summary_data):
    title_to_link = {}
    for n in raw_news:
        norm_title = n['title'].strip().lower()
        if n.get('link') and norm_title not in title_to_link:
            title_to_link[norm_title] = n['link']

    prev_links = set()
    prev_texts = []
    
    if previous_summary_data and "items" in previous_summary_data:
        for item in previous_summary_data["items"]:
            for l in item.get("source_links", []):
                if l: prev_links.add(l)
            
            p_title = item.get("title", "").strip().lower()
            p_desc = item.get("desc", "").strip().lower()
            prev_texts.append(f"{p_title} {p_title} {p_desc}")

    for item in summary_data.get("detailed_summary", []):
        source_titles = item.get("source_titles", [])
        source_links = []
        
        for t in source_titles:
            clean_t = t.strip().lower()
            for raw_title, raw_link in title_to_link.items():
                if clean_t in raw_title or raw_title in clean_t:
                    source_links.append(raw_link)
                    break 

        item["source_links"] = list(set(source_links))

        # KATMAN 1: LİNK KONTROLÜ
        is_new_layer1 = True
        if item["source_links"]:
            if any(l in prev_links for l in item["source_links"]):
                is_new_layer1 = False 

        if not is_new_layer1:
            item["is_new"] = False
            continue 

        # KATMAN 2: METİN BENZERLİĞİ (FALLBACK)
        c_title = item.get("title", "").strip().lower()
        c_desc = item.get("desc", "").strip().lower()
        current_text = f"{c_title} {c_title} {c_desc}" 

        is_new_layer2 = True
        for p_text in prev_texts:
            similarity = difflib.SequenceMatcher(None, current_text, p_text).ratio()
            if similarity > 0.60: 
                is_new_layer2 = False
                break 

        item["is_new"] = is_new_layer2

    return summary_data

def save_to_cdn(summary_data, scanned_count, all_raw_news, previous_seen_links):
    output_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'cdn_data', 'summaries'
    )
    os.makedirs(output_dir, exist_ok=True)
    
    tr_tz = timezone(timedelta(hours=3))
    now_tr = datetime.now(tr_tz)
    yesterday_tr = now_tr - timedelta(hours=24)
    doc_id = now_tr.strftime("%Y-%m-%d_%H-%M")

    # 1. ÖZET JSON KAYDI
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
    print(f"📦 Özet dosyası güncellendi: {latest_path}")

    # 2. LİNK HAFIZASI (CACHE) KAYDI
    updated_seen_links = list(set(previous_seen_links).union(set([n['link'] for n in all_raw_news])))
    updated_seen_links = updated_seen_links[-2000:]
    
    cache_path = os.path.join(output_dir, "seen_links_cache.json")
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump({"seen_links": updated_seen_links}, f, ensure_ascii=False)
    print(f"🗄️ Link cache dosyası güncellendi (Toplam Link: {len(updated_seen_links)})")
    
    # Github action'ı tetiklemek için flag
    with open(os.path.join(output_dir, ".upload_ready"), 'w') as f:
        f.write("ready")

if __name__ == "__main__":
    raw_news = get_todays_news()

    if len(raw_news) == 0:
        print("⚠️ Haber bulunamadı, işlem durduruldu.")
        exit(0)

    total_scanned = len(raw_news)
    
    # ERKEN ÇIKIŞ (DELTA) FİLTRESİ
    seen_links = get_seen_links_cache()
    seen_links_set = set(seen_links)
    
    new_unseen_news = [n for n in raw_news if n['link'] not in seen_links_set]
    
    print(f"📊 Toplam Havuz: {total_scanned} | Daha Önce Görülen: {len(seen_links)} | Yepyeni (Delta): {len(new_unseen_news)}")

    if len(new_unseen_news) == 0:
        print("🛑 Gündeme düşen YENİ hiçbir haber yok. Erken çıkış yapılıyor (Sıfır LLM maliyeti).")
        exit(0)

    prev_summary = get_previous_summary()
    last_error = None

    for deneme in range(4):
        try:
            print(f"🔄 Deneme {deneme + 1}/4...")
            summary = generate_ai_summary(new_unseen_news, prev_summary)
            
            if summary.get("has_changes") is False:
                print("🛑 Yeni haberler var ama gündemi değiştirecek kadar önemli değil. Sadece cache güncelleniyor.")
                
                # 🛡️ SIZINTI ÖNLEME ADIMI: Gündem değişmediyse mevcut özeti aynen koruyoruz.
                fallback_summary_data = {
                    "detailed_summary": prev_summary.get("items", []) if prev_summary else [],
                    "sources_used": prev_summary.get("sources", "") if prev_summary else ""
                }
                save_to_cdn(fallback_summary_data, total_scanned, raw_news, seen_links)
                print("✅ Gündem korunarak yeni linkler hafızaya (cache) alındı!")
                break

            # Önemli yeni gelişme varsa hibrit katman doğrulaması başlar
            summary = resolve_is_new_hybrid(summary, raw_news, prev_summary)

            save_to_cdn(summary, total_scanned, raw_news, seen_links)
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