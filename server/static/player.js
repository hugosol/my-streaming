var v = document.getElementById('v');
var k = 'ms-pos-' + v.dataset.videoId;
var t = parseFloat(localStorage.getItem(k) || 0);
if (t > 1) v.currentTime = t;
setInterval(function() { try { if (v.currentTime > 0) localStorage.setItem(k, v.currentTime); } catch(e) {} }, 5000);
v.addEventListener('pause', function() { try { localStorage.setItem(k, v.currentTime); } catch(e) {} });
v.addEventListener('seeked', function() { try { localStorage.setItem(k, v.currentTime); } catch(e) {} });
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
  pc.style.width = window.innerHeight + 'px';
  pc.style.height = window.innerWidth + 'px';
  document.body.classList.add('custom-fullscreen');
  try { screen.orientation.lock('landscape'); } catch(e) {}
}
function exitFS() {
  var pc = document.getElementById('player-container');
  pc.style.width = '';
  pc.style.height = '';
  document.body.classList.remove('custom-fullscreen');
  try { screen.orientation.unlock(); } catch(e) {}
}
fsBtn.addEventListener('click', function(e) { e.stopPropagation(); enterFS(); });
fsExitBtn.addEventListener('click', function(e) { e.stopPropagation(); exitFS(); });
window.addEventListener('orientationchange', function() {
  if (document.body.classList.contains('custom-fullscreen')) exitFS();
});
