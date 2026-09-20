# 04: 中断、手势终结与不误触原有操作

**Delivers:** P6, P7, P10（自动化覆盖项；真机覆盖项由 05 承载）

**Test owner for:** P6、P7、P10 的自动化（确定性 + 浏览器集成）覆盖项

**Blocked by:** 02, 03

**Status:** closed（自动化覆盖项已完成并实测通过；P6/P7/P10 的**真机**覆盖项未验收 → 这三个承诺整体仍未完成，由 05 承载）

**Parent:** `.scratch/hold-to-double-speed/contract.md`（Seam S1；来源 `spec.md`）

## What to build

切到后台、进入或退出自制横屏全屏、系统取消手势时，本次长按结束：等待期间取消等待，加速期间恢复长按前速度，都不遗留临时倍速。已经结束或取消的手势不能自行复活——手指移回、返回前台、切换观看模式都不会重新出现 2×，重新加速必须重新按下并重新等待完整 500 毫秒。

同时保证：两种模式下短于门槛的短按行为与改动前一致，长按正常结束或被取消后都不产生额外的播放/暂停切换。

## Acceptance criteria

- [x] 页面切到后台时，等待期间取消本次长按，加速期间立即恢复原速；返回前台后速度仍为长按前速度，且没有因此开始播放。（合成 `visibilitychange`，**弱证据**，待真机）
- [x] 进入自制横屏全屏时本次长按结束，退出自制横屏全屏时同样结束（等待期间取消等待，加速期间恢复原速）。
- [x] 系统取消触摸时恢复原速且不遗留临时倍速——即使没有正常收到松手。
- [x] 每种中断分别覆盖等待期间与加速期间，并在两种观看模式下成立。
- [x] 旧手势不复活：滑动取消后把手指移回、返回前台、切换观看模式后都不会重新出现 2×。
- [x] 重新加速必须重新按下并重新等待完整 500 毫秒：取消后立即按住不得沿用之前的按住时长（受控时间验证门槛两侧）。
- [x] 两种观看模式下，短于门槛的短按不改变速度、不改变播放/暂停状态，行为与改动前一致。
- [x] 正常松手结束或被取消已识别的长按后，播放/暂停状态保持不变，不产生额外的切换。
- [x] 后台与系统取消若只能以合成事件验证，必须在票据中标注为较弱证据，并交由 05 真机验收。
- [x] 不新增提示、错误类型、重试或额外 UI。
- [ ] 真机行未验收前不得宣称 P6、P7、P10 完成。→ **未完成**：真机行由 05 承载，本票只关自动化部分。

## 覆盖分区

| 承诺 | 覆盖项 | 验证方式 | 归属（写测试） |
|---|---|---|---|
| P6 | 切到后台（等待期间 / 加速期间） | 浏览器集成（合成可见性变化，弱证据） | 04 |
| P6 | 进入与退出自制横屏全屏（等待期间 / 加速期间） | 浏览器集成（真实按钮点击） | 04 |
| P6 | 系统取消手势（等待期间 / 加速期间） | 浏览器集成（取消触摸输入） | 04 |
| P6 | 中断不启动播放 | 确定性自动化 | 04 |
| P6 | 真机后台、系统手势取消、全屏切换 | 真机人工 | 05 |
| P7 | 滑动移回后不复活旧手势 | 浏览器集成 | 04 |
| P7 | 后台返回、全屏切换后不复活旧手势 | 浏览器集成 | 04 |
| P7 | 重新按下需重新等待完整 500 毫秒 | 确定性自动化（受控时间） | 04 |
| P7 | 真机 | 真机人工 | 05 |
| P10 | 两种模式短按与改动前一致 | 浏览器集成 | 04 |
| P10 | 正常结束或取消已识别长按后不产生额外播放切换 | 浏览器集成 | 04 |
| P10 | 真机短按与长按结束行为 | 真机人工 | 05 |

## 验证限制

- 切后台与系统取消手势在桌面浏览器里通常只能以合成事件触发；这类用例的证据强度必须逐条标注，真机结论由 05 给出。
- 真机行未验收前，本票关闭不等于 P6、P7、P10 完成。

## Comments

### 实现（2026-09-20）

改动只在 `server/static/player.js`，共 3 处新增 + 2 行注释更新，不新增接口/配置/持久化/UI：

1. `enterFS()` 与 `exitFS()` 首行各加 `holdEnd();`——**在切换开始时**（200 毫秒淡出动画之前）结束本次长按，
   不让手势跨越模式切换窗口。已有的 `window orientationchange → exitFS` 因此自动覆盖旋转场景。
