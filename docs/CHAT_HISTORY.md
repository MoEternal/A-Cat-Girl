# 聊天记录

聊天记录与 QQ 路由分离。同一个 QQ 好友或群可以拥有多份互相独立的历史，但任一时刻只有一份是“当前使用”。

## 路由与记录

QQ 路由保持稳定：

- 私聊：`qq:{机器人QQ}:private:{用户QQ}`
- 群聊：`qq:{机器人QQ}:group:{群号}`

首次收到某路由的消息时，运行时自动创建“默认记录”。在管理页“聊天记录”中可以为同一路由继续新建任意多份记录。

每份记录有独立 ID、名称、消息序列、模型与 token 元数据。切换当前记录不会复制、合并或删除其他记录；切换完成后，该 QQ 的下一条用户消息、主动回复、晚安唤醒和其他插件生成都会读取新记录。

## 管理操作

- 选择 QQ 会话：私聊用当前记录关联的角色卡名称显示，不在界面暴露对方 QQ；群聊同时保留群号用于区分。
- 新建聊天记录：为当前 QQ 路由创建一份空记录。
- 使用这份记录：原子切换该路由的活动记录。
- 保存名称：只修改显示名称，不改变消息。
- 删除：删除整份记录及其消息。含消息的 QQ 路由至少保留一份；最后一份空记录可以删除。
- 多选删除消息：进入多选模式后可原子删除任意选中的本地聊天消息，不受 QQ 撤回窗口限制，也不会再次撤回 QQ 中已经发送的消息。关联的 QQ 回合会标记为已编辑，迟到的撤回事件不会误删剩余历史；保留消息的原位置编号，不重排插件楼层。
- 单层编辑：每层右上角的铅笔按钮只修改本地聊天记录文本，不修改、撤回或重发 QQ 消息；编辑后保留原位置编号，并让迟到的 QQ 撤回失效。
- 预览：按实际顺序显示当轮使用的用户卡/角色卡名称、稳定楼层号、文本、时间、模型和 token 总量。名称优先使用生成时保存的身份快照，旧记录从消息关联的历史预设与 QQ 回合还原，没有历史依据时才显示通用“用户/角色”，不会套用当前激活卡。记录总 token 与单条消息 token 使用当前模型对应的本地分词器实时计算，旧消息同样生效。图片历史显示安全占位符，不显示 base64。
- 快速滚动：消息列表左上和左下的悬浮按钮可直接滚动到记录顶部或底部。

切换与新消息写入共用同一个路由锁，因此不会出现一条消息写进两份记录或切换到一半的状态。

## API

- `GET /api/runtime/conversations`
- `POST /api/runtime/conversations`
- `PUT /api/runtime/conversations/{record_id}`
- `POST /api/runtime/conversations/{record_id}/activate`
- `DELETE /api/runtime/conversations/{record_id}`
- `GET /api/runtime/conversations/{record_id}/messages`
- `PUT /api/runtime/conversations/{record_id}/messages/{message_id}`
- `POST /api/runtime/conversations/{record_id}/messages/delete`

批量删除消息请求体为 `{"message_ids":["消息ID"]}`。全部 ID 必须存在且属于同一条记录，否则整次请求拒绝且不删除任何消息。

单层编辑请求体为 `{"content":"修改后的本地文本"}`。消息 ID 必须属于路径中的记录，内容不能为空。

诊断接口 `POST /api/runtime/messages` 接收 QQ 路由 ID，响应中的 `conversation_id` 是实际写入的记录 ID，`route_id` 是稳定 QQ 路由。
