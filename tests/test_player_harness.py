"""骨架自检（票据 01）：证明夹具真的能测东西。

本文件**不自证任何长按临时倍速承诺**——票据 01 不实现该行为，页面可见行为与改动前一致。
这里只有与本次功能无关的自检与骨架硬要求：

- 夹具交付真实页面（真实模板 + 真实 `player.js`/`player.css`）与真的能播放的本地视频；
- 普通页面与自制横屏全屏两种模式下视频都在播放（`currentTime` 推进）；
- 按压可以重复派发、互不残留状态，两种观看模式都能用；
- 受控时间可以确定性判定 500 毫秒门槛（自检二）；
- 真实输入能翻转页面公开状态（自检一）。

观察口径：所有断言只读 Video 元素公开属性（`paused` / `playbackRate` / `currentTime` /
`readyState`）、`body.custom-fullscreen` 与元素可见性。不读实现私有变量、内部计时器或私有状态字段。
每条按压的证据强度由 `TouchGesture.evidence` 给出（可信真实输入 vs 页面内合成事件）。
"""

from __future__ import annotations

import time
import urllib.request

import pytest

from player_harness import (  # noqa: F401  (下面这些是有名字的 pytest 夹具，必须导入本模块才可见)
    PLAYWRIGHT_ENGINES,
    ROOT,
    PlayerHarness,
    PlayerSession,
    harness,
    harness_factory,
    playing_session,
    playwright_instance,
    session,
)

pytestmark = pytest.mark.parametrize("engine", PLAYWRIGHT_ENGINES)

#: 自检二用的页面内探针：只在自检用例里注入，与本次功能无关。
_PROBE_500MS_JS = """
() => {
  window.__harnessProbe = [];
  window.__harnessProbeStart = Date.now();
  setTimeout(() => window.__harnessProbe.push('fired'), 500);
  return window.__harnessProbeStart;
}
"""

#: 送达自检探针：在页面里记录收到的触摸事件（类型、是否可信、touches 数与触点坐标）。
#: 只在送达自检用例里注入——用来证明夹具派发的按压**真的到达页面**，而不是只在夹具侧记账。
_OBSERVE_TOUCH_JS = """
() => {
  window.__harnessTouchSeen = [];
  ['touchstart', 'touchmove', 'touchend', 'touchcancel'].forEach((type) =>
    document.addEventListener(type, (event) => window.__harnessTouchSeen.push({
      type: event.type,
      isTrusted: event.isTrusted,
      touches: event.touches.length,
      changedTouches: event.changedTouches.length,
      x: event.changedTouches[0] ? event.changedTouches[0].clientX : null,
      y: event.changedTouches[0] ? event.changedTouches[0].clientY : null,
    }), true));
  return true;
}
"""

_SEEN_TOUCH_JS = "() => window.__harnessTouchSeen"


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read()


def _assert_points(seen: list[dict], expected: list[tuple[float, float]]) -> None:
    """页面收到的触点坐标必须与夹具派发的坐标一致（允许引擎的浮点/取整误差）。

    容差只吸收引擎浮点表示差异（实测 webkit 会带回 203.8000030517578 这类值），
    不允许量级错误：坐标丢失时会变成 0,0 或坐标错位，都会被这 0.05 的容差挡住。
    """
    assert len(seen) == len(expected)
    for event, (x, y) in zip(seen, expected):
        assert event["x"] == pytest.approx(x, abs=0.05), f"页面收到的 x 与派发不符：{event}"
        assert event["y"] == pytest.approx(y, abs=0.05), f"页面收到的 y 与派发不符：{event}"


def test_fixture_serves_the_real_player_page_and_real_static_assets(harness: PlayerHarness) -> None:
    """夹具渲染真实模板、加载真实静态资源，只把播放源换成夹具视频。"""
    served = _fetch(f"{harness.base_url}/play/{harness.video_id}").decode("utf-8")

    template = (ROOT / "server" / "templates" / "player.html").read_text(encoding="utf-8")
    expected = (
        template.replace("{{title}}", "player-harness")
        .replace("{{playlist_url}}", harness.media_url)
        .replace("{{video_id}}", harness.video_id)
        .replace("{{subtitle_url}}", "")
    )
    assert served == expected, "页面必须由真实模板渲染（唯一差异是播放源）"
    assert f'data-playlist-url="{harness.media_url}"' in served
    assert '<script src="/static/player.js"></script>' in served

    for name in ("player.js", "player.css"):
        real = (ROOT / "server" / "static" / name).read_bytes()
        assert _fetch(f"{harness.base_url}/static/{name}") == real, f"{name} 必须是真实静态资源"


