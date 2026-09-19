# 06: 真实模型语义质量评估（含 203–205 样例）

**Delivers:** P3
**Test owner for:** P3
**Blocked by:** 02 — 中文空缺触发、按 Translation Chunk 集中请求与只改中文的局部修复；03 — 句组内英文切分点移动与组内时间估算
**Status:** ready-for-agent

**Parent:** `.scratch/subtitle-local-alignment-repair/contract.md`

## What to build

本票是覆盖 P3 的语义质量切片，持有 P3 的评估，用**真实模型 adapter** 运行修复，评估中文是否按英文意义出现顺序展示，而不是用结构合法或非空行数证明质量。

评估样本：

- spec 的代表样例——视频 How Games Use Feedback Loops 中字幕编号 203–205 的句组：`This is one reason why it was so important for Capcom to never` / `reveal that Resident Evil 4 was` / `using a dynamic difficulty system.`，当前整句中文集中在 203。评估合理修复后的完整句组；把 `never` 移到下一块与 `reveal` 连排是允许的候选，不是必须的唯一答案。
- 一个只需改中文、无需移动英文切分的样例。

评估口径：意义对应、信息与术语保全（如专有名词不缺失、不混用）、接受翻译腔与未完短语、允许部分改善后仍存在中文空白。评估结果要记录改进与未改进的句组，且明确说明一次真实模型运行不能保证未来输入的语义正确，也不能把「中文空白变少」当作唯一质量证据。

本票需要真实模型凭据与 API 调用，属本功能唯一的真实模型评估活动。

## 前置条件与证据载体

- 需要可用的 `deepseek_api_key`（`config.json`）或 `DEEPSEEK_API_KEY`，以及到模型服务的网络访问。凭据或网络不可用时按阻塞上报，**不**用 mock 候选的评估结果替代真实模型语义质量证据（C3.3）。
- 证据写入本 ticket 文件底部的 `## Comments`：样本输入句组、模型候选、最终逐块字幕（中英对照与时间码），供人工复核。
- 真实模型输出具有不确定性，一次运行不构成对未来输入的保证；若多次运行均无实质改善，如实记录为评估结论上报，不伪造通过。
- 评估不固化为 CI 测试（不确定性 + 依赖凭据），也不主动改写用户已有的字幕产物。

## Acceptance criteria

- [ ] 用真实模型 adapter 处理 203–205 所属句组后，核对实际产物：中文意义不再整体集中在首块（或按英文意义出现顺序重新分配），逐块的中英对应可人工判读（C3.2）。
- [ ] 是否移动 `never` 不作为通过条件；移动与不移动两种合理结果都可接受（C3.2）。
- [ ] 只需改中文的样例在英文与时间码不变的前提下得到逐块对应改善（C3.2）。
- [ ] 事先评定的样例核对：完整意思与术语、专有名词不缺失、不重复、不新增事实；翻译腔与未完短语不计为缺陷（C3.1）。
- [ ] 评估报告只以实际字幕内容的语义对应作为证据；不以非空行数增加、mock 返回值或结构校验通过作为质量证明（C3.3）。
- [ ] 报告明确记录仍然保留中文空白的句组（部分改善被接受），以及本次评估不构成对未来输入的保证（C3.1、C3.3）。
- [ ] 评估在临时目录或可回滚的工作目录中进行，不主动改写用户已有的字幕产物。

## Coverage partition

| Coverage item | Ticket | Test owner |
|---|---|---|
| C3.1 事先评定样例的意义对应、信息保全与术语完整 | 06 | 06 |
| C3.2 真实模型评估（203–205 与只需改中文样例） | 06 | 06 |
| C3.3 不以非空行数／mock／结构合法代替语义质量证明 | 06 | 06 |

## Comments

### 1. 前置条件核对（真实凭据 + 网络访问）

