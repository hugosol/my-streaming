"""票据 02：播放中长按临时倍速（核心规则）的自动化覆盖（P1 / P2 / P3 / P8 / P9）。

覆盖范围与输入证据强度（完成记录见 `.scratch/hold-to-double-speed/implementation/02-hold-temporary-rate-core.md`）：

| 承诺 | 本文件覆盖 | 输入证据强度 |
|---|---|---|
| P1 | 门槛两侧（499 / 500 毫秒）、固定 2×、持续按住保持 2×、左/中/右非控件区域、两种观看模式、1× 与 1.5× 起步 | chromium：CDP `Input.dispatchTouchEvent`（引擎级真实触摸）；webkit：页面内合成 `TouchEvent`（弱证据） |
| P2 | 1×→2×→1×、1.5×→2×→1.5×、未达门槛松手不变、两种模式 | 同上 |
| P3 | 暂停下按住远超 500 毫秒再松开：仍暂停、速度不变、未因此开始播放、两种模式 | 同上 |
| P8 | 等待/加速/恢复三阶段 × 两种模式：可见元素清单与可见文字与基线一致 | 同上（可见 DOM 快照） |
| P9 | 自制全屏进入/退出按钮：停留超过 500 毫秒不触发、按钮仍照常工作；原生控件条的可见操作未被吞掉（桌面证据，仅 chromium） | 按压同上；按钮点亮用引擎级真实 tap；原生控件用真实鼠标（见用例说明） |

观察口径：只读 Video 元素公开属性（`paused` / `playbackRate` / `currentTime` / `readyState`）、
`body.custom-fullscreen`、元素可见性与 `document.elementFromPoint` 命中结果；不读实现私有变量、
内部计时器或私有状态字段，也不以「自己派发的事件被收到」作为结论。

本文件只交付 P1 / P2 / P3 / P8 / P9 的**自动化覆盖项**；真机（iOS Safari）覆盖项由票据 05 承载，
因此自动化通过不代表这 5 个承诺已经完成。webkit 的按压是合成事件（`isTrusted === false`）：
它只证明页面逻辑，不构成触摸平台结论；每个用例都用 `TouchGesture.evidence` 逐条核对强度。
"""

from __future__ import annotations

import pytest

from player_harness import (  # noqa: F401  (下面这些是有名字的 pytest 夹具，必须导入本模块才可见)
    PLAYWRIGHT_ENGINES,
    PlayerSession,
    harness,
    harness_factory,
    playwright_instance,
    session,
)

pytestmark = pytest.mark.parametrize("engine", PLAYWRIGHT_ENGINES)

#: 契约固定值：门槛 500 毫秒、临时速度绝对值 2×（不是当前速度乘二）。
HOLD_MS = 500
TEMPORARY_RATE = 2.0

MODES = ("inline", "fullscreen")

#: 可见内容基线（P8）：容器内只有 `video` 与两个自制全屏按钮，来源是
#: `server/templates/player.html` 与 `server/static/player.css` 的显隐规则，
#: 数值在本功能实现**之前**的真实页面上实测确认（实现后必须完全一致）。
_BASELINE_VISIBLE = {
    "inline": {
        "inventory": [
            "a#back-btn",
            "button#fs-btn",
            "div#player-container",
            "div#top-bar",
            "path",
            "svg",
            "video#v",
        ],
        "text": "\u2190 Back",
    },
    "fullscreen": {
        "inventory": ["button#fs-exit-btn", "div#player-container", "video#v"],
        "text": "\u00d7",  # 自制全屏退出按钮的 &times;
    },
}

_VISIBLE_CONTENT_JS = """
() => {
  const visible = (el) => {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const inventory = Array.from(document.querySelectorAll('body *')).filter(visible)
    .map((el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')).sort();
  return {inventory, text: (document.body.innerText || '').replace(/\\s+/g, ' ').trim()};
}
"""

_ELEMENT_CENTER_JS = """
(selector) => {
  const rect = document.querySelector(selector).getBoundingClientRect();
  return [rect.x + rect.width / 2, rect.y + rect.height / 2];
}
"""


