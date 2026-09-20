# 01: 浏览器验收骨架（真实页面 + 真实视频 + 真实触摸 + 受控时间）

**Delivers:** enabling: unblocks P1–P10 的自动化覆盖项

**Blocked by:** None (can start immediately)

**Status:** closed

**Parent:** `.scratch/hold-to-double-speed/contract.md`（Seam S1；来源 `spec.md`）

## What to build

在真实浏览器里运行真实播放器页面，把「按住、移动、松开」做成可脚本化的真实触摸输入，把 500 毫秒门槛做成受控时间，并且只从页面上公开可观察的状态读出结果。本票不实现任何长按临时倍速行为：它交付后续票据共用的夹具与观察能力，并如实记录哪些证据是可信输入、哪些只是合成事件。

骨架的硬要求：

- **真实页面**：渲染现有播放器页面的真实模板并加载真实静态资源，不得复制一份页面副本，否则测的不是用户看到的页面。
- **真实视频**：页面必须加载并实际播放一段本地生成的、可正常播放的 Video（生成方式自选，但必须验证 `currentTime` 确在推进），避免把缓冲或解码失败误判成倍速行为。线上播放源是 HLS 播放列表，桌面浏览器不一定能播放；夹具只替换播放源，不改页面代码。
- **真实触摸**：能派发一次可控制时长与轨迹的按压（按下 → 可选多次移动 → 抬起），并能在两种观看模式下使用：普通页面，以及与页面现有全屏入口进入的自制横屏全屏。
- **受控时间**：500 毫秒门槛两侧必须能确定性判定，不依赖真实等待、不因机器负载抖动；同时记录受控时间对媒体播放、Web Animations 与事件回调的实际影响。
- **只观察公开状态**：判定只使用 Video 元素公开的播放速度、播放状态与可见 DOM 的变化；不读取手势内部变量、内部计时器或实现私有字段；不以「自己派发的合成事件被收到」作为结论。
- **可运行性**：本机已验证可用 Playwright 浏览器（chromium 与 webkit）、pytest、ffmpeg 与 pip 网络。缺少浏览器自动化设施时整组测试必须自动跳过，不得让既有 `pytest` 套件失败。
- **可复用**：夹具的助手用法自解释；后续票据新增用例不应需要改动夹具的公共助手。

交付时用两个与本次功能无关的自检证明骨架真的能测东西（它们是骨架的可行性证明，不是承诺验收）：其一，用真实输入点击原生控件的播放按钮，观察到播放状态翻转；其二，页面内 `setTimeout(…, 500)` 在受控时间门槛两侧分别不触发与触发。

## Acceptance criteria

- [ ] 夹具渲染真实播放器页面模板与真实静态资源，页面在浏览器中实际播放本地视频（`currentTime` 推进）；普通页面与自制横屏全屏两种模式都能到达并保持该视频在播放。
- [ ] 能派发一次可控制时长与轨迹的按压输入，并能对同一页面重复派发多次而互不残留状态。
- [ ] 500 毫秒门槛可在受控时间下确定性判定（门槛两侧各一次），不需要真实等待；记录所选机制及其对媒体播放、动画与事件回调的实际影响。
- [ ] 判定只读取 Video 元素公开的播放速度、播放状态与可见 DOM；测试中不存在读取实现私有变量、内部计时器或私有状态字段的断言。
- [ ] 自检一：真实输入点击原生控件播放按钮，播放状态发生翻转（证明「真实输入 → 页面公开状态」观察链成立）。
- [ ] 自检二：页面内 500 毫秒定时器在受控时间门槛两侧分别不触发与触发（证明受控时间真实可控）。
- [ ] 明确记录证据强度：每条用例使用的是可信真实输入还是合成事件；记录每个引擎上实际跑通的用例集合。
- [ ] 缺少浏览器自动化设施时整组测试自动跳过；既有 `pytest` 全量套件保持通过。
- [ ] 不实现、不预埋任何长按临时倍速行为；不新增页面可见内容；不改动播放器既有行为。
- [ ] 不为测试新增对外接口、配置项或持久化格式；夹具只存在于测试范围内。

