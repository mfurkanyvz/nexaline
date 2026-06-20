const { app, BrowserWindow, Notification, ipcMain, shell } = require("electron");
const path = require("path");
const fs = require("fs");

app.setAppUserModelId("com.nexaline.desktop");

function readPackagedUrl() {
  try {
    const configPath = path.join(__dirname, "assets", "app-url.json");
    const raw = fs.readFileSync(configPath, "utf8").replace(/^\uFEFF/, "");
    const config = JSON.parse(raw);
    return config.url;
  } catch (_error) {
    return null;
  }
}

const FALLBACK_URLS = [
  process.env.NEXALINE_URL,
  readPackagedUrl(),
  "https://nidar.com.tr"
].filter(Boolean).filter((url, index, urls) => urls.indexOf(url) === index);

function createWindow() {
  let urlIndex = 0;
  const win = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 360,
    minHeight: 620,
    title: "Nidar",
    icon: path.join(__dirname, "assets", "icon.png"),
    backgroundColor: "#10141d",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: true,
      backgroundThrottling: false
    }
  });

  win.webContents.session.setPermissionRequestHandler((_webContents, permission, callback) => {
    callback(["media", "geolocation", "notifications"].includes(permission));
  });
  win.webContents.session.setPermissionCheckHandler((_webContents, permission) => {
    return ["media", "geolocation", "notifications"].includes(permission);
  });

  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  win.webContents.on("render-process-gone", () => {
    win.loadURL(FALLBACK_URLS[urlIndex] || "https://nidar.com.tr");
  });

  win.on("focus", () => {
    win.webContents.send("native-resume");
  });

  win.webContents.on("did-fail-load", (_event, _code, _description, validatedURL, isMainFrame) => {
    if (!isMainFrame || urlIndex >= FALLBACK_URLS.length - 1) {
      return;
    }
    if (validatedURL && validatedURL !== FALLBACK_URLS[urlIndex]) {
      return;
    }
    urlIndex += 1;
    win.loadURL(FALLBACK_URLS[urlIndex]);
  });

  win.loadURL(FALLBACK_URLS[urlIndex] || "https://nidar.com.tr");
}

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

ipcMain.handle("notify", (event, { title, body, tag }) => {
  if (Notification.isSupported()) {
    const notification = new Notification({
      title: title || "Nidar",
      body: body || "",
      tag: tag || "nexaline",
      silent: false,
      timeoutType: tag && String(tag).startsWith("call-") ? "never" : "default"
    });
    notification.on("click", () => {
      const win = BrowserWindow.fromWebContents(event.sender);
      if (win) {
        if (win.isMinimized()) win.restore();
        win.focus();
      }
    });
    notification.show();
  }
});
