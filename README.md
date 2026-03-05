# astrbot_plugin_report_generator

适用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的喜报 / 悲报图片生成插件，支持 Emoji 正确渲染、群组黑白名单过滤及用户
ID 限制。

> 本插件的喜报 / 悲报功能源自 [astrbot_plugin_essential](https://github.com/Soulter/astrbot_plugin_essential)，在此基础上独立拆分，并新增访问控制、Emoji 渲染修复等功能。

## 功能

- **喜报 / 悲报生成**：将任意文字渲染到对应背景图上并发送
- **自动换行**：按像素宽度精确换行，支持中英混排
- **Emoji 渲染**：依赖 [pilmoji](https://github.com/jay3332/pilmoji) 将 Emoji 渲染为 Twemoji 图像，避免乱码方块（未安装时自动降级）
- **群组过滤**：支持黑名单 / 白名单两种模式，私聊始终不受限制
- **用户 ID 限制**：可设置允许使用的用户 ID 列表；列表为空时默认仅 AstrBot 管理员可用
- **并发安全**：每次生成使用唯一临时文件，发送后自动清理，不会与其他并发请求冲突

## 安装

在 AstrBot 管理面板的插件市场中搜索 `astrbot_plugin_report_generator` 并安装，或将本仓库克隆至 `data/plugins/`
目录下，然后安装依赖：

```
pip install pilmoji>=2.0.4
```

> 若不安装 pilmoji，Emoji 字符将显示为空方块，其他功能不受影响。

## 指令

| 指令         | 说明                      |
|------------|-------------------------|
| `/喜报 <内容>` | 生成喜报图片，将内容以红色文字渲染到喜报背景上 |
| `/悲报 <内容>` | 生成悲报图片，将内容以黑色文字渲染到悲报背景上 |

### 示例

```
/喜报 今天摸鱼成功！
/悲报 服务器又崩了
/喜报 🎉 发工资啦 🎉
```

> **注意**：内容中出现与命令相同的词不会被误删。例如 `/喜报 喜报内容` 会正确生成包含"喜报内容"字样的图片。

## 配置项

| 配置项                    | 类型     | 默认值           | 说明                                        |
|------------------------|--------|---------------|-------------------------------------------|
| `report_font_size`     | int    | `65`          | 图片中文字的字体大小                                |
| `group_filter_enabled` | bool   | `false`       | 是否启用群组过滤（私聊始终不受影响）                        |
| `group_filter_mode`    | string | `"blacklist"` | 群组过滤模式：`blacklist`（黑名单）/ `whitelist`（白名单） |
| `group_list`           | list   | `[]`          | 群组 ID 列表，配合过滤模式使用                         |
| `user_filter_enabled`  | bool   | `false`       | 是否启用用户 ID 限制                              |
| `allowed_user_ids`     | list   | `[]`          | 允许使用的用户 ID 列表；为空时默认仅 AstrBot 管理员可用        |

## 访问控制

### 群组过滤

启用 `group_filter_enabled` 后，插件将根据 `group_filter_mode` 和 `group_list` 对群消息进行过滤：

- **blacklist（黑名单）**：`group_list` 内的群组禁止使用，其他群组正常使用
- **whitelist（白名单）**：仅 `group_list` 内的群组可以使用，其他群组拒绝

私聊消息始终不受群组过滤限制。

### 用户 ID 限制

启用 `user_filter_enabled` 后：

- `allowed_user_ids` **非空**：仅列表内的用户可使用
- `allowed_user_ids` **为空**：默认仅 AstrBot 管理员（在 AstrBot 全局配置中设置）可使用

可通过 `/sid` 指令获取用户 ID，通过 `/op <id>` 授权管理员。
