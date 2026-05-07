var v = document.getElementById('v');
var k = 'ms-pos-' + v.dataset.videoId;
var t = parseFloat(localStorage.getItem(k) || 0);
if (t > 1) v.currentTime = t;
setInterval(function() { try { if (v.currentTime > 0) localStorage.setItem(k, v.currentTime); } catch(e) {} }, 5000);
v.addEventListener('pause', function() { try { localStorage.setItem(k, v.currentTime); } catch(e) {} });
var snapBusy = false;
v.addEventListener('seeked', function() {
  try { localStorage.setItem(k, v.currentTime); } catch(e) {}
  if (snapBusy) { snapBusy = false; return; }
  var snap = Math.round(v.currentTime / 10) * 10;
  if (snap > v.duration - 0.5) snap = Math.max(0, v.duration - 0.5);
  if (snap < 0) snap = 0;
  if (Math.abs(v.currentTime - snap) > 0.5) {
    snapBusy = true;
    v.currentTime = snap;
  }
});
v.addEventListener('ended', function() { try { localStorage.removeItem(k); } catch(e) {} });
function lockLandscape() { screen.orientation.lock('landscape').catch(function() {}); }
function unlockOrientation() { try { screen.orientation.unlock(); } catch(e) {} }
document.addEventListener('fullscreenchange', function() { if (document.fullscreenElement) lockLandscape(); else unlockOrientation(); });
document.addEventListener('webkitfullscreenchange', function() { if (document.webkitFullscreenElement) lockLandscape(); else unlockOrientation(); });
v.addEventListener('webkitbeginfullscreen', lockLandscape);
v.addEventListener('webkitendfullscreen', unlockOrientation);

var fsBtn = document.getElementById('fs-btn');
var fsExitBtn = document.getElementById('fs-exit-btn');
function enterFS() {
  var pc = document.getElementById('player-container');
  var fadeOut = pc.animate({ opacity: [1, 0] }, { duration: 200, fill: 'forwards' });
  fadeOut.onfinish = function() {
    pc.style.position = 'fixed';
    pc.style.zIndex = '9999';
    pc.style.top = '0';
    pc.style.left = '100vw';
    pc.style.width = window.innerHeight + 'px';
    pc.style.height = window.innerWidth + 'px';
    document.body.classList.add('custom-fullscreen');
    pc.animate({ opacity: [0, 1] }, { duration: 200, fill: 'forwards' });
    try { screen.orientation.lock('landscape'); } catch(e) {}
  };
}
function exitFS() {
  var pc = document.getElementById('player-container');
  var fadeOut = pc.animate({ opacity: [1, 0] }, { duration: 200, fill: 'forwards' });
  fadeOut.onfinish = function() {
    document.body.classList.remove('custom-fullscreen');
    pc.style.position = '';
    pc.style.zIndex = '';
    pc.style.top = '';
    pc.style.left = '';
    pc.style.width = '';
    pc.style.height = '';
    pc.animate({ opacity: [0, 1] }, { duration: 200, fill: 'forwards' });
    try { screen.orientation.unlock(); } catch(e) {}
  };
}
fsBtn.addEventListener('click', function(e) { e.stopPropagation(); enterFS(); });
fsExitBtn.addEventListener('click', function(e) { e.stopPropagation(); exitFS(); });
window.addEventListener('orientationchange', function() {
  if (document.body.classList.contains('custom-fullscreen')) exitFS();
});
