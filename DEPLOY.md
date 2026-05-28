# NexaLine Deploy

## Render ile yayinlama

1. Bu klasoru GitHub'a yukle.
2. Render Dashboard'da `New` > `Web Service` sec.
3. GitHub reposunu bagla.
4. Render ayarlari:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn --worker-class eventlet -w 1 server:app`
5. Deploy bitince Render sana `https://...onrender.com` seklinde gecici bir adres verir.

## nexaline.xyz domainini baglama

Render servisinde `Settings` > `Custom Domains` bolumune gir ve sunlari ekle:

- `nexaline.xyz`
- `www.nexaline.xyz`

Render her domain icin eklemen gereken DNS kayitlarini gosterir. Domaini aldigin panelde bu kayitlari ekle.

Genelde sunlar gerekir:

- `www` icin `CNAME`
- ana domain (`nexaline.xyz`) icin Render'in gosterdigi `A`, `ALIAS` veya `CNAME flattening` kaydi

DNS yayilmasi birkac dakika ile birkac saat surebilir. Sonra site `https://nexaline.xyz` uzerinden acilir.

## Onemli not

Bu surum baslangic surumudur. Kullanicilar ve mesajlar su anda uygulama belleginde tutulur. Render yeniden baslarsa veriler sifirlanir. Kalici gercek kullanim icin sonraki adim PostgreSQL veritabani eklemektir.