| 检查 | 依据 | 结果 |
|---|---|---|
| 凭据存在（`config.json`） | `jq -r '.deepseek_api_key' config.json`（只打印存在性与长度，**未输出明文**） | 存在，长度 35 |
| 环境变量 | `printenv DEEPSEEK_API_KEY \| wc -c` | `0`（未设置，走 `config.json`） |
| 模型服务连通 | 用同一 key 向 `https://api.deepseek.com` 发一条最小真实请求（`openai` 2.46.0，`model=deepseek-v4-flash-vision-exp`，`max_tokens=8`，thinking disabled） | 返回 `"Pong! 🏓 I'm"`，`usage.total_tokens=13` |

凭据与网络均可用，本票**未进入阻塞**。

### 2. 证据是怎么产生的（真实路径，无 mock 候选）

- 运行形态：真实字幕 → 真实 Translation Chunk 产物 → `worker/scripts/batch_translate.py`（正常路径，`--no-extract --skip-translate`，复用真实 chunk 产物）→ S1 `generate_bilingual_srt(...)` → 真实 `worker._do_finalize`（跑 `finalize-subtitles.ps1`）→ 读取**视频目录里的最终 Bilingual SRT**。下面的判定都基于这个文件的逐块内容，不是中间 `_chinese.txt`，也不是内存对象。
- 交给修复的 `model_call` 就是真实 adapter `worker.translate.repair_alignment_call`（即 `call_skill(skill_name="srt-alignment-repair")` 本身）。请求与回答原样记录在下面的 JSON 与本节中：**没有任何 mock 候选参与语义判断**。
- 每次运行都在系统临时目录里新建自己的 Job 与视频目录。用户 `video_dir`（`E:/Developer/youtube-playground/new-streaaming`）在评估期间没有任何文件变动：`find <dir> -newermt "2026-09-20 04:40"` 无输出；源 `.en.srt` 只读打开。
- 一次性评估脚本：`temp/eval_06_real_model.py`（`.gitignore` 下的 `temp/`，不进入 CI、不注册为测试）。原始记录：`temp/eval_06_runs/*.json`（请求原文、模型原始回答、逐块产物）与对应 `*.log`。
- 判据只看实际字幕内容的语义对应：下面每条结论都是把某一块的英文与同块中文对照读出来的。**非空行数增加、结构校验通过、mock 返回值都不作为质量证据**（C3.3）。

### 3. 样例一：spec 代表样例 —— 真实字幕 203–205（《How Games Use Feedback Loops》）

输入是用户已有的真实产物全量 318 块（203 承载整句中文，204/205 空）；块划分与 Translation Chunk 划分由脚本自身规则产生（12 个 chunk，只有含该句组的那 1 个 chunk 受影响 → 1 次真实请求）。

*真实请求（12.6s）*

```
【组1】
英文：
[1] This is one reason why it was so important for Capcom to never
[2] reveal that Resident Evil 4 was
[3] using a dynamic difficulty system.
中文：
[1] 这就是为什么卡普空绝口不提《生化危机4》使用了动态难度系统的原因之一。
[2] （空）
[3] （空）

```

*模型原始回答*

```
【组1】
中文：
[1] 这就是为什么卡普空绝口不提《生化危机4》
[2] 使用了动态难度系统的原因之一。
[3] （空）
```

最终产物（视频目录里的 `How Games Use Feedback Loops.en.srt`，逐块中英对照与时间码）：


**判定：部分改善（被接受）**——中文不再整体集中在首块（204 现在有中文）；`never` 未移动，属允许结果；205 仍留空（C8.1 允许部分改善）。术语与专名保全：卡普空、《生化危机4》、动态难度系统各出现一次，无缺失、无重复、无新增事实。逐块对应仍有一行偏移：《生化危机4》 在 203，而 *Resident Evil 4* 在 204；「原因之一」在 204，而 *This is one reason why* 在 203。

| 块 | 英文 | 输入中文 | 输出中文 | 时间码 |
|---|---|---|---|---|
| 203 | This is one reason why it was so important for Capcom to never | 这就是为什么卡普空绝口不提《生化危机4》使用了动态难度系统的原因之一。 | 这就是为什么卡普空绝口不提《生化危机4》 | 不变 |
| 204 | reveal that Resident Evil 4 was | （空） | 使用了动态难度系统的原因之一。 | 不变 |
| 205 | using a dynamic difficulty system. | （空） | （空） | 不变 |

