# ASİSTAN Mobil Web

Mevcut ASİSTAN Windows projesinin tasarım dili ve konuşma karakteri temel alınarak hazırlanmış bağımsız mobil PWA sürümüdür. PC bağlantısı veya Windows komutları içermez.

## Özellikler

- Mobil ASİSTAN arayüzü ve durum animasyonları
- Gemini ile Türkçe yazılı konuşma
- Safari destekliyorsa konuşmayı yazıya çevirme
- Sistem sesleriyle yanıt okuma
- Kamera karesi çekip Gemini ile analiz etme
- Hava durumu, saat, cihaz durumu, web/YouTube/Spotify açma
- Cihaza özel yerel hafıza ve konuşma geçmişi
- Ana ekrana eklenebilen PWA ve çevrimdışı arayüz önbelleği

## Güvenlik

Gemini API anahtarı kaynak dosyalarda bulunmaz. Ayarlar ekranından girilen anahtar yalnızca tarayıcının yerel depolama alanında tutulur. Bu kişisel kullanım modeli için hazırlanmıştır; herkese açık ortak kullanımda API çağrıları bir sunucu üzerinden yapılmalıdır.

## Yerel önizleme

`START_PREVIEW.bat` dosyasını çalıştırın veya klasörde:

```powershell
py -3.12 -m http.server 8765 --bind 127.0.0.1
```

Ardından `http://127.0.0.1:8765` adresini açın.

## iPhone kurulumu

Kamera, mikrofon ve PWA kurulumu için site HTTPS üzerinden yayınlanmalıdır. Safari'de siteyi açtıktan sonra **Paylaş → Ana Ekrana Ekle** seçeneğini kullanın.
