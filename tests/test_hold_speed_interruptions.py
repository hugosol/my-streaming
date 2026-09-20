"""票据 04：中断、手势终结与不误触原有操作（P6 / P7 / P10 的自动化覆盖项）。

覆盖范围与输入证据强度（完成记录见 `.scratch/hold-to-double-speed/implementation/04-interruption-and-gesture-end.md`）：

| 承诺 | 本文件覆盖 | 输入证据强度 |
|---|---|---|
| P6 | 进入自制横屏全屏（等待期 / 加速期）：本次长按立即结束，之后推进多久都不触发 | 按压：chromium CDP 真实触摸（可信）/ webkit 页面内合成（弱证据）；按钮：Playwright 真实鼠标点 `#fs-btn`（两引擎都是引擎级真实输入） |
| P6 | 退出自制横屏全屏（等待期 / 加速期）：同上 | 同上（`#fs-exit-btn`） |
| P6 | 切到后台（等待期 / 加速期）：取消等待 / 立即恢复原速，返回前台后速度仍是长按前速度 | **页面内合成 `visibilitychange`（覆写 `document.hidden` / `document.visibilityState`，`isTrusted=false`）= 弱证据**：headless 桌面浏览器无法构造真实后台切换（实测新标签页 `bring_to_front()` 不触发 `visibilitychange`、`document.hidden` 始终 `false`） |
| P6 | 系统取消触摸（等待期 / 加速期）：恢复原速、不遗留临时倍速 | 按压同上：chromium `Input.dispatchTouchEvent` 的 `touchCancel`（可信）/ webkit 合成 `touchcancel`（弱证据） |
| P6 | 中断不启动播放：三种中断分别覆盖播放中与暂停中 | 同上（观察 `video.paused`） |
| P7 | 已结束手势不复活：切后台、切换观看模式结束本次长按后，手指继续按住、继续移动、时间继续推进都不出现 2× | 中断通道见上两行；结束后的移动：chromium 页面内合成精确移动 / webkit 合成（弱证据），只用它证明「移动不复活」 |
| P7 | 取消后重新按下必须重新等待完整 门槛（门槛两侧，两种模式；取消 = 系统取消 / 全屏切换 / 后台往返） | 受控时间（`clock.advance`）+ 按压同上 |
| P10 | 已识别长按正常松手结束、被系统取消后，播放/暂停状态不变 | 按压同上 |
| P10 | 短于门槛的按压（0 / 门槛的 1/4 / 门槛前 1 毫秒）不改变速度、不改变播放/暂停状态 | 按压同上；另用一次**可信真实 tap**核对引擎基线（见下） |
| 不新增提示 | 切到后台（含返回前台）与系统取消两条中断路径上，可见元素清单与可见文字与按压前一致（`document.querySelectorAll('body *')` 快照）；全屏切换会合法地换掉控件，故该行不覆盖 | 可见 DOM 快照（两引擎） |

**P10 的「与改动前一致」怎么判**：`01/02` 实测并记录的平台基线是「webkit 的真实 tap 点击画面会切换
播放/暂停，chromium 的 touch tap 不会」。本文件因此分两层断言，不靠「无视 `paused`」蒙混：

1. 子门槛按压序列（chromium = 可信 CDP 按压；webkit = 合成事件，弱证据）在 0 / 门槛的 1/4 / 门槛前 1 毫秒三种时长下
   速度不变、`paused` 不变——与按压前的同一状态一致（同引擎多种子门槛时长结果一致）；
2. 每个引擎再用一次**引擎级真实 tap**核对基线本身没有被本次改动掩平：webkit 必须切换一次、chromium
   必须不切换，两者速度都保持长按前速度。

**为什么后台/系统取消是弱证据**：桌面 headless 浏览器里「切到后台」只能靠覆写 `document.hidden` /
`document.visibilityState` 再派发 `visibilitychange` 来构造（真实切换实测不触发事件），webkit 的触摸取消
也只能用页面内合成 `TouchEvent`。用例在派发前会确认模拟状态真的生效（否则会空洞通过），但结论本身只能
证明页面逻辑，**不构成 iOS Safari 真机结论**——真机覆盖由票据 05 验收。

观察口径：只读 Video 元素公开属性（`paused` / `playbackRate`）、`document.hidden`、
`body.custom-fullscreen` 与可见 DOM；不读实现私有变量、内部计时器或手势状态字段，也不以「自己派的合成
事件被收到」当结论。每条用例都用 `TouchGesture.evidence` 标注按下序列的输入通道。

本文件只交付 P6 / P7 / P10 的**自动化覆盖项**；真机（iOS Safari 后台切换、系统手势取消、自制全屏切换、
短按体感）由票据 05 承载，因此自动化通过不代表这三个承诺已经完成。
"""