重复运行 3 次（`feedback203.json`、`feedback203_r2.json`、`feedback203_r3.json`）得到**完全相同**的产物：203 得「这就是为什么卡普空绝口不提《生化危机4》」、204 得「使用了动态难度系统的原因之一。」、205 保持空；三次运行的英文与时间码都逐块不变。

### 4. 样例二：只需改中文的样例（英文与时间码必须不变）

**4.1 受控样例**：真实英文字幕与真实时间码（取自真实块 206–209 这一按逗号切分的句组），把该组 4 行真实中文合并到第 1 行，复现 spec 描述的「整句中文集中在一块」状态；为了只考察一个句组，构造时移除了真实块 203–205。共 315 块、12 个 chunk，只有 1 个 chunk 受影响 → 1 次真实请求。

*真实请求（6.8s）*

```
【组1】
英文：
[1] Ultimately, a negative feedback loop telling players that
[2] the game will get harder if they're doing well,
[3] or easier if they're struggling,
[4] is effectively telling good players to make mistakes.
中文：
[1] 归根结底，负反馈循环告诉玩家：如果表现好，游戏就会变难；如果挣扎，游戏就会变简单；这实际上是在让高手们去犯错。
[2] （空）
[3] （空）
[4] （空）

```

*模型原始回答*

```
【组1】
中文：
[1] 归根结底，负反馈循环告诉玩家：
[2] 如果表现好，游戏就会变难；
[3] 如果挣扎，游戏就会变简单；
[4] 这实际上是在让高手们去犯错。
```

最终产物（视频目录里的 `Feedback Loops Controlled.en.srt`）：


**判定：完全改善**——4 行各自得到与同行英文对应的中文；**英文逐块不变、时间码逐块不变**（`english_or_time_changes` 为空，全篇英文单词序列与输入完全一致）。

| 块 | 英文 | 输入中文 | 输出中文 | 时间码 |
|---|---|---|---|---|
| 203 | Ultimately, a negative feedback loop telling players that | 归根结底，负反馈循环告诉玩家：如果表现好，游戏就会变难；如果挣扎，游戏就会变简单；这实际上是在让高手们去犯错。 | 归根结底，负反馈循环告诉玩家： | 不变 |
| 204 | the game will get harder if they're doing well, | （空） | 如果表现好，游戏就会变难； | 不变 |
| 205 | or easier if they're struggling, | （空） | 如果挣扎，游戏就会变简单； | 不变 |
| 206 | is effectively telling good players to make mistakes. | （空） | 这实际上是在让高手们去犯错。 | 不变 |

**4.2 真实产物里本来就存在的「中文空缺」句组**（《Can we Improve Tutorials for Complex Games？》全量 502 块，19 个 chunk，其中 9 个 chunk 受影响 → 9 次真实请求）：

运行记录：`tutorials.json` —— 502 块，9 次真实请求，205.2s，finished=True；英文或时间码发生变化的块：12

全篇校验：英文单词序列与输入完全一致（含标点；`' '.join(english).split()` 逐 token 相同）；时间码只在 3 个移动了切分点的句组里由程序重算，其余块逐块不变。

模型原始回答：

*请求 1 的原始回答（2.9s），对应下表第 1 个句组*

```
【组1】
中文：
[1] 一个实际例子就是
[2] 出色的城市建造游戏《冰汽时代》。
```

*请求 2 的原始回答（24.5s），对应下表第 2 个句组*

```
【组1】
英文：
[1] You can always come back and learn more
[2] when you feel ready to go to the next level
[3] and take your game from
[4] button mashing to beaver whacking.
中文：
[1] 你随时可以回来继续学，
[2] 等你准备好进入下一水平时，
[3] 把水平从
[4] 乱按提升到海狸敲击。
```

*请求 3 的原始回答（5.7s），对应下表第 3 个句组*

