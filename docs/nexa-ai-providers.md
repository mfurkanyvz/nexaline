# Nexa AI provider map

Render > Environment bolumune eklenecek anahtarlarin gorev dagilimi. Anahtar degerleri kaynak koda yazilmaz; sadece Render gizli ortam degiskeni olarak tutulur.

## Primary chat

- `GROQ_API_KEY`
  - Default model: `GROQ_CHAT_MODEL=llama-3.3-70b-versatile`
  - Gorev: hizli genel sohbet, komut yorumlama, Nexa AI cevaplari.

- `DEEPINFRA_API_KEY`
  - Default model: `DEEPINFRA_MODEL=meta-llama/Llama-3.3-70B-Instruct-Turbo`
  - Gorev: Groq yanit vermezse OpenAI uyumlu sohbet yedegi.

- `OPENROUTER_API_KEY`
  - Default model: `OPENROUTER_MODEL=openrouter/free`
  - Gorev: ucretsiz model havuzu uzerinden ikinci sohbet yedegi.

- `GEMINI_API_KEY`
  - Default model: `GEMINI_MODEL=gemini-1.5-flash`
  - Gorev: varsa sohbet, gorsel/dosya yorumlama ve analiz yedegi.

- `HF_TOKEN`
  - Optional model: `HF_VISION_MODEL=Salesforce/blip-image-captioning-large`
  - Gorev: yalnizca gorsel yorumlama yedegi. Gorsel olusturma ozelligi kaldirildi.

## Voice assistant

- `GROQ_API_KEY`
  - Default model: `GROQ_STT_MODEL=whisper-large-v3-turbo`
  - Gorev: sesli asistanda konusmayi yaziya cevirme.

- `ASSEMBLYAI_API_KEY`
  - Optional: `ASSEMBLYAI_LANGUAGE_CODE=tr`, `ASSEMBLYAI_SPEECH_MODEL=best`
  - Gorev: Groq STT calismazsa konusmayi yaziya cevirme yedegi.

- `ELEVENLABS_API_KEY`
  - Optional: `ELEVENLABS_VOICE_ID`, `ELEVENLABS_VOICE_ID_FEMALE`, `ELEVENLABS_VOICE_ID_MALE`, `ELEVENLABS_MODEL_ID=eleven_multilingual_v2`
  - Gorev: Nexa AI yanitlarini gercek ses dosyasi olarak uretme.

## Recommended Render variables

```env
AI_PROVIDER=auto
APP_PUBLIC_URL=https://nexalineapp.xyz
GROQ_API_KEY=...
DEEPINFRA_API_KEY=...
OPENROUTER_API_KEY=...
HF_TOKEN=...
ELEVENLABS_API_KEY=...
ASSEMBLYAI_API_KEY=...
```

Gemini, Mistral, Cohere, Together, Replicate, Stability, Tavily ve Fireworks otomatik anahtar kopyalamayi engelledigi veya ucret/faturalama kosulu istedigi icin bu turda aktif degisken olarak baglanmadi. Gorsel olusturma saglayicilari artik kullanilmiyor.
