// TaskbarLyrics - Service Worker
// Content script connects directly to 127.0.0.1:5678 for lowest latency.
chrome.runtime.onInstalled.addListener(() => {
  console.log('[TaskbarLyrics] Extension installed/updated.');
});

