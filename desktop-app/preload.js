const { contextBridge, ipcRenderer } = require("electron");

const nativeBridge = {
  notify(title, body, tag) {
    ipcRenderer.invoke("notify", { title, body, tag });
  },
  canNotify() {
    return true;
  }
};

contextBridge.exposeInMainWorld("NidarNative", nativeBridge);
contextBridge.exposeInMainWorld("NexaLineNative", nativeBridge);

ipcRenderer.on("native-resume", () => {
  window.dispatchEvent(new Event("nexaline:resume"));
});
