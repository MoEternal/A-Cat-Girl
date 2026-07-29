# 插件开发

一只猫娘的插件是一个 ZIP 包，根目录可以直接放插件文件，也可以只套一层同名目录：

```text
my_plugin.zip
|- plugin.json
`- plugin.py
```

安装后，插件代码与一只猫娘运行在同一个 Python 进程中。它不是沙箱，拥有 Windows 服务账号的文件和网络权限；只应安装来源可信的插件。

## 清单

`plugin.json` 的最小示例：

```json
{
  "id": "my_plugin",
  "name": "示例插件",
  "version": "1.0.0",
  "entrypoint": "plugin.py",
  "author": "作者名",
  "min_app_version": "1.0.0",
  "default_enabled": false,
  "permissions": ["message.send.text"],
  "hooks": ["on_user_message"],
  "settings_schema": {
    "type": "object",
    "properties": {
      "prefix": {
        "type": "string",
        "title": "回复前缀",
        "default": "收到："
      }
    }
  }
}
```

`id` 只能包含小写字母、数字和下划线，并以字母开头。设置支持 `boolean`、`integer`、`number`、`string`，以及 `minimum`、`maximum`、`enum`、`enum_names` 和字符串的 `format: "textarea"`。

## 入口

入口模块导出 `plugin` 对象，或导出返回插件对象的 `create_plugin()`：

```python
from catgirl.plugins import PluginAction, PluginEvent, PluginResult


class MyPlugin:
    def on_user_message(self, context, event: PluginEvent) -> PluginResult:
        text = str(context.settings.get("prefix", "")) + event.text
        return PluginResult(
            actions=[
                PluginAction(
                    kind="send_text",
                    payload={
                        "conversation_id": event.conversation_id,
                        "text": text,
                    },
                )
            ]
        )


plugin = MyPlugin()
```

可声明的钩子：

- `on_startup`：插件启用或重载后调用。
- `on_shutdown`：插件停用、重载或服务关闭前调用。
- `on_user_message`：收到规范化用户消息后调用。
- `before_prompt_compile`：Prompt 编译前调用，可返回 `prompt_addition`。
- `before_response_split`：完整模型回复进入分段处理前调用，可标记不应参与分段的分隔符位置。
- `after_model_response`：模型生成完成后调用。
- `before_send`：QQ 发送前调用。
- `after_send`：QQ 发送完成后调用。

钩子可以是同步或异步方法，签名均为 `(context, event)`。返回 `PluginResult(consume=True)` 可阻止同一事件继续传给后面的插件，适合命令和休眠消息缓冲。

## Context

公开上下文提供：

- `context.settings`：当前设置的只读副本。
- `context.state`、`replace_state()`、`patch_state()`：插件独立持久状态。
- `get_conversation_state()`、`replace_conversation_state()`、`patch_conversation_state()`：按具体聊天记录隔离的持久状态；切换同一 QQ 的聊天记录时不会串线。
- `await context.generate_text()`：使用当前组合预设的供应商执行静默文本分析；结果不发送到 QQ，也不写入聊天历史。插件必须声明 `model.generate.selected_provider`。
- `context.get_conversation_messages()`：只读最近的安全文本历史，用于补漏或统计；插件必须声明 `message.history.read`，最多读取 200 条。
- `resolve_asset(relative_path)`：只允许读取插件目录内的文件。
- `schedule_interval(name, seconds, callback)` 和 `cancel_schedule(name)`：周期任务。
- `get_runtime_value()` 和 `set_runtime_value()`：插件间共享的临时运行状态，重启后清空。
- `ensure_text_safe(text)`：在持久化消息文本前检查图片 data URI。

插件状态和动作只允许 JSON 值，单个字符串最大 100,000 字符，任何 `data:image/...;base64` 都会被拒绝。图片动作应传受控文件路径或媒体引用，不传 base64。

`before_send` 的 `event.response_text` 是即将发给 QQ 的当前文本段，`event.metadata.character_id` 是生成该回复时使用的角色卡 ID。插件可在结果 `metadata.outbound_text` 中返回替换后的文本；返回空字符串会静默跳过该文本动作。此钩子位于 QQ 出口，不会改写模型原始回复、聊天历史或控制台内容。

`before_response_split` 的 `event.response_text` 是尚未分段的完整模型回复，`event.metadata.delimiter` 是当前分隔符。内置正则插件会检查所有启用的全局与角色规则，并通过 `metadata.regex_filter.protected_delimiter_offsets` 标记规则匹配范围内的分隔符；分段回复会跳过这些位置。该钩子只提供边界信息，不会提前执行替换，正则仍只在 `before_send` 阶段处理一次。

## 管理与状态观测

管理界面可以通过通用只读接口查看插件按聊天记录保存的状态：

```text
GET /api/plugins/{plugin_id}/conversation-states
GET /api/plugins/{plugin_id}/conversation-states?conversation_id={record_id}
```

响应中的 `items` 只包含聊天记录元数据，`state` 只包含当前选中或指定记录的一份状态，避免一次传输插件的全部大型状态。该接口用于受信任的管理端观测，状态可能包含私有剧情信息；在管理鉴权完成前不要把服务暴露到公网。

插件全局状态使用以下通用接口读写：

```text
GET /api/plugins/{plugin_id}/state
PUT /api/plugins/{plugin_id}/state
```

`PUT` 请求体为 `{ "state": { ... } }`。如果已加载的插件对象实现 `normalize_state(state)` 或 `validate_state(state)`，保存前会先调用它们；状态仍受 JSON、文本长度和内联图片限制。

## 动作约定

当前内置插件使用以下结构化动作，未来 NapCat 与模型运行时也按这些动作接入：

- `send_text`、`send_image`
- `request_generation`
- `prompt_addition`
- `replace_response`
- `message_buffered`、`sleep_started`

模型生成动作应设置 `provider_policy: "selected_only"`。临时提示使用 `history_policy: "temporary_prompt"`，生成失败时不得污染正式历史。

## 安装限制

管理页接受 ZIP，压缩包最大 32 MB、解压后最大 128 MB、最多 1,200 个文件。安装器拒绝绝对路径、`..` 路径穿越、符号链接、多个清单和覆盖内置插件。内置插件不能卸载，但可以停用。

`request_generation` 已由统一模型运行时执行；`send_text` / `send_image` 会进入持久动作队列，并在 NapCat 反向 WebSocket 在线时通过统一发送器执行。离线时发送动作保持 `pending`。插件本身不应直接连接 NapCat 或自行调用供应商。
