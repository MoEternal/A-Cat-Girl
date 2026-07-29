from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from catgirl.main import create_app
from catgirl.model_client import OpenAICompatibleClient
from catgirl.plugins import PluginEvent
from catgirl.plugins.context import PluginContext
from catgirl.database import PluginConversationState


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "data", allow_unconfigured_management=True))


def configure_runtime(client: TestClient) -> None:
    provider = client.get("/api/providers").json()[0]
    client.put(
        f"/api/providers/{provider['id']}",
        json={"base_url": "https://model.test/v1", "model": "memory-model", "enabled": True},
    )
    preset = next(item for item in client.get("/api/presets").json() if item["is_active"])
    client.put(
        f"/api/presets/{preset['id']}",
        json={"streaming": False, "context_length": 8192, "max_response_tokens": 2048},
    )
    client.put("/api/plugins/sticker_reply", json={"settings": {"probability": 0}})
    client.put("/api/plugins/time_awareness", json={"enabled": False})
    client.put(
        "/api/plugins/memory_system",
        json={
            "enabled": True,
            "settings": {
                "lead_character_names": "玲奈",
                "main_cast_names": "雪音",
                "detail_audit_interval": 0,
            },
        },
    )


def test_chat_lifecycle_updates_and_cleans_file_memory_bindings(tmp_path: Path) -> None:
    route_id = "qq:90001:private:lifecycle"
    with make_client(tmp_path) as client:
        configure_runtime(client)
        runtime = client.app.state.chat_runtime
        first = runtime.create_conversation_record(route_id, "第一条记录")
        second = runtime.create_conversation_record(route_id, "待删除记录")
        manager = client.app.state.plugin_manager
        record = manager.records["memory_system"]
        context = PluginContext(manager, "memory_system", record.path)

        before = record.instance.inspect_conversation_states(context, first.id)
        second_memory_id = next(
            item["memory_id"] for item in before["items"] if item["conversation_id"] == second.id
        )

        runtime.rename_conversation_record(first.id, "改名后的记录")
        renamed = record.instance.inspect_conversation_states(context, first.id)
        first_row = next(
            item for item in renamed["items"] if item["conversation_id"] == first.id
        )
        assert "改名后的记录" in first_row["memory_name"]

        asyncio.run(runtime.delete_conversation_record(second.id))
        after = record.instance.inspect_conversation_states(context, first.id)
        assert second.id not in {item["conversation_id"] for item in after["items"]}
        assert second_memory_id not in {item["id"] for item in after["memories"]}