from __future__ import annotations

import math

import pytest

from player_harness import (  # noqa: F401  (下面这些是有名字的 pytest 夹具，必须导入本模块才可见)
    PLAYWRIGHT_ENGINES,
    configured_hold_ms,
    PlayerSession,
    TouchGesture,
    harness,
    harness_factory,
    playwright_instance,
    session,
)

pytestmark = pytest.mark.parametrize("engine", PLAYWRIGHT_ENGINES)

#: 门槛来自部署配置（config.json → player.hold_ms，默认 500）：测试不写死数值。
#: 临时速度绝对值 2× 是契约固定值；手指位移不参与判定（2026-09-20 决定，见 docs/adr/0002）。
HOLD_MS = configured_hold_ms()
TEMPORARY_RATE = 2.0

#: 门槛前先停住的时长：取门槛的 1/4，保证「连续两次等待」仍停在门槛前（随配置变化）。
WAITING_MS = HOLD_MS // 4

#: 「旧手势不复活」的观察窗口：中断结束后推进这么久仍不得出现 2×。
REVIVAL_MS = 2000

MODES = ("inline", "fullscreen")
PHASES = ("waiting", "accelerating")

#: 按压点比例。`y_frac=0.25` 让普通页面与自制全屏下的按压点都落在视频上、且是 float32 可无损
#: 表示的值（chromium 会把合成触摸坐标压到 float32，见夹具「实测边界」）。
_PRESS_FRAC = {"x_frac": 0.5, "y_frac": 0.25}

#: 手势结束后用来验证「移动不复活」的明显位移（CSS 像素），以及页面读回位移的下限。
_FAR_MOVE_PX = 40.0
_FAR_MIN = 20.0

#: 01/02 实测记录并在本票复测的平台基线：可信真实 tap 点击画面是否切换播放/暂停。
#: webkit 会切换（3/3 稳定），chromium 的 touch tap 不会。本票的改动不得掩平这个差异。
_TRUSTED_TAP_TOGGLES = {"webkit": True, "chromium": False}

#: 合成后台的证据说明：必须逐条写进票据，不能当可信输入用。
_BACKGROUND_EVIDENCE = (
    "页面内合成 visibilitychange（覆写 document.hidden / document.visibilityState 后 dispatch，"
    "isTrusted=false）= 弱证据"
)

_SET_VISIBILITY_JS = """
(hidden) => {
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
  Object.defineProperty(document, 'visibilityState', {
    configurable: true, get: () => (hidden ? 'hidden' : 'visible'),
  });
  document.dispatchEvent(new Event('visibilitychange'));
  return {hidden: document.hidden, state: document.visibilityState};
}
"""

#: 可见内容快照（与 02/03 同一口径）：中断不得新增任何可见元素或文字。
_VISIBLE_JS = """
() => {
  const visible = (el) => {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  return {
    inventory: Array.from(document.querySelectorAll('body *')).filter(visible)
      .map((el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')).sort(),
    text: (document.body.innerText || '').replace(/\\s+/g, ' ').trim(),
  };
}
"""


# --------------------------------------------------------------------------- 前置与观察助手


def _open_playing_session(target: PlayerSession, *, mode: str, rate: float = 1.0) -> PlayerSession:
    """真实页面 + 真实播放 + 受控时间，停在指定观看模式（与 02/03 的前置一致）。"""
    assert mode in MODES
    target.goto(clock=True)
    target.start_playback()
    target.page.evaluate("(rate) => { document.getElementById('v').playbackRate = rate; }", rate)
    target.page.evaluate("() => { document.getElementById('v').loop = true; }")
    target.clock.freeze()
    if mode == "fullscreen":
        target.enter_custom_fullscreen()
    state = target.read()
    assert state.custom_fullscreen is (mode == "fullscreen"), "用例必须停在指定观看模式"
    assert state.paused is False, "前置条件：Video 正在播放"
    assert state.playback_rate == rate, "前置条件：起始速度已按公开 API 生效"
    return target


def _press(target: PlayerSession) -> TouchGesture:
    """在画面非控件区域按下（坐标见 `_PRESS_FRAC`）。"""
    point = target.point_in_video(**_PRESS_FRAC)
    gesture = target.touch()
    gesture.down(*point)
    assert target.hit_id(*point) == "v", f"按压点 {point} 必须落在视频上，不得被浮层截获"
    return gesture