```
【组1】
中文：
[1] 我也觉得这些很好上手，
[2] 因为我已经从《文明5》掌握了基础；
[3] 只需要搞懂新东西，
[4] 所以这些资料片基本创造了
[5] 我一直在说的复杂度逐步提升，
[6] 只不过教程之间隔着完整的战役。
```

*请求 4 的原始回答（4.8s），对应下表第 4 个句组*

```
【组1】
中文：
[1] 在动作游戏里犯错，
[2] 你马上就会发现，但在策略游戏里，
[3] 如果你经济没平衡好，
[4] 可能好几个小时都察觉不到。
```

*请求 5 的原始回答（40.4s），对应下表第 5 个句组*

```
【组1】
英文：
[1] People can bring their own knowledge of history
[2] to make assumptions about how things will work most of the time,
[3] but perhaps the best place
[4] for complex games to look for real-world inspiration
[5] is in the user interfaces we encounter every day.
中文：
[1] 人们可以靠历史知识
[2] 来推测大多数情况下事情会怎么运作，
[3] 但最好的地方
[4] 对复杂游戏来说，寻找现实灵感
[5] 就是我们每天都会遇到的用户界面。
```

*请求 6 的原始回答（9.6s），对应下表第 6 个句组*

```
【组1】
中文：
[1] 叫做“throbber”，而且很抱歉，由我来
[2] 还得告诉你这件事。
```

*请求 7 的原始回答（35.5s），对应下表第 7 个句组*

```
【组1】
英文：
[1] Now, sure, text is almost always necessary
[2] in the tutorial for a complex game,
[3] but designers should try to cut down words,
[4] be consistent with language, avoid jargon,
[5] and maybe this is just a personal preference,
[6] but I really don't like this thing where
[7] some pointless flavor text is spoken by a voice actor,
[8] but the actual important stuff is left unsaid.
中文：
[1] 当然，文字几乎总是需要的
[2] 在复杂游戏的教程里，
[3] 但设计师应该尽量精简用词，
[4] 语言保持前后一致，避免术语，
[5] 也许这只是我个人的偏好，
[6] 但我真的很不喜欢这种处理方式：
[7] 无关紧要的装饰性文案让配音演员念出来，
[8] 真正重要的内容却只字不提。
```

*请求 8 的原始回答（31.0s），对应下表第 8 个句组*

```
【组1】
中文：
[1] 我不否认，一些观点
[2] 在这期视频中受到
[3] 我自身情况的影响：我非常
[4] 偏重动觉和视觉学习，
[5] 而且注意力时长跟六岁小孩差不多。
```

*请求 9 的原始回答（50.5s），对应下表第 9 个句组*

```
【组1】
中文：
[1] 所以在这期视频里，
[2] 我总结了一些技巧，我觉得能让
[3] 教程变得更好：想办法把教程拆开，
[4] 分布在一个战役里，或者分散到
[5] （空）
[6] 多个战役中；想办法让玩家……
```

逐块对照与判定：


组 106–107：只改中文（英文与时间码逐块不变）。**完全改善**：《冰汽时代》落到与 *Frostpunk.* 同块。

| 块 | 英文 | 输入中文 | 输出中文 | 时间码 |
|---|---|---|---|---|
| 106 | An example of this in practice is the | 一个实际例子就是出色的城市建造游戏《冰汽时代》。 | 一个实际例子就是 | 不变 |
| 107 | outstanding city builder Frostpunk. | （空） | 出色的城市建造游戏《冰汽时代》。 | 不变 |

组 172–175：移动了切分点（`more` 上移、`and` 下移），中文按新切分逐块落位，时间码由程序按修复前字符位置估算，英文单词序列不变。**完全改善**。

