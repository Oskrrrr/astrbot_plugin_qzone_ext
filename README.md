<div align="center">

# 🌌 QQ空间外挂

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-27a7d8)](https://github.com/AstrBotDevs/AstrBot)
[![Release](https://img.shields.io/github/v/release/Oskrrrr/astrbot_plugin_qzone_ext?color=27a7d8&label=Release)](https://github.com/Oskrrrr/astrbot_plugin_qzone_ext/releases)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)

**定时发说说 + 自动回评 + 智能配图；图片、时间天气、动态内容都可配，默认 dry-run 先预览。**

</div>

## ✨ 特色

| 你想做什么 | 外挂怎么帮你 |
|---|---|
| 让空间保持活跃 | 每小时随机时间点，按跳过概率、每日上限、发布时段发说说 |
| 有人评论自动回 | 定时读自己说说的新评论，按人格语气回复，已回评论去重 |
| 主动互动 | 可选评论好友说说、顺手点赞，支持忽略名单 |
| 配图不重样 | 预存图按季节/时段/天气匹配；没命中就调 [画图](https://github.com/Oskrrrr/astrbot_plugin_draw) 插件生成 |
| 画面更贴内容 | 动态模式先用文本模型写画面描述，再拼固定要求去画图 |
| 结合当下 | 注入日期、星期、季节、时段、天气；可选从群聊取灵感素材 |
| 角色卡联动 | 读 [角色卡](https://github.com/Oskrrrr/astrbot_plugin_persona_card) 的说说提示词、画风、画面要求/负面词 |
| 先预览再发 | dry-run 只生成不发布，页面看文案和配图预览；失败自动降级纯文字 |

## 🚀 快速开始

1. 把目录放进 `AstrBot/data/plugins/astrbot_plugin_qzone_ext`。
2. 安装依赖：`pip install -r requirements.txt`（Pillow、json5）。
3. 重启 AstrBot，或重载插件。
4. 在 WebUI 插件页打开「QQ空间外挂」页面。

首次使用建议保持 `dry_run` 开着，点「试发一条」看几条文案和配图，满意后再关。

## 🧩 它怎么工作

```text
定时触发 ─→ 生成文案（人格 + 日期时间/天气 + 可选群素材）
        ─→ 决定配图（预存图 / 画图插件生成 / 不配图）
        ─→ dry-run 预览，或真正发布
        ─→ 定时检查新评论，按人格语气回复
```

- 机器人没连上、Cookie 取不到、模型不可用时，只记日志并跳过本轮，不补跑、不崩溃。
- 配图上传失败会自动改发纯文字，并在页面上标注。
- 评论/点赞/回评都有去重记录，避免重复打扰。

## 🖼️ 图片生成：预存图、生成图、动态画面描述

配图由三部分组成：**用哪张图**、**画面描述怎么来**、**风格/负面词从哪来**。三部分独立配置，可以只开其中一部分。

### 1. 预存图：按季节/时段/天气/关键词匹配

- 打开 `stock_image_enabled`，把图片放进 `stock_image_dir`（支持 png/jpg/jpeg/webp，可递归子目录）。
- 文件名里带季节、时段、天气关键词会被加分，例如 `秋天-下午-街景.png`、`雨天-夜晚-便利店.jpg`。
- 还会用动态画面描述里的关键词继续打分；最近用过的 5 张会降权，避免连续重复。
- 按 `stock_image_ratio` 概率决定这轮用预存图还是生成图；预存图目录为空/没命中时自动回落生成。

### 2. 生成图：调用「画图」插件

- 打开 `attach_image` 后，没命中预存图时会调用 [画图](https://github.com/Oskrrrr/astrbot_plugin_draw) 插件的 `generate()`。
- 说说外挂只负责拼提示词和决定风格/负面词；真正用哪个 provider、哪个模型，由画图插件的 `providers.json` 决定。
- 没装画图插件、生成失败、图片上传失败时，自动降级为纯文字，不影响这条说说发出去。
- 页面「概览」会显示 `draw_available` 和当前风格来源，方便排查为什么没配图。

### 3. 动态画面描述：让文本模型先“想画面”

打开 `dynamic_image_prompt` 后，发布前会多一次文本模型调用：

```text
说说正文 + 日期时间 + 天气
        ↓  image_director_prompt
画面描述（60～110 字，只写场景/物件/光线，不写画风、不写文字、不编天气）
        ↓  拼上固定画面要求
交给画图插件生成
```

- 画面描述只写“画什么”，不写“什么风格”；风格由角色卡/画图插件决定。
- 固定画面要求（`image_prompt`）永远生效：只画场景与静物、不要人物/文字/水印、正方形构图等。
- 关闭动态模式时，直接把“固定要求 + 说说正文”交给画图插件，少一次模型调用。

### 4. 风格、画面要求、负面词从哪来

| 内容 | 第一优先 | 第二优先 | 兜底 |
|---|---|---|---|
| 说说提示词 | [角色卡](https://github.com/Oskrrrr/astrbot_plugin_persona_card) 的 `post_prompt` | 外挂的 `post_prompt` | 内置“第一人称 40～80 字” |
| 画面要求 | 角色卡的 `image_requirements` | 外挂的 `image_prompt` | 内置“只画静物、不要人物文字” |
| 画风 | 角色卡的 `image_style` | [画图](https://github.com/Oskrrrr/astrbot_plugin_draw) 插件的画风 | 不追加 |
| 负面词 | 角色卡的 `image_negative` | 外挂的 `image_negative` | 画图插件默认负面词 |

这样分工的好处：**说说配图不继承画图插件聊天用的画面要求**；画图插件只提供“画师能力”，说说外挂/角色卡决定“画什么、什么风格”。

## 🌤️ 日期、时间与天气

### 日期与时间

打开 `inject_time`（默认开）后，每轮会把当前时间注入提示词：

```text
现在是 2026-10-30 18:05（周五，秋天，晚上）
```

包含日期、星期、季节（春/夏/秋/冬）和时段（清晨/上午/中午/下午/晚上/深夜）。模型据此写“今天”“这个点”“秋天”等更自然的内容，而不是自己猜。

### 天气

打开 `inject_weather` 并填 `weather_city` 后，会从 [Open-Meteo](https://open-meteo.com/) 取当前天气：

- 先按城市名做 geocoding，再取当前温度、天气代码、风速、昼夜。
- 天气代码会映射成中文（晴/多云/小雨/雷阵雨/雪等），拼成“上海小雨，18°C”。
- 结果缓存 30 分钟，避免每轮都请求。
- 取不到时明确告诉模型“天气数据获取失败，不要编造天气”，不会瞎写。
- 天气同时会进入说说提示词和动态画面描述；画面里不会出现“晴天却画雪”的冲突。

### 放到哪里

| 开关 | 注入说说文案 | 注入画面描述 | 说明 |
|---|---|---|---|
| `inject_time` | ✅ | ✅ | 日期、星期、季节、时段 |
| `inject_weather` | ✅ | ✅ | 需要填 `weather_city`；失败不编造 |

## 🧠 动态内容：人格、群素材、发布节奏

### 人格

- `persona_id` 填 AstrBot 人格名；留空用默认人格。
- `require_persona` 开启时，配置人格和默认人格都取不到就跳过本轮，不用“无人物风格”凑合。
- 人格决定“谁在说话”；角色卡只是额外提供说说板块，不会替代 AstrBot 的人格系统。

### 群聊素材

打开 `material_enabled` 后，发布前会从群里取一段聊天记录当灵感：

- `material_groups` 限定只从哪些群取；留空表示全部。
- `material_ignore_groups` 排除某些群。
- `material_max_messages` 控制最多参考多少条（20～500）。
- 只取文本，不引用原话、不点名、不写成“群里有人说”；提示词明确要求“用第一人称写自己的小事”。

### 发布节奏

| 配置 | 默认 | 作用 |
|---|---|---|
| `publish_skip_probability` | 0.85 | 每小时有 85% 概率跳过，控制频率 |
| `daily_limit` | 3 | 每天最多发几条 |
| `publish_hours` | 8-23 | 发布时段，支持跨零点如 22-6 |
| `publish_offset_minutes` | 50 | 每小时内的随机范围，避免整点机器感 |

### 回评与主动互动

- **自动回评**：`reply_enabled` 默认开，间隔 15 分钟，单轮最多 3 条，评论至少 60 秒后才回，回复最长 80 字。
- **主动互动**：`interact_enabled` 默认关；开启后评论好友说说、可选顺手点赞；支持忽略用户、最短字数、单轮上限。
- 两者都有去重记录，重启后不会重复回同一条评论/同一条说说。

### dry-run

- `dry_run` 默认开：只生成文案，不真正发布；定时轮不生成配图（省模型/生图费用）。
- 页面「试发一条」会走完整配图链路并显示预览，适合先看效果。
- 确认文案和配图没问题后，再把 `dry_run` 关掉。

## 🔗 和其他两个插件的联动

### 与 [画图](https://github.com/Oskrrrr/astrbot_plugin_draw) 插件

- 说说外挂通过 `context.get_registered_star("astrbot_plugin_draw")` 拿画图插件实例，调用它的 `generate(prompt, style=..., negative=...)`。
- 画图插件负责 provider/模型/密钥/重试；说说外挂负责“画什么、什么风格、什么不要”。
- 没装画图插件时，`draw_available=false`，自动只发文字。
- 画图插件版本较老、`generate()` 不接 `style/negative` 时，会说外挂会自动退到只传 prompt 的调用方式，不报错。
- 说说外挂不修改画图插件的代码、不直接读它的 `providers.json`。

### 与 [角色卡](https://github.com/Oskrrrr/astrbot_plugin_persona_card) 插件

- 说说外挂通过 `context.get_registered_star("astrbot_plugin_persona_card")` 拿角色卡实例，调用 `qzone_profile(persona_id)`。
- 角色卡按“卡片名 / 人格名”匹配，返回这张卡的「说说」板块：`post_prompt`、`image_style`、`image_requirements`、`image_negative`。
- 说说外挂优先用角色卡的值；卡里没写或没装角色卡时，用外挂自己的配置/内置默认。
- 人格本身仍由 AstrBot 人格系统提供；角色卡只是把“这张卡的说说设定”借给外挂，不改变人格选择。
- 角色卡没填「说说」板块时，外挂完全按自己的默认工作，两边可以独立使用。

## ⚙️ 配置

所有设置都在插件页面里，也可以直接在插件配置里改。重点几项：

- **人格**：填 AstrBot 人格名；留空用默认人格。人格决定“谁在说话”。
- **说说提示词**：留空用内置（第一人称、40～80 字、随手发一条的口吻）。
- **画面要求**：构图与禁止项，永远生效；留空用内置（只画静物、不要人物文字、正方形）。
- **画风**：优先取 [角色卡](https://github.com/Oskrrrr/astrbot_plugin_persona_card) 里这张卡的配图画风；卡里没写才用 [画图](https://github.com/Oskrrrr/astrbot_plugin_draw) 插件的画风。
- **画面描述提示词**：动态配图用；留空用内置（只写景物光线，不画人、不写文字、不编天气）。
- **按内容动态写画面描述**：开启后多一次文本模型调用，先写画面再画图；关闭则只把固定要求 + 正文交给 [画图](https://github.com/Oskrrrr/astrbot_plugin_draw) 插件。
- **预存图**：目录里放图片，文件名带季节/时段/天气关键词会被优先匹配；最近用过的 5 张会避开。

## 🖥️ 页面

插件页面分「概览」和「设置」两块：

- 概览：当前状态、dry-run 开关、试发一条、检查评论、最近生成记录（含配图来源和画面描述）
- 设置：发布节奏、配图、时间天气、素材来源、主动互动、各类提示词

## 📌 说明

- 回评/互动依赖 [NapCat](https://github.com/NapNeko/NapCatQQ) 提供的 QQ 空间 cookie，登录态失效时会在日志里提示。
- 所有提示词都可以在页面里改，插件不带任何角色设定或地图名，开箱是通用的。
- 原版 [astrbot_plugin_qzone](https://github.com/Zhalslar/astrbot_plugin_qzone) 负责看/赞/评/删/表白墙等全量操作；本插件只负责“定时 + 回评 + 配图”。两边的定时任务建议只开一个，避免重复发说说/评论。
- 定时轮不配图、页面「试发一条」才配图，是为了在 dry-run 阶段省掉不必要的生图费用；正式发布时按 `attach_image` 正常配图。

## 📄 来源与许可

本插件以 **GPL-3.0** 发布，完整许可见 [LICENSE](./LICENSE)，来源与修改说明见 [NOTICE](./NOTICE)。

- **协议层**：`client.py` 中的 QQ 空间接口（登录 Cookie、`gtk`、JSONP 解析、图片上传、发说说、评论/点赞/回评、说说列表等）移植/改写自 [Zhalslar/astrbot_plugin_qzone](https://github.com/Zhalslar/astrbot_plugin_qzone)。原项目仓库附带的是 **GPLv3** `LICENSE`；其 README 徽章写作 MIT，存在口径不一致，本插件按更严格的 GPLv3 处理。原项目 README 还注明部分代码参考了 [CampuxBot](https://github.com/idoknow/CampuxBot)。
- **新增部分**：定时编排、dry-run、配图链路（预存图/生成图/动态提示词/天气时间/群素材）、[角色卡](https://github.com/Oskrrrr/astrbot_plugin_persona_card) 与 [画图](https://github.com/Oskrrrr/astrbot_plugin_draw) 插件联动、自写配置页、JSON 状态与评论去重、失败降级等。
- 如果你只是在本机自用、不分发本插件，GPLv3 不产生分发义务；如果要公开仓库或上架插件市场，请保留 `LICENSE`/`NOTICE` 与源码，并保留上述署名。
- 如果原作者后续确认实际许可是 MIT，本插件可在保留版权与许可声明的前提下调整；届时会同步更新本段与 `NOTICE`。