## 覆盖分区

本票为 enabling 切片，不占有任何承诺的覆盖项。它使下列自动化覆盖项可被证明：P1、P2、P3、P8、P9 的自动化项（02）、P4、P5 的自动化项（03）、P6、P7、P10 的自动化项（04）。真机覆盖项一律由 05 承载。

## 验证限制

桌面浏览器与合成触摸只提供桌面证据，不构成 iOS Safari 结论（契约规定）。骨架跑通不等于任何承诺的验收完成。

## Comments

### 1. 交付物（构建了什么）

| 文件 | 角色 |
|---|---|
| `tests/player_harness.py` | 夹具本体：真实页面服务 + 夹具视频 + 真实浏览器 + 真实触摸 + 受控时间 + 只读公开状态。公共助手与用法写在模块 docstring 里 |
| `tests/test_player_harness.py` | 骨架自检：7 个用例 × 2 引擎 = 14 项 |
| `requirements-dev.txt` | 测试专用依赖（`playwright>=1.63`）；`requirements.txt`（应用运行依赖）未改动 |

硬要求对应：

- **真实页面**：`/play/<id>` 由 `server/app.py` 的真实模板 `_PLAYER_TPL` 与真实渲染函数 `_render` 渲染，`/static/player.js`、`/static/player.css` 直接来自 `server/static/` 的真实文件。用例对页面 HTML（与真实模板逐字符相等）与两个静态资源（逐字节相等）做比对。唯一差异是播放源：`data-playlist-url="/media/fixture.mp4"`。没有页面/脚本副本；**本票自身没有改动 `server/` 里任何文件**（工作区里 `server/static/player.js` 的改动属于票据 02 的行为实现，不是本票）。
- **真实视频**：ffmpeg 生成 30 秒 320×180 25fps 的 mp4（服务端支持 Range；§10 记录为何从 8 秒加长到 30 秒）。播放成功判据是浏览器里 `paused == false` 且 `currentTime` 在推进，**不是** `canPlayType` 的声明。两个引擎都探测：chromium-1243 与 webkit-2359 都以第一个候选 **mp4/h264** 播放成功（`ok: 播放中 currentTime=0.11` / `0.14`），未回退到 webm；若引擎不支持，夹具会依次回退 webm/vp8、webm/vp9，都不行才整组 skip。
- **真实触摸**：`session.touch()` 派发「按下 → 若干次移动（`move_to` 绝对坐标 / `move_by` 相对按下点）→ 抬起或取消」，每次调用立即派发，时间由测试自己控制（受控时间或真实时间）。chromium 走 CDP `Input.dispatchTouchEvent`（touchStart/touchMove/touchEnd/touchCancel，context `has_touch=True`）＝引擎级真实触摸；普通页面与自制横屏全屏都验证过。
- **只观察公开状态**：见 §5。
- **可复用**：助手清单见 `tests/player_harness.py` 模块 docstring，02/03/04 直接导入 `harness / harness_factory / playwright_instance / playing_session / session` 这几个夹具即可，不需要改动夹具公共助手。

### 2. 复现命令与实测结果

环境：Windows 11、Python 3.11.9、pytest 9.1.1、playwright 1.63.0、ffmpeg（PATH）。

