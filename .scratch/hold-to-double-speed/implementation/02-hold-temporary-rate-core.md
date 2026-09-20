# 02: 播放中长按临时倍速（核心规则）

**Delivers:** P1, P2, P3, P8, P9（自动化覆盖项；真机覆盖项由 05 承载）

**Test owner for:** P1、P2、P3、P8、P9 的自动化（确定性 + 浏览器集成）覆盖项

**Blocked by:** 01

**Status:** closed

**Parent:** `.scratch/hold-to-double-speed/contract.md`（Seam S1；来源 `spec.md`）

## What to build

在现有播放器页面上，播放中的 Video 在非控件区域被按住满 500 毫秒后临时以固定 2× 播放，松手立即恢复长按前的实际速度；暂停时长按既不启动播放也不进入临时倍速。普通页面与页面自制横屏全屏两种观看模式都必须可用。

判定只使用页面公开可观察的状态（播放速度、播放状态、可见内容）。期望值取自契约：门槛 500 毫秒、临时速度固定 2×（不是当前速度乘二）、结束后恢复长按前的实际速度（不是统一恢复为 1×）。

每条规则都要在两种观看模式下成立，并覆盖这些维度：非控件区域左、中、右；1× 与 1.5× 两种起始速度；等待、加速、恢复三个阶段。

## Acceptance criteria

- [x] 非控件区域按住满 500 毫秒后播放速度变为固定 2×；从 1× 起步与从 1.5× 起步都变为 2×。— 自动化已证实（两引擎 × 两模式 × 1×/1.5×；1.5× 起步时断言的是绝对值 2×）。
- [x] 未满 500 毫秒就松手不改变速度；达到门槛后触发（受控时间分别验证门槛两侧）。— 自动化已证实（`advance(499)` 不变、`advance(1)` 触发；门槛前松手后继续推进时间仍不触发）。
- [x] 持续按住期间保持 2×，不随时间漂移、不被无关事件打断。— 自动化已证实（触发后再推进 1/250/5000 毫秒、并派发一次零位移 touchmove，仍为 2×）。
- [x] 松手立即恢复长按前的实际速度：1× → 2× → 1×，1.5× → 2× → 1.5×。— 自动化已证实。
- [x] 左、中、右非控件区域都能触发；自制横屏全屏（由页面现有全屏入口进入与退出）与普通页面行为一致。— 自动化已证实（`x_frac` 0.15/0.5/0.85 × 两模式；全屏进入用页面 `#fs-btn` 真实 tap）。
- [x] 暂停状态下按住超过 500 毫秒并松开：仍处于暂停、速度未改变，且没有因此开始播放。— 自动化已证实（1× 与 1.5× 两种暂停前速度 × 两模式）。
- [x] 等待、加速、恢复三个阶段在两种模式下都不新增文字、图标或浮层：可见内容与改动前一致。— 自动化已证实（可见元素清单 + 可见文字与**实现前**实测基线逐阶段比对；真机视觉结果仍由 05 验收）。
- [x] 控件维持原有响应且不被手势区域截获：触摸落在自制全屏进入/退出按钮上时按钮照常工作，且停留超过 500 毫秒也不触发临时倍速；原生控件可见时其可见操作不被吞掉（此项为桌面证据，真机协作由 05 验收）。不得以覆盖层截获控件触摸。— 桌面证据已证实（自制全屏按钮：两引擎、停留 >500 毫秒不触发 + 真实 tap 照常进入/退出；原生控件：仅 chromium 真实鼠标可稳定驱动，见 `PLAY_CONTROL_OFFSETS`；真机协作由 05 验收）。
- [x] 不新增可配置的时长、阈值或倍速；不新增提示 UI；不改动播放位置记忆、字幕、下载与串流行为。— 已核实：改动只有 `server/static/player.js` 末尾新增的一段（无配置项、无 UI、无持久化），播放位置记忆 / 字幕 / 下载 / 串流代码未触碰。
- [x] 记录证据强度与限制；真机行未验收前不得宣称 P1、P2、P3、P8、P9 完成。— 见下方 `## Comments`；**P1/P2/P3/P8/P9 整体仍未完成**（真机行由 05 承载，本票只交付自动化覆盖项）。

