import os
import json
import feedparser
import time
import tempfile
import subprocess
import socket
from datetime import datetime, timezone, timedelta
from dateutil import parser as date_parser
from google import genai
from pydantic import BaseModel, Field
from typing import Optional, List
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from google.cloud import texttospeech
import re
import imageio_ffmpeg

# 🔥 YENİ (BEST PRACTICE): Global zaman aşımı (timeout) ayarı. 
# Herhangi bir haber sitesi veya ağ isteği 15 saniyede cevap vermezse bağlantıyı zorla koparır.
socket.setdefaulttimeout(15)

# 🛡️ ANTI-BAN (ENGEL ÖNLEYİCİ) KİMLİK
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
feedparser.USER_AGENT = USER_AGENT

# Bir maddenin listede en fazla ne kadar süre kalabileceği (saat).
STALE_ITEM_MAX_AGE_HOURS = 16

SOURCES = [
    {"name": "CNN Türk",     "url": "https://www.cnnturk.com/feed/rss/all/news"},
    {"name": "Hürriyet",     "url": "https://www.hurriyet.com.tr/rss/anasayfa"},
    {"name": "Sözcü",        "url": "https://www.sozcu.com.tr/feeds-son-dakika"},
    {"name": "Sabah",        "url": "https://www.sabah.com.tr/rss/gundem.xml"},
    {"name": "Milliyet",     "url": "https://www.milliyet.com.tr/rss/rssnew/sondakikarss.xml"},
    {"name": "Habertürk",    "url": "https://www.haberturk.com/rss/manset.xml"},
    {"name": "En Son Haber", "url": "https://www.ensonhaber.com/rss/gundem.xml"},
    {"name": "Mynet",        "url": "https://www.mynet.com/haber/rss/sondakika"},
    {"name": "NTV Spor",     "url": "https://www.ntvspor.net/rss/anasayfa"},
    {"name": "Fotomaç",      "url": "https://www.fotomac.com.tr/rss/son24saat.xml"},
    {"name": "Ekonomim",     "url": "https://www.ekonomim.com/rss"}
]

# Yapay Zeka Çıktı Şemaları (Type-Safe Yapı)
class NewsItem(BaseModel):
    title: str = Field(description="Vurucu Kısa Başlık. Maksimum 6 kelime.")
    desc: str = Field(description="Detaylı açıklama. 2-3 cümle.")
    source_titles: List[str] = Field(description="Bu maddeyi oluştururken kullanılan ham RSS başlıkları.")

class SummaryResponse(BaseModel):
    has_changes: bool = Field(description="Sana verilen 'Bir Önceki Saatin Özeti' ile yeni gelen haberleri kıyasladığında, gündemi değiştirecek ÖNEMLİ YENİ BİR GELİŞME var mı?")
    detailed_summary: Optional[List[NewsItem]] = Field(default=None, description="Haber maddelerinin listesi.")
    sources_used: Optional[str] = Field(default=None, description="Kullanılan kaynaklar. Örn: 'CNN Türk • Sözcü'")


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
            # Zaman aşımı veya sunucu hatası olursa buraya düşer, işlemi durdurmaz
            print(f"❌ {source['name']} okunurken hata (Atlandı): {e}")

    return today_news_list


def get_previous_summary():
    local_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'cdn_data', 'summaries', 'hourly_latest.json'
    )
    if os.path.exists(local_path):
        try:
            with open(local_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Lokal önceki özet okunamadı: {e}")
    return None


def get_seen_links_cache():
    local_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'cdn_data', 'summaries', 'seen_links_cache.json'
    )
    if os.path.exists(local_path):
        try:
            with open(local_path, 'r', encoding='utf-8') as f:
                return json.load(f).get("seen_links", [])
        except Exception as e:
            print(f"⚠️ Lokal link cache okunamadı: {e}")
    return []


def expire_stale_items(previous_summary_data, max_age_hours=STALE_ITEM_MAX_AGE_HOURS):
    if not previous_summary_data or "items" not in previous_summary_data:
        return previous_summary_data

    now = datetime.now(timezone(timedelta(hours=3)))
    fresh_items = []
    expired_count = 0

    for item in previous_summary_data["items"]:
        first_seen = item.get("first_seen")
        if first_seen:
            try:
                seen_at = date_parser.parse(first_seen)
                age = now - seen_at
                if age > timedelta(hours=max_age_hours):
                    expired_count += 1
                    continue
            except Exception:
                pass 
        fresh_items.append(item)

    if expired_count:
        print(f"🗑️ {expired_count} bayat madde ({max_age_hours} saatten eski, güncellenmemiş) gündemden düşürüldü.")

    previous_summary_data["items"] = fresh_items
    return previous_summary_data