def _set_rate(target: PlayerSession, rate: float) -> None:
    """用公开 API 把起始速度设成 `rate` 并确认生效（页面当前没有倍速入口）。"""
    target.page.evaluate("(rate) => { document.getElementById('v').playbackRate = rate; }", rate)
    assert target.read().playback_rate == rate, "前置条件：起始速度已按公开 API 生效"


def _press_into_phase(target: PlayerSession, *, phase: str, rate: float | None = None) -> TouchGesture:
    """在**已打开**的会话上按下，并把受控时间推进到中断前的指定阶段。

    `waiting` = 停在门槛前的等待期；`accelerating` = 已满 门槛、正在临时倍速。
    """
    assert phase in PHASES
    if rate is not None:
        _set_rate(target, rate)
    expected = target.read().playback_rate
    gesture = _press(target)
    target.clock.advance(WAITING_MS)
    if phase == "accelerating":
        target.clock.advance(HOLD_MS - WAITING_MS)
        assert target.read().playback_rate == TEMPORARY_RATE, "前置条件：按压已进入临时倍速"
    else:
        assert target.read().playback_rate == expected, "前置条件：按压仍停在 门槛前"
    return gesture


def _open_and_press(target: PlayerSession, *, mode: str, phase: str, rate: float = 1.0) -> TouchGesture:
    """打开指定模式的播放会话并按下到指定阶段。"""
    _open_playing_session(target, mode=mode, rate=rate)
    return _press_into_phase(target, phase=phase)


def _set_page_visibility(target: PlayerSession, *, hidden: bool) -> str:
    """合成一次可见性变化（弱证据）并确认模拟状态真的生效，返回证据说明。

    先确认 `document.hidden` / `document.visibilityState` 已被覆写成期望值：否则用例会因为状态没生效
    而空洞通过。结论仍然只看公开的播放速度/暂停状态，不看「事件被谁收到」。
    """
    observed = target.page.evaluate(_SET_VISIBILITY_JS, hidden)
    assert observed["hidden"] is hidden and observed["state"] == ("hidden" if hidden else "visible"), (
        f"合成的可见性状态没有生效：{observed}"
    )
    return _BACKGROUND_EVIDENCE


def _visible_content(target: PlayerSession) -> dict:
    """当前可见元素清单与可见文字（公开 DOM），用于确认中断没有新增提示。"""
    return target.page.evaluate(_VISIBLE_JS)


def _switch_viewing_mode(target: PlayerSession, *, mode: str) -> str:
    """从当前观看模式切到另一种（真实按钮点击），返回可分档说明的切换描述。"""
    assert mode in MODES
    if mode == "inline":
        target.enter_custom_fullscreen()
        assert target.read().custom_fullscreen is True
        return "普通页面 → 自制横屏全屏（真实点击 #fs-btn）"
    target.exit_custom_fullscreen()
    assert target.read().custom_fullscreen is False
    return "自制横屏全屏 → 普通页面（真实点击 #fs-exit-btn）"


def _delivered_moves(gesture: TouchGesture) -> list[tuple[float, float, float]]:
    """本次按压里**页面实际收到**的每一步移动：相对按下点的 `(dx, dy, 直线距离)`（CSS 像素）。"""
    events = gesture.evidence.events
    assert events and events[0].kind == "touchstart", f"证据里必须以 touchstart 开头：{events}"
    origin = events[0]
    assert origin.x is not None and origin.y is not None, f"按下坐标缺失：{origin}"
    moves = [event for event in events if event.kind == "touchmove"]
    assert moves, f"这次按压没有记录到任何移动事件：{events}"
    return [
        (event.x - origin.x, event.y - origin.y, math.hypot(event.x - origin.x, event.y - origin.y))
        for event in moves
    ]


def _last_move(gesture: TouchGesture) -> tuple[float, float, float]:
    return _delivered_moves(gesture)[-1]


def _press_evidence(gesture: TouchGesture, *, engine: str, what: str) -> str:
    """核对本次按压序列的输入通道并如实描述证据强度。

    chromium 的 CDP 按压是引擎级真实触摸；webkit 没有按压通道，序列只能是页面内合成事件（弱证据）。
    """
    evidence = gesture.evidence
    assert evidence.engine == engine, f"{what}: 引擎不符 {evidence}"
    if engine == "chromium":
        assert evidence.trusted_input is True, f"{what}: chromium 的按压必须是引擎级真实触摸：{evidence.mechanism}"
    else:
        assert evidence.synthetic_events and evidence.trusted_input is False, (
            f"{what}: webkit 的按压只能是页面内合成事件（弱证据）：{evidence.mechanism}"
        )
    return f"{evidence.strength}（{evidence.mechanism}）"