| 命令 | 实测结果 |
|---|---|
| `python -m pip install playwright` | 装上 playwright 1.63.0 |
| `HTTPS_PROXY=http://127.0.0.1:10808 python -m playwright install chromium webkit` | chromium-1243 / chromium_headless_shell-1243 / webkit-2359 下载完成（121.55s）。直连 CDN 实测约 0.2 MB/s，走本机代理约 2.7 MB/s；不加代理时第一次安装 10 分钟无进展，已取消后重跑 |
| `python -m pytest tests/test_player_harness.py -q` | **14 passed in 13.23s** |
| `python -m pytest tests/test_player_harness.py -v` | 14 项全部 PASSED（逐引擎清单见 §3） |
| `python -m pytest -q`（全量） | **94 passed in 28.62s**（既有 80 + 本票 14，无失败、无 skip） |
| `python -m pytest -q --ignore=tests/test_player_harness.py` | **80 passed in 14.92s**（既有套件保持通过；全量里那两条 yt-dlp 网络告警是既有测试自身的输出，不是失败） |
| `PLAYWRIGHT_BROWSERS_PATH=<空目录> python -m pytest tests/test_player_harness.py -q -rs` | **14 skipped in 0.28s**；skip 原因：`无法启动 chromium 浏览器: ... Executable doesn't exist at <空目录>\chromium-1243\chrome-win64\chrome.exe`（webkit 同理）——缺设施时整组自动跳过，既不会有收集错误也不会让既有套件失败 |
| `python -m pytest tests/test_player_harness.py -q`（连续 3 次） | 13.11s / 13.08s / 13.16s，每次 **14 passed**——用例互相隔离、可重复运行（每个用例独立 browser context，`video_id` 每次不同，`localStorage` 不互串） |

### 3. 证据强度与逐用例 × 引擎结果

证据口径：`TouchGesture.evidence` 给出 `mechanism` 与 `trusted_input`；用例显式断言了这两个值（chromium 必须全部 `trusted_input is True`，webkit 必须全部为 `False`），所以下表不是推断。

| 用例（× chromium / webkit） | 用到的输入 | 证据强度 | chromium | webkit |
|---|---|---|---|---|
| `test_fixture_serves_the_real_player_page_and_real_static_assets` | 无输入（HTTP 字节比对） | — | PASSED | PASSED |
| `test_fixture_video_really_plays_in_inline_and_custom_fullscreen` | 真实点击 `#fs-btn` 进自制全屏 | 引擎级真实输入 | PASSED | PASSED |
| `test_press_move_release_repeats_without_leaving_state` | 3 次按压（轻移抬起 / 移远取消 / 多次轨迹抬起） | chromium: CDP `Input.dispatchTouchEvent` = **可信真实触摸**；webkit: 页面内合成 `TouchEvent`（`document.createTouch`+`createTouchList`，`isTrusted=false`）= **弱证据** | PASSED | PASSED |
| `test_press_sequence_works_in_custom_fullscreen` | 自制横屏全屏里按压 | 同上 | PASSED | PASSED |
| `test_selfcheck_1_real_input_flips_a_public_play_state` | chromium: 真实鼠标点原生控件条播放按钮（来回各一次）；webkit: 真实触摸 tap 画面（来回各一次） | 引擎级真实输入 | PASSED | PASSED |
| `test_selfcheck_2_controlled_clock_decides_the_500ms_threshold` | 受控时间（无输入） | — | PASSED | PASSED |
| `test_custom_fullscreen_entry_and_exit_work_under_controlled_time` | 真实点击 `#fs-btn` / `#fs-exit-btn` | 引擎级真实输入 | PASSED | PASSED |

夹具事实采集（同一夹具代码路径的一次性脚本输出，非测试断言）：

```
ENGINE chromium
browser launch: 默认 headless
playback source: mp4/h264 → ok: 播放中 currentTime=0.11
press sequence evidence: CDP Input.dispatchTouchEvent（引擎级真实触摸）| trusted_input: True
events: touchstart@(400.0,175.5), touchmove@(400.0,205.5), touchend
native control drive: paused False -> True   （@(24,560)，即 video 左下方 24,40 偏移）
custom-fullscreen press: CDP Input.dispatchTouchEvent | trusted: True

ENGINE webkit
browser launch: 默认 headless
playback source: mp4/h264 → ok: 播放中 currentTime=0.14
press sequence evidence: 页面内合成 TouchEvent（document.createTouch + createTouchList，isTrusted=false）| trusted_input: False
events: touchstart@(400.0,175.5), touchmove@(400.0,205.5), touchend
picture tap drive: paused False -> True      （@(400,204) 画面内）
custom-fullscreen press: 页面内合成 TouchEvent | trusted: False
```

### 4. 受控时间：机制与实测影响

机制（`ControlledClock`）：`goto(clock=True)` 在**导航前** `page.clock.install()`（避免加载期计时器被冻结卡死）→ `clock.freeze()` 用 `pause_at(Date.now()/1000)` 停在当前假时刻 → `clock.advance(ms)` 用 `fast_forward(ms)`（跳时间并最多触发一次到期计时器）推进。

