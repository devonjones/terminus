import { contextBridge, ipcRenderer, webUtils } from "electron";
import type { TerminusApi } from "../src/api/types";

const api: TerminusApi = {
  getState: () => ipcRenderer.invoke("state:get"),
  setTab: (tab) => ipcRenderer.invoke("state:tab", tab),
  listSites: () => ipcRenderer.invoke("sites:list"),
  openSite: (slug) => ipcRenderer.invoke("site:open", slug),
  pickPhotos: () => ipcRenderer.invoke("site:pick"),
  // A File from a drop carries its path only through webUtils, here in the preload.
  createSite: (files) =>
    ipcRenderer.invoke(
      "site:create",
      files.map((f) => webUtils.getPathForFile(f)),
    ),
  setSpin: (deg) => ipcRenderer.invoke("site:spin", deg),
  horizon: () => ipcRenderer.invoke("site:horizon"),
  disc: () => ipcRenderer.invoke("site:disc"),
  image: (name) => ipcRenderer.invoke("site:image", name),
};
contextBridge.exposeInMainWorld("terminus", api);