def _press_evidence_with_synthetic_moves(gesture: TouchGesture, *, engine: str, what: str) -> str:
    """核对「按压 + 合成移动 + 抬起」这种混合序列的通道，返回证据说明。

    chromium 上按下/抬起是 CDP 真实触摸，移动是页面内合成（精确坐标，`isTrusted=false`）
    ——混用通道时必须如实标成混合（弱证据），不能当成全程可信。
    """
    evidence = gesture.evidence
    assert evidence.engine == engine, f"{what}: 引擎不符 {evidence}"
    synthetic = evidence.synthetic_events
    assert synthetic, f"{what}: 本次按压必须包含页面内合成移动：{evidence.mechanism}"
    if engine == "chromium":
        assert all(record.kind == "touchmove" for record in synthetic), (
            f"{what}: 只有移动允许走合成通道：{evidence.mechanism}"
        )
        trusted = [record.kind for record in evidence.events if record.trusted]
        assert trusted[:1] == ["touchstart"] and trusted[-1:] == ["touchend"], (
            f"{what}: chromium 的按下/抬起必须是真实触摸：{trusted}"
        )
        assert all(kind == "touchmove" for kind in trusted[1:-1]), (
            f"{what}: 可信事件里只允许按下/抬起与真实移动：{trusted}"
        )
        assert evidence.trusted_input is False, f"{what}: 混用通道时必须如实标成弱证据（混合）"
    else:
        assert evidence.trusted_input is False, f"{what}: webkit 的按压序列只能是合成事件（弱证据）"
    return f"{evidence.strength}（{evidence.mechanism}）"


def _assert_no_extra_toggle(target: PlayerSession, *, was_paused: bool, what: str) -> None:
    """`paused` 必须与手势前一致：中断/结束都不得产生额外的播放或暂停切换。"""
    assert target.read().paused is was_paused, f"{what} 不得产生额外的播放/暂停切换"


# --------------------------------------------------------------------------- P6：进入自制横屏全屏


def test_entering_custom_fullscreen_ends_waiting_press(session: PlayerSession) -> None:
    """P6：普通页面上等待期间点进自制横屏全屏 → 本次长按立即结束，之后推进多久都不触发 2×。

    按钮是真实点击（`#fs-btn`），所以中断本身是引擎级真实输入；按压序列的通道见 `evidence`。
    """
    gesture = _open_and_press(session, mode="inline", phase="waiting")
    session.clock.advance(WAITING_MS)  # 累计半个门槛，仍在门槛内
    assert session.read().playback_rate == 1.0, "前置条件：仍在等待期"

    switch = _switch_viewing_mode(session, mode="inline")
    assert session.read().playback_rate == 1.0, f"进入自制全屏必须结束本次长按（{switch}）"

    session.clock.advance(HOLD_MS * 4)
    state = session.read()
    assert state.playback_rate == 1.0, "进入自制全屏后无论再按住多久都不得触发 2×"
    assert state.paused is False, "中断不得启动或暂停播放"
    _assert_no_extra_toggle(session, was_paused=False, what="进入自制全屏")

    gesture.up()
    assert session.read().playback_rate == 1.0
    _press_evidence(gesture, engine=session.harness.engine, what="进入自制全屏期间的按压")


@pytest.mark.parametrize("start_rate", (1.0, 1.5))
def test_entering_custom_fullscreen_restores_rate_while_accelerated(
    session: PlayerSession, start_rate: float
) -> None:
    """P6：普通页面上加速期间点进自制横屏全屏 → 立即恢复长按前的**实际**速度（1× 与 1.5×）。"""
    gesture = _open_and_press(session, mode="inline", phase="accelerating", rate=start_rate)

    switch = _switch_viewing_mode(session, mode="inline")
    assert session.read().playback_rate == start_rate, f"进入自制全屏必须立即恢复长按前的速度（{switch}）"

    session.clock.advance(HOLD_MS * 4)
    state = session.read()
    assert state.playback_rate == start_rate, "进入自制全屏后不得自行回到 2×"
    assert state.paused is False, "中断不得启动或暂停播放"
    _assert_no_extra_toggle(session, was_paused=False, what="进入自制全屏")

    gesture.up()
    assert session.read().playback_rate == start_rate
    _press_evidence(gesture, engine=session.harness.engine, what="进入自制全屏期间的按压")


# --------------------------------------------------------------------------- P6：退出自制横屏全屏