def test_fixture_video_really_plays_in_inline_and_custom_fullscreen(session: PlayerSession) -> None:
    """普通页面与自制横屏全屏两种模式下，夹具视频都真的在播放（`currentTime` 推进）。"""
    session.goto()
    inline = session.start_playback()
    assert inline.paused is False
    assert inline.playback_rate == 1.0

    box = session.video_box()
    assert box["width"] > 0 and box["height"] > 0
    point = session.point_in_video(y_frac=0.3)
    assert session.hit_id(*point) == "v", "默认按压点必须落在视频上，而不是控件上"
    session.wait_for_progress(min_advance=0.2)

    fullscreen = session.enter_custom_fullscreen()
    assert fullscreen.custom_fullscreen is True
    assert fullscreen.fs_button_visible is False
    assert fullscreen.exit_button_visible is True

    fs_point = session.point_in_video(y_frac=0.3)
    assert session.hit_id(*fs_point) == "v", "自制横屏旋转后默认按压点仍必须落在视频上"
    assert session.wait_for_progress(min_advance=0.2).paused is False


def test_press_move_release_repeats_without_leaving_state(session: PlayerSession) -> None:
    """一次按压可控制轨迹与结束方式，并能重复派发而不残留状态。"""
    session.goto(clock=True)
    session.start_playback()
    session.clock.freeze()
    before = session.read()

    evidence = []

    with session.touch() as mild:  # 按下 → 轻微移动 → 抬起（with 退出时自动抬起）
        mild.down(*session.point_in_video(x_frac=0.35, y_frac=0.25))
        mild.move_by(0.0, 8.0)
    evidence.append(mild.evidence)
    after_first = session.read()

    far = session.point_in_video(x_frac=0.65, y_frac=0.6)
    moved = session.touch()  # 按下 → 移到绝对坐标 → 取消
    moved.down(*session.point_in_video(x_frac=0.65, y_frac=0.3))
    moved.move_to(*far)
    moved.cancel()
    evidence.append(moved.evidence)

    traced = session.touch()  # 按下 → 多次相对移动 → 抬起
    traced.down(*session.point_in_video())
    for step in (5.0, 10.0, 15.0):
        traced.move_by(0.0, step)
    traced.up()
    evidence.append(traced.evidence)

    after_all = session.read()
    for state in (after_first, after_all):
        assert state.paused is False, "按压结束后仍应在播放"
        assert state.playback_rate == 1.0
        assert state.custom_fullscreen is False
    assert after_all.current_time >= before.current_time

    mild_events, moved_events, traced_events = (e.events for e in evidence)
    assert [e.kind for e in mild_events] == ["touchstart", "touchmove", "touchend"]
    # move_by 的位移是相对按下点量的
    assert (mild_events[1].x, mild_events[1].y) == (mild_events[0].x, mild_events[0].y + 8.0)
    assert [e.kind for e in moved_events] == ["touchstart", "touchmove", "touchcancel"]
    # move_to 用绝对坐标
    assert (moved_events[1].x, moved_events[1].y) == pytest.approx(far)
    assert [e.kind for e in traced_events] == ["touchstart", "touchmove", "touchmove", "touchmove", "touchend"]
    traced_dy = [e.y - traced_events[0].y for e in traced_events[1:4]]
    assert traced_dy == pytest.approx([5.0, 10.0, 15.0])

    if session.harness.engine == "chromium":
        assert all(e.trusted_input for e in evidence), "chromium 的按压必须是引擎级真实输入"
    else:
        assert not any(e.trusted_input for e in evidence), "webkit 的按压序列只能是合成事件（弱证据）"


def test_press_sequence_reaches_the_page_as_dom_touch_events(session: PlayerSession) -> None:
    """送达自检：夹具派发的按压真的到达页面，事件名、触点数与坐标都正确。

    这是**夹具自身的送达证据**（页面内探针只在本用例里注入），不是任何承诺的证据：
    没有它，夹具可能只在自家账本上记账、页面其实什么都没收到（本票就踩过这个坑，
    见票据 `## Comments` §10）。
    """
    session.goto()
    session.start_playback()
    session.page.evaluate(_OBSERVE_TOUCH_JS)

    press = session.point_in_video(y_frac=0.25)
    gesture = session.touch()
    gesture.down(*press)
    gesture.move_by(0.0, 30.0)
    gesture.up()

    seen = session.page.evaluate(_SEEN_TOUCH_JS)
    assert [event["type"] for event in seen] == ["touchstart", "touchmove", "touchend"]
    _assert_points(seen, [(press[0], press[1]), (press[0], press[1] + 30.0), (press[0], press[1] + 30.0)])
    assert [event["touches"] for event in seen] == [1, 1, 0], "抬起时不再有活动触点"
    assert all(event["changedTouches"] == 1 for event in seen)
    assert [event["isTrusted"] for event in seen] == (
        [True] * 3 if session.harness.engine == "chromium" else [False] * 3
    ), "chromium 应为引擎级可信事件；webkit 只能是不可信合成事件（弱证据）"