## 覆盖分区

| 承诺 | 覆盖项 | 验证方式 | 归属（写测试） |
|---|---|---|---|
| P1 | 左/中/右非控件区域；未满门槛不触发、达门槛触发；1× 与 1.5× 都变 2×；持续按住保持 2× | 确定性自动化（受控时间）+ 浏览器集成 | 02 |
| P1 | 真机两种模式下的真实触摸触发与实际 2× | 真机人工 | 05 |
| P2 | 1×→2×→1×；1.5×→2×→1.5×；两种模式；未达门槛松手不变 | 确定性自动化 | 02 |
| P2 | 真机实际恢复原速 | 真机人工 | 05 |
| P3 | 两种模式；按住超过 500 毫秒后松开仍暂停且速度不变 | 确定性自动化 | 02 |
| P3 | 真机（接触屏幕不恢复播放） | 真机人工 | 05 |
| P8 | 等待/加速/恢复三阶段 × 两模式：无新文字、图标、浮层 | 浏览器集成（可见内容不变） | 02 |
| P8 | 真机视觉结果 | 真机人工 | 05 |
| P9 | 进入/退出自制全屏照常；控件可见时不被手势区域截获；控件上停留超过 500 毫秒不触发 | 浏览器集成 | 02 |
| P9 | 原生控件（播放/暂停、进度拖动）协作 | 真机人工 | 05 |

## 风险与验证限制

- **原生控件触摸是否可达页面**：桌面浏览器会收到落在控件上的事件，iOS Safari 的原生控件可能完全吞掉触摸。本票按「不截获控件」实现并做桌面证据；真机结论由 05 给出。若真机暴露控件触摸会触发长按，需要新的决定，不在本票静默处理。
- **真机最小自查（非阻塞、建议）**：本票完成后用真机自测 P1/P2/P3 与 P9，重点确认自制全屏下长按可用、原生控件不被干扰。结果记入票据，但正式验收记录由 05 承载。
- 松手后是否产生额外播放/暂停切换属于 P10，由 04 交付与验证，本票不重复主张。

## Comments

### 交付物

- **实现**：`server/static/player.js`（文件末尾新增一段 `HOLD_MS`/`HOLD_RATE`/`holdGesture` + `holdBegin`/`holdEnd`/`holdIsPlaying` 与三个触摸监听）。没有新增对外接口、配置项、持久化或可见 UI；改动只有这一段（`git diff` 仅此文件 +39 行）。
- **测试**：`tests/test_hold_speed_core.py`（新增，36 个用例：35 passed + 1 skipped）。公共助手全部复用 01 的 `tests/player_harness.py`；1.5× 起始速度用公开 API `video.playbackRate = 1.5` 建立（页面当前没有倍速入口），期望值取自契约。
- **夹具修复**：`tests/player_harness.py` 两处缺陷（下方「夹具缺陷」），签名与「显式 import 夹具」用法保持不变，改动是与 01 的实现者（T01Harness）协调后落地的。

### 实现机制（03/04 直接复用的事实）