def test_exiting_custom_fullscreen_ends_waiting_press(session: PlayerSession) -> None:
    """P6：自制横屏全屏里等待期间点退出 → 本次长按立即结束，之后推进多久都不触发 2×。"""
    gesture = _open_and_press(session, mode="fullscreen", phase="waiting")
    session.clock.advance(WAITING_MS)
    assert session.read().playback_rate == 1.0, "前置条件：仍在等待期"

    switch = _switch_viewing_mode(session, mode="fullscreen")
    assert session.read().playback_rate == 1.0, f"退出自制全屏必须结束本次长按（{switch}）"

    session.clock.advance(HOLD_MS * 4)
    state = session.read()
    assert state.playback_rate == 1.0, "退出自制全屏后无论再按住多久都不得触发 2×"
    assert state.paused is False, "中断不得启动或暂停播放"

    gesture.up()
    assert session.read().playback_rate == 1.0
    _press_evidence(gesture, engine=session.harness.engine, what="退出自制全屏期间的按压")


@pytest.mark.parametrize("start_rate", (1.0, 1.5))
def test_exiting_custom_fullscreen_restores_rate_while_accelerated(
    session: PlayerSession, start_rate: float
) -> None:
    """P6：自制横屏全屏里加速期间点退出 → 立即恢复长按前的**实际**速度（1× 与 1.5×）。"""
    gesture = _open_and_press(session, mode="fullscreen", phase="accelerating", rate=start_rate)

    switch = _switch_viewing_mode(session, mode="fullscreen")
    assert session.read().playback_rate == start_rate, f"退出自制全屏必须立即恢复长按前的速度（{switch}）"

    session.clock.advance(HOLD_MS * 4)
    state = session.read()
    assert state.playback_rate == start_rate, "退出自制全屏后不得自行回到 2×"
    assert state.paused is False, "中断不得启动或暂停播放"

    gesture.up()
    assert session.read().playback_rate == start_rate
    _press_evidence(gesture, engine=session.harness.engine, what="退出自制全屏期间的按压")


# --------------------------------------------------------------------------- P6：切到后台


@pytest.mark.parametrize("mode", MODES)
def test_page_hidden_ends_waiting_press_and_returning_foreground_revives_nothing(
    session: PlayerSession, mode: str
) -> None:
    """P6：等待期间切到后台取消本次长按；返回前台后速度仍是长按前速度，且没有因此开始播放。

    证据强度：可见性变化是**页面内合成**（弱证据，见模块 docstring），真机后台切换由票据 05 验收。
    """
    gesture = _open_and_press(session, mode=mode, phase="waiting")
    baseline = _visible_content(session)
    session.clock.advance(WAITING_MS)  # 累计半个门槛，仍在门槛内
    assert session.read().playback_rate == 1.0, "前置条件：仍在等待期"

    evidence = _set_page_visibility(session, hidden=True)
    session.clock.advance(HOLD_MS * 4)
    assert session.read().playback_rate == 1.0, f"切到后台必须取消等待（{evidence}）"

    _set_page_visibility(session, hidden=False)
    session.clock.advance(REVIVAL_MS)
    state = session.read()
    assert state.playback_rate == 1.0, "返回前台后速度仍必须是长按前的速度"
    assert state.paused is False, "切后台与返回前台都不得开始播放或暂停播放"
    assert _visible_content(session) == baseline, "切后台与返回前台不得新增任何可见提示"

    gesture.up()
    assert session.read().playback_rate == 1.0
    _press_evidence(gesture, engine=session.harness.engine, what="切后台期间的按压")


@pytest.mark.parametrize("mode", MODES)
def test_page_hidden_restores_rate_while_accelerated(session: PlayerSession, mode: str) -> None:
    """P6：加速期间切到后台立即恢复原速，返回前台也不出现 2×、不改变播放状态（弱证据）。"""
    gesture = _open_and_press(session, mode=mode, phase="accelerating")

    evidence = _set_page_visibility(session, hidden=True)
    assert session.read().playback_rate == 1.0, f"切到后台必须立即恢复长按前的速度（{evidence}）"

    session.clock.advance(HOLD_MS * 4)
    assert session.read().playback_rate == 1.0, "后台期间不得遗留临时倍速"

    _set_page_visibility(session, hidden=False)
    session.clock.advance(REVIVAL_MS)
    state = session.read()
    assert state.playback_rate == 1.0, "返回前台后速度仍必须是长按前的速度"
    assert state.paused is False, "切后台与返回前台都不得开始播放或暂停播放"

    gesture.up()
    assert session.read().playback_rate == 1.0
    _press_evidence(gesture, engine=session.harness.engine, what="切后台期间的按压")


# --------------------------------------------------------------------------- P6：系统取消手势


