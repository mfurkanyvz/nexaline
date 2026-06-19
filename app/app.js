"use strict";

const MODEL = "gemini-2.5-flash";
const API_ENDPOINT = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`;
const STORAGE = {
  apiKey: "asistan.mobil.apiKey",
  memory: "asistan.mobil.hafiza",
  history: "asistan.mobil.gecmis",
  settings: "asistan.mobil.ayarlar",
};

const STATE_COLORS = {
  INITIALISING: { hex: "#ff3344", rgb: "255, 51, 68", label: "Başlatılıyor", status: "BAŞLATILIYOR" },
  LISTENING: { hex: "#00ff88", rgb: "0, 255, 136", label: "Dinliyor", status: "DİNLİYOR" },
  THINKING: { hex: "#ffcc00", rgb: "255, 204, 0", label: "Düşünüyor", status: "DÜŞÜNÜYOR" },
  SPEAKING: { hex: "#4488ff", rgb: "68, 136, 255", label: "Konuşuyor", status: "KONUŞUYOR" },
  MUTED: { hex: "#cc2255", rgb: "204, 34, 85", label: "Mikrofon kapalı", status: "SESSİZ" },
  PAUSED: { hex: "#1e3c37", rgb: "30, 60, 55", label: "Duraklatıldı", status: "DURAKLATILDI" },
  ERROR: { hex: "#ff3344", rgb: "255, 51, 68", label: "Hata", status: "HATA" },
};

const TOOL_DECLARATIONS = [
  {
    name: "get_time",
    description: "Telefonun konuma göre belirlenen yerel saatini ve tarihini verir.",
    parameters: { type: "object", properties: {} },
  },
  {
    name: "get_location",
    description: "Kullanıcının izin verdiği konumun şehir ve saat dilimi bilgisini verir.",
    parameters: { type: "object", properties: {} },
  },
  {
    name: "get_weather",
    description: "Belirtilen şehir veya izin verilmiş güncel konum için hava durumunu getirir.",
    parameters: {
      type: "object",
      properties: { city: { type: "string", description: "İsteğe bağlı şehir adı; boşsa güncel konum" } },
    },
  },
  {
    name: "get_device_info",
    description: "Mobil cihazın tarayıcıdan görülebilen bağlantı, ekran, dil ve pil bilgilerini verir.",
    parameters: { type: "object", properties: {} },
  },
  {
    name: "open_web",
    description: "Bir web adresini, Google aramasını, YouTube veya Spotify aramasını yeni sekmede açar.",
    parameters: {
      type: "object",
      properties: {
        action: { type: "string", enum: ["url", "search", "youtube", "spotify"] },
        value: { type: "string", description: "URL veya arama ifadesi" },
      },
      required: ["action", "value"],
    },
  },
  {
    name: "save_memory",
    description: "Kullanıcının özellikle hatırlanmasını istediği kısa bir bilgiyi bu cihazda saklar.",
    parameters: {
      type: "object",
      properties: {
        key: { type: "string", description: "Kısa başlık" },
        value: { type: "string", description: "Hatırlanacak bilgi" },
      },
      required: ["key", "value"],
    },
  },
  {
    name: "delete_memory",
    description: "Yerel hafızadaki bir kaydı anahtarına veya içeriğine göre siler.",
    parameters: {
      type: "object",
      properties: { query: { type: "string" } },
      required: ["query"],
    },
  },
];

const dom = Object.fromEntries([
  "appShell", "statusDot", "statusLabel", "networkLabel", "clockMini", "orb", "orbStatus",
  "orbParticles", "waveform", "clockMain", "clockSeconds", "dateMain", "weatherTemp",
  "weatherText", "weatherRefresh", "weatherLocationCode", "deviceNetwork", "deviceBattery", "memoryCount", "pwaMode",
  "locationStatus", "locationButton",
  "messages", "composer", "promptInput", "sendButton", "cameraButton", "cameraPreview",
  "cameraVideo", "capturedImage", "captureButton", "closeCameraButton", "micButton", "pauseButton",
  "sfxButton", "installButton", "footerState", "settingsButton", "sheetBackdrop", "settingsSheet",
  "closeSettings", "apiKeyInput", "toggleKey", "voiceSelect", "autoSpeakToggle", "effectsToggle",
  "clearMemoryButton", "saveSettingsButton", "clearConversation", "toast", "fxCanvas",
].map((id) => [id, document.getElementById(id)]));

const defaults = {
  autoSpeak: true,
  effects: true,
  sfx: true,
  voiceURI: "",
  locationEnabled: false,
};

const app = {
  state: "INITIALISING",
  paused: false,
  busy: false,
  apiKey: localStorage.getItem(STORAGE.apiKey) || "",
  memory: readJSON(STORAGE.memory, {}),
  history: readJSON(STORAGE.history, []).slice(-30),
  settings: { ...defaults, ...readJSON(STORAGE.settings, {}) },
  cameraStream: null,
  pendingImage: null,
  recognition: null,
  recognizing: false,
  mediaRecorder: null,
  microphoneStream: null,
  microphoneContext: null,
  microphoneSource: null,
  microphoneAnalyser: null,
  microphoneMonitor: null,
  recordingChunks: [],
  recordingStartedAt: 0,
  recordingLastVoiceAt: 0,
  recordingHeardSpeech: false,
  recordingDiscard: false,
  transcribing: false,
  deferredInstallPrompt: null,
  speechVoices: [],
  audioUnlocked: false,
  location: null,
  locationWatchId: null,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Istanbul",
  lastWeatherAt: 0,
};

function readJSON(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    return value ?? fallback;
  } catch {
    return fallback;
  }
}

function writeJSON(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function escapeText(value) {
  return String(value ?? "").replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, " ").trim();
}

function setState(next, detail = "") {
  const normalized = STATE_COLORS[next] ? next : "LISTENING";
  const visual = STATE_COLORS[normalized];
  app.state = normalized;
  document.body.dataset.state = normalized;
  document.documentElement.style.setProperty("--state", visual.hex);
  document.documentElement.style.setProperty("--state-rgb", visual.rgb);
  dom.statusLabel.textContent = visual.status;
  dom.orbStatus.textContent = `● ${detail || visual.label}`;
  dom.footerState.textContent = visual.status;
}

function showToast(message, type = "info") {
  dom.toast.textContent = message;
  dom.toast.classList.toggle("error", type === "error");
  dom.toast.classList.add("visible");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => dom.toast.classList.remove("visible"), 2800);
}

function addMessage(role, text, persist = false) {
  const clean = escapeText(text);
  if (!clean) return;
  const labels = { user: "SİZ", assistant: "ASİSTAN", system: "SİSTEM", error: "HATA" };
  const item = document.createElement("div");
  item.className = `message ${role}`;
  const label = document.createElement("span");
  label.textContent = labels[role] || "SİSTEM";
  const body = document.createElement("p");
  body.textContent = clean;
  item.append(label, body);
  dom.messages.append(item);
  dom.messages.scrollTop = dom.messages.scrollHeight;

  if (persist && (role === "user" || role === "assistant")) {
    app.history.push({ role, text: clean, at: Date.now() });
    app.history = app.history.slice(-30);
    writeJSON(STORAGE.history, app.history);
  }
}

function playSfx(name, volume = 0.28) {
  if (!app.settings.sfx || !app.settings.effects || !app.audioUnlocked) return;
  const audio = new Audio(`assets/sfx/${name}.mp3`);
  audio.volume = volume;
  audio.play().catch(() => {});
}

function unlockAudio() {
  app.audioUnlocked = true;
}

function updateClock() {
  const now = new Date();
  const clockParts = new Intl.DateTimeFormat("tr-TR", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: app.timezone,
  }).formatToParts(now);
  const part = (type) => clockParts.find((item) => item.type === type)?.value || "00";
  const time = `${part("hour")}:${part("minute")}`;
  const seconds = part("second");
  dom.clockMain.textContent = time;
  dom.clockMini.textContent = time;
  dom.clockSeconds.textContent = `:${seconds}`;
  dom.dateMain.textContent = new Intl.DateTimeFormat("tr-TR", {
    day: "2-digit", month: "long", year: "numeric", weekday: "long", timeZone: app.timezone,
  }).format(now).toLocaleUpperCase("tr-TR");
}

function updateNetwork() {
  const online = navigator.onLine;
  dom.networkLabel.textContent = online ? "AĞ ÇEVRİMİÇİ" : "AĞ ÇEVRİMDIŞI";
  dom.deviceNetwork.textContent = online ? "ÇEVRİMİÇİ" : "ÇEVRİMDIŞI";
  dom.deviceNetwork.style.color = online ? "var(--green)" : "var(--red)";
}

async function updateDeviceInfo() {
  updateNetwork();
  dom.memoryCount.textContent = String(Object.keys(app.memory).length);
  dom.pwaMode.textContent = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone ? "UYGULAMA" : "TARAYICI";
  if (navigator.getBattery) {
    try {
      const battery = await navigator.getBattery();
      const render = () => {
        dom.deviceBattery.textContent = `${Math.round(battery.level * 100)}%${battery.charging ? " +" : ""}`;
      };
      render();
      battery.addEventListener("levelchange", render);
      battery.addEventListener("chargingchange", render);
    } catch {
      dom.deviceBattery.textContent = "YOK";
    }
  }
}

function weatherDescription(code) {
  const exact = {
    0: "Açık", 1: "Çoğunlukla açık", 2: "Parçalı bulutlu", 3: "Kapalı",
    45: "Sisli", 48: "Kırağılı sis", 51: "Hafif çisenti", 53: "Çisenti",
    55: "Yoğun çisenti", 61: "Hafif yağmur", 63: "Yağmurlu", 65: "Kuvvetli yağmur",
    71: "Hafif kar", 73: "Karlı", 75: "Yoğun kar", 80: "Sağanak",
    81: "Kuvvetli sağanak", 82: "Şiddetli sağanak", 95: "Fırtına", 96: "Dolu ihtimali",
  };
  return exact[code] || "Değişken hava";
}

function renderWeather(result) {
  dom.weatherTemp.textContent = `${result.temperature}°`;
  dom.weatherText.textContent = `${result.city.toLocaleUpperCase("tr-TR")} · ${result.condition.toLocaleUpperCase("tr-TR")}`;
  dom.weatherLocationCode.textContent = (result.city || "KONUM").slice(0, 3).toLocaleUpperCase("tr-TR");
}

async function reverseLocation(latitude, longitude) {
  const url = `https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${latitude}&longitude=${longitude}&localityLanguage=tr`;
  const response = await fetch(url);
  if (!response.ok) return { city: "Güncel konum", country: "" };
  const data = await response.json();
  return {
    city: data.city || data.locality || data.principalSubdivision || "Güncel konum",
    country: data.countryName || data.countryCode || "",
  };
}

async function getWeatherByCoordinates(latitude, longitude, updateCard = true) {
  const forecastURL = `https://api.open-meteo.com/v1/forecast?latitude=${latitude}&longitude=${longitude}&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m&timezone=auto`;
  const [response, place] = await Promise.all([
    fetch(forecastURL),
    reverseLocation(latitude, longitude).catch(() => ({ city: "Güncel konum", country: "" })),
  ]);
  if (!response.ok) throw new Error("Konuma göre hava durumu alınamadı.");
  const data = await response.json();
  const current = data.current;
  app.timezone = data.timezone || app.timezone;
  const result = {
    city: place.city,
    country: place.country,
    temperature: Math.round(current.temperature_2m),
    feelsLike: Math.round(current.apparent_temperature),
    condition: weatherDescription(current.weather_code),
    wind: Math.round(current.wind_speed_10m),
    timezone: app.timezone,
  };
  app.lastWeatherAt = Date.now();
  if (updateCard) renderWeather(result);
  return result;
}

