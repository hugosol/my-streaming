"""票据 03：位移容差与滑动取消（P4 / P5 的自动化覆盖项）。

覆盖范围与输入证据强度（完成记录见 `.scratch/hold-to-double-speed/implementation/03-slide-tolerance-and-cancel.md`）：

| 承诺 | 本文件覆盖 | 输入证据强度 |
|---|---|---|
| P4 | 等待触发期间：零位移、轻微位移、恰好 12 CSS 像素（两个轴向）、斜向紧贴门槛内侧；连续多次移动都不取消，满 500 毫秒仍触发 2× | chromium：**CDP 真实按下 + 页面内合成精确移动**（移动是弱证据，见下）；webkit：全序列页面内合成（弱证据） |
| P4 | 已加速期间：同样的位移集合都不结束手势，维持 2× | 同上 |
| P4 | 门槛按 CSS 像素：设备像素比 2 下 12 CSS 像素仍是门槛内侧 | 同上 |
| P5 | 等待期间越过门槛：立即取消，且移回原位、继续按住、再次移动、抬起都不会延迟触发 | 同上 |
| P5 | 已加速期间越过门槛：立即恢复长按前的实际速度（1× 与 1.5× 各一次） | 同上 |
| P5 | 位移按起点至当前点的直线距离：来回移动（累计路径 48 像素）不取消；斜向 9/9（分量都 ≤12、直线 12.73）必须取消 | 同上 |
| P5 | chromium 上引擎级真实移动越过门槛同样结束手势（唯一可用可信输入观察的位移路径） | chromium：**CDP 真实触摸全程可信**；webkit 跳过（没有可信移动通道） |
| 不新增提示 | 保持、等待、取消三条路径上可见内容与按压前完全一致 | 可见 DOM 快照（两引擎） |

**为什么门槛内侧必须用合成移动（弱证据）**：chromium 会丢弃首个小于 16 CSS 像素的真实
touchmove（票据 01 发现、本票复测：2/4/8/12/14 像素在页面上一无所有，16/20/40 像素才送达），
所以「≤12 CSS 像素不取消」这一侧在 chromium 上**不可能**用可信输入观察到——若照原样写用例，
它会因为「事件根本没到页面」而空洞通过。本文件因此：

- 门槛内侧与恰好 12 的位移一律用 `TouchGesture.move_exact_*`（页面内合成 `TouchEvent`，坐标由脚本
  精确给出，`isTrusted === false`）= **弱证据**，且每个用例都核对 `evidence` 里读回的真实位移；
- 门槛外侧另有一条**可信输入**用例（chromium 真实 16 像素移动），证明真实触摸路径同样会取消；
- 引擎对合成坐标的量化（chromium 压到 float32）如实记录在 `evidence.events` 里，边界用例断言的是
  读回值而不是打算派发的值，按压点也选在 float32 无损的位置。

观察口径：只读 Video 元素公开属性（`paused` / `playbackRate`）、`body.custom-fullscreen` 与可见
DOM；不读实现私有变量、内部计时器或手势状态字段。每条用例都用 `TouchGesture.evidence` 标注强度。

本文件只交付 P4 / P5 的**自动化覆盖项**；真机覆盖项（自制横屏旋转后的屏幕尺度、真实滑动体感）
由票据 05 承载，因此自动化通过不代表 P4、P5 已完成。webkit 的按压序列全部是合成事件：
它只证明页面逻辑，不构成触摸平台结论。
"""

from __future__ import annotations

import math

import pytest

from player_harness import (  # noqa: F401  (下面这些是有名字的 pytest 夹具，必须导入本模块才可见)
    PLAYWRIGHT_ENGINES,
    PlayerHarness,
    PlayerSession,
    TouchGesture,
    harness,
    harness_factory,
    playwright_instance,
    session,
)

pytestmark = pytest.mark.parametrize("engine", PLAYWRIGHT_ENGINES)

