<div align="center">

<img src="resources/images/pig.webp" width="128" alt="小猪收集册" />

# 🐷 小猪收集册

**每天领一只小猪，把日子攒成一座猪圈。**

✨ [AstrBot](https://github.com/AstrBotDevs/AstrBot) · QQ 官方机器人 · 每日抽猪与跨群收藏 ✨

[![License: MIT](https://img.shields.io/badge/License-MIT-a3be8c.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-89b4fa.svg)](https://www.python.org/)
[![AstrBot 3.5.3+](https://img.shields.io/badge/AstrBot-3.5.3%2B-f2cd94.svg)](https://github.com/AstrBotDevs/AstrBot)
[![作者 yun474](https://img.shields.io/badge/作者-yun474-f5b7c7.svg)](https://github.com/yun474)

<img src="https://count.getloli.com/@yun474_astrbot_plugin_piggy?name=yun474_astrbot_plugin_piggy&theme=asoul&padding=7&offset=0&align=center&scale=1&pixelated=1&darkmode=auto" alt="访问计数小人" />

[功能亮点](#features) · [效果预览](#preview) · [安装使用](#usage) · [R2 配置](#r2) · [猪库维护](#catalog) · [数据备份](#backup)

</div>

---

<a id="features"></a>

## ✨ 能玩什么

- 每天抽一只小猪；当天重复使用会看到同一只，收藏跨群共享。
- 在图鉴里查看解锁进度，在猪圈里翻看已收藏的小猪。
- 查看种类榜和数量榜，也可以设置自己的展示称呼。

<a id="preview"></a>

## 🍮 效果预览

<table>
  <tr>
    <th>📖 小猪图鉴</th>
    <th>🏡 我的猪圈</th>
  </tr>
  <tr>
    <td valign="top"><img src="docs/images/atlas.png" width="360" alt="小猪图鉴效果" /></td>
    <td valign="top"><img src="docs/images/pen.png" width="360" alt="我的猪圈效果" /></td>
  </tr>
</table>

<a id="usage"></a>

## 🚀 安装与使用

在 AstrBot 插件管理中使用仓库地址 `https://github.com/yun474/astrbot_plugin_piggy` 安装。需要 AstrBot 3.5.3 或更新版本。若已启用旧版“今日小猪”插件，请先停用，避免指令冲突。

在 QQ 群里 @机器人发送：

| 指令 | 用途 |
| --- | --- |
| `今日小猪` / `抽小猪` | 抽取当天的小猪 |
| `小猪图鉴 [页码]` | 查看全部小猪和解锁进度 |
| `我的猪圈 [页码]` | 查看自己的收藏 |
| `小猪排行` | 查看种类榜和数量榜 |
| `小猪称呼 名字` | 设置展示称呼，限 1–24 字 |
| `小猪重载` | 管理员应用猪库修改 |
| `小猪备份` | 管理员创建本地备份 |
| `小猪诊断` | 管理员检查图床上传与 QQ 图片发送 |

每天按东八区自然日计算抽取次数。同一 QQ 官方机器人应用下，收藏和每日抽取结果跨群共享。

<a id="r2"></a>

## 🎛️ 图片发送方式与 R2 配置

插件可以直接向 QQ 发送图片，也可以通过图床发送带快捷按钮的消息。在插件配置的「消息展示」中，分别设置「今日小猪」「小猪图鉴」「我的猪圈」「小猪排行」是否使用图床。默认只有「今日小猪」开启图床；如果没有图床，请先关闭这个开关，其余功能默认可直接使用。

开启图床时，需要在插件配置中填写 R2 / S3 或 HTTP 图床信息；图片公网地址必须能由 QQ 直接访问。使用 R2 时，`endpoint` 填上传接口，`public_base_url` 填已绑定存储桶的公网域名，两者不要填反。若把图鉴、猪圈或排行也设为图床模式，建议给 R2 的 `piggy/temp/` 设置 7 天后删除的生命周期规则，避免临时卡片长期占用空间。

快捷按钮会按「快捷指令唤醒词」生成命令。默认留空即可在 @机器人后直接使用；如果 AstrBot 设置了 `/`、`!` 等唤醒词，请在这里填写相同内容。

<a id="catalog"></a>

## 🧩 猪库维护

首次启动会在 `data/plugin_data/astrbot_plugin_piggy/catalog/` 生成猪库。管理员可编辑 `pigs.json` 和 `images/` 中的图片，再发送 `小猪重载` 应用修改。新增小猪时添加新条目和图片；下架时将对应条目的 `enabled` 设为 `false`。请勿把旧 `id` 分配给另一种猪，以免合并原有收藏。历史收藏不会因下架而删除。

<a id="backup"></a>

## 💾 数据备份

插件会在启动时及之后每 24 小时自动备份；也可以发送 `小猪备份`。备份文件位于插件数据目录的 `backups/`，包含收藏数据、猪库和历史图片。恢复时先停用插件，保留原数据目录作回退，再将可信备份解压到新的空插件数据目录，最后启用插件。图床凭据保存在 AstrBot 配置中，不包含在备份里。

## 💛 致谢

初始猪库与图片来自 [MegSopern/astrbot_plugin_rollpig](https://github.com/MegSopern/astrbot_plugin_rollpig/tree/42490b1b88367260c137c1147b05a3c052f22d33)，感谢 Bear_lele 和 MegSopern。项目代码遵循 [MIT 许可证](LICENSE)；随包字体 [Noto Sans SC](https://github.com/google/fonts/tree/main/ofl/notosanssc) 遵循 `resources/fonts/OFL.txt` 中的 SIL Open Font License。

> 插件反馈交流群：947667614