async function getWeather(city = "İstanbul", updateCard = true) {
  const cityName = escapeText(city);
  if (!cityName && app.location) {
    return getWeatherByCoordinates(app.location.latitude, app.location.longitude, updateCard);
  }
  const fallbackCity = cityName || "İstanbul";
  const geoURL = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(fallbackCity)}&count=1&language=tr&format=json`;
  const geoResponse = await fetch(geoURL);
  if (!geoResponse.ok) throw new Error("Konum bulunamadı.");
  const geo = await geoResponse.json();
  const place = geo.results?.[0];
  if (!place) throw new Error(`${fallbackCity} için konum bulunamadı.`);
  const forecastURL = `https://api.open-meteo.com/v1/forecast?latitude=${place.latitude}&longitude=${place.longitude}&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m&timezone=auto`;
  const response = await fetch(forecastURL);
  if (!response.ok) throw new Error("Hava durumu alınamadı.");
  const data = await response.json();
  const current = data.current;
  app.timezone = data.timezone || app.timezone;
  const result = {
    city: place.name,
    country: place.country || "",
    temperature: Math.round(current.temperature_2m),
    feelsLike: Math.round(current.apparent_temperature),
    condition: weatherDescription(current.weather_code),
    wind: Math.round(current.wind_speed_10m),
    timezone: app.timezone,
  };
  app.lastWeatherAt = Date.now();
  if (updateCard) renderWeather(result);
  return result;
}