确定性判据（自检二，两个引擎都过）：页面内 `setTimeout(…, 500)` 探针，`advance(499)` 后 `__harnessProbe == []`，再 `advance(1)` 后 `== ["fired"]`，且 `Date.now()` 差值**恰好 500**（冻结后不受真实时间影响）。

| 受影响面 | 实测结论 |
|---|---|
| 页面 `setTimeout` / `setInterval` | 冻结后不再自行触发，只能靠 `advance`/`run_for`；`performance`、`Date` 也被替换 |
| 页面 `requestAnimationFrame` | 冻结后 **0 tick**（0.3s 观察）。因此夹具的所有等待都走 Python 侧轮询（`wait_for*`），不用 `wait_for_function`/页面内 rAF 轮询 |
| 媒体播放 | **不受影响**：冻结假时钟期间 `currentTime` 仍由真实时间推进（0.222 → 0.824，0.6s）。这是 02/03 能在受控时间下观察 `playbackRate` 的前提。（02 报告过一次成因未定位的瞬时跳变，见 §10） |
| Web Animations | `enterFS()`/`exitFS()` 的 200ms 淡入 `onfinish` 照常完成，`body.custom-fullscreen` 0.28s 内生效。所以**不需要**「先全屏再装时钟」的分阶段技巧；`test_custom_fullscreen_entry_and_exit_work_under_controlled_time` 就是先冻结时钟再进/出全屏 |
| Playwright 自身的点击/等待 | 不受影响（注入世界独立于被替换的页面世界），`page.click("#fs-btn")` 在冻结时钟下正常 |

### 5. 观察口径（只读公开状态）

`PlayerSession.read()` 只读 Video 元素的 `paused` / `playbackRate` / `currentTime` / `ended` / `readyState`，加上可见 DOM：`body.custom-fullscreen`、`#fs-btn` / `#fs-exit-btn` 的 `display`；另外 `hit_id(x,y)` 读的是 `document.elementFromPoint` 的公开命中结果。全部断言里没有读取任何实现私有变量、内部计时器或私有状态字段；页面里不注入任何业务逻辑——唯一的页面内注入是自检二那个与功能无关的 `setTimeout` 探针，只在自检用例里出现。

### 6. 给下一张票（02）的夹具用法

```python
from player_harness import (          # 夹具必须显式导入到测试模块才可见
    harness, harness_factory, playing_session, playwright_instance, session,
)

pytestmark = pytest.mark.parametrize("engine", ("chromium", "webkit"))

def test_long_press_reaches_2x(session):
    session.goto(clock=True)          # True = 导航前装假时钟
    session.start_playback()          # 夹具视频真的在播放（currentTime 推进）
    session.clock.freeze()            # 冻结时刻：之后只能靠 advance 推进
    g = session.touch()
    g.down(*session.point_in_video(y_frac=0.3))   # 画面非控件区域
    session.clock.advance(499)
    assert session.read().playback_rate == 1.0    # 门槛前
    session.clock.advance(1)
    assert session.read().playback_rate == 2.0    # 门槛后
    g.up()
    assert session.read().playback_rate == 1.0    # 松手恢复
```

要点：`session.touch()` 返回的按压对象每次方法调用立即派发事件，时间由测试控制；`move_by` 是相对**按下点**、`move_to` 是绝对坐标；`session.enter_custom_fullscreen()` / `touch_in_custom_fullscreen()` 进自制横屏；`read()` 的 `custom_fullscreen`、`fs_button_visible` 可用来判可见 DOM；`gesture.evidence` 用来在票据里标注证据强度。

### 7. 未验证与受限之处（如实记录，不静默降级）