#: 契约固定值：门槛 500 毫秒、临时速度绝对值 2×（与 02 一致）、位移容差 12 **CSS** 像素。
HOLD_MS = 500
TEMPORARY_RATE = 2.0
SLOP_PX = 12.0

MODES = ("inline", "fullscreen")

#: 按压点比例。`y_frac=0.25` 让普通页面下的 y（34 + 566/4 = 175.5）与自制全屏下的 y（150.0）
#: 都是 float32 可无损表示的值：chromium 会把合成触摸坐标压到 float32，按压点不在这种位置上时
#: 「恰好 12 像素」的位移在页面上会变成 12.000000000000389（实测），边界用例就失去意义。
_PRESS_FRAC = {"x_frac": 0.5, "y_frac": 0.25}

#: 门槛内侧的相对移动（CSS 像素）：零位移、轻微、恰好 12（两个轴向）、斜向紧贴门槛内侧（11.986）。
_WITHIN_12 = ((0.0, 0.0), (5.0, 0.0), (12.0, 0.0), (0.0, 12.0), (7.19, 9.59))

#: 门槛外侧的相对移动：轴向 12.5 / 13、斜向 12.014、斜向 9/9（两个分量都 ≤12，直线距离 12.73）。
#: 斜向恰好 12 无法构造：3-4-5 的斜边 12 在二进制浮点里不可精确表示（7.2/9.6 的实测直线距离是
#: 11.99999999999999 或 12.00000000000001，取决于坐标），所以斜向用两侧紧贴的 11.986 / 12.014，
#: 「恰好 12」由轴向的精确值承担。
_BEYOND_12 = ((12.5, 0.0), (0.0, 13.0), (7.21, 9.61), (9.0, 9.0))

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


def _open_playing_session(target: PlayerSession, *, mode: str, rate: float = 1.0) -> PlayerSession:
    """真实页面 + 真实播放 + 受控时间，停在指定观看模式（与 02 的前置一致）。

    `rate` 由公开 API 预设（页面当前没有倍速入口），`v.loop = true` 让「正在播放」这一前置
    在冻结假时钟下稳定成立（同 02 的说明）。
    """
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


def _delivered_moves(gesture: TouchGesture) -> list[tuple[float, float, float]]:
    """本次按压里**页面实际收到**的每一步移动：相对按下点的 `(dx, dy, 直线距离)`（CSS 像素）。

    坐标取自 `evidence.events`（合成移动记录的是引擎量化后的读回值），所以断言的是页面真实的
    几何输入，而不是用例打算派发的值。
    """
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


def _last_distance(gesture: TouchGesture) -> float:
    return _delivered_moves(gesture)[-1][2]


def _assert_exact_channel(gesture: TouchGesture, *, engine: str) -> str:
    """核对本次按压的通道并返回如实描述：移动必须是页面内合成（精确坐标），按下/抬起是引擎输入。

    chromium 上 ≤12 像素的移动只能由合成通道给出（CDP 会丢弃它），这条核对防止用例悄悄换成
    另一个通道后仍然绿灯：那时「门槛内侧不取消」会因为事件没到页面而空洞通过。
    """
    evidence = gesture.evidence
    assert evidence.engine == engine, f"引擎不符：{evidence}"
    synthetic = evidence.synthetic_events
    assert synthetic, f"本次按压必须包含合成精确移动：{evidence.mechanism}"
    trusted = [record.kind for record in evidence.events if record.trusted]
    if engine == "chromium":
        assert all(record.kind == "touchmove" for record in synthetic), (
            f"只有移动允许走合成通道，按下/抬起必须是引擎输入：{evidence.mechanism}"
        )
        assert trusted == ["touchstart", "touchend"], f"chromium 的按下/抬起必须是真实触摸：{evidence.mechanism}"
        assert evidence.trusted_input is False, "混用通道时必须如实标成弱证据（混合）"
    else:
        assert trusted == [], f"webkit 的按压序列只能是合成事件（弱证据）：{evidence.mechanism}"
    return f"{evidence.strength}（{evidence.mechanism}）"


