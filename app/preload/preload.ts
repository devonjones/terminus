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
  renameSite: (slug, name) => ipcRenderer.invoke("site:rename", slug, name),
  deleteSite: (slug) => ipcRenderer.invoke("site:delete", slug),
  setSpin: (deg) => ipcRenderer.invoke("site:spin", deg),
  horizon: () => ipcRenderer.invoke("site:horizon"),
  disc: () => ipcRenderer.invoke("site:disc"),
  image: (name) => ipcRenderer.invoke("site:image", name),
  frames: () => ipcRenderer.invoke("site:frames"),
  frameImage: (kind, name) => ipcRenderer.invoke("site:frame-image", kind, name),
  buildImage: (kind, layer) => ipcRenderer.invoke("site:build-image", kind, layer),
  curate: (off, restitch) => ipcRenderer.invoke("site:curate", off, restitch),
  discoverScopes: () => ipcRenderer.invoke("scope:discover"),
  connectScope: (host) => ipcRenderer.invoke("scope:connect", host),
  scopeStatus: () => ipcRenderer.invoke("scope:status"),
  parkScope: () => ipcRenderer.invoke("scope:park"),
  disconnectScope: () => ipcRenderer.invoke("scope:disconnect"),
  pointScope: (az, alt) => ipcRenderer.invoke("scope:point", az, alt),
  takeFrame: (exposureMs) => ipcRenderer.invoke("scope:frame", exposureMs),
  framePreview: () => ipcRenderer.invoke("scope:frame-preview"),
  columns: () => ipcRenderer.invoke("site:columns"),
  editColumn: (az, change) => ipcRenderer.invoke("site:column-edit", az, change),
  fitColumns: () => ipcRenderer.invoke("site:columns-fit"),
  columnFrame: (az, name) => ipcRenderer.invoke("site:column-frame", az, name),
};
contextBridge.exposeInMainWorld("terminus", api);
