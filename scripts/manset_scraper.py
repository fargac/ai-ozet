import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import concurrent.futures

GAZETELER = [
    {"id": "manset_aksam", "name": "Akşam", "slug": "aksam", "link": "https://www.aksam.com.tr"},
    {"id": "manset_aydinlik", "name": "Aydınlık", "slug": "aydinlik", "link": "https://www.aydinlik.com.tr"},
    {"id": "manset_dirilis_postasi", "name": "Diriliş P.", "slug": "dirilis-postasi", "link": "https://www.dirilispostasi.com"},
    {"id": "manset_dogru-haber", "name": "Doğru Haber", "slug": "dogru-haber", "link": "https://dogruhaber.com.tr"},
    {"id": "manset_dunya", "name": "Dünya", "slug": "dunya", "link": "https://www.dunya.com"},
    {"id": "manset_hurriyet", "name": "Hürriyet", "slug": "hurriyet", "link": "https://www.hurriyet.com.tr"},
    {"id": "manset_milat", "name": "Milat", "slug": "milat", "link": "https://www.milatgazetesi.com"},
    {"id": "manset_milli_gazete", "name": "Milli Gazete", "slug": "milli-gazete", "link": "https://www.milligazete.com.tr"},
    {"id": "manset_milliyet", "name": "Milliyet", "slug": "milliyet", "link": "https://www.milliyet.com.tr"},
    {"id": "manset_sabah", "name": "Sabah", "slug": "sabah", "link": "https://www.sabah.com.tr"},
    {"id": "manset_takvim", "name": "Takvim", "slug": "takvim-gazetesi", "link": "https://www.takvim.com.tr"},
    {"id": "manset_turkgun", "name": "Türkgün", "slug": "turkgun", "link": "https://www.turkgun.com"},
    {"id": "manset_turkiye", "name": "Türkiye", "slug": "turkiye", "link": "https://www.turkiyegazetesi.com.tr"},
    {"id": "manset_yeni_akit", "name": "Yeni Akit", "slug": "yeni-akit", "link": "https://www.yeniakit.com.tr"},
    {"id": "manset_yeni_birlik", "name": "Yeni Birlik", "slug": "yeni-birlik", "link": "https://www.gazetebirlik.com"},
    {"id": "manset_yeni_cag", "name": "Yeniçağ", "slug": "yenicag", "link": "https://www.yenicaggazetesi.com.tr"},
    {"id": "manset_yeni_safak", "name": "Yeni Şafak", "slug": "yeni-safak", "link": "https://www.yenisafak.com"},
    {"id": "manset_yeni_soz", "name": "Yenisöz", "slug": "yenisoz", "link": "https://www.yenisoz.com.tr"},
    {"id": "manset_fotomac", "name": "Fotomaç", "slug": "fotomac", "link": "https://www.fotomac.com.tr"},
    {"id": "manset_fanatik", "name": "Fanatik", "slug": "fanatik", "link": "https://www.fanatik.com.tr"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def tek_gazete_cek(gazete):
    url = f"https://www.haber7.com/gazete-mansetleri/{gazete['slug']}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, "html.parser")
        img_tag = soup.find("img", class_="big")
        
        if img_tag and img_tag.get("src"):
            big_url = img_tag["src"]
            
            # HD linkten Thumbnail linki üretiliyor. Zaman damgası %100 aynı olacak.
            small_url = big_url.replace("/big_", "/small_").replace("?v=", "?")
            
            print(f"✅ Başarılı: {gazete['name']}")
            return {
                "id": gazete["id"],
                "name": gazete["name"],
                "todayUrl": big_url,      # HD (Galeri için)
                "thumbUrl": small_url,    # Thumbnail (Grid liste için - HAFİF)
                "webAdresi": gazete["link"]
            }
        else:
            print(f"⚠️ Görsel Bulunamadı: {gazete['name']}")
            return None
    except Exception as e:
        print(f"❌ Hata ({gazete['name']}): {e}")
        return None

def fetch_mansetler_paralel():
    sonuclar = []
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Manşetler PARALEL olarak çekiliyor...\n")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        # Eski thumb_map mantığı kaldırıldı, doğrudan gazeteler işleniyor.
        sonuclar_iterator = executor.map(tek_gazete_cek, GAZETELER)
        
        for data in sonuclar_iterator:
            if data is not None:
                sonuclar.append(data)

    # JSON dosyasına kaydet
    with open("mansetler.json", "w", encoding="utf-8") as f:
        json.dump(sonuclar, f, ensure_ascii=False, indent=4)
        
    print(f"\n🚀 İşlem tamam! Toplam {len(sonuclar)} gazete başarıyla kaydedildi.")

if __name__ == "__main__":
    fetch_mansetler_paralel()