| 块 | 英文 | 输入中文 | 输出中文 | 时间码 |
|---|---|---|---|---|
| 172 | You can always come back and learn | 你随时可以回来继续学， | 你随时可以回来继续学， | 00:07:14,880 --> 00:07:16,413 → **00:07:14,880 --> 00:07:16,657** |
| 173 | more when you feel ready to go to the | 等你准备好进入下一水平， | 等你准备好进入下一水平时， | 00:07:16,458 --> 00:07:18,300 → **00:07:16,706 --> 00:07:18,849** |
| 174 | next level and take your game from | 把水平从乱按提升到海狸敲击时。 | 把水平从 | 00:07:18,352 --> 00:07:20,042 → **00:07:18,898 --> 00:07:20,042** |
| 175 | button mashing to beaver whacking. | （空） | 乱按提升到海狸敲击。 | 不变 |

组 199–204：只改中文（英文与时间码逐块不变）。**完全改善**：原挤在 202 的「我一直在说的复杂度逐步提升」落到 203。

| 块 | 英文 | 输入中文 | 输出中文 | 时间码 |
|---|---|---|---|---|
| 199 | And I found those pretty easy to learn as well | 我也觉得这些很好上手， | 我也觉得这些很好上手， | 不变 |
| 200 | because I already knew the basics from Civ 5; | 因为我已经从《文明5》掌握了基础； | 因为我已经从《文明5》掌握了基础； | 不变 |
| 201 | I just needed to figure out the new stuff, | 只需要搞懂新东西， | 只需要搞懂新东西， | 不变 |
| 202 | and so these expansion packs essentially created that | 所以这些资料片基本创造了我一直在说的复杂度逐步提升， | 所以这些资料片基本创造了 | 不变 |
| 203 | gradual increase of complexity I've been talking about, | （空） | 我一直在说的复杂度逐步提升， | 不变 |
| 204 | just with entire campaigns in between the tutorials. | 只不过教程之间隔着完整的战役。 | 只不过教程之间隔着完整的战役。 | 不变 |

组 310–313：只改中文（英文与时间码逐块不变）。**完全改善**：原挤在 311 的「你马上就会发现，但在策略游戏里，」拆到 310/311。

| 块 | 英文 | 输入中文 | 输出中文 | 时间码 |
|---|---|---|---|---|
| 310 | Now, if you make a mistake in an action game, | 在动作游戏里犯错，你马上就会发现， | 在动作游戏里犯错， | 不变 |
| 311 | you'll see that immediately, but in a strategy game, | 但在策略游戏里，如果你经济没平衡好， | 你马上就会发现，但在策略游戏里， | 不变 |
| 312 | if you don't balance your economy correctly, | （空） | 如果你经济没平衡好， | 不变 |
| 313 | you might not realize for many hours. | 可能好几个小时都察觉不到。 | 可能好几个小时都察觉不到。 | 不变 |

组 368–372：移动了切分点（`to make` 与 `for` 下移），中文逐块落位，时间码为程序估算。**完全改善**。（注意：源翻译本身漏译 `perhaps`「也许」，修复只重排已有中文，既不新增也不丢失信息。）

| 块 | 英文 | 输入中文 | 输出中文 | 时间码 |
|---|---|---|---|---|
| 368 | People can bring their own knowledge of history to make | 人们可以靠历史知识来推测大多数情况下事情会怎么运作， | 人们可以靠历史知识 | 00:15:23,279 --> 00:15:25,883 → **00:15:23,279 --> 00:15:25,504** |
| 369 | assumptions about how things will work most of the time, | （空） | 来推测大多数情况下事情会怎么运作， | 00:15:25,939 --> 00:15:29,911 → **00:15:25,551 --> 00:15:29,911** |
| 370 | but perhaps the best place for | 但对复杂游戏来说，寻找现实灵感最好的地方，就是我们每天都会遇到的用户界面。 | 但最好的地方 | 00:15:29,993 --> 00:15:31,696 → **00:15:29,993 --> 00:15:31,468** |
| 371 | complex games to look for real-world | （空） | 对复杂游戏来说，寻找现实灵感 | 00:15:31,752 --> 00:15:33,929 → **00:15:31,525 --> 00:15:34,688** |
| 372 | inspiration is in the user interfaces we encounter every day. | （空） | 就是我们每天都会遇到的用户界面。 | 00:15:33,995 --> 00:15:37,839 → **00:15:34,751 --> 00:15:37,839** |