def _open_playing_session(session: PlayerSession, *, mode: str, rate: float = 1.0) -> PlayerSession:
    """真实页面 + 真实播放 + 受控时间，停在指定观看模式。

    `rate` 由公开 API 预设（播放器页面当前没有倍速入口，契约里的「1.5× 起步」只能这样建立），
    并立刻用公开状态确认生效。

    `v.loop = true` 也是公开 API 的夹具设置：夹具视频是短片，而 chromium 上冻结的假时钟会让媒体
    位置虚增（实测在两次读取之间假时钟未动、真实时间约 10ms，`currentTime` 就从 0.75 跳到片尾），
    循环播放让「正在播放」这个前置条件在整条用例里稳定成立；本功能与循环播放无关，判定也不读媒体位置。
    """
    assert mode in MODES
    session.goto(clock=True)
    session.start_playback()
    session.page.evaluate("(rate) => { document.getElementById('v').playbackRate = rate; }", rate)
    session.page.evaluate("() => { document.getElementById('v').loop = true; }")
    session.clock.freeze()
    if mode == "fullscreen":
        session.enter_custom_fullscreen()
    state = session.read()
    assert state.custom_fullscreen is (mode == "fullscreen"), "用例必须停在指定观看模式"
    assert state.paused is False, "前置条件：Video 正在播放"
    assert state.playback_rate == rate, "前置条件：起始速度已按公开 API 生效"
    return session


def _press_picture(session: PlayerSession, *, x_frac: float = 0.5, y_frac: float = 0.3):
    """在画面非控件区域按下（`x_frac` 选左/中/右）。返回 `(gesture, point)`。"""
    point = session.point_in_video(x_frac=x_frac, y_frac=y_frac)
    gesture = session.touch()
    gesture.down(*point)
    assert session.hit_id(*point) == "v", f"按压点 {point} 必须落在视频上，不得被浮层截获"
    return gesture, point


def _evidence(gesture, *, engine: str, what: str):
    """如实核对本次按压的输入证据强度，并返回证据对象。"""
    evidence = gesture.evidence
    assert evidence.engine == engine, f"{what}: 引擎不符 {evidence}"
    if engine == "chromium":
        assert evidence.trusted_input is True, f"{what}: chromium 按压必须是引擎级真实触摸：{evidence}"
    else:
        assert evidence.trusted_input is False, f"{what}: webkit 按压只能是合成事件（弱证据）：{evidence}"
    return evidence


def _visible_content(session: PlayerSession) -> dict:
    return session.page.evaluate(_VISIBLE_CONTENT_JS)


# --------------------------------------------------------------------------- P1 / P2


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("start_rate", (1.0, 1.5))
def test_hold_500ms_switches_to_fixed_2x_and_release_restores_previous_rate(
    session: PlayerSession, mode: str, start_rate: float
) -> None:
    """P1 + P2：门槛两侧、固定 2×、松手恢复长按前的实际速度（1× 与 1.5× 起步，两种模式）。"""
    _open_playing_session(session, mode=mode, rate=start_rate)
    gesture, _ = _press_picture(session)

    session.clock.advance(HOLD_MS - 1)
    assert session.read().playback_rate == start_rate, "未满 500 毫秒不得改变速度（门槛前）"

    session.clock.advance(1)
    assert session.read().playback_rate == TEMPORARY_RATE, "满 500 毫秒必须变为固定 2×（门槛后）"

    evidence = _evidence(gesture, engine=session.harness.engine, what="门槛两侧按压")
    gesture.up()
    state = session.read()
    assert state.playback_rate == start_rate, f"松手必须恢复长按前的实际速度（证据：{evidence.strength}）"
    assert state.paused is False, "松手后 Video 仍在播放"


