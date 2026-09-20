"""配置化验证：长按门槛来自 `config.json` 的 `player.hold_ms`，测试不写死数值。

本文件守住两件事：

1. **归一化**：缺省/非数字/越界都必须收敛到可用值，笔误不能让手势失控或形同失效。
2. **配置真的生效**：页面上的生效门槛必须等于配置值，并且门槛两侧的行为随配置移动；
   否则「改了配置没反应」这种 bug 只能靠人上真机才发现。
"""

from __future__ import annotations

import pytest

from player_harness import (  # noqa: F401  (下面这些是有名字的 pytest 夹具，必须导入本模块才可见)
    PLAYWRIGHT_ENGINES,
    PlayerHarness,
    harness,
    harness_factory,
    playwright_instance,
)
from server.app import DEFAULT_HOLD_MS, normalize_hold_ms, render_player_page


def test_normalize_hold_ms_falls_back_and_clamps() -> None:
    """缺省/非数字回退默认值；越界夹取到合理范围（下限避免误触，上限避免手势失效）。"""
    assert normalize_hold_ms(None) == DEFAULT_HOLD_MS
    assert normalize_hold_ms("abc") == DEFAULT_HOLD_MS
    assert normalize_hold_ms("") == DEFAULT_HOLD_MS
    assert normalize_hold_ms(350) == 350
    assert normalize_hold_ms("350") == 350
    assert normalize_hold_ms(10) == 250, "低于下限必须夹取"
    assert normalize_hold_ms(99999) == 1500, "高于上限必须夹取"


@pytest.mark.parametrize("configured", (250, 350, 500, 1500))
def test_player_page_renders_the_configured_threshold(configured: int) -> None:
    """页面必须带上生效门槛，且模板变量一个都不能残留（漏传会留下 `{{…}}`）。"""
    html = render_player_page(
        title="t", playlist_url="/media/x.mp4", video_id="v1", subtitle_url="", hold_ms=configured
    )
    assert "{{" not in html, f"模板变量必须全部替换：{html}"
    assert f'data-hold-ms="{configured}"' in html


@pytest.mark.parametrize("configured", (350, 500))
@pytest.mark.parametrize("engine", PLAYWRIGHT_ENGINES)
def test_configured_threshold_drives_the_boundary(harness: PlayerHarness, configured: int) -> None:
    """门槛随配置移动：同一套边界断言在两个配置值下分别成立（证明配置真的生效）。"""
    original = harness.hold_ms
    harness.hold_ms = configured
    try:
        with harness.session() as player:
            player.goto(clock=True)
            player.start_playback()
            player.clock.freeze()

            assert player.read().hold_ms == configured, "页面上生效的门槛必须等于配置值"

            gesture = player.touch()
            gesture.down(*player.point_in_video(y_frac=0.25))
            player.clock.advance(configured - 1)
            assert player.read().playback_rate == 1.0, "未达配置门槛不得改变速度"
            player.clock.advance(1)
            assert player.read().playback_rate == 2.0, "满配置门槛必须进入临时倍速"
            gesture.up()
            assert player.read().playback_rate == 1.0, "松手必须恢复长按前速度"
    finally:
        harness.hold_ms = original
