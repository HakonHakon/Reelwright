"use strict";
let installPrompt = null;
window.addEventListener("beforeinstallprompt", event => { event.preventDefault(); installPrompt = event; });
window.addEventListener("appinstalled", () => { installPrompt = null; toast("Reelwright installed."); });
$("#mobile-search").onclick = () => openPalette(true);
$("#mobile-jobs").onclick = () => openJobs(true);
const onlineStatus = () => { $("#network-status").hidden = navigator.onLine; };
window.addEventListener("online", onlineStatus); window.addEventListener("offline", onlineStatus); onlineStatus();
$("#install-app").onclick = async () => {
  if (installPrompt) { await installPrompt.prompt(); installPrompt = null; return; }
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  let mobile = {}; try { mobile = await GET("/api/mobile"); } catch { /* installation help still works offline */ }
  modal(`<h2 style="margin-top:0">${standalone ? "Reelwright is installed" : "Install Reelwright"}</h2>
    <p>Run Reelwright from your home screen. Your PC must stay on and connected to the same network.</p>
    ${!window.isSecureContext && mobile.https_url ? `<p>For the full app experience, first set up the secure local connection.</p>
      <ol><li><a href="/local-ca.crt">Download this PC’s local certificate</a>.</li>
      <li>On iPhone: install the downloaded profile in Settings → General → VPN &amp; Device Management, then enable it in General → About → Certificate Trust Settings.</li>
      <li>On Android: install it as a CA certificate in Settings → Security → Encryption &amp; credentials (the exact names vary by phone).</li>
      <li>Open <a href="${esc(mobile.https_url)}">${esc(mobile.https_url)}</a> and follow the steps below.</li></ol>` : ""}
    <p><b>iPhone / iPad:</b> open in Safari, tap Share, then Add to Home Screen. Enable Open as Web App if shown.</p>
    <p><b>Android:</b> open in Chrome, then use its menu → Install app or Add to Home screen.</p>
    <button class="primary" id="install-close">Done</button>`);
  $("#install-close").onclick = closeModal;
};
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {
    // The live studio remains usable if the offline shell cannot be installed.
  }));
}