def test_builtin_memory_manual_file_edits_survive_reload_and_next_write(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        configure_runtime(client)
        runtime = client.app.state.chat_runtime
        conversation = runtime.create_conversation_record("qq:manual-memory", "手动记忆")
        manager = client.app.state.plugin_manager
        record = manager.records["memory_system"]
        context = PluginContext(manager, "memory_system", record.path)

        context.get_conversation_state(conversation.id)
        view = record.instance.inspect_conversation_states(context, conversation.id)
        memory_id = view["selected_memory_id"]
        assert memory_id
        memory_path = context.memory_path / "memory_records" / "memories" / f"{memory_id}.json"
        document = json.loads(memory_path.read_text(encoding="utf-8"))
        document["state"]["saga_summary"] = "这是直接写入 JSON 的手动长期记忆。"
        document["state"]["pinned"].append(
            {"id": "pin_manual", "content": "手动固定事实不会被回退", "created_at": "2026-07-28"}
        )
        memory_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        reloaded = context.get_conversation_state(conversation.id)
        assert reloaded["saga_summary"] == "这是直接写入 JSON 的手动长期记忆。"
        assert reloaded["pinned"][-1]["content"] == "手动固定事实不会被回退"

        reloaded["turn"] = 1
        context.replace_conversation_state(conversation.id, reloaded)
        after_next_write = context.get_conversation_state(conversation.id)
        assert after_next_write["saga_summary"] == "这是直接写入 JSON 的手动长期记忆。"
        assert after_next_write["pinned"][-1]["content"] == "手动固定事实不会被回退"


def memory_payload() -> dict:
    return {
        "scene": {"story_time": "第三日黄昏", "location": "旧王城钟楼", "summary": "玲奈替悠挡下箭矢，两人找到王家徽记。"},
        "main_arc": {
            "summary": "玲奈开始明确信赖悠，但仍隐瞒王族身份。",
            "phase": "在意",
            "central_conflict": "玲奈的王族身份尚未公开",
            "next_pressure": "雪音正在追查徽记来源",
        },
        "characters": [
            {
                "name": "玲奈",
                "aliases": ["小玲"],
                "cast_role": "lead",
                "relationship_stage": "在意",
                "affection_delta": 4,
                "trust_delta": 5,
                "jealousy_delta": 0,
                "current_state": "左肩受伤但仍能行动",
                "current_emotion": "安心又犹豫",
                "current_goal": "隐藏身份并保护悠",
                "current_outfit": "破损的白色斗篷",
                "injuries": ["左肩箭伤"],
                "physical_traits": "银白长发，紫色眼瞳",
                "personality": "谨慎而温柔",
                "occupation": "王族继承人",
                "hobbies": ["阅读古籍"],
                "likes": ["雨后的花园"],
                "residence": "王城北塔",
                "important_info": ["隐瞒王族身份"],
                "user_relationship": "同行者",
                "user_attitude": "开始信赖悠",
                "milestone": "第一次主动替悠挡箭",
            }
        ],
        "relationships": [
            {
                "source": "玲奈",
                "target": "雪音",
                "relation": "旧识",
                "attitude": "警惕",
                "closeness": -12,
                "known_by": ["玲奈", "雪音"],
                "evidence": "雪音追问徽记来源时，玲奈明显回避。",
            }
        ],
        "arcs": [
            {
                "title": "王城徽记之谜",
                "kind": "exploration",
                "status": "active",
                "summary": "众人在钟楼找到与玲奈身世有关的王家徽记。",
                "participants": ["悠", "玲奈", "雪音"],
                "open_threads": ["徽记为何藏在钟楼"],
                "importance": 5,
            }
        ],
        "events": [
            {
                "summary": "玲奈替悠挡下暗处射来的箭，左肩受伤。",
                "kind": "battle",
                "arc": "王城徽记之谜",
                "participants": ["悠", "玲奈"],
                "known_by": ["悠", "玲奈"],
                "story_time": "第三日黄昏",
                "location": "旧王城钟楼",
                "importance": 5,
                "keywords": ["挡箭", "箭伤", "钟楼"],
                "evidence": "玲奈扑开悠，箭尖划入她的左肩。",
            }
        ],
        "memories": [
            {
                "owner": "玲奈",
                "content": "玲奈认定悠值得自己冒险保护。",
                "known_by": ["玲奈"],
                "private": True,
                "emotion": "在意",
                "importance": 5,
                "keywords": ["保护", "信赖"],
                "evidence": "她没有把这个想法说出口。",
            }
        ],
        "facts": [
            {
                "subject": "王家徽记",
                "category": "clue",
                "detail": "徽记背面刻有玲奈幼时使用的名字。",
                "known_by": ["玲奈"],
                "importance": 5,
                "keywords": ["徽记", "身世"],
                "evidence": "玲奈独自看见背面的旧名后立刻收起徽记。",
            }
        ],
        "promises": [
            {
                "content": "悠答应玲奈暂时不追问她的过去。",
                "parties": ["悠", "玲奈"],
                "known_by": ["悠", "玲奈"],
                "status": "pending",
                "importance": 5,
                "evidence": "悠说：等你想说时再告诉我。",
            }
        ],
        "items": [
            {
                "name": "王家徽记",
                "owner": "玲奈",
                "status": "完好，已被收起",
                "location": "玲奈斗篷内袋",
                "importance": 5,
                "evidence": "玲奈把徽记藏进斗篷内袋。",
            }
        ],
    }


def blank_payload() -> dict:
    return {
        "scene": {"story_time": "", "location": "", "summary": ""},
        "main_arc": {},
        "characters": [],
        "relationships": [],
        "arcs": [],
        "events": [],
        "memories": [],
        "facts": [],
        "promises": [],
        "items": [],
    }


def test_memory_analysis_is_silent_and_recalled_on_next_turn(tmp_path: Path) -> None:
    main_requests: list[list[dict]] = []
    analysis_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal analysis_calls
        payload = json.loads(request.content)
        messages = payload["messages"]
        system_text = "\n".join(str(item.get("content", "")) for item in messages if item["role"] == "system")
        if "结构化记忆整理器" in system_text:
            analysis_calls += 1
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(memory_payload(), ensure_ascii=False)}, "finish_reason": "stop"}]},
            )
        main_requests.append(messages)
        reply = "玲奈按住左肩，轻声说自己还能继续。" if len(main_requests) == 1 else "玲奈下意识护住斗篷内袋。"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": reply}, "finish_reason": "stop"}]},
        )

    route_id = "qq:90001:private:memory"
    with make_client(tmp_path) as client:
        configure_runtime(client)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(handler)
        )
        first = client.post(
            "/api/runtime/messages",
            json={"conversation_id": route_id, "text": "玲奈，小心暗处的箭！"},
        )
        assert first.status_code == 200
        assert first.json()["text"] == "玲奈按住左肩，轻声说自己还能继续。"
        assert analysis_calls == 1

        records = client.get("/api/runtime/conversations").json()
        record_id = records[0]["id"]
        state = client.app.state.plugin_manager.get_conversation_state("memory_system", record_id)
        assert state["tables"]["characters"]["columns"][0] == "角色名"
        assert all(not table["rows"] for table in state["tables"].values())
        assert state["characters"][0]["current_outfit"] == "破损的白色斗篷"
        assert state["characters"][0]["injuries"] == ["左肩箭伤"]
        assert state["characters"][0]["hobbies"] == ["阅读古籍"]
        assert state["characters"][0]["user_relationship"] == "同行者"
        assert state["relationships"][0]["target"] == "雪音"
        assert state["memories"][0]["known_by"] == ["玲奈"]
        assert state["facts"][0]["category"] == "clue"
        assert state["items"][0]["location"] == "玲奈斗篷内袋"
        assert state["last_scene"]["story_time"] == "第三日黄昏"
        assert state["events"][0]["story_time"] == "第三日黄昏"

        second = client.post(
            "/api/runtime/messages",
            json={"conversation_id": route_id, "text": "把刚才找到的徽记拿出来看看。"},
        )
        assert second.status_code == 200
        injected = "\n".join(str(item.get("content", "")) for item in main_requests[1])
        assert "王家徽记" in injected
        assert "玲奈斗篷内袋" in injected
        assert "左肩箭伤" in injected
        assert "知情：玲奈" in injected
        assert "悠答应玲奈暂时不追问" in injected
        assert "玲奈 -> 雪音：旧识" in injected

        history = client.get(f"/api/runtime/conversations/{record_id}/messages").json()
        history_text = "\n".join(item["content"] for item in history)
        assert "structured" not in history_text
        assert '"main_arc"' not in history_text
        assert all(item["role"] in {"user", "assistant"} for item in history)