function distanceKm(first, second) {
  if (!first || !second) return Infinity;
  const radians = (value) => value * Math.PI / 180;
  const earth = 6371;
  const dLat = radians(second.latitude - first.latitude);
  const dLon = radians(second.longitude - first.longitude);
  const lat1 = radians(first.latitude);
  const lat2 = radians(second.latitude);
  const value = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return earth * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(1 - value));
}

function setLocationUi(label, active = false) {
  dom.locationStatus.textContent = label.toLocaleUpperCase("tr-TR");
  dom.locationButton.textContent = active ? "KONUMU YENİLE" : "KONUMU ETKİNLEŞTİR";
  dom.locationButton.classList.toggle("active", active);
}

async function applyPosition(position) {
  const next = {
    latitude: Number(position.coords.latitude),
    longitude: Number(position.coords.longitude),
    accuracy: Math.round(Number(position.coords.accuracy || 0)),
  };
  const moved = distanceKm(app.location, next);
  app.location = { ...app.location, ...next };
  app.settings.locationEnabled = true;
  writeJSON(STORAGE.settings, app.settings);
  setLocationUi(app.location.city || "ALINDI", true);

  if (moved < 1 && Date.now() - app.lastWeatherAt < 15 * 60 * 1000) return;
  try {
    const weather = await getWeatherByCoordinates(next.latitude, next.longitude, true);
    app.location = { ...app.location, city: weather.city, country: weather.country, timezone: weather.timezone };
    setLocationUi(weather.city, true);
    updateClock();
  } catch (error) {
    dom.weatherText.textContent = "KONUM ALINDI · HAVA YOK";
    setLocationUi("ALINDI", true);
  }
}

function locationErrorMessage(error) {
  if (error?.code === 1) return "Konum izni reddedildi. Cihaz ayarlarından izin verebilirsin.";
  if (error?.code === 2) return "Cihaz konumu belirleyemedi.";
  if (error?.code === 3) return "Konum isteği zaman aşımına uğradı.";
  return "Konum bilgisi alınamadı.";
}

function startLocationWatch(silent = false) {
  if (!navigator.geolocation) {
    setLocationUi("DESTEKLENMİYOR");
    if (!silent) showToast("Bu cihaz konum paylaşımını desteklemiyor.", "error");
    return;
  }
  if (app.locationWatchId !== null) {
    navigator.geolocation.getCurrentPosition(applyPosition, (error) => {
      if (!silent) showToast(locationErrorMessage(error), "error");
    }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 30000 });
    return;
  }

  setLocationUi("İZİN BEKLENİYOR");
  if (!silent) showToast("Konum izni bekleniyor…");
  app.locationWatchId = navigator.geolocation.watchPosition(
    applyPosition,
    (error) => {
      const message = locationErrorMessage(error);
      setLocationUi(error?.code === 1 ? "İZİN REDDEDİLDİ" : "ALINAMADI");
      if (error?.code === 1) {
        app.settings.locationEnabled = false;
        writeJSON(STORAGE.settings, app.settings);
        navigator.geolocation.clearWatch(app.locationWatchId);
        app.locationWatchId = null;
      }
      if (!silent) showToast(message, "error");
    },
    { enableHighAccuracy: true, timeout: 20000, maximumAge: 60000 },
  );
}

function buildSystemPrompt() {
  const memory = Object.entries(app.memory).map(([key, value]) => `- ${key}: ${value}`).join("\n") || "- Kayıt yok";
  const location = app.location?.city
    ? `${app.location.city}${app.location.country ? `, ${app.location.country}` : ""} · ${app.timezone}`
    : "Konum izni henüz verilmedi";
  return `Senin adın ASİSTAN. NexaLine içinde çalışan kişisel yapay zekâ yardımcısısın.

KİMLİK VE KONUŞMA:
- Türkçe konuş; kullanıcı başka dil kullanırsa o dile geç.
- Kullanıcı "Asistan", "asistan" veya "ASİSTAN" diye seslendiğinde bunun senin adın olduğunu anla ve doğrudan yanıt ver.
- Eski adını hiçbir yanıtta kullanma; kendini her zaman ASİSTAN olarak tanıt.
- Kısa, net, özgüvenli ve etkili ol. Gereksiz tekrar yapma.
- Kullanıcıya "efendim" diye hitap etmek zorunda değilsin; doğal ve sakin ol.
- Yapamadığın bir telefon işlemini yaptığını asla söyleme. Cihazın işletim sistemi sınırını açıkça belirt.
- Bir araç gerekiyorsa aracı çağır; sonucu uydurma.
- Kullanıcı açıkça "bunu hatırla" derse save_memory, unutmanı isterse delete_memory kullan.
- Web sitesi, arama, YouTube veya Spotify açma isteklerinde open_web kullan.
- Kamera görüntüsü eklenmişse görüntüyü doğrudan analiz et.

MOBİL YETENEKLER:
- İzin verildiyse canlı konum, konuma göre saat/tarih ve hava; ayrıca cihaz durumu, web arama, YouTube/Spotify ve yerel hafıza.
- Kamera ve mikrofon yalnızca kullanıcı düğmeye basıp izin verdiğinde kullanılabilir.
- SMS, WhatsApp, telefon ayarları veya diğer uygulamalar üzerinde doğrudan kontrolün yoktur.

GÜNCEL KONUM:
${location}

ŞU ANKİ ZAMAN:
${new Date().toLocaleString("tr-TR", { dateStyle: "full", timeStyle: "short", timeZone: app.timezone })}

YEREL HAFIZA:
${memory}`;
}