- **不构成 iOS Safari 结论**：全部证据来自桌面 headless chromium-1243 / webkit-2359。契约规定真机触摸与原生控件兼容性由票据 05 承载。
- **webkit 的按压序列是弱证据**：Playwright 的协议只对 touchscreen 暴露 `tap`，没有跨引擎的 touchMove 通道；WebKit 也没有 `new Touch()`，`new TouchEvent(type, {touches: []})` 会抛 `TypeError`，只能用 `document.createTouch` + `createTouchList` 合成。所以 webkit 上「按下→移动→抬起」证明的是**逻辑**，不是触摸平台行为；webkit 上可用的可信输入只有 `session.tap()`（真实 tap）。
- **chromium 丢弃首个小于 16 CSS 像素的 touchmove**（实测：4/8/12/14 像素未到达页面，16 像素到达；之后的较小位移正常到达）。⚠️ 对 03 有直接影响：用「按下后移动 12 CSS 像素」验证「未超过阈值不取消」在 chromium 上会**空洞通过**——移动根本没到达页面。03 必须换机制（例如先超过 slop 再回退、或改断言口径/换引擎）并重新实测，不能把「没观察到取消」当作通过。
- **画面点击行为因引擎而异**（写 P10 短按用例时要区分）：webkit 点击/tap 画面会切换播放/暂停（3/3 稳定）；chromium 的 touch tap 不切换，但鼠标点击画面会切换。
- **原生控件条位置随浏览器版本漂移**：`PLAY_CONTROL_OFFSETS` 只登记实测可稳定驱动的 chromium `(24, 40)`（相对 video 左下角）；webkit 控件条命中点不连续（x=60/90/100 命中、70/80 时通时不通），故不登记、改用画面 tap。翻不了时应当重新实测并更新该表，不要放宽判定。
- **夹具播放源**：线上是 HLS（`playlist.m3u8` + `.ts`），桌面上不一定能播，因此夹具只替换播放源（`data-playlist-url` 指向本地 mp4）；页面模板、`player.js`、`player.css` 都是真实的那一份。
- **本机安装事实**：浏览器二进制装在 `C:\Users\hugos\AppData\Local\ms-playwright`（chromium-1243 / webkit-2359 / ffmpeg-1011 / winldd-1007）。直连 Playwright CDN 在本机极慢，需要 `HTTPS_PROXY` 才能装；缺 playwright 或缺浏览器时整组测试自动跳过（§2 末行已验证）。

### 8. 验收项对照（逐条）

上文验收项勾选框按仓库惯例保留原样；逐条判定见下表（其中「自检一」在 webkit 上按本票允许的等效路径完成，原因见 §7）。

| 验收项 | 证据 | 判定 |
|---|---|---|
| 夹具渲染真实模板与真实静态资源，页面真的播放本地视频（`currentTime` 推进），两种观看模式都保持播放 | §1 第 1、2 条；用例 1 的逐字节比对；用例 2 两种模式下 `wait_for_progress` | **成立** |
| 能派发可控制时长与轨迹的按压，同一页面重复派发互不残留状态 | 用例 3（3 次按压：轻移抬起 / 移远取消 / 三次轨迹移动）+ 用例 4（自制全屏）；断言按压结束后 `paused is False`、`playbackRate == 1.0`、`custom_fullscreen` 不变 | **成立** |
| 500 毫秒门槛可在受控时间下确定性判定（两侧各一次），不需要真实等待 | 自检二用例：499 → `[]`、+1 → `["fired"]`、`Date.now()` 差恰为 500；机制与影响见 §4 | **成立** |
| 判定只读 Video 公开速度/播放状态与可见 DOM，不读实现私有变量/内部计时器/私有状态字段 | §5；全部断言只用 `read()` 的公开字段与 `hit_id` | **成立** |
| 自检一：真实输入点击原生控件播放按钮，播放状态翻转 | chromium：真实鼠标点原生控件条播放按钮，`paused` 来回各翻转一次（用例 5） | **成立（chromium）**；webkit 的原生控件条命中点不连续，改用票据允许的等效路径：真实触摸 tap 画面 → `paused` 翻转（来回各一次），依据与证据强度见 §3、§7 |
| 自检二：页面内 500 毫秒定时器在门槛两侧分别不触发与触发 | 见上 | **成立** |
| 明确记录证据强度：每条用例用可信真实输入还是合成事件；记录每个引擎上跑通的用例集合 | §3 表（逐用例 × 引擎 × 证据强度）+ 夹具事实采集输出 | **成立** |
| 缺少浏览器自动化设施时整组测试自动跳过；既有 `pytest` 全量套件保持通过 | §2 末两行：空 `PLAYWRIGHT_BROWSERS_PATH` → 14 skipped；全量 94 passed（既有 80 passed） | **成立** |
| 不实现、不预埋任何长按临时倍速行为；不新增页面可见内容；不改动播放器既有行为 | 交付物只有测试范围内的两个文件与 `requirements-dev.txt`；`git status` 中 `server/` 无改动；页面 HTML 与真实模板逐字符相等；夹具不注入业务逻辑（§5） | **成立** |
| 不为测试新增对外接口、配置项或持久化格式；夹具只存在于测试范围内 | 无应用侧改动；夹具仅在 `tests/`；播放位置记忆用的是页面原有 `localStorage` 键（每个会话独立的 video_id，互不干扰） | **成立** |