def test_time_awareness_forces_memory_to_use_real_time(tmp_path: Path) -> None:
    main_requests: list[list[dict]] = []
    analysis_systems: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        messages = json.loads(request.content)["messages"]
        system_text = "\n".join(
            str(item.get("content", "")) for item in messages if item["role"] == "system"
        )
        if "结构化记忆整理器" in system_text:
            analysis_systems.append(system_text)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(memory_payload(), ensure_ascii=False)
                            },
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        main_requests.append(messages)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "我知道现在是什么时间。"}, "finish_reason": "stop"}
                ]
            },
        )

    route_id = "qq:90001:private:real-time-memory"
    with make_client(tmp_path) as client:
        configure_runtime(client)
        client.put("/api/plugins/time_awareness", json={"enabled": True})
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(handler)
        )

        response = client.post(
            "/api/runtime/messages",
            json={"conversation_id": route_id, "text": "现在继续聊。"},
        )
        assert response.status_code == 200
        prompt_text = "\n".join(str(item.get("content", "")) for item in main_requests[0])
        assert prompt_text.count("<Time_Awareness>") == 1
        assert "<real_time_context>" not in prompt_text
        assert "当前现实时间" in prompt_text
        assert "最近消息的现实时间" in prompt_text
        assert "搜索" not in prompt_text

        state = client.app.state.plugin_manager.get_conversation_state(
            "memory_system", route_id
        )
        real_time = state["last_scene"]["story_time"]
        assert real_time != "第三日黄昏"
        assert "UTC" in real_time
        assert state["events"][0]["story_time"] == real_time
        assert "时间感知已启用" in analysis_systems[0]
        assert real_time in analysis_systems[0]