function historyForGemini() {
  return app.history.slice(-18).map((entry) => ({
    role: entry.role === "assistant" ? "model" : "user",
    parts: [{ text: entry.text }],
  }));
}

async function callGemini(contents) {
  const response = await fetch(API_ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-goog-api-key": app.apiKey,
    },
    body: JSON.stringify({
      systemInstruction: { parts: [{ text: buildSystemPrompt() }] },
      contents,
      tools: [{ functionDeclarations: TOOL_DECLARATIONS }],
      generationConfig: {
        temperature: 0.72,
        topP: 0.9,
        maxOutputTokens: 900,
      },
    }),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const reason = payload.error?.message || `Gemini bağlantı hatası (${response.status})`;
    throw new Error(reason);
  }
  const parts = payload.candidates?.[0]?.content?.parts || [];
  if (!parts.length) throw new Error("Gemini boş yanıt döndürdü.");
  return parts;
}

async function executeTool(name, args = {}) {
  if (name === "get_time") {
    const now = new Date();
    return {
      time: now.toLocaleTimeString("tr-TR", { timeZone: app.timezone }),
      date: now.toLocaleDateString("tr-TR", { dateStyle: "full", timeZone: app.timezone }),
      timezone: app.timezone,
    };
  }
  if (name === "get_location") {
    return app.location?.city
      ? { allowed: true, city: app.location.city, country: app.location.country || "", timezone: app.timezone, accuracyMeters: app.location.accuracy }
      : { allowed: false, message: "Kullanıcı henüz konum izni vermedi." };
  }
  if (name === "get_weather") return getWeather(args.city || "", true);
  if (name === "get_device_info") {
    let battery = "iOS Safari bu bilgiyi paylaşmıyor";
    if (navigator.getBattery) {
      const info = await navigator.getBattery();
      battery = `${Math.round(info.level * 100)}%, ${info.charging ? "şarj oluyor" : "şarj olmuyor"}`;
    }
    return {
      online: navigator.onLine,
      language: navigator.language,
      screen: `${window.screen.width}×${window.screen.height}`,
      mode: dom.pwaMode.textContent,
      battery,
      location: app.location?.city || "İzin verilmedi",
      timezone: app.timezone,
    };
  }
  if (name === "open_web") {
    const value = escapeText(args.value);
    let url = "";
    if (args.action === "search") url = `https://www.google.com/search?q=${encodeURIComponent(value)}`;
    if (args.action === "youtube") url = `https://www.youtube.com/results?search_query=${encodeURIComponent(value)}`;
    if (args.action === "spotify") url = `https://open.spotify.com/search/${encodeURIComponent(value)}`;
    if (args.action === "url") {
      try {
        const parsed = new URL(/^https?:\/\//i.test(value) ? value : `https://${value}`);
        if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("Geçersiz protokol");
        url = parsed.href;
      } catch {
        return { ok: false, error: "Güvenli bir web adresi oluşturulamadı." };
      }
    }
    const popup = window.open(url, "_blank", "noopener,noreferrer");
    return { ok: Boolean(popup), opened: url, note: popup ? "Yeni sekmede açıldı." : "Safari açılır pencereyi engellemiş olabilir." };
  }
  if (name === "save_memory") {
    const key = escapeText(args.key).slice(0, 80);
    const value = escapeText(args.value).slice(0, 500);
    if (!key || !value) return { ok: false, error: "Eksik hafıza bilgisi." };
    app.memory[key] = value;
    writeJSON(STORAGE.memory, app.memory);
    updateDeviceInfo();
    return { ok: true, saved: key };
  }
  if (name === "delete_memory") {
    const query = escapeText(args.query).toLocaleLowerCase("tr-TR");
    const key = Object.keys(app.memory).find((item) =>
      item.toLocaleLowerCase("tr-TR").includes(query) ||
      String(app.memory[item]).toLocaleLowerCase("tr-TR").includes(query)
    );
    if (!key) return { ok: false, error: "Eşleşen hafıza kaydı bulunamadı." };
    delete app.memory[key];
    writeJSON(STORAGE.memory, app.memory);
    updateDeviceInfo();
    return { ok: true, deleted: key };
  }
  return { ok: false, error: `Desteklenmeyen araç: ${name}` };
}

async function generateReply(userText) {
  if (!app.apiKey) {
    openSettings();
    throw new Error("Önce Gemini API anahtarını ayarlamalısın.");
  }

  const contents = historyForGemini();
  const parts = [{ text: userText }];
  if (app.pendingImage) {
    parts.push({ inlineData: { mimeType: app.pendingImage.mimeType, data: app.pendingImage.data } });
  }
  contents.push({ role: "user", parts });

  for (let round = 0; round < 4; round += 1) {
    const responseParts = await callGemini(contents);
    const calls = responseParts.filter((part) => part.functionCall).map((part) => part.functionCall);
    if (!calls.length) {
      const answer = responseParts.map((part) => part.text || "").join(" ").trim();
      if (!answer) throw new Error("ASİSTAN yanıt oluşturamadı.");
      return answer;
    }

    contents.push({ role: "model", parts: responseParts });
    const toolParts = [];
    for (const call of calls) {
      const result = await executeTool(call.name, call.args || {});
      toolParts.push({ functionResponse: { name: call.name, response: { result } } });
    }
    contents.push({ role: "user", parts: toolParts });
  }
  throw new Error("Araç çağrısı sınırı aşıldı.");
}

async function submitPrompt(rawText) {
  const text = escapeText(rawText);
  if (!text || app.busy || app.paused) return;
  unlockAudio();
  app.busy = true;
  dom.sendButton.disabled = true;
  dom.promptInput.value = "";
  resizeTextarea();
  addMessage("user", text, true);
  setState("THINKING");
  playSfx("Think", 0.2);

  try {
    const answer = await generateReply(text);
    addMessage("assistant", answer, true);
    app.pendingImage = null;
    dom.capturedImage.removeAttribute("src");
    closeCamera();
    playSfx("Done", 0.27);
    if (app.settings.autoSpeak) speak(answer);
    else setState("LISTENING");
  } catch (error) {
    const rawMessage = String(error?.message || error || "");
    const message = /failed to fetch|networkerror|load failed/i.test(rawMessage)
      ? "İnternet bağlantısı kurulamadı. Ağını kontrol edip yeniden dene."
      : escapeText(rawMessage);
    addMessage("error", message);
    setState("ERROR", "Bağlantı hatası");
    playSfx("Error", 0.27);
    setTimeout(() => !app.paused && setState(app.apiKey ? "LISTENING" : "INITIALISING"), 2400);
  } finally {
    app.busy = false;
    dom.sendButton.disabled = false;
  }
}

function speak(text) {
  if (!("speechSynthesis" in window)) {
    setState("LISTENING");
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "tr-TR";
  utterance.rate = 1.02;
  utterance.pitch = 0.88;
  const selected = app.speechVoices.find((voice) => voice.voiceURI === app.settings.voiceURI);
  const fallback = app.speechVoices.find((voice) => voice.lang.toLowerCase().startsWith("tr"));
  utterance.voice = selected || fallback || null;
  utterance.onstart = () => setState("SPEAKING");
  utterance.onend = () => !app.paused && setState("LISTENING");
  utterance.onerror = () => !app.paused && setState("LISTENING");
  window.speechSynthesis.speak(utterance);
}

function loadVoices() {
  if (!("speechSynthesis" in window)) return;
  const voices = window.speechSynthesis.getVoices();
  if (!voices.length) return;
  app.speechVoices = voices;
  const previous = dom.voiceSelect.value || app.settings.voiceURI;
  dom.voiceSelect.replaceChildren();
  const sorted = [...voices].sort((a, b) => Number(b.lang.startsWith("tr")) - Number(a.lang.startsWith("tr")));
  sorted.forEach((voice) => {
    const option = document.createElement("option");
    option.value = voice.voiceURI;
    option.textContent = `${voice.name} · ${voice.lang}`;
    dom.voiceSelect.append(option);
  });
  const preferred = sorted.find((voice) => voice.voiceURI === previous) || sorted.find((voice) => voice.lang.startsWith("tr")) || sorted[0];
  dom.voiceSelect.value = preferred.voiceURI;
}

function supportsAudioCapture() {
  return Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
}

function preferredAudioMimeType() {
  if (!window.MediaRecorder?.isTypeSupported) return "";
  return [
    "audio/mp4",
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
  ].find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",")[1] || "");
    reader.onerror = () => reject(new Error("Ses kaydı okunamadı."));
    reader.readAsDataURL(blob);
  });
}

