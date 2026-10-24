'use strict';
// Runs only in the official login document; no native bridge or credential hooks.
(() => {
  if (location.origin !== 'https://accounts.taptap.cn' || location.pathname !== '/login') return;
  if (window.__starfallTapApp) return;
  let challenge = null, expires = 0, requested = false, expired = false;
  const endpoint = value => {
    try { const u = new URL(value, location.href); return u.origin === location.origin && u.pathname === '/api/device-code/get'; }
    catch { return false; }
  };
  const capture = value => {
    const data = value?.data?.data || value?.data || value;
    if (typeof data?.qrcode_url !== 'string' || data.qrcode_url.length > 2048) return;
    challenge = data.qrcode_url;
    expires = Date.now() + Math.min(110000, Number.isFinite(data.expires_in) && data.expires_in > 0 ? data.expires_in * 1000 : 110000);
    expired = false;
  };
  const fetch = window.fetch;
  if (fetch) window.fetch = function(input, ...args) {
    return fetch.call(this, input, ...args).then(response => {
      if (response.ok && endpoint(typeof input === 'string' ? input : input.url)) response.clone().json().then(capture).catch(() => {});
      return response;
    });
  };
  const XHR = window.XMLHttpRequest;
  if (XHR) {
    const open = XHR.prototype.open;
    XHR.prototype.open = function(method, url, ...args) {
      this.addEventListener('load', () => {
        if (!endpoint(url) || this.status < 200 || this.status >= 300) return;
        try {
          if (this.responseType === 'json') capture(this.response);
          else if ((!this.responseType || this.responseType === 'text') && this.responseText.length <= 16384) capture(JSON.parse(this.responseText));
        } catch {}
      }, {once:true});
      return open.call(this, method, url, ...args);
    };
  }
  const read = () => {
    if (location.origin !== 'https://accounts.taptap.cn' || location.pathname !== '/login') { challenge=null; return {phase:'authorizing'}; }
    if (Date.now() >= expires && challenge) { challenge = null; expired = true; }
    return challenge ? {phase:'ready', qrcode_url:challenge} : {phase:expired ? 'expired' : 'waiting'};
  };
  window.__starfallTapApp = {
    read,
    reset() { challenge=null; requested=false; expired=false; },
    request() {
      if (!requested) {
        const icon = document.querySelector('.login-page__content__login-mode-icon');
        if (icon) {
          requested = true;
          if (document.querySelector('.qrcode-login')) {
            // Hook may have been installed after the first response: request a fresh QR.
            icon.click();
            setTimeout(() => document.querySelector('.login-page__content__login-mode-icon')?.click(), 100);
          } else icon.click();
        }
      }
      return read();
    }
  };
})();