def _assert_trusted_channel(gesture: TouchGesture, *, engine: str) -> str:
    """核对本次按压**全程**都是引擎级真实输入（只有 chromium 能构造这种移动）。"""
    evidence = gesture.evidence
    assert evidence.engine == engine == "chromium", f"只有 chromium 有可信移动通道：{evidence}"
    assert evidence.synthetic_events == (), f"本次按压不得包含合成事件：{evidence.mechanism}"
    assert evidence.trusted_input is True, f"本次按压必须全程可信：{evidence.mechanism}"
    return f"{evidence.strength}（{evidence.mechanism}）"


# --------------------------------------------------------------------------- P4：门槛内侧不取消


@pytest.mark.parametrize("mode", MODES)
def test_waiting_phase_survives_every_in_tolerance_move_and_still_triggers(session: PlayerSession, mode: str) -> None:
    """P4：等待触发期间零位移、轻微位移、恰好 12 CSS 像素（两个轴向）、斜向紧贴门槛内侧都不取消；
    连续多笔移动也不取消，满 500 毫秒仍触发 2×。"""
    _open_playing_session(session, mode=mode)
    gesture = _press(session)
    session.clock.advance(200)
    assert session.read().playback_rate == 1.0, "前置条件：尚未到 500 毫秒门槛"

    for offset in _WITHIN_12:
        gesture.move_exact_by(*offset)
        distance = _last_distance(gesture)
        assert distance <= SLOP_PX, f"偏移 {offset} 在页面上必须落在门槛内侧（实际 {distance!r}）"
        assert session.read().playback_rate == 1.0, f"偏移 {offset}（{distance:.3f} CSS 像素）不得取消等待"

    recorded = [distance for _, _, distance in _delivered_moves(gesture)]
    assert 12.0 in recorded, f"必须有一笔位移在页面上精确等于 12 CSS 像素（实测 {recorded}）"

    session.clock.advance(HOLD_MS - 200)
    state = session.read()
    assert state.playback_rate == TEMPORARY_RATE, "门槛内侧的移动不得影响满 500 毫秒触发 2×"
    assert state.paused is False, "按住期间 Video 仍在播放"

    gesture.up()
    assert session.read().playback_rate == 1.0, "松手恢复长按前的速度"
    _assert_exact_channel(gesture, engine=session.harness.engine)


@pytest.mark.parametrize("mode", MODES)
def test_accelerating_phase_survives_every_in_tolerance_move(session: PlayerSession, mode: str) -> None:
    """P4：已加速期间零位移、轻微位移、恰好 12 CSS 像素、斜向内侧都不结束手势，维持 2×。"""
    _open_playing_session(session, mode=mode)
    gesture = _press(session)
    session.clock.advance(HOLD_MS)
    assert session.read().playback_rate == TEMPORARY_RATE, "前置条件：已进入临时倍速"

    for offset in _WITHIN_12:
        gesture.move_exact_by(*offset)
        distance = _last_distance(gesture)
        assert distance <= SLOP_PX, f"偏移 {offset} 在页面上必须落在门槛内侧（实际 {distance!r}）"
        assert session.read().playback_rate == TEMPORARY_RATE, (
            f"偏移 {offset}（{distance:.3f} CSS 像素）不得结束已加速的手势"
        )

    session.clock.advance(HOLD_MS * 2)
    assert session.read().playback_rate == TEMPORARY_RATE, "移动后继续按住仍保持 2×，不得漂移"

    gesture.up()
    assert session.read().playback_rate == 1.0, "松手恢复长按前的速度"
    _assert_exact_channel(gesture, engine=session.harness.engine)


