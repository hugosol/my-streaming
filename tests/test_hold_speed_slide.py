"""滑动不结束长按临时倍速（2026-09-20 用户决定，取代原「12 CSS 像素越界即取消」规则）。

规则（背景与理由见 `docs/adr/0002-hold-speed-ignores-drag.md`）：手势开始后，手指位移**不**参与判定
——等待期滑出多远都照常满门槛触发固定 2×，加速期滑出多远都保持 2×；结束只有两条路：松手，或中断
（切到后台 / 进入或退出自制全屏 / 系统取消触摸 / 第二根手指落下，覆盖在 `test_hold_speed_interruptions.py`）。

覆盖范围与输入证据强度：

| 覆盖 | 用例 | 输入证据强度 |
|---|---|---|
| 等待期任意位移都不取消，满门槛仍触发 2×（两种观看模式） | `test_waiting_phase_far_moves_never_cancel_and_still_trigger` | chromium：**CDP 真实触摸全程可信**；webkit：页面内合成（弱证据，webkit 没有可信移动通道） |
| 加速期任意位移都不结束手势、不恢复速度（1× / 1.5× 起步，两种模式） | `test_accelerating_phase_far_moves_never_end_the_gesture` | 同上 |
| 滑出再滑回：不取消、也不重新计时（满门槛照常触发） | `test_slide_out_and_back_neither_cancels_nor_restarts_the_timer` | 同上 |
| 达标前抬手仍然结束手势：滑出再远也不得「稍后触发」 | `test_far_move_then_release_before_threshold_never_triggers` | 同上 |
| 滑动路径不新增任何可见内容 | `test_slide_handling_shows_no_new_visible_content` | 可见 DOM 快照（两引擎） |

观察口径：只读 Video 元素公开属性（`paused` / `playbackRate`）、`body.custom-fullscreen` 与可见
DOM；不读实现私有变量、内部计时器或手势状态字段。规则里已经没有阈值，所以用例不做数值边界断言，
但每一步滑动都断言**页面读回的位移**足够大，防止「移动根本没到达页面」让用例空洞通过。

本文件只交付桌面浏览器的自动化证据；真机覆盖（iOS Safari 上的滑动体感、放大镜/系统手势是否抢走触摸）
由用户按新验收行复验——自动化通过不代表真机结论。webkit 的按压序列全部是页面内合成事件，
只证明页面逻辑，不构成触摸平台结论。
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
HOLD_MS = configured_hold_ms()
TEMPORARY_RATE = 2.0

#: 门槛前先停住的时长：取门槛的 1/4，保证后面推进到门槛时不会越过边界。
WAITING_MS = HOLD_MS // 4

MODES = ("inline", "fullscreen")

#: 按压点比例：落在画面非控件区域（与 02–04 的前置一致，也是 float32 可无损表示的位置）。
_PRESS_FRAC = {"x_frac": 0.5, "y_frac": 0.25}

#: 连续「明显滑动」的相对位移（CSS 像素）：远大于已废除的 12 像素容差，轴向、斜向、反向都覆盖。
_FAR_OFFSETS = ((40.0, 0.0), (0.0, 40.0), (30.0, 30.0), (-25.0, -15.0))

#: 单步读回位移的下限：证明这一步滑动真的到达页面并且足够「明显」。
_FAR_MIN = 20.0

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
    """真实页面 + 真实播放 + 受控时间，停在指定观看模式（与 02–04 的前置一致）。

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


def _position(gesture: TouchGesture) -> tuple[float, float]:
    """最近一个带坐标的事件的位置（页面读回值，CSS 像素）。"""
    for event in reversed(gesture.evidence.events):
        if event.x is not None and event.y is not None:
            return event.x, event.y
    raise AssertionError(f"证据里没有任何坐标：{gesture.evidence.events}")


