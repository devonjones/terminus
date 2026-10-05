import { mount } from "./renderer";

mount(document.getElementById("app")!, window.terminus).catch((e: Error) => {
  document.body.textContent = `terminus could not load its state: ${e.message}`;
});