@pytest.mark.parametrize("mode", MODES)
def test_release_before_500ms_leaves_rate_untouched(session: PlayerSession, mode: str) -> None:
    """P2：未达门槛松手不改变速度，且不留下会延迟触发的等待。"""
    _open_playing_session(session, mode=mode)

    gesture, _ = _press_picture(session)
    session.clock.advance(HOLD_MS - 1)
    gesture.up()
    evidence = _evidence(gesture, engine=session.harness.engine, what="门槛前松手")

    assert session.read().playback_rate == 1.0
    session.clock.advance(HOLD_MS * 4)
    state = session.read()
    assert state.playback_rate == 1.0, f"松手后残留的等待不得延迟触发（证据：{evidence.strength}）"
    assert state.paused is False

    with session.touch() as brief:  # 更短的一次按压：立即松手
        brief.down(*session.point_in_video(x_frac=0.5, y_frac=0.3))
    session.clock.advance(HOLD_MS * 4)
    assert session.read().playback_rate == 1.0, "极短按压不得触发临时倍速"


@pytest.mark.parametrize("mode", MODES)
def test_rate_stays_at_2x_for_as_long_as_the_press_continues(session: PlayerSession, mode: str) -> None:
    """P1：持续按住期间保持 2×，不随时间漂移，也不被无关事件打断。"""
    _open_playing_session(session, mode=mode)
    gesture, point = _press_picture(session)
    session.clock.advance(HOLD_MS - 1)
    assert session.read().playback_rate == 1.0
    session.clock.advance(1)
    assert session.read().playback_rate == TEMPORARY_RATE

    for extra_ms in (1, 250, 5000):
        session.clock.advance(extra_ms)
        state = session.read()
        assert state.playback_rate == TEMPORARY_RATE, f"按住期间（再推进 {extra_ms} 毫秒）必须保持 2×"
        assert state.paused is False, "按住期间 Video 仍在播放"

    gesture.move_to(*point)  # 零位移的无关触摸事件
    assert session.read().playback_rate == TEMPORARY_RATE, "无位移的触摸事件不得打断临时倍速"

    gesture.up()
    assert session.read().playback_rate == 1.0

    _evidence(gesture, engine=session.harness.engine, what="持续按住按压")


@pytest.mark.parametrize("mode", MODES)
def test_left_center_right_non_control_areas_all_trigger(session: PlayerSession, mode: str) -> None:
    """P1：画面左、中、右非控件区域都能触发；每次按压独立（含抬起后的清理）。"""
    _open_playing_session(session, mode=mode)

    for x_frac in (0.15, 0.5, 0.85):
        gesture, _ = _press_picture(session, x_frac=x_frac)
        session.clock.advance(HOLD_MS - 1)
        assert session.read().playback_rate == 1.0, f"x={x_frac} 处未满门槛不得改变速度"
        session.clock.advance(1)
        assert session.read().playback_rate == TEMPORARY_RATE, f"x={x_frac} 处必须能触发临时倍速"
        gesture.up()
        assert session.read().playback_rate == 1.0, f"x={x_frac} 处松手必须恢复原速"
        _evidence(gesture, engine=session.harness.engine, what=f"x={x_frac} 按压")


# --------------------------------------------------------------------------- P3


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("start_rate", (1.0, 1.5))
def test_paused_hold_neither_plays_nor_enters_temporary_rate(
    session: PlayerSession, mode: str, start_rate: float
) -> None:
    """P3：暂停时按住超过 500 毫秒再松开，仍暂停、速度不变，且没有因此开始播放。"""
    _open_playing_session(session, mode=mode, rate=start_rate)
    session.page.evaluate("() => { document.getElementById('v').pause(); }")
    session.wait_for(lambda state: state.paused, "Video 进入暂停")

    gesture, _ = _press_picture(session)
    session.clock.advance(HOLD_MS - 1)
    assert session.read().paused is True
    session.clock.advance(1)
    assert session.read().playback_rate == start_rate, "暂停时满门槛也不得进入临时倍速"
    session.clock.advance(HOLD_MS * 2)
    paused_state = session.read()
    assert paused_state.paused is True, "暂停时按住不得开始播放"
    assert paused_state.playback_rate == start_rate, "暂停时按住不得改变速度"

    gesture.up()
    session.clock.advance(HOLD_MS * 4)
    released_state = session.read()
    assert released_state.paused is True, "暂停时松开后必须仍然暂停"
    assert released_state.playback_rate == start_rate, "暂停时松开后速度必须不变"
    _evidence(gesture, engine=session.harness.engine, what="暂停状态按压")