def test_cancel_press_reaches_the_page_as_touchcancel(session: PlayerSession) -> None:
    """送达自检：取消手势到达页面时是 `touchcancel`（中断类用例全靠它）。"""
    session.goto()
    session.start_playback()
    session.page.evaluate(_OBSERVE_TOUCH_JS)

    press = session.point_in_video(y_frac=0.3)
    gesture = session.touch()
    gesture.down(*press)
    gesture.cancel()

    seen = session.page.evaluate(_SEEN_TOUCH_JS)
    assert [event["type"] for event in seen] == ["touchstart", "touchcancel"]
    _assert_points(seen, [press, press])
    assert [event["touches"] for event in seen] == [1, 0]


def test_press_sequence_works_in_custom_fullscreen(session: PlayerSession) -> None:
    """自制横屏全屏（用页面现有入口真实点击进入）里同样能派发按压。"""
    session.goto()
    session.start_playback()

    gesture = session.touch_in_custom_fullscreen()
    assert session.read().custom_fullscreen is True

    point = session.point_in_video(y_frac=0.3)
    assert session.hit_id(*point) == "v"
    gesture.down(*point)
    gesture.move_by(0.0, 10.0)
    gesture.up()

    state = session.read()
    assert state.custom_fullscreen is True
    assert state.paused is False
    assert state.playback_rate == 1.0


def test_selfcheck_1_real_input_flips_a_public_play_state(session: PlayerSession) -> None:
    """自检一：真实输入 → 页面公开状态翻转（证明观察链成立）。

    驱动入口按引擎实测结果选择，两个分支都是引擎级真实输入，都不放宽判定：
      - chromium：真实鼠标点在原生控件条的播放按钮上（实测稳定，来回各一次）；
      - webkit：真实触摸 tap 在视频画面上（实测 3/3 稳定翻转 `paused`）——webkit 的原生
        控件条命中点不连续，不能作为确定性判据，见 `PLAY_CONTROL_OFFSETS` 注释。
    """
    session.goto()
    assert session.start_playback().paused is False

    if session.harness.engine == "chromium":
        assert session.tap_play_control().paused is True, "真实输入点原生控件播放按钮后应暂停"
        assert session.tap_play_control().paused is False, "再次真实输入应恢复播放"
    else:
        assert session.tap_picture().paused is True, "真实 tap 画面后应暂停"
        assert session.tap_picture().paused is False, "再次真实 tap 应恢复播放"


def test_selfcheck_2_controlled_clock_decides_the_500ms_threshold(session: PlayerSession) -> None:
    """自检二：受控时间下 500 毫秒门槛两侧确定性判定（不需要真实等待）。"""
    session.goto(clock=True)
    session.clock.freeze()

    started_at = session.page.evaluate(_PROBE_500MS_JS)

    session.clock.advance(499)
    assert session.page.evaluate("() => window.__harnessProbe") == [], "499 毫秒时不应触发"

    session.clock.advance(1)
    assert session.page.evaluate("() => window.__harnessProbe") == ["fired"], "满 500 毫秒时应触发"
    assert session.page.evaluate("() => Date.now()") - started_at == 500

    # 记录受控时间对媒体、动画与事件回调的实际影响（下面三条都是实测结论）。
    session.start_playback()
    before = session.read()
    time.sleep(0.4)
    assert session.read().current_time > before.current_time, "冻结的假时钟不影响媒体时钟"

    ticks = session.page.evaluate(
        "() => { window.__harnessRaf = 0;"
        " const tick = () => { window.__harnessRaf += 1; requestAnimationFrame(tick); };"
        " requestAnimationFrame(tick); return 'scheduled'; }"
    )
    assert ticks == "scheduled"
    time.sleep(0.3)
    assert session.page.evaluate("() => window.__harnessRaf") == 0, "冻结的假时钟会冻结页面内 rAF"


def test_custom_fullscreen_entry_and_exit_work_under_controlled_time(session: PlayerSession) -> None:
    """受控时间与现有全屏入口共存：进入/退出自制横屏全屏（含 200ms 淡入回调）都照常生效。"""
    session.goto(clock=True)
    session.clock.freeze()
    session.start_playback()

    assert session.enter_custom_fullscreen().custom_fullscreen is True
    assert session.read().paused is False
    assert session.exit_custom_fullscreen().custom_fullscreen is False
    assert session.read().paused is False
