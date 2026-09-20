var v = document.getElementById('v');
var trackEl = null;
var subUrl = v.dataset.subtitleUrl;
if (subUrl) {
  v.querySelectorAll('track').forEach(function(t) { t.remove(); });
  trackEl = document.createElement('track');
  trackEl.kind = 'subtitles';
  trackEl.src = subUrl + '?v=' + Date.now();
  trackEl.srclang = 'en';
  trackEl.label = 'Subtitles';
  trackEl.default = true;
  v.appendChild(trackEl);
}
if (trackEl) {
  v.addEventListener('loadedmetadata', function() {
    trackEl.track.mode = 'showing';
  });
}

var k = 'ms-pos-' + v.dataset.videoId;
var startAt = parseFloat(localStorage.getItem(k) || 0);
var src = v.dataset.playlistUrl + '?v=' + Date.now();
if (startAt > 1) src += '&start=' + startAt;
v.src = src;
setInterval(function() { try { if (v.currentTime > 0) localStorage.setItem(k, v.currentTime); } catch(e) {} }, 5000);
v.addEventListener('pause', function() { try { localStorage.setItem(k, v.currentTime); } catch(e) {} });
v.addEventListener('seeked', function() {
  try { localStorage.setItem(k, v.currentTime); } catch(e) {}
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
  holdEnd(); // 观看模式切换一开始就结束本次长按，不让手势跨过 200 毫秒的切换动画
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
  holdEnd(); // 同 enterFS：旋转/退出全屏同样结束本次长按
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

// 长按临时倍速：正在播放时按住画面非控件区域满「配置门槛」临时切到固定 2×，
// 松手立即恢复长按前的实际速度；暂停时不触发，也不开始播放。
// 按住期间的滑动不影响手势：任意方向、任意距离的位移都不结束等待或加速（2026-09-20 用户决定）。
// 结束只有两条路：松手，或中断（切到后台 / 进入或退出自制全屏 / 系统取消触摸 / 第二根手指落下）。
// 唯一的结束出口是 holdEnd。
// 监听挂在视频元素上：自制全屏按钮等控件不是它的子节点，落在控件上的触摸不会进入本手势；
// 全程不调用 preventDefault，原生控件与短按行为照旧。
// 门槛来自服务端注入的配置（config.json → player.hold_ms，默认 500）；
// 缺失或非法时回退默认值，并夹取到合理范围，避免配置笔误让手势失控。
function holdMsFromConfig() {
  var raw = parseFloat(v.dataset.holdMs);
  if (!isFinite(raw)) return 500;
  return Math.min(1500, Math.max(200, raw));
}
var HOLD_MS = holdMsFromConfig();
var HOLD_RATE = 2;

var holdGesture = null;

function holdIsPlaying() { return !v.paused && !v.ended; }

function holdBegin() {
  if (holdGesture) return;
  // 判定基础在按下瞬间固定：当时的速度与是否在播放（位移不参与判定）。
  var gesture = { rate: v.playbackRate, playing: holdIsPlaying(), triggered: false };
  holdGesture = gesture;
  gesture.timer = setTimeout(function() {
    if (!gesture.playing || !holdIsPlaying()) return; // 门槛点仍须正在播放
    gesture.triggered = true;
    v.playbackRate = HOLD_RATE;
  }, HOLD_MS);
}

function holdEnd() {
  if (!holdGesture) return;
  var gesture = holdGesture;
  holdGesture = null;
  clearTimeout(gesture.timer);
  if (gesture.triggered) v.playbackRate = gesture.rate;
}

v.addEventListener('touchstart', function(e) {
  if (e.touches.length !== 1) { holdEnd(); return; } // 多指或异常序列不属于本手势
  holdBegin();
});
// 这里没有 touchmove 监听：手指滑动不参与手势判定，位移既不取消等待也不结束加速（见上方说明）。
v.addEventListener('touchend', function(e) {
  if (e.touches.length === 0) holdEnd();
});
v.addEventListener('touchcancel', function() { holdEnd(); });
// 切到后台：结束本次长按（等待期取消等待，加速期恢复原速）；回到前台不会复活旧手势。
document.addEventListener('visibilitychange', function() { if (document.hidden) holdEnd(); });
