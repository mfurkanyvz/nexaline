# NexaLine Deploy

## Render ile yayinlama

1. Bu klasoru GitHub'a yukle.
2. Render Dashboard'da `New` > `Web Service` sec.
3. GitHub reposunu bagla.
4. Render ayarlari:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn -w 1 --threads 100 server:app`
5. Deploy bitince Render sana `https://...onrender.com` seklinde gecici bir adres verir.

## nexaline.xyz domainini baglama

Render servisinde `Settings` > `Custom Domains` bolumune gir ve sunlari ekle:

- `nexalineapp.xyz`
- `www.nexalineapp.xyz`

Render her domain icin eklemen gereken DNS kayitlarini gosterir. Domaini aldigin panelde bu kayitlari ekle.

Genelde sunlar gerekir:

- `www` icin `CNAME`
- ana domain (`nexalineapp.xyz`) icin Render'in gosterdigi `A`, `ALIAS` veya `CNAME flattening` kaydi

DNS yayilmasi birkac dakika ile birkac saat surebilir. Sonra site `https://nexalineapp.xyz` uzerinden acilir.

## Kalici veritabani

Uygulama `DATABASE_URL` ortam degiskeni varsa PostgreSQL kullanir. Yoksa yerelde `nexaline.db` adli SQLite dosyasini kullanir.

Render'da kalici mesajlar icin:

1. Render Dashboard'da `New` > `PostgreSQL` sec.
2. Veritabanina `nexaline-db` gibi bir ad ver.
3. Olusan PostgreSQL sayfasinda `Internal Database URL` degerini kopyala.
4. NexaLine web servisinde `Environment` bolumune gir.
5. Yeni ortam degiskeni ekle:
   - Key: `DATABASE_URL`
   - Value: PostgreSQL sayfasindan kopyaladigin `Internal Database URL`
6. Web servisini tekrar deploy et.

Tablolar uygulama acilirken otomatik olusturulur.

## Gmail dogrulama maili

Kayit, Gmail degistirme ve sifremi unuttum kodlari icin Render'da HTTPS tabanli mail servisi kullanmak daha guvenlidir. Render'in ucretsiz web servislerinde Gmail SMTP baglantisi zaman asimina dusebilir.

Onerilen seceneklerden birini Render web servisinin `Environment` bolumune ekle:

### Resend

- `RESEND_API_KEY`: Resend API anahtari
- `RESEND_FROM`: Resend'de dogrulanmis gonderici adresi

### Brevo

- `BREVO_API_KEY`: Brevo SMTP/API anahtari
- `BREVO_FROM`: Brevo'da dogrulanmis gonderici adresi

SMTP'ye izin veren bir ortamda calistiriyorsan yedek olarak su degiskenler de desteklenir:

- `SMTP_HOST`: Ornek `smtp.gmail.com`
- `SMTP_PORT`: Genelde `587`
- `SMTP_USERNAME`: Mail gonderecek Gmail adresi
- `SMTP_PASSWORD`: Gmail uygulama sifresi
- `MAIL_FROM`: Gonderici olarak gorunecek adres

## Onemli not

Bu surum baslangic surumudur. PostgreSQL baglanmazsa Render'daki dosya sistemi kalici olmadigi icin veriler sifirlanabilir. Gercek kullanim icin Render'da `DATABASE_URL` mutlaka PostgreSQL'e baglanmalidir.