# --------------------------------------------------------------------------- P5：越过门槛结束手势


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("offset", _BEYOND_12)
def test_waiting_phase_move_beyond_12px_cancels_and_never_fires_later(
    session: PlayerSession, mode: str, offset: tuple[float, float]
) -> None:
    """P5：等待期间位移超过 12 CSS 像素立即取消本次长按；之后移回原位、继续按住、再次移动、
    抬起都不会延迟触发临时倍速。"""
    _open_playing_session(session, mode=mode)
    gesture = _press(session)
    session.clock.advance(200)
    assert session.read().playback_rate == 1.0, "前置条件：尚未到 500 毫秒门槛"

    gesture.move_exact_by(*offset)
    distance = _last_distance(gesture)
    assert distance > SLOP_PX, f"偏移 {offset} 在页面上必须越过门槛外侧（实际 {distance!r}）"

    session.clock.advance(HOLD_MS * 4)
    assert session.read().playback_rate == 1.0, f"越过 {SLOP_PX} CSS 像素（{distance:.3f}）必须取消等待"

    gesture.move_exact_by(0.0, 0.0)  # 移回按下点
    session.clock.advance(HOLD_MS * 4)
    assert session.read().playback_rate == 1.0, "移回原位不得让已取消的手势复活"

    gesture.move_exact_by(5.0, 0.0)  # 再挪一点点（仍在门槛内）
    session.clock.advance(HOLD_MS * 4)
    state = session.read()
    assert state.playback_rate == 1.0, "取消后继续按住 + 再次移动都不得延迟触发"
    assert state.paused is False, "取消手势不得中断播放"

    gesture.up()
    session.clock.advance(HOLD_MS * 4)
    assert session.read().playback_rate == 1.0, "抬起后也不得触发"

    _assert_exact_channel(gesture, engine=session.harness.engine)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("start_rate", (1.0, 1.5))
def test_accelerating_phase_move_beyond_12px_immediately_restores_previous_rate(
    session: PlayerSession, mode: str, start_rate: float
) -> None:
    """P5：已加速期间位移超过 12 CSS 像素立即恢复长按前的**实际**速度（1× 与 1.5× 各验一次）。"""
    _open_playing_session(session, mode=mode, rate=start_rate)
    gesture = _press(session)
    session.clock.advance(HOLD_MS)
    assert session.read().playback_rate == TEMPORARY_RATE, "前置条件：已进入临时倍速"

    gesture.move_exact_by(13.0, 0.0)
    distance = _last_distance(gesture)
    assert distance > SLOP_PX, f"用例的位移必须越过门槛（实际 {distance!r}）"
    assert session.read().playback_rate == start_rate, (
        f"越过门槛必须立即恢复长按前的速度（{distance:.3f} CSS 像素）"
    )

    gesture.move_exact_by(0.0, 0.0)  # 移回原位
    session.clock.advance(HOLD_MS * 2)
    assert session.read().playback_rate == start_rate, "已结束的手势不得因移回原位而复活"

    gesture.up()
    assert session.read().playback_rate == start_rate, "抬起后速度仍是长按前的实际速度"
    _assert_exact_channel(gesture, engine=session.harness.engine)


@pytest.mark.parametrize("mode", MODES)
def test_distance_is_straight_line_from_press_point_not_cumulative_path(
    session: PlayerSession, mode: str
) -> None:
    """P5（距离度量）：来回移动的累计路径 48 像素、直线距离始终 ≤12，不得取消；斜向 9/9 的两个分量
    都 ≤12，但直线距离 12.73 > 12，必须结束手势。

    这条用例区分三种可能实现：按累计路径判定会在来回移动上误取消；按分量判定会在 9/9 上放行；
    只有「起点到当前点的直线距离」同时满足两端。
    """
    _open_playing_session(session, mode=mode)
    gesture = _press(session)

    for offset in ((12.0, 0.0), (0.0, 0.0), (12.0, 0.0), (0.0, 0.0)):
        gesture.move_exact_by(*offset)
        distance = _last_distance(gesture)
        assert distance <= SLOP_PX, f"偏移 {offset} 的直线距离必须落在门槛内侧（实际 {distance!r}）"
        assert session.read().playback_rate == 1.0, "来回移动的累计路径不得取消等待"

    session.clock.advance(HOLD_MS)
    assert session.read().playback_rate == TEMPORARY_RATE, "来回移动后仍应满门槛触发 2×"

    gesture.move_exact_by(9.0, 9.0)
    distance = _last_distance(gesture)
    assert distance > SLOP_PX, f"斜向 9/9 的直线距离必须越过门槛（实际 {distance!r}）"
    assert session.read().playback_rate == 1.0, "斜向 9/9 的分量都 ≤12，但直线距离超门槛，必须结束手势"

    gesture.move_exact_by(0.0, 0.0)
    session.clock.advance(HOLD_MS * 2)
    assert session.read().playback_rate == 1.0, "结束后不得因移回原位而复活"

    gesture.up()
    _assert_exact_channel(gesture, engine=session.harness.engine)