function releaseMicrophone() {
  clearInterval(app.microphoneMonitor);
  app.microphoneMonitor = null;
  try { app.microphoneSource?.disconnect(); } catch { /* already disconnected */ }
  try { app.microphoneAnalyser?.disconnect(); } catch { /* already disconnected */ }
  app.microphoneSource = null;
  app.microphoneAnalyser = null;
  if (app.microphoneContext) {
    app.microphoneContext.close().catch(() => {});
    app.microphoneContext = null;
  }
  app.microphoneStream?.getTracks().forEach((track) => track.stop());
  app.microphoneStream = null;
  app.mediaRecorder = null;
  app.recordingChunks = [];
  app.recordingStartedAt = 0;
  app.recordingLastVoiceAt = 0;
  app.recordingHeardSpeech = false;
  app.recordingDiscard = false;
  app.recognizing = false;
  dom.micButton.classList.remove("active");
  dom.orb.classList.remove("listening");
}

async function transcribeAudio(blob) {
  if (!app.apiKey) {
    openSettings();
    throw new Error("Sesinizi çözümlemek için önce Gemini API anahtarını ayarlamalısın.");
  }
  const data = await blobToBase64(blob);
  if (!data) throw new Error("Ses kaydı boş geldi.");
  const mimeType = (blob.type || "audio/webm").split(";")[0];
  const response = await fetch(API_ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-goog-api-key": app.apiKey,
    },
    body: JSON.stringify({
      contents: [{
        role: "user",
        parts: [
          { text: "Bu ses kaydındaki kullanıcının söylediklerini Türkçe metne çevir. Yalnızca söylenen cümleyi yaz; açıklama, tırnak veya başlık ekleme. Anlaşılır konuşma yoksa yalnızca [SESSİZ] yaz." },
          { inlineData: { mimeType, data } },
        ],
      }],
      generationConfig: { temperature: 0, maxOutputTokens: 180 },
    }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error?.message || `Ses çözümleme hatası (${response.status})`);
  }
  const transcript = (payload.candidates?.[0]?.content?.parts || [])
    .map((part) => part.text || "")
    .join(" ")
    .replace(/^[\s"'“”]+|[\s"'“”]+$/g, "")
    .trim();
  return /^\[?sessiz\]?$/i.test(transcript) ? "" : transcript;
}

async function processRecordedAudio(blob, heardSpeech, discarded) {
  releaseMicrophone();
  if (discarded || app.paused) return;
  if (!blob || blob.size < 600) {
    showToast("Ses kaydı alınamadı. Mikrofona biraz daha yakın konuşup yeniden dene.", "error");
    setState(app.apiKey ? "LISTENING" : "INITIALISING");
    return;
  }

  app.transcribing = true;
  setState("THINKING", "Sesiniz çözümleniyor");
  try {
    const transcript = await transcribeAudio(blob);
    if (!transcript) {
      showToast(heardSpeech ? "Söylediğinizi anlayamadım; yeniden deneyin." : "Ses algılanmadı; mikrofona yakın konuşun.", "error");
      setState(app.apiKey ? "LISTENING" : "INITIALISING");
      return;
    }
    dom.promptInput.value = transcript;
    resizeTextarea();
    await submitPrompt(transcript);
  } catch (error) {
    const message = escapeText(error?.message || error || "Ses çözümlenemedi.");
    showToast(message, "error");
    setState("ERROR", "Mikrofon hatası");
    setTimeout(() => !app.paused && setState(app.apiKey ? "LISTENING" : "INITIALISING"), 2400);
  } finally {
    app.transcribing = false;
  }
}

function stopAudioCapture({ discard = false } = {}) {
  const recorder = app.mediaRecorder;
  app.recordingDiscard = discard;
  if (recorder?.state === "recording") {
    recorder.stop();
  } else {
    releaseMicrophone();
  }
}

async function startAudioCapture() {
  if (!supportsAudioCapture() || app.transcribing || app.busy) return false;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    });
    const mimeType = preferredAudioMimeType();
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    app.microphoneStream = stream;
    app.mediaRecorder = recorder;
    app.recordingChunks = [];
    app.recordingStartedAt = Date.now();
    app.recordingLastVoiceAt = 0;
    app.recordingHeardSpeech = false;
    app.recordingDiscard = false;

    recorder.addEventListener("dataavailable", (event) => {
      if (event.data?.size) app.recordingChunks.push(event.data);
    });
    recorder.addEventListener("stop", () => {
      const blob = new Blob(app.recordingChunks, { type: recorder.mimeType || mimeType || "audio/webm" });
      const heardSpeech = app.recordingHeardSpeech;
      const discarded = app.recordingDiscard;
      processRecordedAudio(blob, heardSpeech, discarded);
    }, { once: true });
    recorder.addEventListener("error", () => {
      releaseMicrophone();
      showToast("Mikrofon kaydı başlatılamadı.", "error");
      setState(app.apiKey ? "LISTENING" : "INITIALISING");
    }, { once: true });

    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      const context = new AudioContextClass();
      await context.resume().catch(() => {});
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.15;
      source.connect(analyser);
      app.microphoneContext = context;
      app.microphoneSource = source;
      app.microphoneAnalyser = analyser;
      const samples = new Uint8Array(analyser.fftSize);
      app.microphoneMonitor = setInterval(() => {
        if (recorder.state !== "recording") return;
        analyser.getByteTimeDomainData(samples);
        let energy = 0;
        for (const sample of samples) {
          const value = (sample - 128) / 128;
          energy += value * value;
        }
        const rms = Math.sqrt(energy / samples.length);
        const now = Date.now();
        const elapsed = now - app.recordingStartedAt;
        if (rms > 0.012) {
          app.recordingHeardSpeech = true;
          app.recordingLastVoiceAt = now;
        }
        if (app.recordingHeardSpeech && elapsed > 900 && now - app.recordingLastVoiceAt > 1250) {
          stopAudioCapture();
        } else if (elapsed > 20000) {
          stopAudioCapture();
        }
      }, 100);
    } else {
      app.microphoneMonitor = setTimeout(() => stopAudioCapture(), 20000);
    }

    app.recognizing = true;
    dom.micButton.classList.add("active");
    dom.orb.classList.add("listening");
    setState("LISTENING", "Sizi dinliyorum · bitirmek için tekrar dokunun");
    playSfx("Start", 0.2);
    recorder.start(250);
    return true;
  } catch (error) {
    releaseMicrophone();
    const denied = error?.name === "NotAllowedError" || error?.name === "SecurityError";
    showToast(denied ? "Mikrofon izni verilmedi. Safari site ayarlarından mikrofonu açın." : "Mikrofon açılamadı; başka bir uygulamanın kullanmadığını kontrol edin.", "error");
    setState(app.apiKey ? "LISTENING" : "INITIALISING");
    return false;
  }
}