- 监听挂在 **`v`（video 元素）**上：`touchstart`（只有 `e.touches.length === 1` 才开始）→ `holdBegin(clientX, clientY)`；`touchend`（`e.touches.length === 0`）与 `touchcancel` → `holdEnd()`。全程不调用 `preventDefault`，也不使用 `pointer`/`mouse`。
- **判定基础在按下瞬间固定**：`holdGesture = {x, y, rate, playing, triggered, timer}`。`x/y` 是按下点的 `clientX/clientY`（**当前只存不用**，03 的「相对按下点 12 CSS 像素」判定直接用这两个数，不要在 move 里重算起点）；`rate` 是按下瞬间的 `v.playbackRate`；`playing` 是按下瞬间是否在播放（`!paused && !ended`）。
- 只用**一个** `setTimeout(HOLD_MS)`。回调里再检查 `gesture.playing && !v.paused && !v.ended` 才 `v.playbackRate = 2`（固定绝对值，不是当前速度乘二）——所以「暂停保护」在门槛点仍然成立，且触发后不会再有任何计时器（持续按住期间不会漂移）。
- **手势状态的唯一清理出口是 `holdEnd()`**：清空 `holdGesture`（置 `null`）→ `clearTimeout` → 若 `triggered` 则恢复 `gesture.rate`。03/04 的取消与中断只需调用它（或在其前面加判定），不会留下旧的按住时长。
- 控件不被截获是结构性保证：`#fs-btn`/`#fs-exit-btn`/`#back-btn` 都不是 `v` 的子节点，落在它们上面的触摸不会进入手势；没有新增任何覆盖层。

### 跑过的命令与结果

| 阶段 | 命令 | 结果 |
|---|---|---|
| 红（实现前） | `python -m pytest tests/test_hold_speed_core.py -q --tb=line -p no:randomly` | 20 failed / 15 passed / 1 skipped（失败全部来自未实现的行为；另有一处是我写错的 fullscreen 可见文字基线，已修正） |
| 绿（实现后） | 同上 | 35 passed / 1 skipped |
| 验收命令 | `python -m pytest tests/test_hold_speed_core.py tests/test_player_harness.py -q`（连跑 2 次，均针对夹具最终修订 `tests/player_harness.py` 14:12:48，该修订含 01 实现者的 teardown 收紧与假时钟撞点重试） | **53 passed, 1 skipped**（两次相同；35 + 18，1 skipped = webkit 无法做的原生控件桌面证据） |

### 承诺 → 自动化覆盖 → 证据强度

| 覆盖 | 用例 | chromium 输入 | webkit 输入 |
|---|---|---|---|
| P1 门槛两侧 + 固定 2×（1×/1.5× × 两模式） | `test_hold_500ms_switches_to_fixed_2x_and_release_restores_previous_rate` | 引擎级真实触摸 | 合成 TouchEvent（弱） |
| P1 持续按住保持 2× / 不被无关事件打断 | `test_rate_stays_at_2x_for_as_long_as_the_press_continues` | 同上 | 同上 |
| P1 左/中/右非控件区域 | `test_left_center_right_non_control_areas_all_trigger` | 同上 | 同上 |
| P2 1×→2×→1×、1.5×→2×→1.5× | 同上第一个用例 | 同上 | 同上 |
| P2 未达门槛松手不变 | `test_release_before_500ms_leaves_rate_untouched` | 同上 | 同上 |
| P3 暂停保护（两模式 × 两起始速度） | `test_paused_hold_neither_plays_nor_enters_temporary_rate` | 同上 | 同上 |
| P8 三阶段 × 两模式无新增文字/图标/浮层 | `test_no_visible_prompt_appears_in_waiting_accelerating_or_restoring_phase` | 同上 | 同上 |
| P9 自制全屏进入/退出按钮 | `test_custom_fullscreen_buttons_keep_working_and_never_trigger_the_hold` | 按压同上 + 点按钮用引擎级真实 tap | 同左 |
| P9 原生控件桌面证据 | `test_native_control_bar_operations_are_not_swallowed` | 真实鼠标点原生控件条 | 跳过（`PLAY_CONTROL_OFFSETS` 未实测可稳定驱动 webkit） |

