# 03: 位移容差与滑动取消

**Delivers:** P4, P5（自动化覆盖项；真机覆盖项由 05 承载）

**Test owner for:** P4、P5 的自动化（确定性 + 浏览器集成）覆盖项

**Blocked by:** 02

**Status:** closed

**Parent:** `.scratch/hold-to-double-speed/contract.md`（Seam S1；来源 `spec.md`）

## What to build

手指相对最初按下位置的直线位移不超过 12 CSS 像素时，本次长按不因该移动取消；一旦直线位移超过 12 CSS 像素（含斜向），本次手势结束：尚未触发时取消等待且之后不再触发，已经加速时立即恢复长按前速度。两种观看模式一致。

位移按「最初按下位置到当前位置的直线距离」衡量，不是累计路径长度；门槛按 CSS 像素，不按设备像素。

## Acceptance criteria

- [x] 等待触发期间零位移、轻微位移、恰为 12 CSS 像素的移动都不取消等待，满 500 毫秒仍触发 2×。— 自动化已证实（两引擎 × 普通/自制全屏；合成精确坐标，逐笔核对页面读回的位移，其中一笔精确等于 12.0 CSS 像素）
- [x] 已加速期间零位移、轻微位移、恰为 12 CSS 像素的移动都不结束手势，维持 2×。— 自动化已证实（同一组位移，先触发 2× 再移动，之后继续按住仍为 2×）
- [x] 相对位移超过 12 CSS 像素时，等待期间取消本次长按，且之后（手指移回、继续按住、再次移动）都不会延迟触发临时倍速。— 自动化已证实（4 种越界轨迹：轴向 12.5 / 13、斜向 12.014、斜向 9/9；取消后再推进 2000 毫秒仍为长按前速度，移回原位、继续按住、再次移动、抬起都不触发）
- [x] 相对位移超过 12 CSS 像素时，已加速期间立即恢复长按前速度。— 自动化已证实（1× 与 1.5× 各一次；另有一条 chromium **可信输入**用例：真实 16 CSS 像素移动越过门槛后立即恢复原速）
- [x] 位移按起点到当前点的直线距离衡量：斜向移动、先移出再移回都与直线距离规则一致，不按累计路径长度判定。— 自动化已证实（来回移动的累计路径 48 像素、直线距离始终 ≤12：不取消且仍触发；斜向 9/9 两个分量都 ≤12 但直线距离 12.728：取消。把度量换成「分量最大值」的变异会让这两条用例变红）
- [x] 门槛两侧都验证：恰为 12 CSS 像素保持手势，略大于 12 CSS 像素结束手势。— 自动化已证实（轴向 ±12 的读回位移精确为 12.0 → 保持；12.5 / 13.0 → 结束；斜向 11.986 / 12.014 两侧。门槛改成 5 的灵敏度变异会让门槛内侧用例全红）
- [x] 两种观看模式（普通页面、自制横屏全屏）下规则一致。— 自动化已证实（保持与取消两条路径都在普通页面与 `enter_custom_fullscreen()` 后的自制横屏全屏里跑）
- [x] 门槛按 CSS 像素判定：缩放或设备像素比变化不改变判定（自制横屏旋转后的屏幕尺度由 05 真机验收）。— **部分自动化**：设备像素比 2 的会话里 12 CSS 像素仍是门槛内侧、13 仍是外侧（chromium 上真实 16 CSS 像素 = 32 设备像素的移动同样取消）；页面缩放与真机旋转后的屏幕尺度没有自动化设施，**由 05 真机验收**
- [x] 不新增可配置阈值，不新增提示。— 已核实：实现只有 `HOLD_SLOP_PX = 12` 一个硬编码常量（无配置项、无对外接口、无持久化）；保持/等待/取消/恢复四个阶段可见内容与按压前逐项一致（两引擎）
- [x] 记录证据强度与限制；真机行未验收前不得宣称 P4、P5 完成。— 见下方 `## Comments`；**P4、P5 整体仍未完成**（旋转后屏幕尺度与真机滑动体感由 05 承载，本票只交付自动化覆盖项）

## 覆盖分区