function setupRecognition() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    dom.micButton.disabled = !supportsAudioCapture();
    dom.micButton.title = supportsAudioCapture()
      ? "Mikrofona dokunup konuşun; ASİSTAN sesinizi çözümler."
      : "Bu tarayıcı mikrofon kaydını desteklemiyor.";
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "tr-TR";
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  let finalText = "";

  recognition.onstart = () => {
    app.recognizing = true;
    finalText = "";
    dom.micButton.classList.add("active");
    dom.orb.classList.add("listening");
    setState("LISTENING", "Sizi dinliyorum");
    playSfx("Start", 0.2);
  };
  recognition.onresult = (event) => {
    let interim = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const transcript = event.results[index][0].transcript;
      if (event.results[index].isFinal) finalText += transcript;
      else interim += transcript;
    }
    dom.promptInput.value = finalText || interim;
    resizeTextarea();
  };
  recognition.onerror = (event) => {
    if (event.error !== "no-speech" && event.error !== "aborted") {
      showToast(`Mikrofon: ${event.error}`, "error");
    }
  };
  recognition.onend = () => {
    app.recognizing = false;
    dom.micButton.classList.remove("active");
    dom.orb.classList.remove("listening");
    if (finalText.trim()) submitPrompt(finalText.trim());
    else if (!app.paused && !app.busy) setState(app.apiKey ? "LISTENING" : "INITIALISING");
  };
  app.recognition = recognition;
}

async function toggleRecognition() {
  unlockAudio();
  if (app.paused || app.busy || app.transcribing) return;
  if (app.mediaRecorder?.state === "recording") {
    stopAudioCapture();
    return;
  }
  if (app.recognizing && app.recognition) {
    app.recognition.stop();
    return;
  }
  window.speechSynthesis?.cancel();
  if (supportsAudioCapture()) {
    await startAudioCapture();
    return;
  }
  if (!app.recognition) return;
  try { app.recognition.start(); } catch { /* Safari start race */ }
}