def _move_far(
    gesture: TouchGesture, *, engine: str, offset: tuple[float, float]
) -> tuple[float, float, float]:
    """滑动一步（`offset` 相对当前位置），返回页面读回的 `(dx, dy, 直线距离)`。

    chromium 走 CDP 真实触摸（可信输入）；webkit 没有可信移动通道，只能走页面内合成 `TouchEvent`
    （弱证据）。两种情况都断言读回的单步位移 ≥ `_FAR_MIN`——移动被引擎丢弃时用例必须失败，
    不能因为「事件没到页面」而空洞通过。
    """
    x0, y0 = _position(gesture)
    if engine == "chromium":
        gesture.move_by(*offset)
    else:
        gesture.move_exact_by(*offset)
    x1, y1 = _position(gesture)
    distance = math.hypot(x1 - x0, y1 - y0)
    assert distance >= _FAR_MIN, (
        f"这一步滑动必须真的到达页面并且足够远（读回 {distance!r} CSS 像素，期望 {offset}）"
    )
    return x1 - x0, y1 - y0, distance


def _assert_channel(gesture: TouchGesture, *, engine: str) -> str:
    """核对本次按压的输入通道并返回如实描述（强弱证据不因本文件通过而升级）。"""
    evidence = gesture.evidence
    assert evidence.engine == engine, f"引擎不符：{evidence}"
    if engine == "chromium":
        assert evidence.synthetic_events == (), f"chromium 上必须全程走可信真实触摸：{evidence.mechanism}"
        assert evidence.trusted_input is True, f"chromium 上的滑动必须是可信输入：{evidence.mechanism}"
    else:
        assert evidence.synthetic_events, f"webkit 的序列必须是页面内合成事件：{evidence.mechanism}"
        assert evidence.trusted_input is False, f"webkit 的按压序列只能是合成事件（弱证据）：{evidence}"
    return f"{evidence.strength}（{evidence.mechanism}）"


# --------------------------------------------------------------------------- 位移不结束手势


@pytest.mark.parametrize("mode", MODES)
def test_waiting_phase_far_moves_never_cancel_and_still_trigger(session: PlayerSession, mode: str) -> None:
    """等待期：连续明显滑动（轴向、斜向、反向）都不取消等待，满门槛照常触发固定 2×，松手恢复原速。"""
    _open_playing_session(session, mode=mode)
    gesture = _press(session)
    session.clock.advance(WAITING_MS)
    assert session.read().playback_rate == 1.0, "前置条件：尚未到门槛"

    for offset in _FAR_OFFSETS:
        _, _, distance = _move_far(gesture, engine=session.harness.engine, offset=offset)
        assert session.read().playback_rate == 1.0, (
            f"等待期的滑动 {offset}（{distance:.1f} CSS 像素）不得取消等待"
        )

    session.clock.advance(HOLD_MS - WAITING_MS)
    state = session.read()
    assert state.playback_rate == TEMPORARY_RATE, "滑动多远都必须满门槛触发固定 2×"
    assert state.paused is False, "按住期间 Video 仍在播放"

    gesture.up()
    assert session.read().playback_rate == 1.0, "松手恢复长按前的速度"
    _assert_channel(gesture, engine=session.harness.engine)


@pytest.mark.parametrize("start_rate", (1.0, 1.5))
@pytest.mark.parametrize("mode", MODES)
def test_accelerating_phase_far_moves_never_end_the_gesture(
    session: PlayerSession, mode: str, start_rate: float
) -> None:
    """加速期：连续明显滑动都不结束手势、不恢复速度；继续按住仍是 2×，只有松手才恢复长按前实际速度。"""
    _open_playing_session(session, mode=mode, rate=start_rate)
    gesture = _press(session)
    session.clock.advance(HOLD_MS)
    assert session.read().playback_rate == TEMPORARY_RATE, "前置条件：已进入临时倍速"

    for offset in _FAR_OFFSETS:
        _, _, distance = _move_far(gesture, engine=session.harness.engine, offset=offset)
        assert session.read().playback_rate == TEMPORARY_RATE, (
            f"加速期的滑动 {offset}（{distance:.1f} CSS 像素）不得结束手势或恢复原速"
        )

    session.clock.advance(HOLD_MS * 2)
    assert session.read().playback_rate == TEMPORARY_RATE, "滑动后继续按住仍是 2×，不得漂移或自行恢复"

    gesture.up()
    assert session.read().playback_rate == start_rate, "松手才恢复长按前的实际速度"
    _assert_channel(gesture, engine=session.harness.engine)