# --------------------------------------------------------------------------- P8


@pytest.mark.parametrize("mode", MODES)
def test_no_visible_prompt_appears_in_waiting_accelerating_or_restoring_phase(
    session: PlayerSession, mode: str
) -> None:
    """P8：等待、加速、恢复三阶段都不新增文字、图标或浮层——可见内容与基线一致。"""
    _open_playing_session(session, mode=mode)
    baseline = _BASELINE_VISIBLE[mode]

    assert _visible_content(session) == baseline, "前置条件：未按压时可见内容就是基线"

    gesture, _ = _press_picture(session)
    session.clock.advance(200)  # 等待阶段
    assert session.read().playback_rate == 1.0
    assert _visible_content(session) == baseline, "等待阶段不得出现提示"

    session.clock.advance(HOLD_MS - 200)  # 加速阶段
    assert session.read().playback_rate == TEMPORARY_RATE
    assert _visible_content(session) == baseline, "加速阶段不得出现提示"

    gesture.up()  # 恢复阶段
    assert session.read().playback_rate == 1.0
    assert _visible_content(session) == baseline, "恢复阶段不得出现提示"
    _evidence(gesture, engine=session.harness.engine, what="无提示三阶段按压")


# --------------------------------------------------------------------------- P9


def test_custom_fullscreen_buttons_keep_working_and_never_trigger_the_hold(session: PlayerSession) -> None:
    """P9：自制全屏进入/退出按钮照常工作；在按钮上停留超过 500 毫秒也不触发临时倍速。"""
    _open_playing_session(session, mode="inline")

    for selector, expected_after in (("#fs-btn", True), ("#fs-exit-btn", False)):
        point = session.page.evaluate(_ELEMENT_CENTER_JS, selector)
        assert session.hit_id(*point) in {"fs-btn", "fs-exit-btn", "svg", "path"}, (
            f"{selector} 必须仍然可被触摸命中（不得被手势区域或浮层截获）"
        )

        with session.touch() as gesture:
            gesture.down(*point)
            session.clock.advance(HOLD_MS - 1)
            assert session.read().playback_rate == 1.0, f"{selector} 上按压未满门槛不得改变速度"
            session.clock.advance(HOLD_MS * 3)
            assert session.read().playback_rate == 1.0, f"{selector} 上停留超过 500 毫秒不得触发临时倍速"
            gesture.cancel()
            assert session.read().playback_rate == 1.0
        _evidence(gesture, engine=session.harness.engine, what=f"{selector} 长时间按压")

        # 控件照常工作：引擎级真实 tap（两个引擎都是真实输入）点亮按钮本身的行为
        session.tap(*point)
        session.wait_for(
            lambda state, want=expected_after: state.custom_fullscreen is want,
            f"真实 tap {selector} 后 custom_fullscreen 变为 {expected_after}",
        )
        assert session.read().playback_rate == 1.0, "进出自制全屏不得留下临时倍速"


def test_native_control_bar_operations_are_not_swallowed(session: PlayerSession) -> None:
    """P9（桌面证据）：手势区域就位后，原生控件条的可见操作照常生效。

    只用真实鼠标点原生控件条（位置为实测值，见 `player_harness.PLAY_CONTROL_OFFSETS`）；
    原生控件在 UA shadow DOM 内、webkit 的命中点不连续，因此这条只在 chromium 上跑。
    iOS Safari 原生控件的真机协作由票据 05 验收。
    """
    if session.harness.engine != "chromium":
        pytest.skip("webkit 原生控件条命中点不连续，无法做成确定性判据（见 PLAY_CONTROL_OFFSETS）")

    _open_playing_session(session, mode="inline")
    assert session.tap_play_control().paused is True, "原生控件条播放/暂停按钮必须照常响应"
    assert session.tap_play_control().paused is False, "原生控件条播放/暂停按钮必须照常响应（反向）"
    assert session.read().playback_rate == 1.0, "原生控件条操作不得改变播放速度"