每个用例都用 `TouchGesture.evidence` 断言强度：chromium 必须 `trusted_input is True`，webkit 必须为 `False`（合成事件，只能证明逻辑，不构成触摸平台结论）。观察口径只有 `video.playbackRate` / `paused` / `currentTime` / `ended` / `readyState`、`body.custom-fullscreen`、元素可见性与 `document.elementFromPoint`。

### 夹具缺陷（01 交付物，本票修掉两处）

1. **webkit 合成事件的事件名大小写**：`_SYNTHETIC_TOUCH_JS` 把 CDP 风格的名字（`touchStart`/`touchEnd`/`touchCancel`）直接传给 `TouchEvent` 构造函数 → 页面上的 `touchstart`/`touchend` 监听器**从来没收到过**这些按压（页面侧观察器实测 `[['touchStart','v',1,false]]`）。改为 `arg.type.toLowerCase()` 后 webkit 的按压才真正到达页面（仍 `isTrusted=false`）。01 自检之所以全绿，是因为它只验证 harness 侧记录与页面状态，没有验证「页面收到按压」。
2. **会话级夹具跨模块重复实例化**：夹具是被测试模块显式 import 的，pytest 会按模块各实例化一次 → 第二个模块再跑 `sync_playwright()` 时抛 `It looks like you are using Playwright Sync API inside the asyncio loop.`（实测：两文件同跑 = 35 passed + **18 errors** 全在 `test_player_harness.py`；单跑它则 18 passed）。已按模块级注册表（`_SESSION_PLAYWRIGHT`/`_SESSION_HARNESSES`/`_SESSION_FAILURES`）修成「进程内只建一次、创建者负责 teardown」，签名不变。

此外 01 的实现者随后还修了合成触摸坐标 payload（`_as_js_point`，原先数组 → `point.x === undefined` → webkit 坐标恒为 0,0）并把夹具片长改成 30 秒；03 的位移判定依赖这两个修复后的坐标。

### 已知限制与实测观察

- **webkit 的按压仍是弱证据**：合成 `TouchEvent` 绕过 UA 手势识别，只证明页面逻辑；webkit 的可信输入只有真实 `tap()`（本票用它验证自制全屏按钮照常工作）。
- **chromium 冻结假时钟下媒体位置会虚增**：实测（`g.up()` 前后两次读取之间，假时钟未动、真实时间约 10ms）`currentTime` 从 0.75 直接变成 8.00（片尾）。因此用例给夹具视频设了公开 API `loop = true` 并完全不读媒体位置；本功能与循环播放无关。
- **原生控件条对可信触摸的处理（桌面探针，仅记录、不作断言）**：chromium 上把可信触摸按在实测的控件条播放按钮位置时，**页面完全收不到**触摸事件（观察器为空），原生控件把视频暂停了、速度保持 1×——即桌面 chromium 上控件区域根本不会触发长按。webkit 的合成按压在该位置仍会触发（合成事件直接派发到 `elementFromPoint` = video，绕开 UA 控件条），所以 webkit 结果不能说明真机行为。真机（iOS Safari 原生控件是否吞掉触摸）由 05 给出结论。
- **两引擎差异如实记录**：webkit 的真实 tap 会切换播放/暂停、chromium 的不会（01 已记录），本票没有用放宽断言抹平；本票也刻意不断言「松手后的播放/暂停切换」（P10，04 交付）。

### 真机待验收（05）

- P1：真机两种模式下的真实触摸触发与实际 2×。
- P2：真机松手后实际恢复原速。
- P3：真机（接触屏幕不恢复播放）。
- P8：真机视觉结果（无提示 UI）。
- P9：原生控件（播放/暂停、进度拖动）真机协作；本票风险段提到的「真机控件触摸是否触发长按」也由 05 给出结论。
- 本票建议的自查（可选）：进自制全屏后长按画面确认可用、松手回原速、暂停时长按不启动播放、控件未被干扰。

**P1/P2/P3/P8/P9 整体仍未完成**（真机行未验收）；本票只交付自动化覆盖项。