def test_memory_state_isolated_per_chat_record_and_commands_work(tmp_path: Path) -> None:
    route_id = "qq:90001:private:multi-memory"
    with make_client(tmp_path) as client:
        configure_runtime(client)
        runtime = client.app.state.chat_runtime
        first = runtime.create_conversation_record(route_id, "第一条线")
        second = runtime.create_conversation_record(route_id, "第二条线")
        manager = client.app.state.plugin_manager
        record = manager.records["memory_system"]
        context = PluginContext(manager, "memory_system", record.path)

        fixed = record.instance.on_user_message(
            context,
            PluginEvent(
                name="on_user_message",
                conversation_id=route_id,
                text="/记忆固定 玲奈害怕雷声",
                metadata={"record_id": first.id},
            ),
        )
        assert fixed.consume is True
        assert "已固定" in fixed.actions[0].payload["text"]
        assert context.get_conversation_state(first.id)["pinned"][0]["content"] == "玲奈害怕雷声"
        assert context.get_conversation_state(second.id)["pinned"] == []

        overview = record.instance.inspect_conversation_states(context)
        first_item = next(item for item in overview["items"] if item["conversation_id"] == first.id)
        second_item = next(item for item in overview["items"] if item["conversation_id"] == second.id)
        assert first_item["memory_id"] != second_item["memory_id"]

        core = record.instance.on_user_message(
            context,
            PluginEvent(
                name="on_user_message",
                conversation_id=route_id,
                text="/记忆核心 雪音,玲奈",
                metadata={"record_id": second.id},
            ),
        )
        assert "雪音 / 玲奈" in core.actions[0].payload["text"]
        assert context.get_conversation_state(second.id)["lead_overrides"] == ["雪音", "玲奈"]
        assert context.get_conversation_state(first.id)["lead_overrides"] == []

        cleared = record.instance.on_user_message(
            context,
            PluginEvent(
                name="on_user_message",
                conversation_id=route_id,
                text="/记忆清除 确认",
                metadata={"record_id": first.id},
            ),
        )
        assert "已清除" in cleared.actions[0].payload["text"]
        cleared_state = context.get_conversation_state(first.id)
        assert cleared_state["turn"] == 0
        assert cleared_state["pinned"] == []


def test_periodic_audit_recovers_missed_continuity_detail(tmp_path: Path) -> None:
    audit_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal audit_calls
        messages = json.loads(request.content)["messages"]
        system_text = "\n".join(str(item.get("content", "")) for item in messages if item["role"] == "system")
        if "补漏审计器" in system_text:
            audit_calls += 1
            payload = blank_payload()
            payload["characters"] = [
                {
                    "name": "雪音",
                    "cast_role": "main_cast",
                    "relationship_stage": "相识",
                    "current_outfit": "红色围巾",
                    "injuries": [],
                }
            ]
            payload["facts"] = [
                {
                    "subject": "雪音",
                    "category": "appearance",
                    "detail": "雪音当前戴着红色围巾。",
                    "known_by": ["悠", "雪音"],
                    "importance": 2,
                    "keywords": ["红色围巾"],
                    "evidence": "雪音把新换的红色围巾绕紧了一些。",
                }
            ]
            content = json.dumps(payload, ensure_ascii=False)
        elif "结构化记忆整理器" in system_text:
            content = json.dumps(blank_payload(), ensure_ascii=False)
        else:
            content = "雪音把新换的红色围巾绕紧了一些。"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        )

    with make_client(tmp_path) as client:
        configure_runtime(client)
        client.put(
            "/api/plugins/memory_system",
            json={"settings": {"lead_character_names": "玲奈", "detail_audit_interval": 1, "detail_audit_lookback_rounds": 2}},
        )
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(handler)
        )
        response = client.post(
            "/api/runtime/messages",
            json={"conversation_id": "qq:audit", "text": "雪音今天换了什么？"},
        )
        assert response.status_code == 200
        assert audit_calls == 1
        record_id = client.get("/api/runtime/conversations").json()[0]["id"]
        state = client.app.state.plugin_manager.get_conversation_state("memory_system", record_id)
        snow = next(item for item in state["characters"] if item["name"] == "雪音")
        assert snow["current_outfit"] == "红色围巾"
        assert state["facts"][0]["category"] == "appearance"
        assert state["facts"][0]["evidence"].startswith("雪音把新换的")