2. 新增 `document.addEventListener('visibilitychange', function() { if (document.hidden) holdEnd(); });`
   （放在手势监听的 `touchcancel` 之后）。
3. `touchcancel` 未改动（02 已接 `holdEnd()`），本票只补覆盖。

三处都只调用唯一的结束出口 `holdEnd()`；没有在别处直接写 `v.playbackRate`，也没有改动
`holdBegin`/`holdEnd`/`touchmove` 的实现，所以 02/03 交付的语义（固定 2×、恢复长按前实际速度、
12 CSS 像素门槛、直线距离度量）原样保留。

### 测试（`tests/test_hold_speed_interruptions.py`，新增，66 个用例 = 33 条 × 2 引擎）

引擎：`chromium` 与 `webkit`，`pytestmark = pytest.mark.parametrize("engine", PLAYWRIGHT_ENGINES)`。
未修改 `tests/player_harness.py`（本票不需要新夹具能力）。

### 命令与结果

```
python -m pytest tests/test_hold_speed_interruptions.py -q
→ 66 passed in 56.45s            （红灯阶段（实现前、同一份用例）：30 failed / 36 passed，失败集中在全屏
                                  与后台中断；系统取消、P10、不复活、重新计时等 02/03 已成立的行当时就是绿灯）

python -m pytest tests/test_hold_speed_interruptions.py tests/test_hold_speed_slide.py \
                 tests/test_hold_speed_core.py tests/test_player_harness.py -q -rs
→ 165 passed, 5 skipped in 126.49s
  5 个 skip 全部是既有的引擎条件跳过（slide ×4：webkit 没有可信移动通道；
  core ×1：webkit 原生控件条命中点不连续），与本票改动无关。
```

### 逐覆盖项证据强度

| 承诺 | 覆盖项（用例） | 中断输入 | 力度 |
|---|---|---|---|
| P6 | 进入自制全屏：等待期 `test_entering_custom_fullscreen_ends_waiting_press`；加速期 `..._restores_rate_while_accelerated`（1×/1.5×） | Playwright 真实鼠标点 `#fs-btn`；按压 = chromium CDP 真实触摸 / webkit 页面内合成 | 按钮**可信**（两引擎）；按压 chromium 可信、webkit 弱 |
| P6 | 退出自制全屏：等待期 `test_exiting_custom_fullscreen_ends_waiting_press`；加速期 `..._restores_rate_while_accelerated`（1×/1.5×） | 真实点 `#fs-exit-btn`；按压同上 | 同上 |
| P6 | 切后台：等待期 `test_page_hidden_ends_waiting_press_and_returning_foreground_revives_nothing`；加速期 `test_page_hidden_restores_rate_while_accelerated`（两模式各一） | **页面内合成 `visibilitychange`（覆写 `document.hidden` / `visibilityState`，`isTrusted=false`）= 弱证据** | **弱**，必须真机复验 |
| P6 | 系统取消：等待期 `test_touchcancel_ends_waiting_press`；加速期 `test_touchcancel_restores_rate_while_accelerated`（两模式） | chromium `Input.dispatchTouchEvent` 的 `touchCancel`（可信）/ webkit 合成 `touchcancel` | chromium **可信**；webkit **弱** |
| P6 | 中断不启动播放：`test_interruption_never_starts_playback_while_video_is_paused`（暂停下三种中断）＋ 所有中断用例都断言 `paused` 不变 | 三种中断同上 | 同上 |
| P7 | 滑动取消后移回不复活：`test_ended_press_never_revives_when_finger_returns`（两模式 × 等待/加速期）；越界移动 chromium 真实 16 CSS 像素、webkit 合成精确移动 | chromium **可信**（移动到达页面）；webkit **弱** |
| P7 | 后台往返 / 切换观看模式后不复活：`..._after_background_roundtrip`、`..._after_viewing_mode_switch` | 可见性合成（弱）+ 真实按钮（可信） | 混合，逐条标注 |
| P7 | 重新按下需重新等待完整 500 毫秒：`test_press_after_cancel_waits_full_hold_ms_again`（两模式）、`..._after_fullscreen_interruption_...`、`..._after_background_roundtrip_...`；受控时间断言 `advance(499)` 仍是 1×、`advance(1)` 才 2× | 取消 = 系统取消（可信/弱按引擎）、全屏（可信）、后台（弱） | 逐条标注 |
| P10 | 正常松手结束 / 被取消的已识别长按不额外切换：`test_recognized_long_press_release_and_cancel_do_not_toggle_playback`（两模式） | 按压同上 | chromium 可信 / webkit 弱 |
| P10 | 子门槛短按与改动前一致：`test_sub_threshold_press_keeps_pre_change_behaviour`（两模式，0/200/499 毫秒） | chromium CDP 按压可信；webkit 合成（弱）＋ **一次引擎级真实 tap** 核对基线 | 见下 |
| 不新增提示 | 后台（含返回前台）与系统取消两条路径的可见元素清单/文字与按压前一致 | 可见 DOM 快照 | 两引擎 |