async function openCamera() {
  unlockAudio();
  if (!navigator.mediaDevices?.getUserMedia) {
    showToast("Bu tarayıcı kamera erişimini desteklemiyor.", "error");
    return;
  }
  try {
    app.cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } },
      audio: false,
    });
    dom.cameraVideo.srcObject = app.cameraStream;
    dom.cameraPreview.classList.remove("hidden");
    dom.messages.style.display = "none";
    dom.capturedImage.removeAttribute("src");
    setState("LISTENING", "Kamera aktif");
  } catch (error) {
    showToast("Kamera izni verilmedi veya kamera açılamadı.", "error");
  }
}

function captureCamera() {
  if (!app.cameraStream || !dom.cameraVideo.videoWidth) return;
  const canvas = document.createElement("canvas");
  const maxWidth = 1280;
  const scale = Math.min(1, maxWidth / dom.cameraVideo.videoWidth);
  canvas.width = Math.round(dom.cameraVideo.videoWidth * scale);
  canvas.height = Math.round(dom.cameraVideo.videoHeight * scale);
  canvas.getContext("2d").drawImage(dom.cameraVideo, 0, 0, canvas.width, canvas.height);
  const dataURL = canvas.toDataURL("image/jpeg", 0.82);
  app.pendingImage = { mimeType: "image/jpeg", data: dataURL.split(",")[1] };
  dom.capturedImage.src = dataURL;
  dom.cameraVideo.style.display = "none";
  stopCameraStream();
  addMessage("system", "Kamera karesi eklendi. Şimdi görüntü hakkında sorunu yazabilirsin.");
  playSfx("Done", 0.22);
}

function stopCameraStream() {
  app.cameraStream?.getTracks().forEach((track) => track.stop());
  app.cameraStream = null;
  dom.cameraVideo.srcObject = null;
}

function closeCamera() {
  stopCameraStream();
  dom.cameraPreview.classList.add("hidden");
  dom.messages.style.display = "block";
  dom.cameraVideo.style.display = "block";
  if (!app.paused && !app.busy) setState(app.apiKey ? "LISTENING" : "INITIALISING");
}

function resizeTextarea() {
  dom.promptInput.style.height = "auto";
  dom.promptInput.style.height = `${Math.min(dom.promptInput.scrollHeight, 110)}px`;
}

function openSettings() {
  dom.apiKeyInput.value = app.apiKey;
  dom.autoSpeakToggle.classList.toggle("active", app.settings.autoSpeak);
  dom.effectsToggle.classList.toggle("active", app.settings.effects);
  dom.autoSpeakToggle.setAttribute("aria-checked", String(app.settings.autoSpeak));
  dom.effectsToggle.setAttribute("aria-checked", String(app.settings.effects));
  dom.settingsSheet.classList.remove("hidden");
  dom.sheetBackdrop.classList.remove("hidden");
  setTimeout(() => !app.apiKey && dom.apiKeyInput.focus(), 120);
}

function closeSettings() {
  dom.settingsSheet.classList.add("hidden");
  dom.sheetBackdrop.classList.add("hidden");
}

function toggleSwitch(button, setting) {
  app.settings[setting] = !app.settings[setting];
  button.classList.toggle("active", app.settings[setting]);
  button.setAttribute("aria-checked", String(app.settings[setting]));
  applySettings();
}

function applySettings() {
  document.body.classList.toggle("effects-off", !app.settings.effects);
  dom.sfxButton.classList.toggle("active", app.settings.sfx);
  writeJSON(STORAGE.settings, app.settings);
}

async function saveSettings() {
  const key = dom.apiKeyInput.value.trim();
  if (key && key.length < 20) {
    showToast("API anahtarı çok kısa görünüyor.", "error");
    return;
  }
  app.apiKey = key;
  app.settings.voiceURI = dom.voiceSelect.value;
  if (key) localStorage.setItem(STORAGE.apiKey, key);
  else localStorage.removeItem(STORAGE.apiKey);
  writeJSON(STORAGE.settings, app.settings);
  closeSettings();
  setState(key ? "LISTENING" : "INITIALISING", key ? "Mobil çekirdek hazır" : "API anahtarı bekleniyor");
  addMessage("system", key ? "Gemini bağlantısı yapılandırıldı." : "API anahtarı kaldırıldı.");
  showToast(key ? "Ayarlar kaydedildi. ASİSTAN hazır." : "Ayarlar kaydedildi.");
  playSfx("Done", 0.2);
}

function togglePause() {
  unlockAudio();
  app.paused = !app.paused;
  document.body.classList.toggle("paused", app.paused);
  dom.pauseButton.classList.toggle("active", app.paused);
  if (app.paused) {
    app.recognition?.abort();
    stopAudioCapture({ discard: true });
    window.speechSynthesis?.cancel();
    setState("PAUSED");
    addMessage("system", "ASİSTAN duraklatıldı.");
  } else {
    setState(app.apiKey ? "LISTENING" : "INITIALISING");
    addMessage("system", "ASİSTAN devam ediyor.");
  }
}

async function installPWA() {
  if (app.deferredInstallPrompt) {
    app.deferredInstallPrompt.prompt();
    await app.deferredInstallPrompt.userChoice;
    app.deferredInstallPrompt = null;
    return;
  }
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  showToast(ios ? "Safari: Paylaş düğmesi → Ana Ekrana Ekle" : "Tarayıcı menüsünden 'Uygulamayı yükle' seçeneğini kullan.");
}

function buildOrbParticles() {
  const fragment = document.createDocumentFragment();
  for (let index = 0; index < 76; index += 1) {
    const particle = document.createElement("i");
    const angle = Math.random() * Math.PI * 2;
    const radius = Math.sqrt(Math.random()) * 46;
    const x = 50 + Math.cos(angle) * radius;
    const y = 50 + Math.sin(angle) * radius;
    particle.style.setProperty("--x", `${x}%`);
    particle.style.setProperty("--y", `${y}%`);
    particle.style.setProperty("--size", `${1 + Math.random() * 2.5}px`);
    particle.style.setProperty("--alpha", String(.18 + Math.random() * .72));
    particle.style.setProperty("--speed", `${1.6 + Math.random() * 4.8}s`);
    particle.style.setProperty("--delay", `${-Math.random() * 5}s`);
    particle.style.setProperty("--dx", `${-7 + Math.random() * 14}px`);
    particle.style.setProperty("--dy", `${-7 + Math.random() * 14}px`);
    fragment.append(particle);
  }
  dom.orbParticles.append(fragment);

  for (let index = 0; index < 22; index += 1) {
    const bar = document.createElement("i");
    dom.waveform.append(bar);
  }
}