# --------------------------------------------------------------------------- P5：可信输入路径（chromium）


@pytest.mark.parametrize("mode", MODES)
def test_chromium_trusted_move_beyond_12px_cancels_waiting(session: PlayerSession, mode: str) -> None:
    """P5（可信输入）：引擎级真实 touchmove 越过门槛时取消等待——唯一能用可信输入观察的位移路径。

    CDP 会丢弃首个小于 16 CSS 像素的真实移动（见 `player_harness` 模块「实测边界」），所以真实
    输入只能构造「越过 12 像素」的移动；16 像素这里就是引擎能送达的最小位移。门槛内侧由
    `move_exact_*` 的合成通道覆盖（弱证据），真机滑动体感由票据 05 验收。
    """
    if session.harness.engine != "chromium":
        pytest.skip("webkit 没有可信的移动通道（按压序列只能是页面内合成事件）")

    _open_playing_session(session, mode=mode)
    gesture = _press(session)
    session.clock.advance(200)
    assert session.read().playback_rate == 1.0, "前置条件：尚未到 500 毫秒门槛"

    gesture.move_by(16.0, 0.0)
    dx, dy, distance = _delivered_moves(gesture)[-1]
    assert distance > SLOP_PX, f"真实移动必须越过门槛（实际 dx={dx!r} dy={dy!r}）"

    session.clock.advance(HOLD_MS * 4)
    assert session.read().playback_rate == 1.0, "真实触摸越过门槛必须取消等待，之后也不得触发"
    assert session.read().paused is False, "取消手势不得中断播放"

    gesture.up()
    session.clock.advance(HOLD_MS * 4)
    assert session.read().playback_rate == 1.0, "抬起后也不得触发"
    _assert_trusted_channel(gesture, engine="chromium")


@pytest.mark.parametrize("mode", MODES)
def test_chromium_trusted_move_beyond_12px_restores_rate_while_accelerated(
    session: PlayerSession, mode: str
) -> None:
    """P5（可信输入）：已加速期间引擎级真实 touchmove 越过门槛，立即恢复长按前的速度。"""
    if session.harness.engine != "chromium":
        pytest.skip("webkit 没有可信的移动通道（按压序列只能是页面内合成事件）")

    _open_playing_session(session, mode=mode)
    gesture = _press(session)
    session.clock.advance(HOLD_MS)
    assert session.read().playback_rate == TEMPORARY_RATE, "前置条件：已进入临时倍速"

    gesture.move_by(16.0, 0.0)
    dx, _, distance = _delivered_moves(gesture)[-1]
    assert distance > SLOP_PX, f"真实移动必须越过门槛（实际 dx={dx!r}）"
    assert session.read().playback_rate == 1.0, "真实触摸越过门槛必须立即恢复长按前的速度"

    session.clock.advance(HOLD_MS * 2)
    assert session.read().playback_rate == 1.0, "结束后不得自行回到 2×"
    gesture.up()
    assert session.read().playback_rate == 1.0
    _assert_trusted_channel(gesture, engine="chromium")


# --------------------------------------------------------------------------- 门槛按 CSS 像素