@pytest.mark.parametrize("mode", MODES)
def test_touchcancel_ends_waiting_press(session: PlayerSession, mode: str) -> None:
    """P6：等待期间收到系统取消（`touchcancel`，没有正常松手）→ 取消等待，之后也不触发。"""
    gesture = _open_and_press(session, mode=mode, phase="waiting")
    baseline = _visible_content(session)
    session.clock.advance(WAITING_MS)
    assert session.read().playback_rate == 1.0, "前置条件：仍在等待期"

    gesture.cancel()
    session.clock.advance(HOLD_MS * 4)
    state = session.read()
    assert state.playback_rate == 1.0, "系统取消必须取消等待，之后不得延迟触发"
    assert state.paused is False, "系统取消不得启动或暂停播放"
    assert _visible_content(session) == baseline, "系统取消不得新增任何可见提示"

    _press_evidence(gesture, engine=session.harness.engine, what="等待期的系统取消")


@pytest.mark.parametrize("mode", MODES)
def test_touchcancel_restores_rate_while_accelerated(session: PlayerSession, mode: str) -> None:
    """P6：加速期间收到系统取消 → 立即恢复原速且不遗留临时倍速（没有正常松手也要恢复）。"""
    gesture = _open_and_press(session, mode=mode, phase="accelerating", rate=1.5)

    gesture.cancel()
    session.clock.advance(HOLD_MS * 4)
    state = session.read()
    assert state.playback_rate == 1.5, "系统取消必须立即恢复长按前的实际速度"
    assert state.paused is False, "系统取消不得启动或暂停播放"

    _press_evidence(gesture, engine=session.harness.engine, what="加速期的系统取消")


# --------------------------------------------------------------------------- P6：中断不启动播放


@pytest.mark.parametrize("mode", MODES)
def test_interruption_never_starts_playback_while_video_is_paused(session: PlayerSession, mode: str) -> None:
    """P6（中断不启动播放）：暂停状态下的按压遇上三种中断，仍不得开始播放，也不进入临时倍速。

    「切到后台」是合成事件（弱证据），另两种是真实输入（引擎级 touchCancel / 真实按钮点击）。
    """
    _open_playing_session(session, mode=mode)
    session.page.evaluate("() => { document.getElementById('v').pause(); }")
    session.wait_for(lambda state: state.paused, "Video 进入暂停")

    interrupters = (
        ("系统取消触摸", lambda gesture: gesture.cancel()),
        ("切到后台", lambda gesture: _set_page_visibility(session, hidden=True)),
        ("切换观看模式", lambda gesture: _switch_viewing_mode(session, mode=mode)),
    )
    for what, interrupt in interrupters:
        gesture = _press_into_phase(session, phase="waiting")
        assert session.read().paused is True, f"前置条件：{what} 前 Video 仍暂停"
        interrupt(gesture)
        session.clock.advance(HOLD_MS * 2)
        state = session.read()
        assert state.paused is True, f"暂停状态下的按压遇上{what}不得开始播放"
        assert state.playback_rate == 1.0, f"暂停状态下的按压遇上{what}不得改变速度"
        _press_evidence(gesture, engine=session.harness.engine, what=f"{what}期间的按压")


# --------------------------------------------------------------------------- P7：旧手势不复活


@pytest.mark.parametrize("mode", MODES)
def test_ended_press_never_revives_after_background_roundtrip(session: PlayerSession, mode: str) -> None:
    """P7：切后台结束本次长按；回到前台后手指继续按住、继续移动都不会让旧手势复活。

    可见性是页面内合成事件（弱证据）；结束后的移动走合成通道（`move_exact_by`），只用来证明
    「移动不复活」，不构成触摸平台结论。
    """
    gesture = _open_and_press(session, mode=mode, phase="waiting")
    _set_page_visibility(session, hidden=True)
    session.clock.advance(REVIVAL_MS)
    assert session.read().playback_rate == 1.0, "切后台必须结束本次长按"

    _set_page_visibility(session, hidden=False)
    session.clock.advance(REVIVAL_MS)
    state = session.read()
    assert state.playback_rate == 1.0, "回到前台后旧手势不得复活（不得延迟触发临时倍速）"
    assert state.paused is False, "后台往返不得改变播放状态"

    gesture.move_exact_by(_FAR_MOVE_PX, 0.0)  # 手指继续移动：位移已不参与判定
    dx, dy, distance = _last_move(gesture)
    assert distance >= _FAR_MIN, f"这一步移动必须真的到达页面（读回 dx={dx!r} dy={dy!r}）"
    session.clock.advance(REVIVAL_MS)
    assert session.read().playback_rate == 1.0, "结束后的移动不得让旧手势复活"

    gesture.up()
    session.clock.advance(REVIVAL_MS)
    assert session.read().playback_rate == 1.0, "抬起后也不得触发"
    _press_evidence_with_synthetic_moves(gesture, engine=session.harness.engine, what="后台往返期间的按压")