### 9. 声明

本票**没有实现、也没有预埋任何「长按临时倍速」行为**：本票的交付物只落在测试范围内（两个测试文件 + `requirements-dev.txt` + 票据记录），本票自身没有改动 `server/` 下任何文件，页面由真实模板渲染、播放源换成夹具视频是本票唯一的页面差异——即本票改动前后，页面可见行为一致。

注意工作区现状：`server/static/player.js` 目前带有**票据 02** 的长按临时倍速实现（不是本票的改动，本票交付时它还不存在）。本票的自检用例在该实现存在的情况下依然全绿（自检只断言与功能无关的输入/观察链）。骨架跑通不等于 P1–P10 任何一条被验收；真机覆盖仍由 05 承载。

### 10. 复审后修复（02 票据发现的两个夹具缺陷 + 一处夹具竞态）

02（T02CoreHold）在同一个夹具上写用例时发现并报告了两个夹具缺陷。两个都已复现、定位、修复，并补了**送达自检**用例——它们原本会让骨架「绿得毫无意义」（断言只查夹具侧账本），所以属于本票的必补项。

| # | 缺陷 | 复现证据 | 修复 | 回归覆盖 |
|---|---|---|---|---|
| 1 | webkit 合成触摸把 CDP 事件名（`touchStart`）直接当 DOM 事件名用，页面的 `touchstart`/`touchend` 监听器**收不到任何事件** | 页面内观察器在旧代码下看到 `page saw []`（大写名对照实验同样为 `[]`） | `arg.type.toLowerCase()`（02 已改，保留） | `test_press_sequence_reaches_the_page_as_dom_touch_events`（断言事件名是小写 DOM 名） |
| 2 | 合成触摸坐标**全是 `0,0`**：`_dispatch` 把 `point` 以数组传进 `evaluate`，JS 里 `point.x` 取到 `undefined`；`anchor` 是对象所以 `touchend` 坐标正常，掩盖了问题 | 观察器：旧 `[["touchstart", false, 1, "0,0"], …]` → 新 `[["touchstart", false, 1, "400,176"], …]` | 新增 `_as_js_point()`，`_dispatch` 统一传 `{x, y}` | 同上（坐标断言容差 0.05；坐标丢失或错位必失败） |
| 3 | `ControlledClock.freeze()` 竞态：读到 `Date.now()` 与 `pause_at()` 之间有 1~2ms 真实时间窗口，偶发 `Clock.pause_at: Error: Cannot fast-forward to the past` | 合并跑两个测试模块的 53 个用例时命中 2 次（`test_release_before_500ms_leaves_rate_untouched[fullscreen-webkit]`、`test_rate_stays_at_2x_for_as_long_as_the_press_continues[inline-webkit]`） | 先按读到的时刻试一次，撞上「过去」就带 +50ms 余量重试（`freeze()` 幂等，且此时还没有功能计时器） | `test_selfcheck_2…`、`test_custom_fullscreen_entry_and_exit_work_under_controlled_time`（都要先 freeze） |

缺陷 1、2 的**敏感性证明**（不改仓库文件，直接重放旧 payload / 旧 JS）：