def test_threshold_follows_css_pixels_not_device_pixels(harness: PlayerHarness) -> None:
    """P4/P5：设备像素比变为 2 后判定不变——恰好 12 CSS 像素仍是门槛内侧，13 仍是门槛外侧
    （chromium 上真实 16 CSS 像素 = 32 设备像素的移动同样取消）。

    真机自制横屏旋转后的屏幕尺度由票据 05 验收，本用例只证明判定不把设备像素当输入。
    """
    with harness.session(device_scale_factor=2) as high_dpi:
        _open_playing_session(high_dpi, mode="inline")
        assert high_dpi.page.evaluate("() => window.devicePixelRatio") == 2, "前置条件：设备像素比 2 已生效"

        gesture = _press(high_dpi)
        high_dpi.clock.advance(200)
        gesture.move_exact_by(12.0, 0.0)
        distance = _last_distance(gesture)
        assert distance == SLOP_PX, f"恰好 12 CSS 像素必须精确成立（实际 {distance!r}）"
        high_dpi.clock.advance(HOLD_MS - 200)
        assert high_dpi.read().playback_rate == TEMPORARY_RATE, "12 CSS 像素（24 设备像素）仍是门槛内侧"

        gesture.move_exact_by(13.0, 0.0)
        distance = _last_distance(gesture)
        assert distance > SLOP_PX, f"13 CSS 像素必须越过门槛（实际 {distance!r}）"
        assert high_dpi.read().playback_rate == 1.0, "13 CSS 像素（26 设备像素）必须结束手势"

        gesture.up()
        _assert_exact_channel(gesture, engine=high_dpi.harness.engine)

        if high_dpi.harness.engine == "chromium":
            trusted = _press(high_dpi)
            high_dpi.clock.advance(200)
            trusted.move_by(16.0, 0.0)
            assert _last_distance(trusted) > SLOP_PX
            high_dpi.clock.advance(HOLD_MS * 2)
            assert high_dpi.read().playback_rate == 1.0, "真实 16 CSS 像素移动在设备像素比 2 下同样取消"
            trusted.up()
            _assert_trusted_channel(trusted, engine="chromium")


# --------------------------------------------------------------------------- 不新增提示


@pytest.mark.parametrize("mode", MODES)
def test_slide_handling_shows_no_new_visible_content(session: PlayerSession, mode: str) -> None:
    """不新增提示：门槛内侧移动、越过门槛取消、已加速、松手恢复，四个阶段可见内容与按压前一致。"""
    _open_playing_session(session, mode=mode)
    baseline = session.page.evaluate(_VISIBLE_JS)
    assert baseline["inventory"], "前置条件：页面上必须能取到可见元素清单"

    gesture = _press(session)
    session.clock.advance(200)
    assert session.page.evaluate(_VISIBLE_JS) == baseline, "等待阶段不得出现提示"
    gesture.move_exact_by(5.0, 0.0)
    assert session.page.evaluate(_VISIBLE_JS) == baseline, "门槛内侧移动不得出现提示"
    gesture.move_exact_by(13.0, 0.0)
    assert session.page.evaluate(_VISIBLE_JS) == baseline, "越过门槛取消不得出现提示"
    gesture.up()
    assert session.page.evaluate(_VISIBLE_JS) == baseline, "取消后不得留下提示"

    triggered = _press(session)
    session.clock.advance(HOLD_MS)
    assert session.read().playback_rate == TEMPORARY_RATE, "前置条件：已进入临时倍速"
    assert session.page.evaluate(_VISIBLE_JS) == baseline, "加速阶段不得出现提示"
    triggered.move_exact_by(12.0, 0.0)
    assert session.page.evaluate(_VISIBLE_JS) == baseline, "门槛内侧移动不得出现提示（已加速）"
    triggered.up()
    assert session.read().playback_rate == 1.0
    assert session.page.evaluate(_VISIBLE_JS) == baseline, "恢复阶段不得出现提示"