组 406–407：只改中文（英文与时间码逐块不变）。**改善**：「还得告诉你这件事」落到 407，但「由我来 / 还得告诉你这件事」语序生硬——属翻译腔，按 C3.1 不计为缺陷。

| 块 | 英文 | 输入中文 | 输出中文 | 时间码 |
|---|---|---|---|---|
| 406 | Called throbbers, and I'm really sorry to be the one | 叫做“throbber”，而且很抱歉，还得由我来告诉你这件事。 | 叫做“throbber”，而且很抱歉，由我来 | 不变 |
| 407 | to have to tell you this anyway. | （空） | 还得告诉你这件事。 | 不变 |

组 424–431：移动了切分点（`in` 与 `some` 下移），中文逐块落位，时间码为程序估算。**完全改善**。

| 块 | 英文 | 输入中文 | 输出中文 | 时间码 |
|---|---|---|---|---|
| 424 | Now, sure, text is almost always necessary in | 当然，复杂游戏的教程里几乎总是需要文字， | 当然，文字几乎总是需要的 | 00:17:55,760 --> 00:17:58,437 → **00:17:55,760 --> 00:17:58,258** |
| 425 | the tutorial for a complex game, | 但设计师应该尽量精简用词， | 在复杂游戏的教程里， | 00:17:58,496 --> 00:18:00,188 → **00:17:58,318 --> 00:18:00,188** |
| 426 | but designers should try to cut down words, | 语言保持前后一致，避免术语， | 但设计师应该尽量精简用词， | 不变 |
| 427 | be consistent with language, avoid jargon, | 也许这只是我个人的偏好， | 语言保持前后一致，避免术语， | 不变 |
| 428 | and maybe this is just a personal preference, | 但我真的很不喜欢这种处理方式： | 也许这只是我个人的偏好， | 不变 |
| 429 | but I really don't like this thing where some | 无关紧要的装饰性文案让配音演员念出来， | 但我真的很不喜欢这种处理方式： | 00:18:08,528 --> 00:18:10,669 → **00:18:08,528 --> 00:18:10,431** |
| 430 | pointless flavor text is spoken by a voice actor, | 真正重要的内容却只字不提。 | 无关紧要的装饰性文案让配音演员念出来， | 00:18:10,725 --> 00:18:13,520 → **00:18:10,478 --> 00:18:13,520** |
| 431 | but the actual important stuff is left unsaid. | （空） | 真正重要的内容却只字不提。 | 不变 |

组 446–450：只改中文（英文与时间码逐块不变）。**完全改善**：5 行全部有与同行英文对应的中文，「在这期视频中受到」对上 *in this video are biased by the*。

| 块 | 英文 | 输入中文 | 输出中文 | 时间码 |
|---|---|---|---|---|
| 446 | I won't deny that some of the ideas | 我不否认，这期视频中的一些观点 | 我不否认，一些观点 | 不变 |
| 447 | in this video are biased by the | 受我自身情况的影响： | 在这期视频中受到 | 不变 |
| 448 | fact that I am personally a very | 我非常偏重动觉和视觉学习， | 我自身情况的影响：我非常 | 不变 |
| 449 | kinesthetic and visual learner, | 而且注意力时长跟六岁小孩差不多。 | 偏重动觉和视觉学习， | 不变 |
| 450 | and I have the attention span of a six-year-old child. | （空） | 而且注意力时长跟六岁小孩差不多。 | 不变 |

组 472–477：只改中文（英文与时间码逐块不变）。**部分改善**：中文从左移到 473/474/475/477，但第 5 行（476）仍留空——该组英文本身重复残缺（*multiple across a campaign or across*），中文素材不足以逐行铺满。