| 承诺 | 覆盖项 | 验证方式 | 归属（写测试） |
|---|---|---|---|
| P4 | 等待触发 / 已加速 × 零位移 / 轻微位移 / 恰为 12 CSS 像素 | 确定性自动化（受控时间 + 可控制轨迹的按压） | 03 |
| P4 | 自制横屏旋转后仍按屏幕上的 CSS 像素衡量 | 真机人工 | 05 |
| P5 | 等待期间取消且之后不延迟触发 | 确定性自动化 | 03 |
| P5 | 已加速期间恢复原速 | 确定性自动化 | 03 |
| P5 | 两种观看模式 | 确定性自动化 + 浏览器集成 | 03 |
| P5 | 位移按起点至当前点直线距离（含斜向） | 确定性自动化 | 03 |
| P5 | 真机滑动体感 | 真机人工 | 05 |

## 验证限制

合成触摸的坐标是脚本给出的，能精确验证 12 CSS 像素门槛两侧；真机上手指的绝对位移与屏幕尺度由 05 验收。桌面浏览器结论不构成 iOS Safari 结论。

## Comments

### 交付物

- **实现**：`server/static/player.js` 现有手势段（`git diff` 只有这一个文件有改动）。本票增量 = 1 行注释 + `var HOLD_SLOP_PX = 12;` + 6 行 `touchmove` 监听；没有新增配置项、对外接口、持久化或可见 UI，下载/字幕/串流/播放位置记忆都未触碰（P1–P3、P8、P9 的既有行为由 02 的 35 个用例守住）。
- **测试**：`tests/test_hold_speed_slide.py`（新增，50 个用例 = 46 passed + 4 skipped）。
- **夹具**：`tests/player_harness.py` 增补（**向后兼容**，公共签名与默认路径不变——chromium 的 `touch()` 默认仍走 CDP）：
  - `TouchEventRecord.trusted`（逐事件标明通道，默认 `True`）；
  - `TouchGesture.move_exact_to/move_exact_by`（**总是**走页面内合成 `TouchEvent` 的精确坐标移动；记录**页面读回**的坐标）；
  - `GestureEvidence.synthetic_events`、`trusted_input` 语义收紧为「全部事件都可信才 True」、`strength` 增加混合档；
  - `PlayerHarness.session(device_scale_factor=…)` / `PlayerSession(device_scale_factor=…)`；
  - `_SYNTHETIC_TOUCH_JS` 现在把合成触点读回的坐标返回给夹具（一处顺带修掉的缺陷：`up()/cancel()` 之前把释放事件记成 `trusted=True`，在 webkit 上会把全合成序列误标成「混合」）；
  - 模块 docstring 的「实测边界」补齐本票复测到的两条事实（CDP 丢弃首个 <16 px 移动、合成坐标量化），
    并按票据 01 §12 的复核结论补上「混用通道时抬起的触点是各通道自己记的位置」（chromium 上 CDP 按下 +
    合成移动时，随后的 CDP `touchend` 报的是按下点，不是合成路径的最后位置）——防止 04 断言松手瞬间坐标时在
    chromium 上空洞通过。
  - 01 的 18 个自检在改动后仍全绿。

### 实现机制（04 直接复用的事实）

- `touchmove` 判定就在 `v` 上新增的第四个监听里，位置在现有 `touchstart` 之后、`touchend` 之前：
  `if (!holdGesture || e.touches.length !== 1) return;` → 用 `e.touches[0].clientX/clientY` 减去
  `holdGesture.x/y`（**按下瞬间固定**的起点）算 `Math.hypot(dx, dy)` → `> 12` 就 `holdEnd()`。
  不重算起点、不把起点改成上一次移动位置（否则会变成累计路径语义）、不读 `changedTouches`、多指时不判定。
- **结束的唯一出口仍是 `holdEnd()`**：置 `holdGesture = null` → `clearTimeout(timer)` → 若 `triggered` 则恢复
  `gesture.rate`。它幂等（`if (!holdGesture) return`），所以中断处理可以直接调用，不会重复恢复速度。
- 结束后的状态：`holdGesture` 为 `null` + 计时器已清 + 速度已按需恢复；后续 `touchmove` 在第一行就早退，
  所以「移回原位 / 继续按住 / 再移动」都不会复活旧手势；再次按下必然走新的 `holdBegin`（重新计时）。