```
point 传数组（旧 bug）: page saw [["touchstart", false, 1, "0,0"]]
point 传 {x,y}（已修）: page saw [["touchstart", false, 1, "400,200"]]
事件名 touchStart（旧 bug）: page saw []
事件名 touchstart（已修）: page saw [["touchstart", false, 1, "400,200"]]
```

附带修正：

- **抬起/取消的触点位置改为最后位置**：原来传按下点，与 CDP（`touchend` 带回最后位置）不一致；现在两个引擎都带回最后位置（`test_press_sequence_reaches_the_page_as_dom_touch_events` 断言 30px 位移后抬起仍在 `+30`）。
- **夹具视频 8 秒 → 30 秒**：媒体位置由真实时间推进，用例跑久了会把短片播到结尾，Chromium 到片尾会 `paused=true` / `ended=true`，凭空打断后续断言。02 报告过一次**瞬时**跳变（`g.up()` 前后两次 evaluate 之间、真实约 10ms、假时钟未动，`currentTime` 从 0.75 变 8.00，`paused=false`/`ended=false`），发生在他们刚做过 4 次 `advance(2001)` 的同一会话里。本机补测**未复现**（30 轮 `advance(500)` → +0.01~0.04；`advance(600000)`、`fast_forward("30:00")` 都不推进媒体位置；真实 0.5s 稳定 +0.50）。`8.00` 恰好等于原来的片长，但 02 明确说明那是同一次读取窗口内的瞬时值而不是跨轮累积——**成因未定位，如实记为夹具保真度限制**；片长改 30 秒、加上 02 测试侧 `loop=true`（与本功能无关的公开 API 夹具设置）后，双方都没再遇到。
- **会话级夹具跨测试模块共享**：本模块的夹具被测试模块显式 import，而 pytest 对「从别的模块导入的夹具」**按测试模块各实例化一次**，于是第二个用到夹具的测试模块会再进一次 `sync_playwright()` 并抛 `It looks like you are using Playwright Sync API inside the asyncio loop.`（实测：合并跑两个模块 → 35 passed + **18 errors**，全在 `test_player_harness.py`）。现在真正的会话状态放在进程级注册表（`_SESSION_PLAYWRIGHT` / `_SESSION_HARNESSES` / `_SESSION_FAILURES`），第二个实例复用第一份；teardown 统一由创建者负责（**先关夹具再关 Playwright**，避免「先关连接再关夹具」的顺序问题），`PlayerHarness.stop()` 幂等。
- **受控时间对媒体时钟的影响（补测，回答 02 的疑问）**：连续 30 轮 `advance(500)`（假时间共 +15s）后 `currentTime` 只动 +0.01~0.04；单次 `advance(600000)`（10 分钟）与 `fast_forward("30:00")` 同样不推进媒体位置；真实 0.5s 则稳定推进 +0.50，`ended=false`。即在夹具的用法下**没有**观察到「假时钟让媒体位置虚增」。§4 的结论保持不变。
- **合成触摸绕开 UA 控件条（02 实测，记录为机制保真度限制）**：chromium 上用可信触摸按在原生控件条播放按钮的位置时，控件条自己吞掉事件并暂停视频，页面根本收不到触摸；webkit 的合成按压落在同一个点却会到达页面（合成事件直接派给 `elementFromPoint`＝video，绕开 UA 控件条）。所以「按压落在控件条上不触发手势」这类用例在 webkit 上只能用来说明**逻辑**，不能说明真机行为。