| 块 | 英文 | 输入中文 | 输出中文 | 时间码 |
|---|---|---|---|---|
| 472 | So in this video, | 所以在这期视频里， | 所以在这期视频里， | 不变 |
| 473 | I've identified some techniques that I think could make | 我总结了一些我觉得能让教程变得更好的技巧： | 我总结了一些技巧，我觉得能让 | 不变 |
| 474 | tutorials better: finding ways to break the tutorial up either | 想办法把教程拆开，分布在一个战役里， | 教程变得更好：想办法把教程拆开， | 不变 |
| 475 | across a campaign or across | 或者分散到多个战役中；想办法让玩家…… | 分布在一个战役里，或者分散到 | 不变 |
| 476 | multiple across a campaign or across | （空） | （空） | 不变 |
| 477 | multiple campaigns finding ways to have the player get... | （空） | 多个战役中；想办法让玩家…… | 不变 |

### 5. 仍然保留中文空缺的句组（如实记录）

| 句组 | 输入空缺 | 输出空缺 | 说明 |
|---|---|---|---|
| 真实 203–205（spec 样例，3 次运行一致） | 204、205 空 | 205 仍空 | 部分改善被接受（C8.1）；逐块对应存在一行偏移 |
| 教程产物 472–477 | 476、477 空 | 476 仍空 | 部分改善被接受；该组英文自身重复残缺，中文素材不足以铺满 |

其余 8 个真实句组与受控样例的输出**没有**留下中文空缺。

### 6. 本次评估的边界（不构成保证）

- 这是本功能**唯一**的真实模型评估活动：spec 样例 3 次运行、受控样例 1 次、真实产物 9 个句组 1 次。模型输出具有不确定性——**一次真实运行不能保证未来输入的语义正确**，也不证明程序能识别所有语义错误：程序只校验结构不变量（英文单词保全、块数、句组归属、时间合法性），语义由模型给出。
- 「中文空白变少」本身**不是**质量证据。本次结论来自逐块中英对照判读（见上表），而非空白行数或非空行数：只看空白行数，样例一（205 仍空）与 4.1（完全铺满）会被错误地等同看待。
- 本次评估未改写用户已有字幕产物：全部运行在临时目录完成，用户 `video_dir` 无文件变动。
- 本评估不固化为 CI 测试（不确定 + 依赖凭据）。

### 7. 验收项对照（逐条）

| 验收项 | 证据 | 判定 |
|---|---|---|
| 真实模型 adapter 处理 203–205 后，中文不再整体集中在首块、逐块中英对应可判读（C3.2） | §3 表：204 由空变有；`feedback203*.json` 三次运行 | **成立**（并如实记录一行偏移） |
| 是否移动 `never` 不作为通过条件（C3.2） | §3：三次运行均未移动 `never`，仍判为可接受的改善 | **成立** |
| 只需改中文的样例在英文与时间码不变前提下逐块改善（C3.2） | §4.1 受控样例：`english_or_time_changes` 为空；§4.2 中 6 个句组（106–107、199–204、310–313、406–407、446–450、472–477）逐块时间码「不变」 | **成立** |
| 事先评定样例的意义对应、信息与术语保全、专名不缺失/不混用/不新增事实；翻译腔与未完短语不计缺陷（C3.1） | §3、§4 每组的判定；术语逐个核对（卡普空、《生化危机4》、动态难度系统、《文明5》、《冰汽时代》、《王权》等） | **成立**（「把水平从」「由我来」等未完/生硬片段按口径不计缺陷；源翻译自身漏译的 `perhaps` 已注明属上游遗漏） |
| 只以实际字幕内容的语义对应为证据，不用非空行数／mock／结构校验证明质量（C3.3） | §2 末条、§6 第二条；本票全部判定都来自逐块中英对照 | **成立**（零 mock 候选；样例一保留空行仍被判为「部分改善」，正是结构/行数口径无法给出的结论） |
| 明确记录仍保留中文空白的句组与「不构成对未来输入的保证」（C3.1、C3.3） | §5、§6 | **成立** |
| 评估在临时目录/可回滚目录进行，不改写用户已有产物 | §2 第三条（`find -newermt` 无输出） | **成立** |
