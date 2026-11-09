# doc-meta-strip

**PDF, DOCX, XLSX, JPEG, PNG ve WebP dosyalarındaki GPS konumunu, yazar adını,
şirket bilgisini, düzenleme geçmişini ve dahili dosya yollarını; hiçbir yere veri
göndermeden ve tek bir pikseli yeniden kodlamadan yerel olarak silen komut
satırı aracı.**

[![CI](https://github.com/YusufBeratZengin/doc-meta-strip/actions/workflows/ci.yml/badge.svg)](https://github.com/YusufBeratZengin/doc-meta-strip/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Lisans: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## Sorun ne?

Paylaştığınız her dosya bir gölge taşır: fotoğrafı çeken makine (ve nerede
durduğu), belgeyi yazan hesabın adı, dizüstü bilgisayarın hangi şirkete ait
olduğu, dosyanın kaydedildiği `C:\Users\...` yolu, "silinmiş" ama kabul
edilmemiş revizyonlar ve yorumlar.

Bir PDF'i yayımlamak, ekran görüntüsünü herkese açık bir depoya göndermek veya
sözleşmeyi başka bir şirkete iletmek çoğu zaman bunların hepsini sızdırır.
`doc-meta-strip` tek komutla hepsini temizler, bunu çevrimdışı yapar ve neyi
çıkardığını size tek tek söyler.

## Kurulum

```bash
pip install doc-meta-strip
```

Python 3.10 veya üzeri. Sistem paketi yok, C derleyicisi yok, Office kurulumu
yok, Poppler yok, ExifTool yok.

## 30 saniyelik örnek

```bash
$ doc-meta-strip scan sunum.docx
⚠️  sunum.docx — 17 sensitive metadata item(s) (office)
      • Descriptive metadata [core:title]: Fake Internal Title
      • Author / identity [core:creator]: Fake Author
      • Author / identity [core:lastModifiedBy]: Fake Author
      • Editing history [core:revision]: 7
      • Software fingerprint [app:Application]: Fake Office 16.0
      • Organisation [app:Company]: Example Corp
      • Author / identity [app:Manager]: Fake Author
      • Internal file path [app:HyperlinkBase]: C:\Users\fake\Documents\internal
      • Internal file path [custom:ClientPath]: C:\Users\fake\Documents\internal
      • Author / identity [custom:Reviewer]: Fake Author
      • Embedded thumbnail [office:thumbnail]: embedded preview image docProps/thumbnail.jpeg
      • Comment [office:word/comments.xml]: comments part (docx)
      • Author / identity [office:comment_author]: Fake Author
      • Comment [office:comment_text]: internal comment text
      • Editing history [word:tracked-deletion]: deleted secret
      • Comment [word:comment-anchor]: 2 comment reference(s)
      • Editing history [word:rsid]: 1 revision-save id block(s)

1 file(s) scanned, 1 with metadata, 17 item(s) found

$ doc-meta-strip clean sunum.docx --in-place
✅ sunum.docx: 17 item(s) removed (4309 → 2577 bytes, in place)
      • Descriptive metadata [core:title]: Fake Internal Title
      • Author / identity [core:creator]: Fake Author
      • Author / identity [core:lastModifiedBy]: Fake Author
      • Editing history [core:revision]: 7
      • Software fingerprint [app:Application]: Fake Office 16.0
      • Organisation [app:Company]: Example Corp
      • Author / identity [app:Manager]: Fake Author
      • Internal file path [app:HyperlinkBase]: C:\Users\fake\Documents\internal
      • Internal file path [custom:ClientPath]: C:\Users\fake\Documents\internal
      • Author / identity [custom:Reviewer]: Fake Author
      • Embedded thumbnail [office:thumbnail]: embedded preview image docProps/thumbnail.jpeg
      • Comment [office:word/comments.xml]: comments part (docx)
      • Author / identity [office:comment_author]: Fake Author
      • Comment [office:comment_text]: internal comment text
      • Editing history [word:tracked-deletion]: deleted secret
      • Comment [word:comment-anchor]: 2 comment reference(s)
      • Editing history [word:rsid]: 1 revision-save id block(s)

1 file(s), 1 cleaned, 0 already clean, 17 item(s) removed

$ doc-meta-strip scan sunum.docx
✔ sunum.docx: clean (office)

1 file(s) scanned, 0 with metadata, 0 item(s) found
```

(Yukarıdaki çıktı depodaki test örneğinden alınmıştır: isim, şirket ve yol
kasten uydurmadır. Durum ve özet mesajları varsayılan olarak İngilizcedir;
`--lang tr` veya `DOC_META_STRIP_LANG=tr` ile Türkçeleşir. JSON rapor
anahtarları İngilizce kalır.)

## Neleri siler?

| Biçim | Silinen | Korunan |
| --- | --- | --- |
| **JPEG** | EXIF (makine, seri numarası, sahibi, GPS), XMP, IPTC/APP13, APP12, yorumlar, diğer tüm APPn blokları | JFIF (APP0), ICC renk profili (APP2), Adobe renk dönüşümü (APP14), tüm sıkıştırılmış görüntü baytları |
| **PNG** | `tEXt`, `zTXt`, `iTXt` (Stable Diffusion / Midjourney istemleri burada durur), `eXIf`, `tIME`, `dSIG` | `IHDR`, `PLTE`, `IDAT`, `iCCP`, `gAMA`, `pHYs`, … |
| **WebP** | `EXIF` ve `XMP ` parçaları ve `VP8X` içindeki ilgili özellik bayrakları | `VP8`/`VP8L`/`ALPH`/`ANIM` verisi |
| **DOCX** | `docProps/core.xml`, `app.xml` (şirket, yönetici, `HyperlinkBase`), `custom.xml`, küçük resimler, `word/comments*.xml`, `word/people.xml`, izlenen silmeler (`w:del`), yorumlar, `w:rsid*` kayıt kimlikleri, revizyon öznitelikleri | belge gövdesi, stiller, numaralandırma, üstbilgiler, eklenen (`w:ins`) metin, görsellerin piksel verisi (gömülü görselin üst verisi silinir) |
| **XLSX** | aynı `docProps` parçaları, hücre yorumları, iş parçacıklı yorumlar, kişi listesi, eski yorum çizimleri | hücre değerleri, formüller, stiller, grafikler |
| **PPTX** | `docProps` parçaları, küçük resimler, yorum parçaları | slaytlar, notlar, medyanın piksel verisi (gömülü görselin üst verisi silinir) |
| **PDF** | Info sözlüğü (`Author`, `Producer`, `Creator`, `Title`…), XMP paketi, `/EmbeddedFiles`, `/JavaScript` ad ağacı, sayfa küçük resimleri, dosya eki/film/ses açıklamaları, `/PieceInfo` | metin katmanı, yazı tipleri, görseller, form alanları, yer imleri, sayfa yapısı |

DOCX/XLSX/PPTX içindeki görseller paketin neresinde olursa olsun temizlenir: JPEG, PNG, WebP; ayrıca GIF (yorum ve XMP/uygulama uzantıları), SVG (`title`, `desc`, `metadata`, yorumlar, editör ad alanları) ve EMF (başlık açıklaması). WMF serbest üst veri taşımaz. Başka biçimdeki görsel (ör. TIFF) incelenemez ve `office:media-uninspected` olarak raporlanır; `scan --strict` dosyaya "temiz" demek yerine sıfırdan farklı döner.

Oluşturma ve değiştirme tarihleri varsayılan olarak korunur; belge denetimi için
genelde gerekirler. Tamamen silmek için `--strip-dates` kullanın.

## Kullanım

```
doc-meta-strip scan  <yol>...            # yalnızca raporlar, dosyaya dokunmaz
doc-meta-strip clean <yol>... -o CIKTI   # tek dosya -> CIKTI
doc-meta-strip clean <yol>... --in-place # üzerine yazar (--backup ile .bak)
doc-meta-strip clean --staged            # git indeksindekileri temizler
```

Her iki alt komutta geçerli seçenekler:

| Seçenek | Anlamı |
| --- | --- |
| `--json` | JSON denetim raporunu stdout'a yazar |
| `--report DOSYA` | JSON denetim raporunu `DOSYA`ya yazar (`-` ile stdout) |
| `--strict` | hassas üstveri bulunduğunda (`scan`) veya silindiğinde (`clean`) 3 koduyla çıkar (CI kapısı) |
| `--no-recursive` | alt klasörlere inmez |
| `--strip-dates` | tarihleri de siler |
| `--password METIN` | parola korumalı PDF parolası (`-` ile standart girdiden tek satır okunur; `--ask-password` tercih edilir) |
| `--ask-password` | PDF parolasını sorar; parola kabuk geçmişine ve süreç listesine yazılmaz |
| `--lang {en,tr}` | durum ve özet mesajlarının dili (varsayılan: `en` veya `DOC_META_STRIP_LANG`) |
| `--keep-comments` | Office yorumlarını korur |
| `--keep-embedded` | gömülü dosyaları, ekleri ve JavaScript'i korur |
| `--keep-thumbnails` | küçük resimleri korur |
| `--quiet` / `-q` | hata dışında hiçbir şey yazmaz |

Çıkış kodları: `0` başarılı, `1` bir dosya işlenemedi, `3` `--strict` üstveri
buldu veya temizledi, `2` hatalı komut satırı.

Parola `DOC_META_STRIP_PASSWORD`, arayüz dili `DOC_META_STRIP_LANG` ortam
değişkenlerinden de okunabilir. Hiçbiri denetim raporuna veya bir günlüğe
yazılmaz. `--lang tr` insan tarafından okunan durum ve özet satırlarını
Türkçeleştirir; JSON rapor anahtarları tasarım gereği İngilizce kalır.

## JSON denetim raporu

```bash
doc-meta-strip clean ./belgeler/ --in-place --report denetim.json
```

```json
{
  "tool": "doc-meta-strip",
  "version": "0.1.0",
  "mode": "clean",
  "summary": {"files": 1, "cleaned": 1, "already_clean": 0, "errors": 0, "items_removed": 2},
  "files": [
    {
      "path": "belgeler/foto.jpg",
      "format": "image",
      "status": "cleaned",
      "bytes_before": 1318,
      "bytes_after": 1052,
      "removed": [
        {"key": "exif:GPS", "value": "41.008200, 28.978400", "category": "gps", "label": "GPS location"},
        {"key": "exif:Artist", "value": "Fake Author", "category": "identity", "label": "Author / identity"}
      ],
      "warnings": [],
      "error": null
    }
  ]
}
```

Rapor, OSINT savunmasının kanıtıdır: hangi kişisel verinin hangi dosyadan
çıkarıldığını kaydeder, böylece temizlik sonradan denetlenebilir.

## Pre-commit entegrasyonu

Depo `.pre-commit-hooks.yaml` ile gelir; projeye eklemeniz gereken tek şey:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/YusufBeratZengin/doc-meta-strip
    rev: v0.1.0
    hooks:
      - id: doc-meta-strip
```

Kanca `doc-meta-strip clean --staged` çalıştırır: git indeksindeki dosyaları
temizler ve yeniden `git add` yapar. Çalışma ağacındaki dosyada henüz indekse
eklenmemiş (unstaged) değişiklikler varsa `--staged` yalnızca git indeksindeki
blob'u temizleyip günceller (`git hash-object` + `git update-index`) ve çalışma
kopyasına dokunmaz; böylece kaydedilmemiş değişiklikler ne ezilir ne de
yanlışlıkla commit'e dahil edilir. Böylece GPS konumu taşıyan bir fotoğraf
commit edilemez. Aynı davranışı elle de kullanabilirsiniz:
`doc-meta-strip clean --staged`.

## Neden kayıpsız?

* **Görseller** hiç çözülmez. JPEG işaretçi blokları, PNG parçaları ve WebP RIFF
  parçaları bayt bayt kopyalanır, yalnızca üstveri taşıyan kutular atılır. Test
  paketi öncesi/sonrası piksel karşılaştırması yapar.
* **Office dosyaları** XML parçalarından oluşan ZIP arşivleridir. Belge gövdesine
  dokunulmaz; üstveri parçaları silinir, paket ilişkileri ve
  `[Content_Types].xml` kayıtları temizlenir ki Word/Excel dosyayı "onarım"
  uyarısı vermeden açsın.
* **PDF** dosyaları `pypdf` ile yeniden yazılır, içerik akışları aynen kopyalanır.
  Ardından erişilebilirlik taraması yapılıp artık referans verilmeyen tüm nesneler
  boşaltılır; böylece silinen XMP paketinin sahipsiz bir kopyası dosya baytlarında
  kalmaz (test, çıktının ham baytlarında sızan dizeleri arar).

## Bağımlılıklar

İki çalışma zamanı bağımlılığı; ikisi de izin verici lisanslı ve tekerlek (wheel)
olarak hazır:

| Paket | Lisans | Neden burada |
| --- | --- | --- |
| [`pypdf`](https://pypi.org/project/pypdf/) | BSD-3-Clause | PDF nesne grafiğini yeniden yazmak (Info sözlüğü, XMP referansı, ad ağaçları, açıklamalar) elle yapılacak iş değil; pypdf içerik akışlarını aynen kopyalar ve sonrasında erişilemeyen nesneleri temizlememize izin verir. |
| [`Pillow`](https://pypi.org/project/Pillow/) | MIT-CMU | **Yalnızca okuma**; `scan` "bir APP1 bloğu" yerine `Fake Author` yazabilsin diye EXIF etiketlerini çözer. Silme işlemi Pillow'dan geçmez, piksellerin bit düzeyinde aynı kalmasının nedeni budur. |

Geri kalan her şey — ZIP, XML, JPEG işaretçileri, PNG parçaları, WebP RIFF ve
komut satırı — standart kütüphanedir. Kurulacak `exiftool`, `poppler`, `cairo`
veya `libmagic` yok; Windows ve `pre-commit` kurulumlarını sıkıcı kılan da budur.

## Neden `mat2` veya `exiftool` değil?

`mat2` ve `exiftool` çok iyidir ve egzotik biçimlerde çok daha kapsamlıdır.
`doc-meta-strip` başka bir iş akışını hedefler:

* **Saf Python.** `mat2` Poppler, Cairo ve GdkPixbuf ister; `exiftool` büyük bir
  Perl dağıtımıdır. Bu paket Windows, macOS ve Linux CI imajlarında sistem paketi
  olmadan `pip` ile kurulur — onu pre-commit kancası olarak kullanılabilir kılan
  şey budur.
* **`--staged` yerleşik.** Git indeksini temizler ve tek adımda yeniden ekler.
* **Sessizlik değil denetim izi.** `--report` dosya başına neyin çıkarıldığını
  JSON olarak yazar; `--strict` "üstveri bulundu" durumunu sıfırdan farklı çıkış
  koduna çevirir.
* **Revizyonlar ve yorumlar.** DOCX/XLSX içinde kabul edilmemiş `w:del`
  revizyonları ve yorumlar da silinir; yalnızca `docProps` değil.

## Sınırlar (lütfen okuyun)

* Yalnızca yapısal üstveri temizlenir. Metin veya piksel OCR ile taranmaz,
  karartılmaz — paragrafın ortasına yazılmış bir isim orada kalır.
* Parola korumalı PDF'ler `--password`, `--password -` (standart girdi) veya
  `--ask-password` ile açılmalıdır; temizlenen kopya **şifresiz** yazılır.
  `--ask-password` veya `DOC_META_STRIP_PASSWORD` ortam değişkeni tercih edin:
  komut satırına yazılan parola kabuk geçmişinde ve komut çalışırken süreç
  listesinde görünür. Parola hiçbir yerde saklanmaz, kaydedilmez ve denetim
  raporuna yazılmaz.
* Dijital imzalar korunmaz. İmzalı bir PDF veya Office belgesini temizlemek
  imzayı geçersiz kılar; bu tasarım gereğidir, çünkü baytlar değişir.
* Office belgeleri test paketinde `python-docx`/`openpyxl` ile doğrulanır,
  Microsoft Word'ün kendisiyle değil. Dosyalar yapısal olarak geçerli OPC
  paketleridir; yine de makro, gömülü OLE veya içerik denetimleri içeren sıra dışı
  belgeleri yayımlamadan önce bir kez açıp kontrol edin.
* Çok büyük PDF'ler belleğe alınır; akış (streaming) modu henüz yok. Office ZIP
  arşivlerinde sıkıştırma bombalarına karşı üst sınırlar (`10.000` girdi,
  parça başına `64 MiB`, toplam açılmış boyutta `256 MiB`) uygulanır.
* Araç neyin hassas olduğuna karar vermez. Yukarıdaki üstveri kategorilerini
  siler; `scan` bulduklarını gösterir, kararı siz verirsiniz.

## Geliştirme

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
bash scripts/check.sh        # ruff + ruff format + mypy + pytest (kapsam >= %85)
```

`scripts/check.sh` CI'ın kullandığı tek kalite kapısıdır; lint, biçim, tipler ve
tüm testler geçmeden 0 döndürmez. Testler örnek dosyaların hepsini geçici
klasörde üretir — ikili dosya yok, ağ erişimi yok.

```
doc_meta_strip/
├── cli.py              # argparse arayüzü
├── engine.py           # biçim algılama, dosya bulma, atomik yazma
├── hook.py             # --staged (git indeksi)
├── report.py           # JSON denetim raporu
├── base.py             # Finding / CleanOptions / Handler
└── handlers/
    ├── image_handler.py   # JPEG / PNG / WebP, bayt düzeyinde
    ├── office_handler.py  # DOCX / XLSX / PPTX, zipfile + xml.etree
    └── pdf_handler.py     # pypdf nesne grafiği + çöp toplama
```

## Lisans

MIT — bkz. [LICENSE](LICENSE).