def test_old_events_are_compacted_without_touching_structured_ledgers(tmp_path: Path) -> None:
    archive_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal archive_calls
        messages = json.loads(request.content)["messages"]
        system_text = "\n".join(str(item.get("content", "")) for item in messages if item["role"] == "system")
        if "档案压缩器" in system_text:
            archive_calls += 1
            content = json.dumps(
                {"summary": "早期十轮探索中，玲奈和悠确认了遗迹入口。", "participants": ["玲奈", "悠"], "keywords": ["遗迹"], "importance": 4},
                ensure_ascii=False,
            )
        else:
            content = json.dumps(blank_payload(), ensure_ascii=False)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        )

    with make_client(tmp_path) as client:
        configure_runtime(client)
        client.put(
            "/api/plugins/memory_system",
            json={"settings": {"active_event_limit": 40, "compact_batch_size": 10, "detail_audit_interval": 0}},
        )
        runtime = client.app.state.chat_runtime
        conversation = runtime.create_conversation_record("qq:compact", "压缩测试")
        manager = client.app.state.plugin_manager
        record = manager.records["memory_system"]
        context = PluginContext(manager, "memory_system", record.path)
        state = {
            "turn": 41,
            "events": [
                {"id": f"event_{index:06d}", "summary": f"探索事件{index}", "turn": index, "importance": 3}
                for index in range(1, 42)
            ],
            "facts": [{"id": "fact_1", "subject": "遗迹", "detail": "入口在北侧", "turn": 3}],
            "counters": {"event": 41},
        }
        context.replace_conversation_state(conversation.id, state)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(handler)
        )
        client.portal.call(
            record.instance.after_model_response,
            context,
            PluginEvent(
                name="after_model_response",
                conversation_id="qq:compact",
                text="继续探索",
                response_text="众人继续前进。",
                metadata={"record_id": conversation.id},
            ),
        )
        saved = context.get_conversation_state(conversation.id)
        assert archive_calls == 1
        assert len(saved["events"]) == 31
        assert saved["archives"][0]["summary"].startswith("早期十轮探索")
        assert saved["facts"][0]["detail"] == "入口在北侧"


def test_core_characters_auto_adapt_to_single_or_multi_cast(tmp_path: Path) -> None:
    def character(name: str, role: str) -> dict:
        return {
            "id": f"char_{name}",
            "name": name,
            "aliases": [],
            "cast_role": role,
            "relationship_stage": "在意",
            "affection": 10,
            "trust": 12,
            "jealousy": 0,
            "current_state": "正常",
            "current_emotion": "平静",
            "current_goal": "继续旅行",
            "current_outfit": "旅行装",
            "injuries": [],
            "milestones": [],
            "last_turn": 3,
        }

    with make_client(tmp_path) as client:
        configure_runtime(client)
        client.put(
            "/api/plugins/memory_system",
            json={"settings": {"lead_character_names": "", "main_cast_names": "", "detail_audit_interval": 0}},
        )
        runtime = client.app.state.chat_runtime
        single = runtime.create_conversation_record("qq:single", "单人")
        multi = runtime.create_conversation_record("qq:multi", "多人")
        manager = client.app.state.plugin_manager
        record = manager.records["memory_system"]
        context = PluginContext(manager, "memory_system", record.path)
        context.replace_conversation_state(single.id, {"turn": 3, "characters": [character("玲奈", "lead")]})
        context.replace_conversation_state(
            multi.id,
            {"turn": 3, "characters": [character("玲奈", "lead"), character("雪音", "main_cast")]},
        )

        single_result = record.instance.before_prompt_compile(
            context,
            PluginEvent(name="before_prompt_compile", text="继续", metadata={"record_id": single.id}),
        )
        multi_result = record.instance.before_prompt_compile(
            context,
            PluginEvent(name="before_prompt_compile", text="继续", metadata={"record_id": multi.id}),
        )
        single_text = single_result.actions[0].payload["content"]
        multi_text = multi_result.actions[0].payload["content"]
        assert "核心角色状态台账" in single_text
        assert "玲奈" in single_text and "雪音" not in single_text
        assert "玲奈" in multi_text and "雪音" in multi_text


