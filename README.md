# NexaLine Platform

`nexalineapp.xyz` için hazırlanan dağıtım sitesi ve konum destekli ASİSTAN web uygulamasıdır.

- `/` indirme ve tanıtım sayfası
- `/app/` iPhone, Android ve masaüstü tarayıcılar için PWA
- `/downloads/` Windows, Android ve macOS paketleri

Logo bilerek eklenmedi. Son logo geldiğinde üst menü, PWA manifesti ve uygulama paketlerine uygulanacak.

## Hazır paketler

- `downloads/windows/NexaLine-ASISTAN-Windows.zip`
- `downloads/android/NexaLine-ASISTAN.apk`
- `downloads/macos/NexaLine-ASISTAN-macOS.zip`

Android uygulaması `com.nexaline.asistan` kimliğiyle derlenmiştir ve NIDAR
uygulamasından ayrıdır. Konum izni kullanıcı tarafından verildiğinde şehir,
saat dilimi ve hava durumu canlı konuma göre güncellenir.

GitHub yayın dalı: `mfurkanyvz/nexaline` deposundaki `asistan-site` dalı.
