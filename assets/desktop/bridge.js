"use strict";

// The Windows EXE exchanges messages over WebView2 and Python's standard pipes.
// No browser, HTTP server or Python resize callback is involved.
if (window.chrome && window.chrome.webview) {
  const pending = new Map();
  let sequence = 0;
  const api = {};
  for (const method of ["get_state", "refresh_devices", "select_device", "set_device_note", "update_config", "start", "stop", "retry_task", "open_screenshot", "check_updates", "download_update", "cancel_update_download", "install_update"]) {
    api[method] = (...args) => new Promise((resolve, reject) => {
      const id = ++sequence;
      const timeout = setTimeout(() => {
        pending.delete(id);
        reject(new Error("Python 后台响应超时，请重新打开程序"));
      }, 15000);
      pending.set(id, {resolve, reject, timeout});
      window.chrome.webview.postMessage({id, method, args});
    });
  }
  window.chrome.webview.addEventListener("message", event => {
    const response = event.data;
    if (response.fatal) {
      for (const request of pending.values()) {
        clearTimeout(request.timeout);
        request.reject(new Error(response.fatal));
      }
      pending.clear();
      window.dispatchEvent(new CustomEvent("gamebot-backend-error", {detail: response.fatal}));
      return;
    }
    const request = pending.get(response.id);
    if (!request) return;
    pending.delete(response.id);
    clearTimeout(request.timeout);
    if (response.error) request.reject(new Error(response.error));
    else request.resolve(response.result);
  });
  window.pywebview = {api};
}
