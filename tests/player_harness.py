"""浏览器验收骨架：真实页面 + 真实视频 + 真实触摸 + 受控时间（票据 01）。

本模块只提供夹具与观察能力，**不实现、不预埋任何长按临时倍速行为**，也不改动播放器
任何既有行为：页面由真实模板渲染，播放源换成夹具视频（线上是 HLS，桌面浏览器不一定能播）。

## 后续票据（02/03/04）怎么用

```python
from player_harness import (  # 夹具必须显式导入到测试模块才可见
    harness, harness_factory, playing_session, playwright_instance, session,
)

pytestmark = pytest.mark.parametrize("engine", ("chromium", "webkit"))


def test_long_press_reaches_2x(session):
    session.goto(clock=True)      # True = 导航前安装假时钟（避免加载期计时器被冻结卡住）
    session.start_playback()      # 夹具视频真的在解码播放（currentTime 推进）
    session.clock.freeze()        # 冻结当前时刻：之后的计时器只能靠 advance/run_for 触发

    gesture = session.touch()                                 # 一次按压序列（见下）
    gesture.down(*session.point_in_video(y_frac=0.3))         # 按在画面非控件区域
    session.clock.advance(499)                                # 受控时间推进 499 毫秒
    assert session.read().playback_rate == 1.0                # 门槛前
    session.clock.advance(1)                                   # 满 500 毫秒
    assert session.read().playback_rate == 2.0                # 门槛后
    gesture.up()
    assert session.read().playback_rate == 1.0                # 松手恢复
```

公共助手（`PlayerSession`，以及它 `touch()` 返回的 `TouchGesture`）：

| 助手 | 作用 |
|---|---|
| `goto(clock=False)` | 加载真实 `/play/<id>` 页面（真实模板 + 真实 `player.js`/`player.css`） |
| `read()` | 公开可观察状态：`paused` / `playback_rate` / `current_time` / `ended` / `ready_state` / `custom_fullscreen` / 控件显隐 |
| `touch()` | 开始一次按压序列：`down(x, y)` → `move_to(x, y)` / `move_by(dx, dy)`（相对**按下点**）→ `up()` / `cancel()`；`with` 退出时自动抬起 |
| `move_exact_to(x, y)` / `move_exact_by(dx, dy)` | 同上，但移动**总是**走页面内合成 `TouchEvent`（精确坐标、不受引擎触摸 slop 影响、`isTrusted=false` 弱证据）——用于 chromium 上无法用 CDP 构造的 <16 像素移动 |
| `tap(x, y)` | 一次可信真实 tap（Playwright touchscreen，两个引擎都是引擎级输入） |
| `point_in_video(x_frac, y_frac)` | 画面内一点（默认 `y_frac=0.3` 避开底部原生控件条），两种观看模式下都落在视频上 |
| `hit_id(x, y)` | 该点命中的元素 id，用来确认按压落在视频上而不是控件上 |
| `video_box()` | video 元素矩形（自制横屏下是旋转后的外接矩形） |
| `enter_custom_fullscreen()` / `exit_custom_fullscreen()` | 用页面现有 `#fs-btn` / `#fs-exit-btn` 真实点击进出，并等 `body.custom-fullscreen` |
| `touch_in_custom_fullscreen()` | 进入自制横屏全屏后开始一次按压 |
| `clock.install()` / `clock.freeze()` / `clock.advance(ms)` / `clock.run_for(ms)` | 受控时间 |
| `start_playback()` | 程序化 `video.play()`（公开 API，不是输入手势）+ 等 `currentTime` 推进 |
| `wait_until_playing()` / `wait_for_progress(min_advance)` / `wait_for(predicate, what)` | Python 侧轮询等待（不受冻结的假时钟影响） |
| `tap_play_control()` | 真实鼠标点原生控件条播放按钮（仅 chromium，位置实测） |
| `tap_picture()` | 真实 tap 画面并等 `paused` 翻转（webkit 实测稳定；chromium 不切换） |

`PlayerHarness` 还有：`session()`、`render_player_page(id)`、`source`（选中的夹具视频与探测结论）、
`notes`（实测记录：启动方式、播放源探测、控件点击位置）、`media_dir`、`base_url`。

## 证据强度（必须如实标注）

每次按压的 `TouchGesture.evidence` 给出引擎、机制与 `trusted_input`：

- chromium：CDP `Input.dispatchTouchEvent`（touchStart/touchMove/touchEnd/touchCancel）= **引擎级真实触摸**；
- webkit：Playwright 没有等价的按压通道（协议只暴露 `touchscreen.tap`），按压序列是页面内
  合成 `TouchEvent`（`document.createTouch` + `createTouchList`，`isTrusted === false`）= **弱证据**，
  只能证明逻辑，不构成触摸平台结论；webkit 的可信输入只有 `tap()`（真实 tap）。

## 实测边界（这些事实决定了上面助手的适用范围）

- **chromium 丢弃首个小于 16 CSS 像素的 touchmove**：4/8/12/14 像素的移动不会到达页面，
  ≥16 像素才送达（后续小位移正常送达）。做「12 像素阈值」类用例时必须考虑这一点。
  票据 03 复测（CDP 真实触摸，在 (400, 203.8) 处按下）：2/4/8/12/14 像素 → 页面收到 0 个
  touchmove；16/20/40 像素 → 各 1 个，`isTrusted=true` 且 `clientX` 精确等于派发值。因此
  chromium 上**可信输入只能构造「越过 12 像素门槛」的移动**，门槛内侧与恰好 12 的移动必须用
  `move_exact_*`（页面内合成，弱证据）——这决定了 03 的用例结构，不能把「事件没到达」当绿灯。
- **引擎会量化合成触摸坐标**：chromium 用 `new Touch(...)` 构造时坐标被压到 float32
  （实测按下点 y=203.79999999999998 → 合成触点读回 203.80000305175781，于是「12 像素」的
  位移在页面上变成 12.000000000000389）；webkit 的 `document.createTouch` 不做量化
  （派发值原样读回）。所以「恰好 12 CSS 像素」这类边界用例必须选能被量化无损表示的按压点与
  目标点，并断言 `evidence.events` 里**读回**的真实位移，而不是断言打算派发的位移。
- **webkit 无法用真实输入做按压序列**：`new Touch(...)` 不存在；`new TouchEvent(type, {touches: []})`
  会抛 `TypeError`，必须传 `document.createTouchList(...)`；`document.createTouch` 是可用路径。
  合成事件必须用小写 DOM 事件名（`touchstart`，不是 CDP 的 `touchStart`），坐标必须以 `{x, y}`
  对象传入——这两条都踩过坑，见 `test_press_sequence_reaches_the_page_as_dom_touch_events`。
- **抬起的触点是最后位置**（两个引擎一致）：页面在 `touchend`/`touchcancel` 收到的是手指最后所在
  位置，不是按下位置。想测「松手时的位移」要按这个前提写。**但混用通道时以各通道自己记的位置为准**
  （票据 01 复核实测）：chromium 上「CDP 按下 + 合成移动」时合成移动不更新浏览器内部触点状态，
  随后的 CDP `touchend` 报的是 CDP 侧最后位置（= 按下点），纯合成路径的 `touchend` 才报最后移动位置。
  所以断言松手瞬间的坐标时必须按引擎与通道区分，别让它在 chromium 上空洞通过。
- **webkit 点击画面会切换播放/暂停**（3/3 稳定），chromium 的 touch tap 不会，
  但两个引擎的鼠标点击画面都会切换——写「短按保持原行为」类用例时要按引擎区分。
- 受控时间：假时钟冻结时页面 `setTimeout`/`setInterval`/`requestAnimationFrame` 不再自行触发
  （`performance`、`Date` 也被替换），但**媒体时钟仍由真实时间推进**：实测连续 30 轮
  `advance(500)`（假时间 +15s）后 `currentTime` 只动 +0.01~0.04，单次 `advance(600000)`
  与 `fast_forward("30:00")` 同样不推进媒体位置。Web Animations（`enterFS` 的 200ms 淡入）
  照常完成；Python 侧轮询等待不受影响，所以 `wait_for*` 用真实等待，与 500 毫秒门槛无关。
- **夹具视频 30 秒**：媒体位置靠真实时间推进，用例跑久了会把短片播到结尾，届时
  Chromium 会 `paused=true` / `ended=true` 打断后续断言，所以留足余量。

观察口径：`PlayerSession.read()` 只读取 Video 元素公开的 `paused` / `playbackRate` /
`currentTime` / `ended` / `readyState` 与可见 DOM（`body.custom-fullscreen`、控件按钮显隐）。
不读取任何实现私有变量、内部计时器或私有状态字段；页面里不注入任何业务逻辑。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterator, Sequence
from urllib.parse import unquote, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import app as server_app  # noqa: E402  (真实模板、真实渲染函数、真实静态目录)

#: 引擎列表。两种引擎都用真实浏览器；WebKit 的按压序列只能是合成事件（弱证据），见 TouchGesture。
PLAYWRIGHT_ENGINES = ("chromium", "webkit")

VIEWPORT = {"width": 800, "height": 600}

#: 原生控件条上播放/暂停按钮的**实测**位置，相对 video 矩形左下角（CSS 像素）。
#: 实测方式：800x600 视口下扫控件条，只有这些偏移能让 `paused` 翻转。
#: 只登记实测可稳定驱动的引擎；未登记的引擎请用 `PlayerSession.tap_picture()`。
#:   chromium-1243: x∈{12,24,40} 且 y∈{bottom-48..bottom-32} 命中，取中心 (24, 40)
#:   webkit-2359:   控件条上的命中点不连续（x=60/90/100 命中而 70/80 时通时不通），
#:                  无法作为确定性判据，故不登记；webkit 用画面 tap 翻转 `paused`（3/3 稳定）。
#: 位置随浏览器版本漂移；用例若发现翻转失败应重新实测并更新本表，而不是放宽判定。
PLAY_CONTROL_OFFSETS = {
    "chromium": (24.0, 40.0),
}

#: 夹具视频时长。留足余量：真实播放时 `currentTime` 由真实时间推进，用例跑够长会把 8 秒级的
#: 短片播到结尾（Chromium 到片尾会 `paused=true`、`ended=true`，凭空打断后续断言）。
VIDEO_SECONDS = 30
VIDEO_SIZE = "320x180"
VIDEO_FPS = 25

#: 夹具视频候选（容器/编码），按顺序探测：第一个在当前引擎里真的能播放的被选中。
#: 播放成功判据 = `paused == false` 且 `currentTime` 在推进，不看 `canPlayType` 的声明。
VIDEO_CANDIDATES = (
    ("mp4", "h264", ("-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart")),
    ("webm", "vp8", ("-c:v", "libvpx", "-b:v", "400k", "-pix_fmt", "yuv420p")),
    ("webm", "vp9", ("-c:v", "libvpx-vp9", "-b:v", "400k", "-pix_fmt", "yuv420p")),
)

_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".js": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
}

_READ_STATE_JS = """
() => {
  const v = document.getElementById('v');
  const fs = document.getElementById('fs-btn');
  const exit = document.getElementById('fs-exit-btn');
  const shown = (el) => !!el && getComputedStyle(el).display !== 'none';
  return {
    paused: v.paused,
    playbackRate: v.playbackRate,
    currentTime: v.currentTime,
    ended: v.ended,
    readyState: v.readyState,
    customFullscreen: document.body.classList.contains('custom-fullscreen'),
    fsButtonVisible: shown(fs),
    exitButtonVisible: shown(exit),
  };
}
"""

_VIDEO_BOX_JS = """
() => {
  const r = document.getElementById('v').getBoundingClientRect();
  return {x: r.x, y: r.y, width: r.width, height: r.height};
}
"""

_START_PLAYBACK_JS = """
() => {
  const v = document.getElementById('v');
  const p = v.play();
  return p && p.then ? p.then(() => 'ok', (e) => String(e && e.name || e)) : 'ok';
}
"""


class HarnessUnavailable(RuntimeError):
    """本机缺少浏览器自动化设施；夹具整体不可用。"""


def _as_js_point(point: tuple[float, float] | None) -> dict | None:
    """把 (x, y) 元组转成页面里能按 `.x` / `.y` 取值的对象。

    不能直接把元组传给 `page.evaluate`：它到 JS 侧是数组，`point.x` 会是 `undefined`，
    合成出来的 Touch 坐标就全变成 0（这个错误曾经让 webkit 的按压坐标静默丢失）。
    """
    return None if point is None else {"x": float(point[0]), "y": float(point[1])}


# --------------------------------------------------------------------------- 证据


@dataclass(frozen=True)
class TouchEventRecord:
    """一次派发给页面的触摸事件（harness 侧记录，坐标单位是视口 CSS 像素）。

    `x`/`y` 是**页面实际收到/构造出**的坐标：走 CDP 时等于派发坐标，走页面内合成事件时是
    合成触点读回的坐标（引擎可能对合成坐标做量化，见模块 docstring「实测边界」）。

    `trusted` 为 False 表示这一个事件来自页面内合成 `TouchEvent`（`isTrusted === false`）。
    同一次按压里可以混用两种通道（例如 chromium：CDP 真实按下 + 合成精确移动），所以强度按
    事件逐个记录；`GestureEvidence.trusted_input` 只有全部事件都可信时才为 True。
    """

    kind: str  # touchstart / touchmove / touchend / touchcancel
    x: float | None = None
    y: float | None = None
    trusted: bool = True

    def __str__(self) -> str:
        where = "" if self.x is None else f"@({self.x:.1f},{self.y:.1f})"
        return f"{self.kind}{where}{'' if self.trusted else '(合成)'}"


@dataclass(frozen=True)
class GestureEvidence:
    """一次按压输入的证据强度。

    `trusted_input` 为 True 表示这次按压的**每个**事件都是引擎级真实输入
    （页面收到的事件 `isTrusted === true`）；为 False 表示至少有一个事件是页面内合成事件
    （弱证据，只能证明逻辑，不构成平台结论）。混用两种通道时按混合标注，见 `strength`。
    """

    engine: str
    mechanism: str
    trusted_input: bool
    events: tuple[TouchEventRecord, ...]

    @property
    def synthetic_events(self) -> tuple[TouchEventRecord, ...]:
        """本次按压里来自页面内合成事件的那些事件。"""
        return tuple(record for record in self.events if not record.trusted)

    @property
    def strength(self) -> str:
        if self.trusted_input:
            return "可信真实输入"
        if any(record.trusted for record in self.events):
            return "混合：引擎级真实输入 + 页面内合成事件（弱证据）"
        return "合成事件（弱证据）"


@dataclass(frozen=True)
class PlaybackState:
    """只由 Video 元素公开属性与可见 DOM 得出的观察结果。"""

    paused: bool
    playback_rate: float
    current_time: float
    ended: bool
    ready_state: int
    custom_fullscreen: bool
    fs_button_visible: bool
    exit_button_visible: bool


@dataclass(frozen=True)
class PlaybackSource:
    """夹具播放源：容器/编码 + 该引擎上的真实播放探测结果。"""

    name: str
    container: str
    codec: str
    probe_detail: str


# --------------------------------------------------------------------------- 触摸


class TouchGesture:
    """一次「按下 → 若干次移动 → 抬起/取消」的真实输入序列。

    每次方法调用立即派发一个事件，所以测试自己控制两件事之间的时间：
    受控时间（`session.clock.advance`）或真实时间。坐标是视口 CSS 像素。

    chromium：CDP `Input.dispatchTouchEvent`（touchStart/touchMove/touchEnd/touchCancel），
    页面收到的是引擎级可信事件（`isTrusted === true`）。
    webkit：Playwright 没有等价 CDP 通道，按压序列用页面内合成 `TouchEvent` 派发
    （`isTrusted === false`，弱证据）；需要可信输入时只能用 `session.tap()`（真实 tap）。

    `move_exact_*` 在**两个引擎上都**走页面内合成通道（弱证据），理由见模块「实测边界」：
    chromium 的 CDP 会丢弃首个 <16 CSS 像素的真实移动。一次按压里因此可以两种通道混用，
    `evidence` 会如实标成混合；只有全部事件都可信时 `trusted_input` 才是 True。

    用完必须 `up()`/`cancel()`；`with session.touch() as g:` 会在退出时自动抬起。
    """

    def __init__(self, session: "PlayerSession") -> None:
        self._session = session
        self._down: tuple[float, float] | None = None
        self._origin: tuple[float, float] | None = None
        self._last: tuple[float, float] | None = None
        self._events: list[str] = []
        self._synthetic_mechanism: str | None = None
        self._identifier = 1
        if session.harness.engine == "chromium":
            self._cdp = session.page.context.new_cdp_session(session.page)
        else:
            self._cdp = None

    # -- 输入 ------------------------------------------------------------

    def down(self, x: float, y: float) -> "TouchGesture":
        """按下（touchstart）。坐标是视口 CSS 像素。"""
        if self._down is not None:
            raise RuntimeError("上一个按压还没有抬起/取消")
        self._down = (float(x), float(y))
        self._origin = self._down
        self._last = self._down
        delivered, trusted = self._dispatch("touchStart", self._down)
        point = delivered or self._down
        self._events.append(TouchEventRecord("touchstart", point[0], point[1], trusted))
        return self

    def move_to(self, x: float, y: float) -> "TouchGesture":
        """移动到绝对坐标（一步 touchmove）。"""
        self._require_down()
        target = (float(x), float(y))
        self._last = target
        delivered, trusted = self._dispatch("touchMove", target)
        point = delivered or target
        self._events.append(TouchEventRecord("touchmove", point[0], point[1], trusted))
        return self

    def move_by(self, dx: float, dy: float) -> "TouchGesture":
        """相对**按下点**偏移移动（一步 touchmove），不是相对上一步。"""
        self._require_down()
        ox, oy = self._origin  # type: ignore[misc]
        return self.move_to(ox + float(dx), oy + float(dy))

    def move_exact_to(self, x: float, y: float) -> "TouchGesture":
        """移动到绝对坐标（一步 touchmove），**总是**走页面内合成事件。

        与 `move_to` 的区别：`move_to` 在 chromium 上走 CDP，而 CDP 会丢弃首个小于 16 CSS 像素的
        真实 touchmove（见模块 docstring「实测边界」），所以 12 CSS 像素门槛附近的移动在 chromium
        上根本无法用可信输入构造。本方法用页面内合成 `TouchEvent` 精确给出坐标：不受引擎触摸 slop
        影响，但事件 `isTrusted === false`，是**弱证据**。

        记录的坐标是页面实际构造出的触点（`evidence.events` 里逐事件标 `trusted=False`）：引擎对
        合成坐标的量化会如实反映在记录里，用例据此判断自己到底构造了多大的位移。
        """
        self._require_down()
        point = (float(x), float(y))
        self._last = point
        delivered = self._dispatch_synthetic("touchMove", point) or point
        self._events.append(TouchEventRecord("touchmove", delivered[0], delivered[1], trusted=False))
        return self

    def move_exact_by(self, dx: float, dy: float) -> "TouchGesture":
        """相对**按下点**偏移的精确移动（一步 touchmove），见 `move_exact_to`。"""
        self._require_down()
        ox, oy = self._origin  # type: ignore[misc]
        return self.move_exact_to(ox + float(dx), oy + float(dy))

    def up(self) -> "TouchGesture":
        """抬起（touchend）。

        页面把抬起当作触点回到**最后位置**（与 CDP 的行为一致），不是按下位置。
        """
        self._require_down()
        _, trusted = self._dispatch("touchEnd", None)
        self._events.append(TouchEventRecord("touchend", trusted=trusted))
        self._down = None
        return self

    def cancel(self) -> "TouchGesture":
        """系统取消（touchcancel）——对应切后台/系统手势一类中断。

        与抬起一样，触点位置是**最后位置**。
        """
        self._require_down()
        _, trusted = self._dispatch("touchCancel", None)
        self._events.append(TouchEventRecord("touchcancel", trusted=trusted))
        self._down = None
        return self

    # -- 状态 ------------------------------------------------------------

    @property
    def down_point(self) -> tuple[float, float]:
        """当前按下的坐标；已抬起时报错。"""
        self._require_down()
        return self._down  # type: ignore[return-value]

    @property
    def evidence(self) -> GestureEvidence:
        """这次按压的证据强度，供用例记录“可信输入 vs 合成事件”。

        `trusted_input` 只有在**每个**事件都来自引擎级真实输入时才为 True；混用（chromium 的
        CDP 真实按下 + 合成精确移动）会如实标成混合，机制串里给出两条通道。
        """
        harness = self._session.harness
        detail = self._synthetic_mechanism or "尚未派发"
        synthetic = f"页面内合成 TouchEvent（{detail}，isTrusted=false）"
        if self._cdp is None:
            mechanism = synthetic
        elif self._synthetic_mechanism is None:
            mechanism = "CDP Input.dispatchTouchEvent（引擎级真实触摸）"
        else:
            mechanism = f"CDP Input.dispatchTouchEvent（引擎级真实触摸）按下/抬起 + {synthetic}移动"
        trusted_input = bool(self._events) and not any(not record.trusted for record in self._events)
        return GestureEvidence(harness.engine, mechanism, trusted_input, tuple(self._events))

    # -- 上下文管理 ------------------------------------------------------

    def __enter__(self) -> "TouchGesture":
        return self

    def __exit__(self, *exc: object) -> None:
        if self._down is not None:
            self.up()

    # -- 内部 ------------------------------------------------------------

    def _require_down(self) -> None:
        if self._down is None:
            raise RuntimeError("还没有按下（先调用 down(x, y)）")

    def _dispatch(self, kind: str, point: tuple[float, float] | None) -> tuple[tuple[float, float] | None, bool]:
        """派发一个触摸事件，返回 `(页面收到的坐标, 是否引擎级可信输入)`。

        没有 CDP 通道时（webkit）走页面内合成 `TouchEvent`；chromium 默认走 CDP，只有
        `move_exact_*` 会显式要求合成通道。释放类事件（touchEnd/touchCancel）坐标为 None。
        """
        if self._cdp is not None:
            # CDP 的 touchEnd/touchCancel 用空 touchPoints 表示释放；它自己记得最后位置。
            points = [] if point is None else [{"x": point[0], "y": point[1], "id": self._identifier}]
            self._cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})
            return point, True
        return self._dispatch_synthetic(kind, point), False

    def _dispatch_synthetic(self, kind: str, point: tuple[float, float] | None) -> tuple[float, float] | None:
        """页面内合成 `TouchEvent` 派发，返回合成触点**读回**的坐标（引擎可能量化它）。"""
        anchor = self._origin if self._origin is not None else point
        current = point if point is not None else self._last
        result = self._session.page.evaluate(
            _SYNTHETIC_TOUCH_JS,
            {
                "type": kind,
                "point": _as_js_point(current),
                "anchor": _as_js_point(anchor),
                "id": self._identifier,
            },
        )
        if self._synthetic_mechanism is None:
            self._synthetic_mechanism = str(result["mechanism"])
        if result["x"] is None or result["y"] is None:
            return None
        return (float(result["x"]), float(result["y"]))


#: WebKit 回退路径：在页面里合成 TouchEvent。刻意保持最小——只派发事件，不碰页面任何状态。
#: WebKit 没有 `new Touch()`，只能用 `document.createTouch` / `createTouchList` 构造真正的
#: Touch 对象（本机 webkit-2359 实测可用）；两者都没有时退化到给 TouchEvent 伪造同名属性。
#: 坐标同时作为 clientX/pageX/screenX 填入——播放器页面 `html, body { overflow: hidden }`，页面不滚动。
#: 两个已踩过的坑（都有自检用例兜住，见 `test_press_sequence_reaches_the_page_*`）：
#:   1. `arg.type` 是 CDP 的事件名（`touchStart`/…），派发前必须转成 DOM 的小写事件名，
#:      否则页面的 `touchstart`/`touchend` 监听器根本收不到（实测：大写名进不了监听器）。
#:   2. `point`/`anchor` 必须以 `{x, y}` 对象传入（`_as_js_point`），传数组会让 `point.x` 取到
#:      `undefined`，合成出来的 Touch 坐标全是 0（实测：页面收到 0,0）。
_SYNTHETIC_TOUCH_JS = """
(arg) => {
  const point = arg.point || arg.anchor;
  const anchor = arg.anchor;
  const target = document.elementFromPoint(anchor.x, anchor.y) || document.body;
  const domType = arg.type.toLowerCase();
  const released = arg.type === 'touchEnd' || arg.type === 'touchCancel';
  const empty = () => (typeof document.createTouchList === 'function' ? document.createTouchList() : []);
  const x = point.x;
  const y = point.y;

  let mechanism;
  let list;
  if (typeof document.createTouch === 'function' && typeof document.createTouchList === 'function') {
    mechanism = 'document.createTouch + createTouchList';
    list = document.createTouchList(document.createTouch(window, target, arg.id, x, y, x, y, x, y));
  } else if (typeof Touch === 'function') {
    mechanism = 'new Touch + 数组';
    list = [new Touch({identifier: arg.id, target: target, clientX: x, clientY: y, pageX: x, pageY: y,
                       screenX: x, screenY: y, radiusX: 1, radiusY: 1, force: 1})];
  } else {
    mechanism = '伪造 Touch 属性';
    list = [{identifier: arg.id, target: target, clientX: x, clientY: y, pageX: x, pageY: y,
             screenX: x, screenY: y}];
  }

  const active = released ? empty() : list;
  let event;
  try {
    event = new TouchEvent(domType, {bubbles: true, cancelable: true, composed: true,
                                     touches: active, targetTouches: active, changedTouches: list});
  } catch (error) {
    mechanism += '（属性伪造）';
    event = new TouchEvent(domType, {bubbles: true, cancelable: true, composed: true});
    Object.defineProperty(event, 'touches', {value: active});
    Object.defineProperty(event, 'targetTouches', {value: active});
    Object.defineProperty(event, 'changedTouches', {value: list});
  }
  target.dispatchEvent(event);
  // 把合成触点**读回**的坐标返回给夹具：引擎可能对合成坐标做量化（实测 chromium 会压到
  // float32 精度），记录量化后的值才能让「门槛两侧」的用例知道页面上真实的位移是多少。
  const observed = list[0];
  const number = (value, fallback) => (typeof value === 'number' && isFinite(value) ? value : fallback);
  return {mechanism: mechanism,
          x: observed ? number(observed.clientX, null) : null,
          y: observed ? number(observed.clientY, null) : null};
}
"""


# --------------------------------------------------------------------------- 受控时间


class ControlledClock:
    """500 毫秒门槛两侧的确定性判定：Playwright clock API 的薄封装。

    用法：`goto(clock=True)`（导航前安装，避免加载期计时器被冻结卡住），
    然后 `freeze()` 冻结当前时刻，`advance(ms)` 跳时间。
    `advance` 走 `fast_forward`：跳时间并**最多触发一次**到期计时器——
    门槛两侧因此是确定性的，不依赖真实等待。
    """

    def __init__(self, page) -> None:
        self._page = page
        self.installed = False

    def install(self, *, now=None) -> None:
        self._page.clock.install(time=now)
        self.installed = True

    def freeze(self, *, timeout: float = 3.0) -> None:
        """停在当前假时刻（不再随真实时间前进），之后的计时器只能靠 advance/run_for 触发。

        `install()` 之后假时钟仍随真实时间走，读到时刻与 `pause_at` 之间有 1~2 毫秒的窗口：
        直接传读到的时刻会偶发撞上 `Cannot fast-forward to the past`（实测 flaky）。
        所以先按读到的时刻试一次，撞上了就带上一点前向余量重试。
        """
        from playwright.sync_api import Error as PlaywrightError

        deadline = time.monotonic() + timeout
        offset_ms = 0.0
        while True:
            target = self._page.evaluate("(offset) => (Date.now() + offset) / 1000", offset_ms)
            try:
                self._page.clock.pause_at(target)
                return
            except PlaywrightError as exc:
                if "past" not in str(exc) or time.monotonic() > deadline:
                    raise
                offset_ms += 50.0

    def advance(self, ms: int) -> None:
        """受控推进 `ms` 毫秒（跳时间 + 触发到期计时器）。"""
        self._page.clock.fast_forward(ms)

    def run_for(self, ms: int) -> None:
        """受控推进 `ms` 毫秒（逐一定时器依次触发）。"""
        self._page.clock.run_for(ms)


# --------------------------------------------------------------------------- 页面会话


class PlayerSession:
    """真实播放器页面的一个浏览器会话（普通页面 + 自制横屏全屏两种模式）。

    `device_scale_factor` 用来验证「阈值按 CSS 像素而不是设备像素」类断言：视口尺寸与布局仍是
    CSS 像素，只有设备像素比变化（`window.devicePixelRatio`）。不传就用引擎默认值。
    """

    def __init__(self, harness: "PlayerHarness", *, device_scale_factor: float | None = None) -> None:
        self.harness = harness
        self.device_scale_factor = device_scale_factor
        options: dict[str, object] = {"viewport": dict(VIEWPORT), "has_touch": True}
        if device_scale_factor is not None:
            options["device_scale_factor"] = device_scale_factor
        self.context = harness.browser.new_context(**options)
        self.page = self.context.new_page()
        self.clock = ControlledClock(self.page)
        self._gestures: list[TouchGesture] = []

    # -- 生命周期 --------------------------------------------------------

    def goto(self, *, clock: bool = False) -> "PlayerSession":
        """加载真实 /play/<id> 页面（真实模板、真实 player.js/player.css）。"""
        if clock:
            self.clock.install()
        url = f"{self.harness.base_url}/play/{self.harness.video_id}"
        self.page.goto(url, wait_until="load")
        self.wait_for_metadata()
        return self

    def close(self) -> None:
        for gesture in self._gestures:
            if gesture._down is not None:  # 防止漏抬的按压残留到下一个会话
                gesture.up()
        self.context.close()

    def __enter__(self) -> "PlayerSession":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- 观察（只读公开状态） --------------------------------------------

    def read(self) -> PlaybackState:
        """只由 Video 公开属性与可见 DOM 得出的观察结果。"""
        raw = self.page.evaluate(_READ_STATE_JS)
        return PlaybackState(
            paused=raw["paused"],
            playback_rate=raw["playbackRate"],
            current_time=raw["currentTime"],
            ended=raw["ended"],
            ready_state=raw["readyState"],
            custom_fullscreen=raw["customFullscreen"],
            fs_button_visible=raw["fsButtonVisible"],
            exit_button_visible=raw["exitButtonVisible"],
        )

    def video_box(self) -> dict:
        """Video 元素在视口里的矩形（自制横屏下是旋转后的外接矩形）。"""
        return self.page.evaluate(_VIDEO_BOX_JS)

    def point_in_video(self, *, x_frac: float = 0.5, y_frac: float = 0.3) -> tuple[float, float]:
        """画面内一点（按可见矩形比例取，落在视频元素上，默认靠上避开原生控件）。

        默认 `y_frac=0.3` 是为了避开底部原生控件条；两种观看模式下都落在视频元素内。
        """
        box = self.video_box()
        return (box["x"] + box["width"] * x_frac, box["y"] + box["height"] * y_frac)

    def wait_for_metadata(self, *, timeout: float = 15.0) -> PlaybackState:
        """等到 `readyState >= HAVE_METADATA`（尺寸/时长可用）。"""
        return self.wait_for(lambda s: s.ready_state >= 1, "video metadata", timeout=timeout)

    def wait_for_progress(self, *, min_advance: float = 0.3, timeout: float = 20.0) -> PlaybackState:
        """等到 `currentTime` 相对调用时刻至少推进 `min_advance` 秒（证明媒体真的在解码播放）。

        用 Python 侧轮询 + 真实等待，避免受控时间（冻结假时钟）时页面内 rAF/setTimeout 不跑。
        """
        baseline = self.read().current_time
        return self.wait_for(
            lambda s: s.current_time >= baseline + min_advance,
            f"currentTime 推进 {min_advance}s（起点 {baseline:.3f}）",
            timeout=timeout,
        )

    def wait_until_playing(self, *, min_advance: float = 0.2, timeout: float = 20.0) -> PlaybackState:
        """等到真的在播放：`paused == false` 且 `currentTime` 至少推进 `min_advance` 秒。

        这是「夹具视频真的能播」的判据，不看 `canPlayType` 的声明。
        """
        self.wait_for(lambda s: not s.paused, "video playing", timeout=timeout)
        return self.wait_for_progress(min_advance=min_advance, timeout=timeout)

    def wait_for(
        self,
        predicate: Callable[[PlaybackState], bool],
        what: str,
        *,
        timeout: float = 5.0,
    ) -> PlaybackState:
        """轮询公开状态直到满足条件（Python 侧轮询，不受受控时间影响）。"""
        deadline = time.monotonic() + timeout
        state = self.read()
        while time.monotonic() < deadline:
            state = self.read()
            if predicate(state):
                return state
            time.sleep(0.02)
        raise AssertionError(f"等待 {what} 超时：{state}")

    def hit_id(self, x: float, y: float) -> str:
        """视口坐标下的命中元素 id（公开 DOM 几何，用来确认按压落在视频上而非控件上）。"""
        return self.page.evaluate(
            "([x, y]) => { const el = document.elementFromPoint(x, y);"
            " return el ? (el.id || el.tagName.toLowerCase()) : null; }",
            [x, y],
        )

    # -- 播放控制（公开 API，不是输入手势） ------------------------------

    def start_playback(self) -> PlaybackState:
        """开始播放夹具视频并等到 `currentTime` 推进。

        这是**程序化**的公开 API 调用（`video.play()`），不是输入手势，因此不作为任何
        「真实输入」证据。为绕开 autoplay 策略，先在顶栏空白处点一次真实输入取得用户激活。
        """
        self.page.mouse.click(*self._neutral_point())
        result = self.page.evaluate(_START_PLAYBACK_JS)
        if result != "ok":
            self.harness.notes.append(f"{self.harness.engine}: play() 首次返回 {result}")
            self.page.mouse.click(*self._neutral_point())
            result = self.page.evaluate(_START_PLAYBACK_JS)
            if result != "ok":
                raise AssertionError(f"video.play() 失败: {result}")
        return self.wait_until_playing()

    def _neutral_point(self) -> tuple[float, float]:
        """顶栏空白处（不覆盖任何按钮/链接）——只用来取得用户激活。"""
        box = self.page.evaluate(
            "() => { const r = document.getElementById('top-bar').getBoundingClientRect();"
            " return {x: r.x, y: r.y, width: r.width, height: r.height}; }"
        )
        return (box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)

    # -- 输入（真实/合成，见各方法说明） --------------------------------

    def touch(self) -> TouchGesture:
        """开始一次按压序列。chromium = 可信真实触摸；webkit = 合成事件（弱证据）。"""
        gesture = TouchGesture(self)
        self._gestures.append(gesture)
        return gesture

    def tap(self, x: float, y: float) -> None:
        """一次可信真实 tap（Playwright touchscreen；两个引擎都是引擎级输入）。"""
        self.page.touchscreen.tap(x, y)

    def tap_play_control(self, *, reveal_ms: float = 350.0, timeout: float = 3.0) -> PlaybackState:
        """用真实输入点在原生控件条的播放/暂停按钮上，并等 `paused` 翻转。

        原生控件在 UA shadow DOM 里，选择器抓不到元素，所以按实测位置取点
        （相对 video 矩形左下角，见 `PLAY_CONTROL_OFFSETS`）；先移动鼠标再点击，
        因为 chromium 播放在进行时控件条需要鼠标移到画面上才显示。
        只支持已实测可稳定驱动的引擎；其余引擎抛错，请改用 `tap_picture()`。
        超时未翻转即报错（不静默降级）。
        """
        if self.harness.engine not in PLAY_CONTROL_OFFSETS:
            raise RuntimeError(
                f"{self.harness.engine} 的原生控件条位置未实测可稳定驱动，请改用 tap_picture()"
            )
        left, bottom = PLAY_CONTROL_OFFSETS[self.harness.engine]
        box = self.video_box()
        x, y = box["x"] + left, box["y"] + box["height"] - bottom
        before = self.read()
        self.page.mouse.move(x, y)
        time.sleep(reveal_ms / 1000.0)
        self.page.mouse.click(x, y)
        state = self.wait_for(lambda s: s.paused != before.paused, "原生控件条播放/暂停翻转", timeout=timeout)
        self.harness.notes.append(
            f"{self.harness.engine}: 原生控件条点击 @({x:.0f},{y:.0f}) paused {before.paused} -> {state.paused}"
        )
        return state

    def tap_picture(self, *, y_frac: float = 0.3, timeout: float = 3.0) -> PlaybackState:
        """在画面上做一次可信真实 tap，并等 `paused` 翻转。

        实测：webkit-2359 上点击画面 3/3 稳定切换播放/暂停；chromium-1243 上 touch tap
        不切换（原生控件按钮才可靠，见 `tap_play_control`），所以 chromium 请用
        `tap_play_control`；本助手用于 webkit 的「真实输入 → 媒体公开状态翻转」自检。
        超时未翻转即报错（不静默降级）。
        """
        point = self.point_in_video(y_frac=y_frac)
        before = self.read()
        self.tap(*point)
        state = self.wait_for(lambda s: s.paused != before.paused, f"画面 tap @({point[0]:.0f},{point[1]:.0f}) 翻转 paused", timeout=timeout)
        self.harness.notes.append(
            f"{self.harness.engine}: 画面 tap @({point[0]:.0f},{point[1]:.0f}) paused {before.paused} -> {state.paused}"
        )
        return state

    def enter_custom_fullscreen(self, *, timeout: float = 5.0) -> PlaybackState:
        """点页面现有 `#fs-btn`（真实输入）进入自制横屏全屏，等 `body.custom-fullscreen` 生效。"""
        self.page.click("#fs-btn")
        return self.wait_for(lambda s: s.custom_fullscreen, "body.custom-fullscreen", timeout=timeout)

    def exit_custom_fullscreen(self, *, timeout: float = 5.0) -> PlaybackState:
        """点页面现有 `#fs-exit-btn`（真实输入）退出自制横屏全屏。"""
        self.page.click("#fs-exit-btn")
        return self.wait_for(lambda s: not s.custom_fullscreen, "退出 body.custom-fullscreen", timeout=timeout)

    def touch_in_custom_fullscreen(self) -> TouchGesture:
        """进入自制横屏全屏后开始一次按压（坐标用 `point_in_video` 取，旋转后仍然落在视频上）。"""
        self.enter_custom_fullscreen()
        return self.touch()


# --------------------------------------------------------------------------- 夹具服务器


class _FixtureServer:
    """给真实页面用的最小 HTTP 服务：真实模板渲染出的页面 + 真实静态资源 + 夹具视频。"""

    def __init__(self, harness: "PlayerHarness") -> None:
        self.harness = harness
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base_url = ""

    def start(self) -> None:
        harness = self.harness

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:  # 静音
                pass

            def do_GET(self) -> None:  # noqa: N802
                path = unquote(urlparse(self.path).path)
                if path.startswith("/play/"):
                    self._serve_player(path.rsplit("/", 1)[-1])
                elif path.startswith("/static/"):
                    self._serve_static(path[len("/static/"):])
                elif path.startswith("/media/"):
                    self._serve_media(path[len("/media/"):])
                else:
                    self.send_error(404)

            def _serve_player(self, video_id: str) -> None:
                body = harness.render_player_page(video_id).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _serve_static(self, name: str) -> None:
                path = server_app._STATIC_DIR / name  # 真实静态资源，不复制
                if not path.is_file():
                    self.send_error(404)
                    return
                body = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", _CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _serve_media(self, name: str) -> None:
                path = harness.media_dir / name
                if not path.is_file():
                    self.send_error(404)
                    return
                body = path.read_bytes()
                size = len(body)
                rng = self.headers.get("Range")
                start, end = 0, size - 1
                partial = False
                if rng and rng.startswith("bytes="):
                    spec = rng[len("bytes="):].split(",")[0].split("-")
                    try:
                        if spec[0]:
                            start = int(spec[0])
                            end = int(spec[1]) if len(spec) > 1 and spec[1] else size - 1
                        else:
                            start = max(0, size - int(spec[1]))
                        partial = True
                    except ValueError:
                        start, end = 0, size - 1
                        partial = False
                end = min(end, size - 1)
                chunk = body[start:end + 1]
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", _CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(chunk)))
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(chunk)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


# --------------------------------------------------------------------------- 夹具本体


class PlayerHarness:
    """一个引擎上的一套夹具：本地服务 + 夹具视频 + 真实浏览器。

    生命周期由 pytest 的会话级 fixture 管理；`start()` 成功即代表夹具可用，
    缺少 playwright/ffmpeg/浏览器时抛 `HarnessUnavailable`，由 fixture 转成 skip。
    """

    def __init__(self, engine: str, playwright=None) -> None:
        self.engine = engine
        self.tmp_dir = Path(tempfile.mkdtemp(prefix=f"player-harness-{engine}-"))
        self.media_dir = self.tmp_dir / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.video_id = "harness" + uuid.uuid4().hex[:6]
        self.notes: list[str] = []
        self.source: PlaybackSource | None = None
        self.launch_detail = ""
        self._playwright = playwright
        self._owns_playwright = playwright is None
        self.browser = None
        self._server: _FixtureServer | None = None
        self._ffmpeg = ""

    # -- 生命周期 --------------------------------------------------------

    def start(self) -> "PlayerHarness":
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - 取决于本机环境
            raise HarnessUnavailable(f"playwright 不可用: {exc}") from exc
        self._ffmpeg = shutil.which("ffmpeg") or ""
        if not self._ffmpeg:
            raise HarnessUnavailable("ffmpeg 不在 PATH，无法生成夹具视频")

        if self._playwright is None:
            # 同步 API 在一个线程里只能有一个正在运行的 event loop，所以默认由 session 夹具注入共享实例。
            self._playwright = sync_playwright().start()

        from playwright.sync_api import Error as PlaywrightError

        try:
            self.browser = self._launch()
        except PlaywrightError as exc:
            self.stop()
            raise HarnessUnavailable(f"无法启动 {self.engine} 浏览器: {exc}") from exc

        self._server = _FixtureServer(self)
        self._server.start()
        self.source = self._probe_playback_source()
        return self

    def _launch(self):
        """启动真实浏览器。headless 里若装了 headless shell 会用它，失败再退回完整浏览器。"""
        from playwright.sync_api import Error as PlaywrightError

        browser_type = getattr(self._playwright, self.engine)
        attempts: list[tuple[str, dict]] = [("默认 headless", {"headless": True})]
        if self.engine == "chromium":
            attempts.append(("完整 Chromium 二进制 (channel=chromium)", {"headless": True, "channel": "chromium"}))
        last: Exception | None = None
        for label, kwargs in attempts:
            try:
                browser = browser_type.launch(**kwargs)
            except PlaywrightError as exc:
                last = exc
                self.notes.append(f"{self.engine}: 启动失败（{label}）: {str(exc).splitlines()[0]}")
                continue
            self.launch_detail = label
            return browser
        raise last  # type: ignore[misc]

    def stop(self) -> None:
        if self.browser is not None:
            self.browser.close()
            self.browser = None
        if self._owns_playwright and self._playwright is not None:
            self._playwright.stop()
        self._playwright = None
        if self._server is not None:
            self._server.stop()
            self._server = None
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def __enter__(self) -> "PlayerHarness":
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- 页面 ------------------------------------------------------------

    @property
    def base_url(self) -> str:
        assert self._server is not None, "夹具未启动"
        return self._server.base_url

    @property
    def media_url(self) -> str:
        assert self.source is not None, "夹具视频未就绪"
        return f"/media/{self.source.name}"

    def session(self, *, device_scale_factor: float | None = None) -> PlayerSession:
        """新建一个浏览器会话（独立 context：localStorage 干净）。

        `device_scale_factor` 见 `PlayerSession`（用于「按 CSS 像素判定」类断言）。
        """
        return PlayerSession(self, device_scale_factor=device_scale_factor)

    def render_player_page(self, video_id: str) -> str:
        """真实模板 + 真实 `server.app._render`；唯一改动是播放源指向夹具视频。"""
        return server_app._render(
            server_app._PLAYER_TPL,
            title="player-harness",
            playlist_url=self.media_url,
            video_id=video_id,
            subtitle_url="",
        )

    # -- 夹具视频 --------------------------------------------------------

    def _generate_video(self, container: str, codec: str, args: Sequence[str]) -> Path:
        dest = self.media_dir / f"fixture.{container}"
        cmd = [
            self._ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={VIDEO_SIZE}:rate={VIDEO_FPS}",
            "-t", str(VIDEO_SECONDS), "-g", str(VIDEO_FPS), *args, str(dest),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise HarnessUnavailable(f"ffmpeg 生成 {container}/{codec} 失败: {proc.stderr.strip()[:400]}")
        return dest

    def _probe_playback_source(self) -> PlaybackSource:
        """按候选顺序找当前引擎真的能播的夹具视频（判据：paused==false 且 currentTime 推进）。"""
        failures: list[str] = []
        for container, codec, args in VIDEO_CANDIDATES:
            path = self._generate_video(container, codec, args)
            detail = self._probe_candidate(path.name)
            self.notes.append(f"{self.engine}: 探测夹具视频 {container}/{codec} → {detail}")
            if detail.startswith("ok"):
                return PlaybackSource(path.name, container, codec, detail)
            failures.append(f"{container}/{codec}: {detail}")
        raise HarnessUnavailable(f"{self.engine} 无法播放任何候选夹具视频: {'; '.join(failures)}")

    def _probe_candidate(self, name: str) -> str:
        """在空白探针页里加载并播放候选视频，返回可读结论。"""
        context = self.browser.new_context(viewport=dict(VIEWPORT))
        page = context.new_page()
        try:
            page.set_content('<video id="p" playsinline></video>')
            url = f"{self.base_url}/media/{name}"
            started = page.evaluate(
                "(src) => { const v = document.getElementById('p'); v.src = src;"
                " const p = v.play(); return p && p.then ? p.then(() => 'ok', (e) => 'reject:' + (e && e.name)) : 'ok'; }",
                url,
            )
            if started != "ok":
                return started
            deadline = time.monotonic() + 8.0
            state = {}
            while time.monotonic() < deadline:
                state = page.evaluate(
                    "() => { const v = document.getElementById('p');"
                    " return {paused: v.paused, currentTime: v.currentTime, readyState: v.readyState,"
                    " error: v.error ? v.error.code : null}; }"
                )
                if not state["paused"] and state["currentTime"] > 0.1:
                    return f"ok: 播放中 currentTime={state['currentTime']:.2f}"
                if state["error"]:
                    return f"解码错误 error.code={state['error']}"
                time.sleep(0.05)
            return f"超时: paused={state.get('paused')} currentTime={state.get('currentTime')} readyState={state.get('readyState')}"
        finally:
            context.close()


# --------------------------------------------------------------------------- pytest 夹具

#: 进程内共享的会话状态。本模块的夹具被测试模块**显式 import** 使用，而 pytest 对「从别的模块
#: 导入的夹具」会按测试模块各实例化一次（fixture cache key 含模块），于是第二个用到夹具的测试
#: 模块会再跑一次 `sync_playwright()`；同步 API 在一个线程里只能有一个实例（第一个实例的
#: dispatcher greenlet 停在 `run_until_complete` 里，主 greenlet 会看到线程内已有 running loop），
#: 直接抛 "It looks like you are using Playwright Sync API inside the asyncio loop."。
#: 所以真正的会话状态放在这三个注册表里：第二个实例复用第一份，只有创建者负责 teardown。
_SESSION_PLAYWRIGHT: dict[str, object] = {}
_SESSION_HARNESSES: dict[str, PlayerHarness] = {}
_SESSION_FAILURES: dict[str, str] = {}


def _stop_session_harnesses() -> None:
    """关掉本进程共享的所有夹具（幂等：`PlayerHarness.stop()` 可以重复调用）。"""
    for engine in list(_SESSION_HARNESSES):
        _SESSION_HARNESSES.pop(engine).stop()


@pytest.fixture(scope="session")
def playwright_instance():
    """整个进程共用一个 Playwright 实例（多个测试模块导入本夹具时也只建一次）。

    同步 API 在一个线程里只能有一个正在运行的 event loop，第二个 `sync_playwright().start()`
    会直接报错，所以两个引擎、以及多个测试模块共用这一个实例。
    创建者统一负责 teardown：先关夹具（还要用连接），再关 Playwright。
    """
    shared = _SESSION_PLAYWRIGHT.get("playwright")
    if shared is not None:
        yield shared
        return
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        pytest.skip(f"playwright 不可用: {exc}")
    with sync_playwright() as playwright:
        _SESSION_PLAYWRIGHT["playwright"] = playwright
        try:
            yield playwright
        finally:
            _stop_session_harnesses()
            _SESSION_PLAYWRIGHT.pop("playwright", None)


@pytest.fixture(scope="session")
def harness_factory(playwright_instance):
    """会话级：每个引擎一套夹具（本地服务 + 夹具视频 + 浏览器），跨测试模块共享。

    夹具不可用时 `pytest.skip`——整组测试自动跳过，缺设施不会让既有套件失败。
    这里不负责回收：共享夹具由 `playwright_instance` 的创建者统一关停，避免多个测试模块的
    同名夹具各自 teardown、以及「先关 Playwright 再关夹具」的顺序问题。
    """

    def get(engine: str) -> PlayerHarness:
        if engine in _SESSION_FAILURES:
            pytest.skip(_SESSION_FAILURES[engine])
        if engine not in _SESSION_HARNESSES:
            harness = PlayerHarness(engine, playwright=playwright_instance)
            try:
                harness.start()
            except HarnessUnavailable as exc:
                harness.stop()
                _SESSION_FAILURES[engine] = str(exc)
                pytest.skip(str(exc))
            _SESSION_HARNESSES[engine] = harness
        return _SESSION_HARNESSES[engine]

    yield get


@pytest.fixture
def harness(harness_factory, engine: str) -> PlayerHarness:
    return harness_factory(engine)


@pytest.fixture
def session(harness: PlayerHarness) -> Iterator[PlayerSession]:
    player = harness.session()
    try:
        yield player
    finally:
        player.close()


@pytest.fixture
def playing_session(session: PlayerSession) -> PlayerSession:
    """已停在真实页面、夹具视频真的在播放的会话。"""
    session.goto()
    session.start_playback()
    return session