def test_memory_state_can_be_inspected_for_visualization(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.put("/api/plugins/memory_system", json={"enabled": True})
        blank = client.get("/api/plugins/memory_system/conversation-states")
        assert blank.status_code == 200
        assert blank.json()["items"] == []
        assert blank.json()["state"]["tables"]["spacetime"]["rows"] == []
        assert blank.json()["state"]["tables"]["characters"]["columns"][0] == "角色名"

        runtime = client.app.state.chat_runtime
        first = runtime.create_conversation_record("qq:chart:first", "图表记录甲")
        second = runtime.create_conversation_record("qq:chart:second", "图表记录乙")
        manager = client.app.state.plugin_manager
        manager.set_conversation_state(
            "memory_system",
            first.id,
            {
                "turn": 7,
                "characters": [{"name": "玲奈", "affection": 24, "trust": 31, "jealousy": -4}],
                "events": [{"summary": "找到钟楼"}],
                "facts": [{"subject": "徽记", "detail": "藏在斗篷内袋"}],
            },
        )
        manager.set_conversation_state("memory_system", second.id, {"turn": 2, "events": []})

        with client.app.state.database.session_factory() as session:
            assert session.get(PluginConversationState, ("memory_system", first.id)) is None
            assert session.get(PluginConversationState, ("memory_system", second.id)) is None

        overview = client.get("/api/plugins/memory_system/conversation-states")
        assert overview.status_code == 200
        assert len(overview.json()["items"]) == 2
        assert overview.json()["selected_id"] == second.id
        assert overview.json()["state"]["turn"] == 2

        selected = client.get(
            "/api/plugins/memory_system/conversation-states",
            params={"conversation_id": first.id},
        )
        assert selected.status_code == 200
        assert selected.json()["selected_id"] == first.id
        assert selected.json()["state"]["characters"][0]["trust"] == 31
        first_summary = next(
            item for item in selected.json()["items"] if item["conversation_id"] == first.id
        )
        assert first_summary["title"] == "图表记录甲"
        second_summary = next(
            item for item in selected.json()["items"] if item["conversation_id"] == second.id
        )
        assert first_summary["memory_id"] != second_summary["memory_id"]

        shared = client.post(
            "/api/plugins/memory_system/admin-actions/bind-memory",
            json={
                "payload": {
                    "conversation_id": second.id,
                    "memory_id": first_summary["memory_id"],
                }
            },
        )
        assert shared.status_code == 200, shared.text
        shared_view = shared.json()["result"]
        assert shared_view["selected_memory_id"] == first_summary["memory_id"]
        assert shared_view["state"]["characters"][0]["trust"] == 31

        missing = client.get(
            "/api/plugins/memory_system/conversation-states",
            params={"conversation_id": "missing"},
        )
        assert missing.status_code == 404


def test_shared_memory_analysis_is_serialized_by_memory_id(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.put("/api/plugins/memory_system", json={"enabled": True})
        runtime = client.app.state.chat_runtime
        first = runtime.create_conversation_record("qq:shared:first", "共享记录甲")
        second = runtime.create_conversation_record("qq:shared:second", "共享记录乙")
        manager = client.app.state.plugin_manager
        overview = manager.inspect_conversation_states("memory_system")
        first_memory_id = next(
            item["memory_id"]
            for item in overview["items"]
            if item["conversation_id"] == first.id
        )
        bound = client.post(
            "/api/plugins/memory_system/admin-actions/bind-memory",
            json={
                "payload": {
                    "conversation_id": second.id,
                    "memory_id": first_memory_id,
                }
            },
        )
        assert bound.status_code == 200, bound.text

        active = 0
        max_active = 0

        async def analyze(_plugin_id, _conversation_id, messages, _max_tokens, _temperature):
            nonlocal active, max_active
            marker = "甲" if "甲消息" in messages[-1]["content"] else "乙"
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            return json.dumps(
                {
                    "scene": {"summary": f"{marker}事件"},
                    "events": [{"summary": f"{marker}事件", "importance": 3}],
                },
                ensure_ascii=False,
            )

        manager.analysis_sink = analyze
        record = manager.records["memory_system"]
        context = PluginContext(manager, "memory_system", record.path)

        async def run_both():
            await asyncio.gather(
                record.instance.after_model_response(
                    context,
                    PluginEvent(
                        name="after_model_response",
                        text="甲消息",
                        response_text="甲回复",
                        metadata={"record_id": first.id},
                    ),
                ),
                record.instance.after_model_response(
                    context,
                    PluginEvent(
                        name="after_model_response",
                        text="乙消息",
                        response_text="乙回复",
                        metadata={"record_id": second.id},
                    ),
                ),
            )

        client.portal.call(run_both)

        state = manager.get_conversation_state("memory_system", first.id)
        assert max_active == 1
        assert state["turn"] == 2
        assert {item["summary"] for item in state["events"]} == {"甲事件", "乙事件"}
        assert manager.get_conversation_state("memory_system", second.id) == state