**弱证据的明确标注（不得当作真机结论）**

1. **切后台**：桌面 headless 里无法构造真实后台切换——实测新开标签页 `bring_to_front()` 不触发
   `visibilitychange`、`document.hidden` 始终 `false`，所以只能覆写 `document.hidden` /
   `document.visibilityState` 后 `dispatchEvent(new Event('visibilitychange'))`（`isTrusted=false`）。
   用例在派发前会断言模拟状态真的生效（防止空洞通过），但结论只证明页面逻辑。
2. **webkit 的按压/取消/移动**：Playwright 在 webkit 上只暴露 `touchscreen.tap`，没有按压通道，
   按压序列一律是页面内合成 `TouchEvent`；webkit 的可信输入只有真实 tap。
3. **P10 短按的两层判定**（不用「无视 `paused`」蒙混）：
   - 子门槛按压序列本身：0 / 200 / 499 毫秒三种时长下 `playbackRate` 与 `paused` 都必须与按压前一致
     （同引擎多种子门槛时长结果一致）；
   - 引擎基线核对：每个引擎再做一次**引擎级真实 tap**，断言 01/02 记录的差异未被掩平
     （webkit 切换一次播放/暂停、chromium 不切换，两者速度都保持 1×）。
   两条都通过，所以本票的改动没有改变短按行为；webkit 的按压部分是弱证据，真机体感仍由 05 验收。

### 真机待验收项（交 05）

- P6：真实切后台（App 切换 / 锁屏 / 来电）在等待期与加速期都结束长按、返回后仍为长按前速度且不自动播放。
- P6：真机进入/退出自制横屏全屏（含旋转）在等待期与加速期都结束长按、不遗留 2×。
- P6：iOS 系统手势（边缘返回、控制中心）导致的 `touchcancel` 恢复原速。
- P7：真机滑动取消后移回手指、后台返回、模式切换后都不复活；重新按下要重新等满 500 毫秒。
- P10：真机短按（点击画面）行为与改动前一致（含 webkit tap 会切换播放/暂停这一平台基线）；
  长按正常松手与被系统取消都不额外切换播放/暂停。
- P8/P9 的真机结论仍归 02；本票的改动不新增任何可见元素（可见 DOM 快照已覆盖）。

### 真机验收应重点看的风险点

1. **iOS 原生全屏不在本票范围**：`holdEnd()` 目前只接在自制全屏的 `enterFS`/`exitFS`、`visibilitychange`
   与 `touchcancel` 上；`webkitbeginfullscreen`/`webkitendfullscreen`（iPhone 从原生控件进入的全屏）
   只做了方向锁定，**没有**接 `holdEnd()`。若真机上存在「按住画面期间进入原生全屏」的路径，
   可能残留临时倍速——需要 05 在真机上确认这条路径可达/不可达，再决定是否补钩子（属新范围，需先改契约）。
2. **切后台的恢复时机**：iOS 上 `visibilitychange` 与 `pagehide`/`freeze` 的触发顺序可能与桌面不同；
   若后台期间视频被系统暂停，`holdEnd()` 恢复的是长按前记录的速度（`gesture.rate`），仍应正确，
   但需确认真机上不会出现「回到前台仍是 2×」或「回到前台速度不是长按前速度」。
3. **中断与动画的竞态**：进入/退出全屏的 200 毫秒淡出期间手势已被结束；真机上若旋转与
   `orientationchange → exitFS` 交错，需确认没有残留 2×（桌面测试用受控时间覆盖了静态顺序，
   真实旋转时序需要真机观察）。
4. **重新计时的真机体感**：取消后立刻重新按住必须重新等满 500 毫秒（受控时间已证明逻辑正确，
   真人手上需确认没有「沿用上次按住时长」的感觉）。

### 已知限制

- 本票只证明页面逻辑与桌面浏览器行为：`P6`（尤其后台与系统取消）与 `P7`、`P10` 的**整体完成**
  取决于 05 的真机行，现在**不算完成**。
- 未触碰下载/字幕/串流代码；未改 `requirements.txt`；未新增接口、配置、持久化或 UI。
- 测试只读 Video 元素的公开属性、`document.hidden`、`body.custom-fullscreen` 与可见 DOM，
  不读实现私有变量或内部计时器；也不以「自己派的合成事件被收到」当结论。
