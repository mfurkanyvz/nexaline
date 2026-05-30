const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("NexaLineNative", {
  notify(title, body, tag) {
    ipcRenderer.invoke("notify", { title, body, tag });
  },
  canNotify() {
    return true;
  }
});

ipcRenderer.on("native-resume", () => {
  window.dispatchEvent(new Event("nexaline:resume"));
});