- 中断（04 / P6）最安全的挂法：**只调用 `holdEnd()`**，不要在别处直接写 `v.playbackRate`（否则与
  `gesture.rate` 记录不一致）。注意现有结构：
  - `#fs-btn` / `#fs-exit-btn` / `#back-btn` 都不是 `v` 的子节点，进出自制全屏**不会**经过手势监听 → P6 需要在
    `enterFS`/`exitFS` 或页面已有的 `document` `fullscreenchange` / `webkitfullscreenchange` 监听里调用它；
  - 页面已有 `window` `orientationchange`（会自动 `exitFS`）；`visibilitychange` 目前**没有**监听；
  - 计时器只在 `holdBegin` 里创建一次，只要走 `holdEnd()` 就不会残留；
  - 测试侧可用：`move_exact_*`（合成精确移动，弱证据）与 `evidence.trusted_input`（混用即 `False`）；
    `evidence.synthetic_events` 过滤合成事件。

### 空洞通过的识别与处置（本票的主要风险）

- **风险**：chromium 会丢弃首个小于 16 CSS 像素的真实 touchmove（01 发现，本票复测确认：2/4/8/12/14 像素
  到达页面的 touchmove 数为 0，16/20/40 像素各 1 个且 `isTrusted=true`、`clientX` 精确等于派发值）。若门槛内侧
  用例照原样走 CDP，「移动根本没到页面」会让「不取消」空洞通过。
- **处置**：门槛内侧全部改走 `move_exact_*`（页面内合成 `TouchEvent`，坐标由脚本精确给出，**弱证据**），并逐用例做三件事：
  1. 核对通道——合成事件只允许出现在移动上，chromium 的按下/抬起仍必须是引擎级真实触摸（`trusted_input` 因此如实为 `False`/混合）；
  2. 断言 `evidence.events` 里**页面读回**的位移落在预期一侧（恰好 12 的用例要求读回值精确为 12.0）；
  3. 门槛外侧另加**可信输入**用例（chromium 真实 16 像素移动，等待与加速两阶段 × 两模式），证明真实触摸路径同样会取消。
- **顺带发现并记录**：chromium 会对合成坐标做 float32 量化（按下点 y=203.79999999999998 → 合成触点读回
  203.80000305175781，于是「12 像素」在页面上变成 12.000000000000389）。所以本文件把按压点固定为
  `y_frac=0.25`（普通页面 175.5、自制全屏 150.0，都是 float32 无损值），并且所有边界断言基于读回值而不是派发值。
- **斜向恰好 12 不可构造**（构造限制，不是放宽断言）：3-4-5 的斜边 12 在二进制浮点里无法精确表示
  （7.2/9.6 的实测直线距离随坐标落在 11.99999999999999 或 12.00000000000001），叠加引擎量化后更不可控。
  故「恰好 12」由轴向 ±12（读回精确 12.0）承担，斜向用 11.986 / 12.014 紧贴两侧，另加 9/9（12.728）证明直线距离语义。

### 跑过的命令与结果

| 阶段 | 命令 | 结果 |
|---|---|---|
| 红（实现前，`touchmove` 监听还不存在） | `python -m pytest tests/test_hold_speed_slide.py -q --tb=no` | 38 failed / 8 passed / 4 skipped；失败全部来自 P5 的取消与恢复路径。其中 2 个失败是我自己的通道断言助手写错（webkit 的按下/抬起本来就是合成事件），修正助手后与本票实现无关 |
| 红等价复核（修正助手后，把门槛设成永不触发 = 实现前的可观察行为） | 同上（`HOLD_SLOP_PX = 100000`） | 34 failed / 12 passed / 4 skipped；**12 个绿灯全部是「门槛内侧不取消」用例**——它们在「根本没有取消逻辑」时本来就成立，所以不能单独当证据，34 个取消类用例全红 |
| 绿（实现后） | `python -m pytest tests/test_hold_speed_slide.py -q` | **46 passed, 4 skipped** |
| 灵敏度变异 1：门槛 12 → 5 | 同上 | 14 failed（门槛内侧 / 恰好 12 / 设备像素比两侧用例全红）→ 门槛两侧的绿灯对阈值敏感 |
| 灵敏度变异 2：`Math.hypot(dx, dy)` → `Math.max(Math.abs(dx), Math.abs(dy))` | 同上 | 12 failed，正好是斜向 12.014 / 12.728 与直线距离用例 → 用例确实区分「直线距离」与「分量」两种度量 |
| 验收命令（本票 + 02 + 夹具自检） | `python -m pytest tests/test_hold_speed_slide.py tests/test_hold_speed_core.py tests/test_player_harness.py -q` | **99 passed, 5 skipped**（46+4、35+1、18）→ 02 的行为没有被改坏；变异与复核均在恢复实现后重跑到 46 passed 才收尾 |
| 一次性探针（已删除） | `.scratch/hold-to-double-speed/tmp-probe-03.py`、`tmp-probe-03b.py` | 用来测：CDP 移动的送达门槛、合成坐标的量化、CDP 按下的坐标保真（实测精确送达 400/175.5）、dsf=2 会话可用性；结论已写进 `player_harness` 的「实测边界」与上方「空洞通过」小节 |