@pytest.mark.parametrize("mode", MODES)
def test_ended_press_never_revives_after_viewing_mode_switch(session: PlayerSession, mode: str) -> None:
    """P7：切换观看模式结束本次长按；切换后手指继续按住、继续移动都不会让旧手势复活。

    切换是真实按钮点击（引擎级输入）；结束后的移动走合成通道，只用来证明「移动不复活」。
    """
    gesture = _open_and_press(session, mode=mode, phase="waiting")
    switch = _switch_viewing_mode(session, mode=mode)
    session.clock.advance(REVIVAL_MS)
    assert session.read().playback_rate == 1.0, f"切换观看模式必须结束本次长按（{switch}）"

    gesture.move_exact_by(_FAR_MOVE_PX, 0.0)  # 手指继续移动：位移已不参与判定
    dx, dy, distance = _last_move(gesture)
    assert distance >= _FAR_MIN, f"这一步移动必须真的到达页面（读回 dx={dx!r} dy={dy!r}）"
    session.clock.advance(REVIVAL_MS)
    assert session.read().playback_rate == 1.0, "结束后的移动不得让旧手势复活"

    switch_back = _switch_viewing_mode(session, mode="fullscreen" if mode == "inline" else "inline")
    session.clock.advance(REVIVAL_MS)
    state = session.read()
    assert state.playback_rate == 1.0, f"切回原模式后旧手势也不得复活（{switch_back}）"
    assert state.paused is False, "切换观看模式不得改变播放状态"

    gesture.up()
    assert session.read().playback_rate == 1.0
    _press_evidence_with_synthetic_moves(gesture, engine=session.harness.engine, what="观看模式切换期间的按压")


# --------------------------------------------------------------------------- P7：重新计时


@pytest.mark.parametrize("mode", MODES)
def test_press_after_cancel_waits_full_hold_ms_again(session: PlayerSession, mode: str) -> None:
    """P7：系统取消后重新按下必须重新等待完整 门槛（受控时间验证门槛两侧）。

    第一次按压只消耗了 WAITING_MS；若实现沿用了之前的按住时长，第二次按压会在远早于门槛时触发 2×，
    门槛前的断言就会失败。
    """
    first = _open_and_press(session, mode=mode, phase="waiting")
    first.cancel()
    session.clock.advance(50)

    second = _press(session)
    session.clock.advance(HOLD_MS - 1)
    assert session.read().playback_rate == 1.0, "取消后重新按下不得沿用之前的按住时长（门槛前）"

    session.clock.advance(1)
    assert session.read().playback_rate == TEMPORARY_RATE, "重新按下满 门槛才触发 2×"

    second.up()
    assert session.read().playback_rate == 1.0, "松手恢复原速"
    _press_evidence(second, engine=session.harness.engine, what="取消后重新按压")


def test_press_after_fullscreen_interruption_waits_full_hold_ms_again(session: PlayerSession) -> None:
    """P7：进入自制全屏结束手势后，在新模式里重新按下同样要重新等待完整 门槛。"""
    first = _open_and_press(session, mode="inline", phase="waiting")
    session.clock.advance(WAITING_MS)
    _switch_viewing_mode(session, mode="inline")
    assert session.read().playback_rate == 1.0, "进入自制全屏必须结束本次长按"

    second = _press(session)
    session.clock.advance(HOLD_MS - 1)
    assert session.read().playback_rate == 1.0, "自制全屏里重新按下不得沿用之前已按住的时间"

    session.clock.advance(1)
    assert session.read().playback_rate == TEMPORARY_RATE, "满 门槛后触发 2×"

    second.up()
    assert session.read().playback_rate == 1.0
    _press_evidence(second, engine=session.harness.engine, what="全屏切换后重新按压")