修复后实测：

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/test_player_harness.py -q` | **18 passed**（新增 2 个送达自检 × 2 引擎） |
| `python -m pytest tests/test_hold_speed_core.py tests/test_player_harness.py -q`（两个模块共用夹具，连跑 2 次） | **53 passed, 1 skipped**（修复前：35 passed + 18 errors） |
| `python -m pytest -q`（全量，含 02 的 36 项） | 见 §11 |

### 11. 全量套件与既有失败（如实记录）

此时仓库里已有 02 的 `tests/test_hold_speed_core.py`（36 项）与 02 的 `server/static/player.js` 行为改动。

| 命令 | 结果 |
|---|---|
| `python -m pytest -q`（全量，第 1 次） | `1 failed, 132 passed, 1 skipped`；失败项 **`tests/test_delete_job.py::test_delete_failed_job`**（`assert not WindowsPath('…/jobs/4e3c4270eabe').exists()`） |
| `python -m pytest -q`（全量，第 2 次） | `1 failed, 132 passed, 1 skipped`；失败项换成 **`tests/test_retry_job.py::test_retry_validation_progress_broken`**（`want 400 got 200`） |
| `python -m pytest tests/test_delete_job.py -q` | `4 passed`（连跑 2 次） |
| `python -m pytest tests/test_retry_job.py -q` | `5 passed` |
| `python -m pytest -q --ignore=tests/test_player_harness.py --ignore=tests/test_hold_speed_core.py` | `80 passed in 16.32s`（既有套件单独跑，连跑多次都通过） |

结论：这两个失败都是**与本票改动无关的既有间歇性失败**——它们各自单独跑都通过，既有套件（去掉两个浏览器测试文件）稳定通过，两次全量失败还是**不同的**用例。不能排除是全量并发/时序下暴露的既有隐患（本票新增的浏览器用例给整套加了约 30s 负载）。按票据约定「与本票改动无关的既有失败如实记录、不顺手修」，**本票未修**这两处。

本票交付前（当时仓库还没有 02 的文件）的全量记录：`94 passed in 27.69s`（既有 80 + 本票 14），复跑一致；`--ignore=tests/test_player_harness.py` 为 `80 passed`。

### 12. 关闭后的附加扩展与复核（票据 03 追加）

本票关闭后，`tests/player_harness.py` 又被票据 03 做了**纯增量**扩展（公共助手签名与默认路径不变）：`TouchEventRecord.trusted`（逐事件信任）、`TouchGesture.move_exact_to` / `move_exact_by`（始终走页面内合成，记录页面回读坐标）、`GestureEvidence.synthetic_events` 与「全部事件可信才为 True」的 `trusted_input`、`PlayerHarness.session(device_scale_factor=…)`。03 同时修掉一处自己引入的标注错误：`up()`/`cancel()` 原来按默认值记成可信，会把 webkit 的全合成序列误标成「混合」。

由夹具原负责人复核（交付后）：

| 检查 | 结果 |
|---|---|
| `python -m pytest tests/test_player_harness.py -q` | **18 passed**（送达自检与逐事件证据断言在扩展后仍然成立） |
| `python -m pytest tests/test_hold_speed_slide.py tests/test_hold_speed_core.py tests/test_player_harness.py -q` | **99 passed, 5 skipped** |
| 逐事件信任标注 | chromium 默认路径 → 全部可信（`trusted_input=True`）；webkit → 全部合成（`trusted_input=False`，strength=合成事件（弱证据））；chromium 用 `move_exact_*` → 3 个事件里 1 个合成，`trusted_input=False`，strength=混合 |
| `move_exact_by(0, 12)` 的页面送达 | chromium：页面收到 `touchmove` @ 按下点 +12px、`isTrusted=false`；坐标 payload 走的仍是 `_as_js_point`，没有回退到 0,0 |

复核时发现的引擎差异（记给 03/04）：chromium 上混用「CDP 按下 + 合成移动」时，**合成移动不会更新浏览器内部的触点状态**，所以随后的 CDP `touchend` 带回的是 CDP 侧记下的最后位置（即按下点），而合成 `touchmove` 的坐标照常送达页面。实测：按下 y=175.5 → 合成移动 12px 页面收到 188 → CDP `touchend` 仍报 176。用精确通道测「移动期间的位移判定」不受影响；但若要断言**松手时**的坐标，必须注意这条差异。

03 随后把这条写进了模块 docstring 的「实测边界」（`抬起的触点是最后位置` 那一条加上了「混用通道时以各通道自己记的位置为准」的限定），并注明来源是本票的复核；本票复核后 `python -m pytest tests/test_player_harness.py -q` 仍为 **18 passed**。

