// Tells the server the viewer's UTC offset (in minutes) so matches are grouped by
// the viewer's own calendar day. Stored in a cookie called "tz".
(function () {
  try {
    var offset = String(-new Date().getTimezoneOffset());
    var match = document.cookie.match(/(?:^|;\s*)tz=(-?\d+)/);
    if (match && match[1] === offset) return;
    document.cookie = "tz=" + offset + "; path=/; max-age=31536000; SameSite=Lax";
    var saved = /(?:^|;\s*)tz=/.test(document.cookie);
    // First visit (or the viewer travelled): reload once so the page uses the right day.
    if (saved && document.currentScript && document.currentScript.dataset.reload) {
      window.location.reload();
    }
  } catch (e) {}
})();