@pytest.mark.parametrize("mode", MODES)
def test_slide_out_and_back_neither_cancels_nor_restarts_the_timer(session: PlayerSession, mode: str) -> None:
    """滑出一大段再滑回：等待期照常计时——不取消，也不重新计时；满门槛即触发 2×。"""
    _open_playing_session(session, mode=mode)
    gesture = _press(session)
    session.clock.advance(WAITING_MS)

    _move_far(gesture, engine=session.harness.engine, offset=(60.0, 0.0))
    assert session.read().playback_rate == 1.0, "滑出不得取消等待"

    _move_far(gesture, engine=session.harness.engine, offset=(-55.0, 0.0))
    assert session.read().playback_rate == 1.0, "滑回不得触发临时倍速"

    session.clock.advance(HOLD_MS - WAITING_MS)
    assert session.read().playback_rate == TEMPORARY_RATE, (
        "门槛从按下起算：滑出/滑回不得重置计时（若被重置，这里仍是原速）"
    )

    gesture.up()
    assert session.read().playback_rate == 1.0, "松手恢复长按前的速度"
    _assert_channel(gesture, engine=session.harness.engine)


@pytest.mark.parametrize("mode", MODES)
def test_far_move_then_release_before_threshold_never_triggers(session: PlayerSession, mode: str) -> None:
    """达标前抬手仍然结束手势：滑出再远，松手后推进多久都不得触发 2×。"""
    _open_playing_session(session, mode=mode)
    gesture = _press(session)
    session.clock.advance(WAITING_MS)
    _move_far(gesture, engine=session.harness.engine, offset=(0.0, 60.0))
    assert session.read().playback_rate == 1.0, "前置条件：滑出后仍在等待期"

    gesture.up()
    session.clock.advance(HOLD_MS * 4)
    state = session.read()
    assert state.playback_rate == 1.0, "达标前抬手即结束：滑动再远也不得延迟触发"
    assert state.paused is False, "结束手势不得改变播放状态"
    _assert_channel(gesture, engine=session.harness.engine)


# --------------------------------------------------------------------------- 不新增提示


@pytest.mark.parametrize("mode", MODES)
def test_slide_handling_shows_no_new_visible_content(session: PlayerSession, mode: str) -> None:
    """不新增提示：等待期滑动、加速期滑动、松手恢复三个阶段，可见内容与按压前完全一致。"""
    _open_playing_session(session, mode=mode)
    baseline = session.page.evaluate(_VISIBLE_JS)
    assert baseline["inventory"], "前置条件：页面上必须能取到可见元素清单"

    gesture = _press(session)
    session.clock.advance(WAITING_MS)
    assert session.page.evaluate(_VISIBLE_JS) == baseline, "等待阶段不得出现提示"

    _move_far(gesture, engine=session.harness.engine, offset=(40.0, 0.0))
    assert session.page.evaluate(_VISIBLE_JS) == baseline, "等待期滑动不得出现提示"

    session.clock.advance(HOLD_MS - WAITING_MS)
    assert session.read().playback_rate == TEMPORARY_RATE, "前置条件：滑动后仍满门槛触发 2×"
    assert session.page.evaluate(_VISIBLE_JS) == baseline, "加速阶段不得出现提示"

    _move_far(gesture, engine=session.harness.engine, offset=(0.0, 40.0))
    assert session.page.evaluate(_VISIBLE_JS) == baseline, "加速期滑动不得出现提示"

    gesture.up()
    assert session.read().playback_rate == 1.0, "松手恢复原速"
    assert session.page.evaluate(_VISIBLE_JS) == baseline, "恢复阶段不得出现提示"