function animateWaveform() {
  const active = app.state === "SPEAKING" || app.recognizing;
  [...dom.waveform.children].forEach((bar, index) => {
    const wave = active ? 3 + Math.abs(Math.sin(Date.now() / 120 + index * .63)) * (app.state === "SPEAKING" ? 13 : 9) : 3;
    bar.style.height = `${wave}px`;
  });
  requestAnimationFrame(animateWaveform);
}

function setupCanvas() {
  const canvas = dom.fxCanvas;
  const context = canvas.getContext("2d");
  const points = Array.from({ length: 34 }, () => ({
    x: Math.random(), y: Math.random(), size: .4 + Math.random() * 1.2,
    vx: (-.5 + Math.random()) * .00009, vy: (-.5 + Math.random()) * .00009,
    alpha: .08 + Math.random() * .3,
  }));
  function resize() {
    const scale = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(innerWidth * scale);
    canvas.height = Math.floor(innerHeight * scale);
    context.setTransform(scale, 0, 0, scale, 0, 0);
  }
  function draw() {
    context.clearRect(0, 0, innerWidth, innerHeight);
    const color = STATE_COLORS[app.state]?.rgb || STATE_COLORS.LISTENING.rgb;
    points.forEach((point) => {
      point.x = (point.x + point.vx + 1) % 1;
      point.y = (point.y + point.vy + 1) % 1;
      context.fillStyle = `rgba(${color}, ${point.alpha})`;
      context.beginPath();
      context.arc(point.x * innerWidth, point.y * innerHeight, point.size, 0, Math.PI * 2);
      context.fill();
    });
    requestAnimationFrame(draw);
  }
  resize();
  addEventListener("resize", resize, { passive: true });
  draw();
}

function renderSavedHistory() {
  app.history.slice(-12).forEach((entry) => addMessage(entry.role, entry.text, false));
}

function bindEvents() {
  dom.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    submitPrompt(dom.promptInput.value);
  });
  dom.promptInput.addEventListener("input", resizeTextarea);
  dom.promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      dom.composer.requestSubmit();
    }
  });
  dom.orb.addEventListener("click", toggleRecognition);
  dom.orb.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") toggleRecognition();
  });
  dom.micButton.addEventListener("click", toggleRecognition);
  dom.pauseButton.addEventListener("click", togglePause);
  dom.sfxButton.addEventListener("click", () => {
    unlockAudio();
    app.settings.sfx = !app.settings.sfx;
    applySettings();
    showToast(app.settings.sfx ? "Sistem sesleri açık." : "Sistem sesleri kapalı.");
  });
  dom.installButton.addEventListener("click", installPWA);
  dom.cameraButton.addEventListener("click", openCamera);
  dom.captureButton.addEventListener("click", captureCamera);
  dom.closeCameraButton.addEventListener("click", closeCamera);
  dom.weatherRefresh.addEventListener("click", async () => {
    dom.weatherText.textContent = "GÜNCELLENİYOR";
    try { await getWeather(app.location ? "" : "İstanbul", true); } catch { dom.weatherText.textContent = "VERİ ALINAMADI"; }
  });
  dom.locationButton.addEventListener("click", () => startLocationWatch(false));
  dom.settingsButton.addEventListener("click", openSettings);
  dom.closeSettings.addEventListener("click", closeSettings);
  dom.sheetBackdrop.addEventListener("click", closeSettings);
  dom.toggleKey.addEventListener("click", () => {
    const showing = dom.apiKeyInput.type === "text";
    dom.apiKeyInput.type = showing ? "password" : "text";
    dom.toggleKey.textContent = showing ? "GÖSTER" : "GİZLE";
  });
  dom.autoSpeakToggle.addEventListener("click", () => toggleSwitch(dom.autoSpeakToggle, "autoSpeak"));
  dom.effectsToggle.addEventListener("click", () => toggleSwitch(dom.effectsToggle, "effects"));
  dom.saveSettingsButton.addEventListener("click", saveSettings);
  dom.clearMemoryButton.addEventListener("click", () => {
    app.memory = {};
    writeJSON(STORAGE.memory, app.memory);
    updateDeviceInfo();
    showToast("Yerel hafıza temizlendi.");
  });
  dom.clearConversation.addEventListener("click", () => {
    app.history = [];
    localStorage.removeItem(STORAGE.history);
    [...dom.messages.querySelectorAll(".message")].forEach((message) => message.remove());
    addMessage("system", "Konuşma günlüğü temizlendi.");
  });
  addEventListener("online", updateNetwork);
  addEventListener("offline", updateNetwork);
  addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    app.deferredInstallPrompt = event;
    dom.installButton.classList.add("active");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      app.recognition?.abort();
      stopAudioCapture({ discard: true });
      stopCameraStream();
    }
  });
}

async function init() {
  buildOrbParticles();
  setupCanvas();
  animateWaveform();
  bindEvents();
  setupRecognition();
  renderSavedHistory();
  applySettings();
  updateClock();
  setInterval(updateClock, 1000);
  updateDeviceInfo();
  if (app.settings.locationEnabled) {
    startLocationWatch(true);
  } else {
    getWeather("İstanbul", true).catch(() => { dom.weatherText.textContent = "VERİ ALINAMADI"; });
  }

  if ("speechSynthesis" in window) {
    loadVoices();
    window.speechSynthesis.onvoiceschanged = loadVoices;
  }
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }

  if (app.apiKey) {
    setState("LISTENING", "Mobil çekirdek hazır");
    addMessage("system", "Gemini anahtarı bu cihazda bulundu. ASİSTAN hazır.");
  } else {
    setState("INITIALISING", "API anahtarı bekleniyor");
    setTimeout(openSettings, 650);
  }
}

init();
