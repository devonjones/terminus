import { contextBridge, ipcRenderer } from "electron";
import type { TerminusApi } from "../src/api/types";

const api: TerminusApi = {
  getState: () => ipcRenderer.invoke("state:get"),
  setTab: (tab) => ipcRenderer.invoke("state:tab", tab),
};
contextBridge.exposeInMainWorld("terminus", api);