def track_first_seen(summary_items, previous_summary_data, now_iso):
    prev_first_seen = {}
    if previous_summary_data and "items" in previous_summary_data:
        for item in previous_summary_data["items"]:
            if item.get("title") and item.get("first_seen"):
                prev_first_seen[item["title"]] = item["first_seen"]

    for item in summary_items:
        title = item.get("title")
        item["first_seen"] = prev_first_seen.get(title, now_iso)

    return summary_items


def generate_ai_summary(new_news_data, previous_summary_data=None, use_fallback=False):
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

Aşağıda SON GÜNCELLEMEDEN BU YANA sisteme düşmüş olan SADECE YENİ HABERLER (DELTA HAVUZU) bulunuyor:
{news_text}

Aşağıda ise sistemde kayıtlı olan MEVCUT GÜNDEM bulunuyor (Bu veri, önceki çalıştırmada üretilmiş JSON'daki `detailed_summary` listesidir):
{prev_context}

GÖREVİN VE EDİTORYAL KURALLAR:
0. BOŞ DELTA KONTROLÜ:
Eğer Delta Havuzu boşsa doğrudan:
{{"has_changes": false}}
JSON'unu döndür ve işlemi bitir.

1. KESİN ÇIKTI FORMATI:
Yanıtın SADECE VE SADECE geçerli bir JSON olmalıdır.
- Markdown (```json) kullanma.
- Kod bloğu kullanma.
- Açıklama yazma.
- Ön metin yazma.
- Sadece ham JSON string'i döndür.

2. KARAR AKIŞI (ÇOK KRİTİK):
Önce Delta Havuzu ile Mevcut Gündemi karşılaştır.
Önce değişiklik gerekip gerekmediğine karar ver.
Daha sonra yalnızca uygun JSON çıktısını üret.
JSON'u üretmeden önce editoryal kararını tamamla.

3. HARMANLAMA VE HAFIZA (KRİTİK):
"MEVCUT GÜNDEM" senin ana listendir.
Listeyi sıfırdan oluşturma.
Mevcut gündemdeki `title` alanları o olayların değişmez kimliğidir (ID), sadece stilistik/kozmetik sebeplerle değiştirilmez.
FAKAT olayın DURUMU değiştiyse (örn: "devam ediyor" iken sona erdiyse, "başlayacak" iken başladıysa,
iptal edildiyse, sonuçlandıysa) title bu yeni durumu yansıtacak şekilde GÜNCELLENMELİDİR.
Örnek: "NATO Zirvesi Ankara'da Devam Ediyor" → zirve bittiyse → "NATO Zirvesi Ankara'da Sona Erdi"
Böyle bir durum güncellemesi yapıldığında `desc` de yeni sonucu yansıtacak şekilde güncellenir.
Bunun dışındaki durumlarda:
- Yeni bilgi varsa yalnızca `desc` güncellenebilir.
- Yeni bilgi yoksa `desc` değiştirilmemelidir.
- Başlıkları gereksiz yere yeniden yazma.

4. KAYNAK BAŞLIKLARI (source_titles):
`source_titles`, o olaya ait tüm geçerli RSS başlıklarının birleşimidir.
Güncelleme sırasında:
- yalnızca aynı olaya ait yeni ve gerçek RSS başlıklarını ekle,
- mevcut ilgili başlıkları koru,
- aynı başlığı ikinci kez ekleme,
- farklı olaylara ait başlıkları aynı dizide birleştirme.

5. ANLAMLI GELİŞME KRİTERİ:
Aynı olay için yalnızca farklı kaynaklardan haberler geldiyse fakat kamuoyu açısından anlamlı yeni bir gelişme yoksa `has_changes=false` döndür.
RSS akışındaki ufak kelime değişikliklerine, tekrar haberlere ve farklı kaynakların aynı olayı yeniden yayınlamasına kanma.

6. OLAY BİRLEŞTİRME (DEDUPLICATION):
Aynı olaya ait farklı RSS başlıklarını tek maddede birleştir.
Bir olay için listede yalnızca BİR madde bulunmalıdır.

7. LİSTE BOYUTU:
Bu kural yalnızca `has_changes=true` olduğunda oluşturulan `detailed_summary` için geçerlidir.
Nihai liste:
- en az 4,
- en fazla 6 maddeden oluşmalıdır.

8. KIYASLAMA VE STABİLİTE:
Yeni bir olay, mevcut listedeki en düşük öncelikli olaydan DAHA ÖNEMLİ DEĞİLSE listeye eklenmeyecektir.
Yeni olay ile mevcut listedeki en düşük öncelikli olay benzer önem seviyesindeyse mevcut liste korunmalıdır.
Kararsız kalınan tüm durumlarda mevcut gündemi koru.
Nihai listeyi önem derecesine göre sırala.
Ancak mevcut sıralamayı yalnızca önem dengesi gerçekten değişmişse değiştir.
Benzer önemdeki olayların sırası korunmalıdır.

9. KAYNAKLAR ÖZETİ (sources_used):
`sources_used` alanına yalnızca nihai listedeki haberlerde kullanılan haber kuruluşlarının benzersiz adlarını yaz.
Örnek:
AA • Reuters • BBC • TRT Haber
Aynı kaynak adını tekrar etme.

10. DEĞİŞTİRME KARARI VE ÇIKTI OPTİMİZASYONU:
`has_changes=true` yalnızca şu durumlarda döndürülmelidir:
- Listeye gerçekten daha önemli yeni bir olay girdiyse.
- Mevcut önemli bir olayda kamuoyu açısından anlamlı yeni bir gelişme oluştuysa.
- Mevcut listedeki bir olayın DURUMU değiştiyse (madde 3'teki gibi).

Bunun dışındaki tüm durumlarda:
{{"has_changes": false}}
JSON'unu döndür.
Eğer `has_changes=false` ise başka hiçbir alan döndürme.
"""

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SummaryResponse
            )
        )
        return json.loads(response.text)
    except Exception as e:
        error_str = str(e)
        if not use_fallback and ("429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "503" in error_str or "UNAVAILABLE" in error_str):
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
            prev_texts.append(f"{p_title} {p_desc}")

    for item in summary_data.get("detailed_summary", []):
        if not item: continue

        raw_titles = item.get("source_titles", [])
        item["source_titles"] = list(dict.fromkeys(raw_titles))[-10:]
        source_titles = item.get("source_titles", [])
        source_links = []

        for t in source_titles:
            clean_t = t.strip().lower()
            best_link, best_score = None, 0
            for raw_title, raw_link in title_to_link.items():
                score = fuzz.ratio(clean_t, raw_title)
                if score > best_score:
                    best_score, best_link = score, raw_link
            if best_score >= 90:
                source_links.append(best_link)

        item["source_links"] = list(set(source_links))

        is_new_layer1 = True
        if item["source_links"]:
            if any(l in prev_links for l in item["source_links"]):
                is_new_layer1 = False

        if not is_new_layer1:
            item["is_new"] = False
            continue

        c_title = item.get("title", "").strip().lower()
        c_desc = item.get("desc", "").strip().lower()
        current_text = f"{c_title} {c_desc}"

        is_new_layer2 = True
        for p_text in prev_texts:
            score = fuzz.token_set_ratio(current_text, p_text)
            if score > 75:
                is_new_layer2 = False
                break

        item["is_new"] = is_new_layer2

    return summary_data


def reencode_cbr(audio_bytes: bytes) -> bytes:
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp_in, \
         tempfile.NamedTemporaryFile(suffix=".mp3") as tmp_out:
        tmp_in.write(audio_bytes)
        tmp_in.flush()

        # 🔥 YENİ (BEST PRACTICE): FFmpeg kilitlenmesini engellemek için timeout eklendi
        subprocess.run([
            ffmpeg_path, "-y", "-i", tmp_in.name,
            "-c:a", "libmp3lame",
            "-b:a", "48k",
            "-ac", "1",    
            "-ar", "24000",
            tmp_out.name
        ], check=True, capture_output=True, timeout=30) 

        tmp_out.seek(0)
        return tmp_out.read()


def generate_tts_audio(summary_items, output_dir):
    if not summary_items:
        return

    print("🎙️ Sesli özetler (MP3) oluşturuluyor...")

    text_to_read = ""
    for item in summary_items:
        text_to_read += f"{item['title']}. {item['desc']} . [pause] "
    text_to_read += "Şimdilik gelişmeler bu kadar,gezo gündemi dinlediğiniz için teşekkürler."
    text_to_read = text_to_read.replace("'", "").replace("’", "").replace('"', '')
    text_to_read = re.sub(r'([A-Za-zÇÖĞÜŞİçöğüşı]+)-(\d+)', r'\1 \2', text_to_read)

    def fix_allcaps(match):
        word = match.group(0)
        return word.capitalize() if len(word) >= 5 else word

    text_to_read = re.sub(r'\b[A-ZÇÖĞÜŞİ]{2,}\b', fix_allcaps, text_to_read)
    try:
        client = texttospeech.TextToSpeechClient()
    except Exception as e:
        print(f"⚠️ TTS İstemcisi başlatılamadı (GCP Kimlik bilgileri eksik olabilir): {e}")
        return

    synthesis_input = texttospeech.SynthesisInput(markup=text_to_read)

    voice_profiles = {
        "summary_male.mp3": "tr-TR-Chirp3-HD-Charon",
        "summary_female.mp3": "tr-TR-Chirp3-HD-Kore"
    }

    for filename, voice_name in voice_profiles.items():
        try:
            voice = texttospeech.VoiceSelectionParams(
                language_code="tr-TR",
                name=voice_name
            )

            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=1.0
            )

            response = client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config
            )

            file_path = os.path.join(output_dir, filename)
            with open(file_path, "wb") as out:
                out.write(reencode_cbr(response.audio_content))
            print(f"✅ Ses dosyası kaydedildi: {file_path}")

        except Exception as e:
            print(f"❌ {voice_name} için ses oluşturulurken hata: {e}")


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

    cdn_payload = {
        "date": doc_id,
        "generated_at": now_tr.isoformat(timespec='seconds'),
        "range": {
            "start": yesterday_tr.isoformat(timespec='seconds'),
            "end": now_tr.isoformat(timespec='seconds')
        },
        "scanned_count": scanned_count,
        "items": summary_data.get('detailed_summary') or [],
        "sources": summary_data.get('sources_used') or ""
    }

    latest_path = os.path.join(output_dir, "hourly_latest.json")
    with open(latest_path, 'w', encoding='utf-8') as f:
        json.dump(cdn_payload, f, ensure_ascii=False, separators=(',', ':'))
    print(f"📦 Özet dosyası güncellendi: {latest_path}")

    combined_links = previous_seen_links + [n['link'] for n in all_raw_news]
    updated_seen_links = list(dict.fromkeys(combined_links))[-2000:]

    cache_path = os.path.join(output_dir, "seen_links_cache.json")
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump({"seen_links": updated_seen_links}, f, ensure_ascii=False)
    print(f"🗄️ Link cache dosyası güncellendi (Toplam Link: {len(updated_seen_links)})")

    with open(os.path.join(output_dir, ".upload_ready"), 'w') as f:
        f.write("ready")


if __name__ == "__main__":
    raw_news = get_todays_news()

    if len(raw_news) == 0:
        print("⚠️ Haber bulunamadı, işlem durduruldu.")
        exit(0)

    total_scanned = len(raw_news)

    seen_links = get_seen_links_cache()
    seen_links_set = set(seen_links)

    new_unseen_news = [n for n in raw_news if n['link'] not in seen_links_set]

    print(f"📊 Toplam Havuz: {total_scanned} | Daha Önce Görülen: {len(seen_links)} | Yepyeni (Delta): {len(new_unseen_news)}")

    if len(new_unseen_news) == 0:
        print("🛑 Gündeme düşen YENİ hiçbir haber yok. Erken çıkış yapılıyor (Sıfır LLM maliyeti).")
        exit(0)

    prev_summary = get_previous_summary()
    prev_summary = expire_stale_items(prev_summary)

    last_error = None
    tr_tz = timezone(timedelta(hours=3))

    for deneme in range(4):
        try:
            print(f"🔄 Deneme {deneme + 1}/4...")
            summary = generate_ai_summary(new_unseen_news, prev_summary)

            if summary.get("has_changes") is False:
                print("🛑 Yeni haberler var ama gündemi değiştirecek kadar önemli değil. Sadece cache güncelleniyor.")

                fallback_items = prev_summary.get("items", []) if prev_summary else []
                for item in fallback_items:
                    item["is_new"] = False

                fallback_summary_data = {
                    "detailed_summary": fallback_items,
                    "sources_used": prev_summary.get("sources", "") if prev_summary else ""
                }
                save_to_cdn(fallback_summary_data, total_scanned, raw_news, seen_links)
                print("✅ Gündem korunarak yeni linkler hafızaya (cache) alındı!")
                break

            summary = resolve_is_new_hybrid(summary, raw_news, prev_summary)

            now_iso = datetime.now(tr_tz).isoformat(timespec='seconds')
            summary["detailed_summary"] = track_first_seen(
                summary.get("detailed_summary", []), prev_summary, now_iso
            )

            output_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'cdn_data', 'summaries'
            )
            os.makedirs(output_dir, exist_ok=True)
            generate_tts_audio(summary.get("detailed_summary", []), output_dir)

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