### 承诺 → 覆盖项 → 证据强度

| 覆盖 | 用例 | chromium 输入 | webkit 输入 |
|---|---|---|---|
| P4 等待期间：零位移 / 轻微 / 恰好 12（轴向）/ 斜向 11.986 | `test_waiting_phase_survives_every_in_tolerance_move_and_still_triggers` | CDP 真实按下 + **合成精确移动**（弱证据） | 全序列合成（弱证据） |
| P4 已加速期间：同一组位移 | `test_accelerating_phase_survives_every_in_tolerance_move` | 同上 | 同上 |
| P5 等待期间越界取消 + 之后不延迟触发（4 种轨迹） | `test_waiting_phase_move_beyond_12px_cancels_and_never_fires_later` | 同上 | 同上 |
| P5 已加速期间越界恢复原速（1× / 1.5×） | `test_accelerating_phase_move_beyond_12px_immediately_restores_previous_rate` | 同上 | 同上 |
| P5 直线距离而非累计路径 + 斜向分量 | `test_distance_is_straight_line_from_press_point_not_cumulative_path` | 同上 | 同上 |
| P5 越界取消（**可信输入**） | `test_chromium_trusted_move_beyond_12px_cancels_waiting` | **CDP 真实触摸全程可信**（16 CSS 像素） | 跳过（webkit 没有可信移动通道） |
| P5 越界恢复原速（**可信输入**） | `test_chromium_trusted_move_beyond_12px_restores_rate_while_accelerated` | **CDP 真实触摸全程可信** | 跳过（同上） |
| P4/P5 门槛按 CSS 像素（设备像素比 2） | `test_threshold_follows_css_pixels_not_device_pixels` | 合成精确移动 + 真实 16 像素移动（混用） | 合成精确移动（弱证据） |
| 不新增提示 | `test_slide_handling_shows_no_new_visible_content` | CDP 真实按下 + 合成精确移动 | 全序列合成 |

**弱证据**：所有 `move_exact_*` 参与的断言（chromium 上的门槛内侧与恰好 12、两种观看模式、设备像素比 2 的
12 像素用例；webkit 上的全部用例）。**可信输入**：chromium 的真实 16 CSS 像素移动用例，以及各用例里 chromium
的真实按下/抬起（合成移动不会篡改浏览器内部触摸状态，几何一致）。**证据强度不因本票通过而升级**。

### 真机待验收（05 承载；未验收前不得宣称 P4、P5 完成）

- 自制横屏旋转（`screen.orientation.lock('landscape')` 后的实际屏幕尺度）下，12 CSS 像素仍是屏幕上的 12 CSS 像素；
- 真机滑动体感：12 CSS 像素容差是否合适、斜向滑动、滑出后再滑回的行为；
- 真机手指的绝对位移与触点量化（本票的坐标由脚本精确给出，真机还包含设备的坐标量化与触摸 slop）。

### 已知限制

- 桌面 chromium/webkit（chromium-1243、webkit-2359）+ 合成触摸不构成 iOS Safari 结论。
- webkit 的按压序列只能是页面内合成事件（`isTrusted=false`）：P5 的可信输入证据只在 chromium 上有。
- 12 像素在真实坐标量化下是「软边界」：客户端坐标若因设备量化落到 12.000001，按契约（`> 12` 结束）会结束手势。
  实现按契约不加 epsilon，也不新增可配置阈值。
- 「缩放」维度只覆盖到设备像素比 2（Playwright 没有页面缩放 / 捏合手势 API）；旋转后的屏幕尺度留给 05。