@pytest.mark.parametrize("mode", MODES)
def test_press_after_background_roundtrip_waits_full_hold_ms_again(session: PlayerSession, mode: str) -> None:
    """P7：后台往返结束手势后，重新按下同样要重新等待完整 门槛（弱证据：合成可见性变化）。"""
    first = _open_and_press(session, mode=mode, phase="waiting")
    session.clock.advance(WAITING_MS)
    evidence = _set_page_visibility(session, hidden=True)
    session.clock.advance(HOLD_MS)
    assert session.read().playback_rate == 1.0, f"切到后台必须取消等待（{evidence}）"
    _set_page_visibility(session, hidden=False)
    first.up()

    second = _press(session)
    session.clock.advance(HOLD_MS - 1)
    assert session.read().playback_rate == 1.0, "返回前台后重新按下不得沿用之前的按住时长"

    session.clock.advance(1)
    assert session.read().playback_rate == TEMPORARY_RATE, "重新按下满 门槛才触发 2×"

    second.up()
    assert session.read().playback_rate == 1.0
    _press_evidence(second, engine=session.harness.engine, what="后台往返后重新按压")


# --------------------------------------------------------------------------- P10：长按结束不误触


@pytest.mark.parametrize("mode", MODES)
def test_recognized_long_press_release_and_cancel_do_not_toggle_playback(session: PlayerSession, mode: str) -> None:
    """P10：已识别长按正常松手结束、以及被系统取消时，播放/暂停状态都保持不变。"""
    _open_playing_session(session, mode=mode)

    released = _press_into_phase(session, phase="accelerating")
    released.up()
    state = session.read()
    assert state.playback_rate == 1.0, "松手恢复长按前的速度"
    assert state.paused is False, "正常松手结束已识别的长按不得额外切换播放/暂停"
    session.clock.advance(HOLD_MS)
    _assert_no_extra_toggle(session, was_paused=False, what="正常松手结束")
    _press_evidence(released, engine=session.harness.engine, what="正常松手结束的长按")

    cancelled = _press_into_phase(session, phase="accelerating")
    cancelled.cancel()
    state = session.read()
    assert state.playback_rate == 1.0, "系统取消必须恢复长按前的速度"
    assert state.paused is False, "取消已识别的长按不得额外切换播放/暂停"
    session.clock.advance(HOLD_MS)
    _assert_no_extra_toggle(session, was_paused=False, what="系统取消已识别的长按")
    _press_evidence(cancelled, engine=session.harness.engine, what="被取消的长按")


# --------------------------------------------------------------------------- P10：短按与改动前一致


@pytest.mark.parametrize("mode", MODES)
def test_sub_threshold_press_keeps_pre_change_behaviour(session: PlayerSession, mode: str) -> None:
    """P10：短于门槛的按压（0 / 门槛的 1/4 / 门槛前 1 毫秒）不改变速度、不改变播放/暂停状态，并与引擎基线一致。

    两层断言见模块 docstring：子门槛按压序列本身的行为，以及可信真实 tap 的引擎基线（webkit 切换、
    chromium 不切换）没有被本次改动掩平。按压序列在 chromium 上全程可信；webkit 上只能是合成事件，
    因此 webkit 的「短按一致」由合成按压 + 可信 tap 基线共同承担（弱证据部分如实标注）。
    """
    _open_playing_session(session, mode=mode)
    point = session.point_in_video(**_PRESS_FRAC)
    baseline = session.read()
    assert baseline.paused is False, "前置条件：Video 正在播放"

    observed: list[tuple[int, bool, float]] = []
    for duration in (0, WAITING_MS, HOLD_MS - 1):
        gesture = session.touch()
        gesture.down(*point)
        if duration:
            session.clock.advance(duration)
        gesture.up()
        session.clock.advance(HOLD_MS * 2)
        state = session.read()
        assert state.playback_rate == 1.0, f"{duration} 毫秒（短于门槛）的按压不得改变速度"
        assert state.paused is baseline.paused, (
            f"{duration} 毫秒（短于门槛）的按压不得改变播放/暂停状态"
        )
        observed.append((duration, state.paused, state.playback_rate))
        _press_evidence(gesture, engine=session.harness.engine, what=f"{duration} 毫秒短按")

    assert len({(paused, rate) for _, paused, rate in observed}) == 1, (
        f"同引擎的多种子门槛时长必须结果一致：{observed}"
    )

    # 可信输入基线核对（01/02 实测记录，本票复测）：改动不得掩平引擎差异。
    before_tap = session.read()
    session.tap(*point)
    session.page.wait_for_timeout(300)
    after_tap = session.read()
    if _TRUSTED_TAP_TOGGLES[session.harness.engine]:
        assert after_tap.paused is not before_tap.paused, (
            "webkit 的真实 tap 点击画面会切换播放/暂停（01/02 记录基线），本次改动不得掩平"
        )
    else:
        assert after_tap.paused is before_tap.paused, (
            "chromium 的 touch tap 不切换播放/暂停（01/02 记录基线），本次改动不得掩平"
        )
    assert after_tap.playback_rate == 1.0, "真实 tap 也不得改变播放速度"
