# 消息与模型运行时

当前运行时已经接通组合预设、Prompt 编译器、插件钩子、OpenAI 兼容模型客户端、会话历史、持久动作队列和 NapCat/OneBot 发送器。没有反向 WebSocket 连接时，`send_text` / `send_image` 动作保持 `pending`；连接建立后自动重新排队。

## 处理顺序

```text
规范化用户消息
  -> 同一会话串行锁
  -> on_user_message
  -> 安全文本历史
  -> before_prompt_compile
  -> 当前组合预设 + 世界书 + 宏 + 历史裁剪
  -> OpenAI 兼容 Chat Completions（普通或 SSE）
  -> before_response_split（完整回复的分段边界保护）
  -> after_model_response
  -> 安全助手历史
  -> send_text / send_image 动作队列
```

插件的 `request_generation` 也进入同一动作队列，并使用当前组合预设中选定的唯一供应商。临时提示只存在于本次模型请求；请求失败或成功后都不会写入聊天历史。

QQ 路由和聊天记录相互分离。运行时先按路由解析当前活动记录，再在该记录内编译历史和追加回复；管理页切换记录与消息写入共用同一路由锁。

## 内部 API

`POST /api/runtime/messages` 会真实调用当前组合预设的供应商，仅用于 NapCat 接入前的诊断或内部适配器：

```json
{
  "conversation_id": "qq:private:123456",
  "user_id": "123456",
  "channel": "qq_private",
  "text": "你好",
  "media": []
}
```

查询接口：

- `GET /api/runtime/conversations`
- `GET /api/runtime/conversations/{conversation_id}/messages`
- `GET /api/runtime/actions?limit=100`

## 安全边界

- API Key 只在单次请求构造时解密，不进入请求日志、会话或动作负载。
- 模型 HTTP 错误会替换响应中回显的当前 API Key。
- 输入、模型输出、历史和动作负载都拒绝字符串化的图片 data URI。
- OneBot 收到的图片先安全下载并规范化，只在当前模型请求中构造结构化 data URI；历史和动作只保存受控引用与占位符。内部诊断 API 的媒体引用不会自行读取文件或内联。
- Prompt 超预算时只从最旧历史开始裁剪，始终保留最新一条历史；固定 Prompt 自身超限则中止请求。
- 同一 `conversation_id` 的模型请求串行执行，不同会话可以并发。

## 动作状态

- `pending`：等待发送器，或等待工作器执行。
- `processing`：正在生成或发送。
- `completed`：内部动作或生成动作执行成功。
- `failed`：执行失败，`error` 保存不超过 4,000 字符的错误摘要。

服务重启时，遗留的 `processing` 动作会恢复为 `pending`。未接发送器时，文本和图片动作只保留，不会循环重试或丢失。
