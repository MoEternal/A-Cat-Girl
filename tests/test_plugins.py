from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from catgirl.main import create_app
from catgirl.database import PluginInstallation
from catgirl.media import MediaValidationError
from catgirl.plugins import PluginEvent
from catgirl.plugins.context import PluginContext
from catgirl.plugins.types import PluginManifest


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "data", allow_unconfigured_management=True))


def plugin_zip(manifest: dict, source: str, extra: dict[str, bytes] | None = None) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("plugin.py", source)
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return output.getvalue()


def test_manifest_rejects_unsafe_ids_entrypoints_and_unknown_hooks() -> None:
    base = {
        "id": "valid_plugin",
        "name": "测试插件",
        "version": "1.0.0",
        "entrypoint": "plugin.py",
        "hooks": [],
    }
    assert PluginManifest.model_validate(base).id == "valid_plugin"

    with pytest.raises(ValidationError):
        PluginManifest.model_validate({**base, "id": "../escape"})
    with pytest.raises(ValidationError):
        PluginManifest.model_validate({**base, "entrypoint": "../plugin.py"})
    with pytest.raises(ValidationError):
        PluginManifest.model_validate({**base, "admin_ui": "../ui/index.html"})
    with pytest.raises(ValidationError):
        PluginManifest.model_validate({**base, "hooks": ["on_everything"]})


def test_zip_installer_rejects_path_traversal(tmp_path: Path) -> None:
    manifest = {
        "id": "unsafe_plugin",
        "name": "不安全插件",
        "version": "1.0.0",
        "entrypoint": "plugin.py",
        "hooks": [],
    }
    archive = plugin_zip(manifest, "plugin = object()", {"../escaped.txt": b"no"})
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/plugins/install",
            content=archive,
            headers={"Content-Type": "application/zip"},
        )

    assert response.status_code == 400
    assert "越界路径" in response.json()["detail"]
    assert not (tmp_path / "escaped.txt").exists()

    duplicate = BytesIO()
    with ZipFile(duplicate, "w", ZIP_DEFLATED) as archive_file:
        archive_file.writestr("plugin.json", json.dumps(manifest, ensure_ascii=False))
        archive_file.writestr("plugin.py", "plugin = object()")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive_file.writestr("plugin.py", "plugin = object()")
    with make_client(tmp_path / "duplicate") as client:
        response = client.post(
            "/api/plugins/install",
            content=duplicate.getvalue(),
            headers={"Content-Type": "application/zip"},
        )
    assert response.status_code == 400
    assert "重复路径" in response.json()["detail"]


def test_zip_installer_rejects_a_plugin_for_a_newer_app_version(tmp_path: Path) -> None:
    manifest = {
        "id": "future_plugin",
        "name": "未来插件",
        "version": "1.0.0",
        "min_app_version": "99.0.0",
        "entrypoint": "plugin.py",
        "hooks": [],
    }
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/plugins/install",
            content=plugin_zip(manifest, "plugin = object()"),
            headers={"Content-Type": "application/zip"},
        )

        assert response.status_code == 400
        assert "需要一只猫娘 99.0.0" in response.json()["detail"]
        assert not (tmp_path / "data" / "plugins" / "future_plugin").exists()


def test_third_party_plugin_lifecycle_and_public_hooks(tmp_path: Path) -> None:
    manifest = {
        "id": "hello_plugin",
        "name": "问候插件",
        "version": "1.2.0",
        "description": "测试公开插件 API",
        "entrypoint": "plugin.py",
        "author": "test",
        "default_enabled": False,
        "permissions": ["message.send.text", "state.persist"],
        "hooks": ["on_startup", "on_user_message"],
        "settings_schema": {
            "type": "object",
            "properties": {
                "greeting": {"type": "string", "title": "问候", "default": "你好"}
            },
        },
    }
    source = """
from catgirl.plugins import PluginAction, PluginResult

class HelloPlugin:
    def on_startup(self, context, event):
        context.patch_state(starts=int(context.state.get('starts', 0)) + 1)

    def on_user_message(self, context, event):
        return PluginResult(actions=[PluginAction(kind='send_text', payload={
            'conversation_id': event.conversation_id,
            'text': context.settings['greeting'] + ' ' + event.text,
        })])

plugin = HelloPlugin()
"""
    with make_client(tmp_path) as client:
        installed = client.post(
            "/api/plugins/install",
            content=plugin_zip(manifest, source),
            headers={"Content-Type": "application/zip"},
        )
        assert installed.status_code == 201
        assert installed.json()["enabled"] is False

        enabled = client.put(
            "/api/plugins/hello_plugin",
            json={"enabled": True, "settings": {"greeting": "收到"}},
        )
        assert enabled.status_code == 200
        assert enabled.json()["loaded"] is True

        manager = client.app.state.plugin_manager
        assert manager.get_state("hello_plugin")["starts"] == 1
        result = client.portal.call(
            manager.dispatch,
            "on_user_message",
            PluginEvent(name="on_user_message", conversation_id="qq:1", text="测试"),
        )
        assert result.actions[0].payload["text"] == "收到 测试"

        reloaded = client.post("/api/plugins/hello_plugin/reload")
        assert reloaded.status_code == 200
        assert manager.get_state("hello_plugin")["starts"] == 2
        built_ins = [item for item in client.get("/api/plugins").json() if item["built_in"]]
        assert all(item["loaded"] for item in built_ins if item["enabled"])

        removed = client.delete("/api/plugins/hello_plugin")
        assert removed.status_code == 204
        assert all(item["id"] != "hello_plugin" for item in client.get("/api/plugins").json())


def test_plugin_admin_page_assets_and_actions_are_scoped(tmp_path: Path) -> None:
    manifest = {
        "id": "admin_page_plugin",
        "name": "管理页面插件",
        "version": "1.0.0",
        "entrypoint": "plugin.py",
        "admin_ui": "ui/index.html",
        "default_enabled": True,
        "hooks": [],
    }
    source = """
class AdminPagePlugin:
    def admin_action(self, context, action, payload):
        return {'action': action, 'payload': payload}

plugin = AdminPagePlugin()
"""
    with make_client(tmp_path) as client:
        installed = client.post(
            "/api/plugins/install",
            content=plugin_zip(
                manifest,
                source,
                {
                    "ui/index.html": b"<!doctype html><title>Private admin</title>",
                    "ui/app.js": b"document.body.dataset.ready = '1'",
                },
            ),
            headers={"Content-Type": "application/zip"},
        )
        assert installed.status_code == 201, installed.text
        assert installed.json()["admin_ui"] == "ui/index.html"

        page = client.get("/api/plugins/admin_page_plugin/assets/ui/index.html")
        assert page.status_code == 200
        assert "Private admin" in page.text
        assert client.get("/api/plugins/admin_page_plugin/assets/plugin.py").status_code == 404

        action = client.post(
            "/api/plugins/admin_page_plugin/admin-actions/inspect",
            json={"payload": {"value": 7}},
        )
        assert action.status_code == 200, action.text
        assert action.json()["result"] == {
            "action": "inspect",
            "payload": {"value": 7},
        }


def test_regex_filter_starts_with_thinking_rule_and_applies_independent_scopes(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        plugin = next(item for item in client.get("/api/plugins").json() if item["id"] == "regex_filter")
        assert plugin["enabled"] is True
        assert plugin["loaded"] is True
        assert plugin["hooks"] == ["before_response_split", "before_send"]
        assert client.get("/api/plugins/regex_filter/state").json()["state"] == {
            "global_rules": [
                {
                    "id": "hide-thinking",
                    "name": "隐藏 Thinking",
                    "enabled": True,
                    "pattern": r"<\s*thinking\b[^>]*>.*?<\s*/\s*thinking\s*>",
                    "replacement": "",
                    "flags": "is",
                }
            ],
            "character_rules": {},
        }

        manager = client.app.state.plugin_manager
        response_text = "<Thinking>内部|||推理</Thinking>可见|||回复"
        protected = client.portal.call(
            manager.dispatch,
            "before_response_split",
            PluginEvent(
                name="before_response_split",
                response_text=response_text,
                metadata={"delimiter": "|||"},
            ),
        )
        assert protected.metadata["regex_filter"]["protected_delimiter_offsets"] == [
            response_text.index("|||")
        ]

        hidden = client.portal.call(
            manager.dispatch,
            "before_send",
            PluginEvent(
                name="before_send",
                response_text="<Thinking>内部推理</Thinking>可见回复",
            ),
        )
        assert hidden.metadata["outbound_text"] == "可见回复"

        character = client.get("/api/characters").json()[0]
        other_character = client.post(
            "/api/characters",
            json={"name": "另一角色卡"},
        ).json()
        state = {
            "global_rules": [
                {
                    "id": "global-enabled",
                    "name": "全局启用",
                    "enabled": True,
                    "pattern": "(secret)",
                    "replacement": "[$1]",
                    "flags": "i",
                },
                {
                    "id": "global-disabled",
                    "name": "全局关闭",
                    "enabled": False,
                    "pattern": "visible",
                    "replacement": "hidden",
                    "flags": "",
                },
            ],
            "character_rules": {
                character["id"]: [
                    {
                        "id": "character-enabled",
                        "name": "角色启用",
                        "enabled": True,
                        "pattern": "cat",
                        "replacement": "role",
                        "flags": "",
                    }
                ]
            },
        }
        updated = client.put("/api/plugins/regex_filter/state", json={"state": state})
        assert updated.status_code == 200, updated.text

        matching = client.portal.call(
            manager.dispatch,
            "before_send",
            PluginEvent(
                name="before_send",
                response_text="SECRET visible cat",
                metadata={"character_id": character["id"]},
            ),
        )
        other = client.portal.call(
            manager.dispatch,
            "before_send",
            PluginEvent(
                name="before_send",
                response_text="SECRET visible cat",
                metadata={"character_id": other_character["id"]},
            ),
        )

        assert matching.metadata["outbound_text"] == "[SECRET] visible role"
        assert matching.metadata["regex_filter"]["applied_rule_ids"] == [
            "global-enabled",
            "character-enabled",
        ]
        assert other.metadata["outbound_text"] == "[SECRET] visible cat"

        state["global_rules"][0]["pattern"] = "(unclosed"
        invalid = client.put("/api/plugins/regex_filter/state", json={"state": state})
        assert invalid.status_code == 400
        assert "全局启用" in invalid.json()["detail"]


def test_builtin_plugin_upgrade_migrates_old_defaults_without_losing_custom_state(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        manager = client.app.state.plugin_manager
        with client.app.state.database.session_factory() as session:
            proactive = session.get(PluginInstallation, "proactive_reply")
            proactive.version = "1.0.1"
            proactive.settings = {
                **proactive.settings,
                "min_minutes": 37,
                "first_prompt": "{{user}}已经有一段时间没有说话。请自然地主动开启话题，可以表达好奇、关心或分享此刻想说的事，不要提及这条系统提示。",
                "second_prompt": "{{user}}在你主动联系后仍没有回复。请更明显地表达不满、担心或带有人设特点的抱怨，但不要提及这条系统提示。",
            }
            segmented = session.get(PluginInstallation, "segmented_reply")
            segmented.version = "1.0.1"
            segmented.settings = {
                **segmented.settings,
                "prompt": "模拟真人聊天，在自然需要连续发送多条消息时使用 ||| 分隔回复（代替换行符）。",
            }
            time_awareness = session.get(PluginInstallation, "time_awareness")
            time_awareness.settings = {
                **time_awareness.settings,
                "prompt": "<time_awareness>\n自定义时间规则。\n</time_awareness>",
            }
            web_search = session.get(PluginInstallation, "web_search")
            web_search.settings = {
                **web_search.settings,
                "search_model_prompt": (
                    "<Search_Model>\n"
                    "# 网络检索\n"
                    "- 你是独立的联网搜索模型，只负责检索和整理可核验资料，不代替聊天角色回答用户。\n"
                    "- 当前现实时间：{{current_time}}\n"
                    "- 本次查询：{{query}}\n\n"
                    "## 时间规则\n"
                    "- 必须以当前现实时间解释\"今天\"、\"昨天\"、\"最近\"等相对时间，不得把搜索引擎收录时间当成新闻发生时间。\n"
                    "- 用户询问\"今天\"时，优先且仅将当前本地自然日发布的可靠报道列为今日新闻；没有可靠的当日结果就明确说明。\n"
                    "- 严格区分报道发布时间、事件发生时间和数据发布时间。今天发布但描述昨天事件的内容，必须明确标注为\"今天报道、事件发生于昨天\"，不能直接说成今天发生。\n"
                    "- 旧事件出现当日新进展时，只把新进展归为今天，并注明原事件发生时间。\n\n"
                    "## 结果要求\n"
                    "- 每条结果提供：标题、来源、原始链接、报道发布时间、事件或数据对应时间、摘要。\n"
                    "- 优先政府机构、官方公告、通讯社、主流媒体和事件直接相关方；交叉核验关键事实。\n"
                    "- 不确定的日期或事实必须明确标注，不得猜测、补写或把旧闻包装成最新消息。\n"
                    "- 只返回检索材料，不使用聊天角色口吻。\n"
                    "</Search_Model>"
                ),
                "timeout_seconds": 15,
            }
            good_night = session.get(PluginInstallation, "good_night")
            good_night.version = "1.0.1"
            good_night.settings = {
                **good_night.settings,
                "wake_greeting_prompt": "你刚刚睡醒。请按照当前人设自然地向用户问候，不要提及这条系统提示。",
                "pending_reply_prompt": "以下是用户在你休息期间发来的消息。请结合时间顺序统一回复，不要声称自己实时看到了这些消息。",
            }
            regex_filter = session.get(PluginInstallation, "regex_filter")
            regex_filter.version = "1.0.1"
            regex_filter.state = {
                "global_rules": [
                    {
                        "id": "custom-rule",
                        "name": "用户规则",
                        "enabled": True,
                        "pattern": "foo",
                        "replacement": "bar",
                        "flags": "",
                    }
                ],
                "character_rules": {"character-1": []},
            }
            sticker = session.get(PluginInstallation, "sticker_reply")
            sticker.settings = {
                **sticker.settings,
                "positive_categories": "happy,like,shy,meow,color,fool,see,surprised,morning",
                "max_asset_bytes": 1572864,
            }
            sticker.settings.pop("max_asset_mb", None)
            session.commit()

        manager.discover()

        settings = manager.get_settings("proactive_reply")
        assert settings["min_minutes"] == 37
        assert settings["first_prompt"] == (
            "{{user}}已经有一段时间没有说话。请自然地主动开启话题，可以表达好奇、关心或分享此刻想说的事。"
        )
        assert settings["second_prompt"] == (
            "{{user}}在你主动联系后仍没有回复。请更明显地表达不满、担心或带有人设特点的抱怨示。"
        )
        assert manager.get_settings("segmented_reply")["prompt"] == (
            manager.records["segmented_reply"].manifest.default_settings()["prompt"]
        )
        assert manager.get_settings("time_awareness")["prompt"] == (
            "<Time_Awareness>\n自定义时间规则。\n</Time_Awareness>"
        )
        search_settings = manager.get_settings("web_search")
        assert search_settings["timeout_seconds"] == 60
        assert search_settings["search_model_prompt"] == (
            manager.records["web_search"].manifest.default_settings()["search_model_prompt"]
        )
        assert manager.get_settings("good_night")["wake_greeting_prompt"] == (
            "你刚刚睡醒。请根据自身人设自然地向{{user}}问候。"
        )
        assert manager.get_settings("good_night")["pending_reply_prompt"] == (
            "以下是{{user}}在你休息期间发来的消息。结合时间顺序统一回复。"
        )
        state = manager.get_state("regex_filter")
        assert [item["id"] for item in state["global_rules"]] == [
            "custom-rule",
            "hide-thinking",
        ]
        assert state["character_rules"] == {"character-1": []}
        sticker_settings = manager.get_settings("sticker_reply")
        assert sticker_settings["positive_categories"] == (
            "happy,like,confused,proud,thinking,neutral,cute,smug,wink,tease,cool"
        )
        assert sticker_settings["max_asset_mb"] == 1.5
        assert "max_asset_bytes" not in sticker_settings


def test_builtin_plugin_upgrade_preserves_custom_prompts(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        manager = client.app.state.plugin_manager
        with client.app.state.database.session_factory() as session:
            proactive = session.get(PluginInstallation, "proactive_reply")
            proactive.version = "1.0.1"
            proactive.settings = {
                **proactive.settings,
                "first_prompt": "我的首次提示",
                "second_prompt": "我的第二次提示",
            }
            web_search = session.get(PluginInstallation, "web_search")
            web_search.settings = {
                **web_search.settings,
                "search_model_prompt": "我的搜索模型提示",
            }
            session.commit()

        manager.discover()

        settings = manager.get_settings("proactive_reply")
        assert settings["first_prompt"] == "我的首次提示"
        assert settings["second_prompt"] == "我的第二次提示"
        assert manager.get_settings("web_search")["search_model_prompt"] == "我的搜索模型提示"


def test_builtin_defaults_and_plugin_order_are_persisted(tmp_path: Path) -> None:
    expected_enabled = {
        "recall",
        "regex_filter",
        "reply_merge",
        "segmented_reply",
        "time_awareness",
    }
    with make_client(tmp_path) as client:
        plugins = client.get("/api/plugins").json()
        assert {item["id"] for item in plugins if item["enabled"]} == expected_enabled
        manager = client.app.state.plugin_manager
        assert {
            plugin_id
            for plugin_id, record in manager.records.items()
            if record.manifest.default_enabled
        } == expected_enabled
        memory = next(item for item in plugins if item["id"] == "memory_system")
        assert memory["name"] == "记忆系统（测试）"
        assert memory["version"] == "0.1.0 beta"
        versions = {item["id"]: item["version"] for item in plugins}
        assert versions["good_night"] == "1.0.0"
        assert versions["proactive_reply"] == "1.0.0"
        assert versions["regex_filter"] == "1.0.0"
        assert versions["segmented_reply"] == "1.0.0"
        assert versions["reply_merge"] == "1.0.0"
        assert [item["id"] for item in plugins] == [
            "regex_filter",
            "recall",
            "memory_system",
            "reply_merge",
            "segmented_reply",
            "proactive_reply",
            "sticker_reply",
            "time_awareness",
            "good_night",
            "web_search",
        ]
        reply_merge = next(item for item in plugins if item["id"] == "reply_merge")
        batch_delay = reply_merge["settings_schema"]["properties"]["message_batch_delay"]
        assert reply_merge["enabled"] is True
        assert reply_merge["description"] == (
            "短时间内连续发送的消息会自动合并发送给AI，实现用户端可分段回复。设为 0 秒时不合并。"
        )
        assert batch_delay["description"] == (
            "每次发送新消息后自动挂起等待；期间的消息会合并为一轮。设为 0 时逐条立即回复。"
        )
        assert batch_delay["default"] == 4
        assert batch_delay["minimum"] == 0
        assert batch_delay["maximum"] == 60
        sticker = next(item for item in plugins if item["id"] == "sticker_reply")
        assert sticker["description"] == "自动按情绪分类向模型提供可选表情。（需自行添加表情图片）"
        assert sticker["settings_schema"]["properties"]["positive_categories"]["default"] == (
            "happy,like,confused,proud,thinking,neutral,cute,smug,wink,tease,cool"
        )
        asset_limit = sticker["settings_schema"]["properties"]["max_asset_mb"]
        assert asset_limit["type"] == "number"
        assert asset_limit["title"] == "单个表情大小上限（MB）"
        assert asset_limit["default"] == 16
        sleep = next(item for item in plugins if item["id"] == "good_night")
        assert sleep["name"] == "睡眠模拟"
        assert sleep["description"] == (
            "双方互道休眠关键词后（晚安/午安）后AI进入休眠，会暂存消息并在醒来时统一生成问候与回复。"
        )
        sleep_settings = sleep["settings_schema"]["properties"]
        assert sleep_settings["keywords"]["title"] == "休眠关键词"
        assert sleep_settings["wake_greeting_prompt"]["default"] == (
            "你刚刚睡醒。请根据自身人设自然地向{{user}}问候。"
        )
        assert sleep_settings["pending_reply_prompt"]["default"] == (
            "以下是{{user}}在你休息期间发来的消息。结合时间顺序统一回复。"
        )
        recall = next(item for item in plugins if item["id"] == "recall")
        assert recall["name"] == "同步撤回"
        assert recall["settings_schema"]["properties"]["recall_window_seconds"]["description"] == (
            "在此时间范围内用户撤回消息后，AI将同步撤回；超过窗口时保持静默；非QQ群聊管理员上限为 120 秒。"
        )
        segmented_prompt = next(
            item for item in plugins if item["id"] == "segmented_reply"
        )["settings_schema"]["properties"]["prompt"]["default"]
        time_prompt = next(item for item in plugins if item["id"] == "time_awareness")[
            "settings_schema"
        ]["properties"]["prompt"]["default"]
        web_prompt = next(item for item in plugins if item["id"] == "web_search")[
            "settings_schema"
        ]["properties"]["prompt"]["default"]
        assert segmented_prompt.count("<Segmented_Reply>") == 1
        assert segmented_prompt.count("</Segmented_Reply>") == 1
        assert time_prompt.count("<Time_Awareness>") == 1
        assert time_prompt.count("</Time_Awareness>") == 1
        assert web_prompt.count("<Web_Search>") == 1
        assert web_prompt.count("</Web_Search>") == 1

        reversed_ids = [item["id"] for item in reversed(plugins)]
        response = client.put("/api/plugins/order", json={"plugin_ids": reversed_ids})
        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()] == reversed_ids
        assert [item["position"] for item in response.json()] == list(
            range(1, len(reversed_ids) + 1)
        )
        assert [item["id"] for item in client.get("/api/plugins").json()] == reversed_ids

        invalid = client.put("/api/plugins/order", json={"plugin_ids": reversed_ids[:-1]})
        assert invalid.status_code == 400


def test_builtin_discovery_preserves_existing_enabled_choices(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        manager = client.app.state.plugin_manager
        with client.app.state.database.session_factory() as session:
            session.get(PluginInstallation, "memory_system").enabled = True
            session.get(PluginInstallation, "recall").enabled = False
            session.commit()

        manager.discover()

        enabled = {
            item["id"]
            for item in client.get("/api/plugins").json()
            if item["enabled"]
        }
        assert "memory_system" in enabled
        assert "recall" not in enabled


def test_proactive_reply_state_machine_and_sleep_pause(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        manager = client.app.state.plugin_manager
        client.portal.call(manager.configure, "proactive_reply", True)
        record = manager.records["proactive_reply"]
        context = PluginContext(manager, "proactive_reply", record.path)
        now = datetime.now(timezone.utc)
        record.instance.on_user_message(
            context,
            PluginEvent(name="on_user_message", conversation_id="qq:active", text="在吗", created_at=now),
        )
        state = context.state
        state["conversations"]["qq:active"]["next_due_at"] = (now - timedelta(seconds=1)).isoformat()
        context.replace_state(state)

        result = record.instance.check_idle(context)
        assert [action.kind for action in result.actions] == ["request_generation"]
        assert result.actions[0].payload["provider_policy"] == "selected_only"
        assert result.actions[0].payload["history_policy"] == "temporary_prompt"
        assert context.state["conversations"]["qq:active"]["proactive_count"] == 1

        state = context.state
        state["conversations"]["qq:active"]["next_due_at"] = (now - timedelta(seconds=1)).isoformat()
        context.replace_state(state)
        context.set_runtime_value("conversation:qq:active:sleeping", True)
        assert record.instance.check_idle(context).actions == []


def test_good_night_buffers_safe_refs_and_wakes_in_order(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        manager = client.app.state.plugin_manager
        client.portal.call(manager.configure, "good_night", True)
        record = manager.records["good_night"]
        context = PluginContext(manager, "good_night", record.path)
        now = datetime.now(timezone.utc)
        sleeping = record.instance.after_model_response(
            context,
            PluginEvent(
                name="after_model_response",
                conversation_id="qq:sleep",
                text="那我睡了，晚安",
                response_text="晚安，明天见",
                created_at=now,
            ),
        )
        assert sleeping.actions[0].kind == "sleep_started"
        assert context.get_runtime_value("conversation:qq:sleep:sleeping") is True

        buffered = record.instance.on_user_message(
            context,
            PluginEvent(
                name="on_user_message",
                conversation_id="qq:sleep",
                text="半夜想起一件事",
                media=[{"kind": "image", "ref": "received/one.jpg", "name": "one.jpg"}],
                created_at=now + timedelta(minutes=30),
            ),
        )
        assert buffered.consume is True
        pending = context.state["conversations"]["qq:sleep"]["pending_messages"]
        assert pending[0]["media"][0]["ref"] == "received/one.jpg"

        with pytest.raises(MediaValidationError):
            record.instance.on_user_message(
                context,
                PluginEvent(
                    name="on_user_message",
                    conversation_id="qq:sleep",
                    text="data:image/jpeg;base64," + "A" * 100,
                    created_at=now + timedelta(minutes=31),
                ),
            )

        state = context.state
        state["conversations"]["qq:sleep"]["wake_at"] = (now - timedelta(seconds=1)).isoformat()
        context.replace_state(state)
        result = record.instance.check_wake(context)
        assert [action.payload["purpose"] for action in result.actions] == [
            "wake_greeting",
            "sleep_pending_reply",
        ]
        assert context.state["conversations"]["qq:sleep"]["pending_messages"] == []
        assert context.get_runtime_value("conversation:qq:sleep:sleeping") is False


def test_sticker_catalog_matches_empty_public_asset_skeleton() -> None:
    plugin_root = Path(__file__).parents[1] / "plugins" / "sticker_reply"
    expected = [
        "happy", "like", "shy", "angry", "sad", "cry", "surprised", "confused",
        "embarrassed", "excited", "scared", "tired", "bored", "disgusted", "proud",
        "sorry", "thankful", "thinking", "neutral", "cute", "smug", "wink", "panic",
        "sleep", "tease", "tsundere", "cool", "evil", "other",
    ]
    metadata = json.loads((plugin_root / "memes_data.json").read_text("utf-8"))
    assert list(metadata) == expected
    assert sorted(item.name for item in (plugin_root / "assets").iterdir() if item.is_dir()) == sorted(expected)
    assert not [
        item
        for item in (plugin_root / "assets").rglob("*")
        if item.is_file() and item.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    ]
    assert all((plugin_root / "assets" / category / ".asset-index").is_file() for category in expected)


def test_sticker_plugin_resolves_triggerable_category_and_excludes_other(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        manager = client.app.state.plugin_manager
        client.portal.call(manager.configure, "sticker_reply", True)
        record = manager.records["sticker_reply"]
        plugin_root = tmp_path / "sticker-fixture"
        (plugin_root / "assets" / "happy").mkdir(parents=True)
        (plugin_root / "assets" / "other").mkdir(parents=True)
        (plugin_root / "memes_data.json").write_text(
            (record.path / "memes_data.json").read_text("utf-8"),
            "utf-8",
        )
        happy_asset = plugin_root / "assets" / "happy" / "happy.png"
        other_asset = plugin_root / "assets" / "other" / "other.png"
        happy_asset.write_bytes(b"test-happy-image")
        other_asset.write_bytes(b"test-other-image")
        context = PluginContext(manager, "sticker_reply", plugin_root)

        parsed = record.instance.after_model_response(
            context,
            PluginEvent(
                name="after_model_response",
                conversation_id="qq:sticker",
                response_text="第一段|||第二段",
                metadata={"sticker_category": "happy"},
            ),
        )
        action = parsed.actions[0]
        assert action.kind == "replace_response"
        assert action.payload["sticker_category"] == "happy"
        assert "text_segments" not in action.payload
        assert Path(action.payload["asset_ref"]).is_file()
        assert "base64" not in json.dumps(action.payload, ensure_ascii=False)

        ignored = record.instance.after_model_response(
            context,
            PluginEvent(
                name="after_model_response",
                conversation_id="qq:sticker",
                metadata={"sticker_category": "other"},
            ),
        )
        assert ignored.actions[0].kind == "replace_response"
        assert "asset_ref" not in ignored.actions[0].payload

        preview = record.instance.before_prompt_compile(
            context,
            PluginEvent(
                name="before_prompt_compile",
                conversation_id="qq:sticker",
                metadata={"preview": True},
            ),
        )
        prompt = preview.actions[0].payload["content"]
        assert "- happy: 高兴" in prompt
        assert "- other:" not in prompt

        direct = record.instance.on_user_message(
            context,
            PluginEvent(name="on_user_message", conversation_id="qq:sticker", text="发个表情包"),
        )
        assert direct.consume is True
        assert direct.actions[0].kind == "send_image"
        assert Path(direct.actions[0].payload["asset_ref"]) == happy_asset


def test_sticker_plugin_compresses_oversized_asset_in_place_before_sending(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        manager = client.app.state.plugin_manager
        client.portal.call(
            manager.configure,
            "sticker_reply",
            True,
            {"max_asset_mb": 0.1, "probability": 0},
        )
        record = manager.records["sticker_reply"]
        plugin_root = tmp_path / "sticker-compression"
        asset_dir = plugin_root / "assets" / "happy"
        asset_dir.mkdir(parents=True)
        (plugin_root / "memes_data.json").write_text(
            (record.path / "memes_data.json").read_text("utf-8"),
            "utf-8",
        )
        asset = asset_dir / "large.png"
        Image.effect_noise((768, 768), 100).convert("RGB").save(asset, format="PNG")
        original_size = asset.stat().st_size
        assert original_size > 0.1 * 1024 * 1024

        context = PluginContext(manager, "sticker_reply", plugin_root)
        result = record.instance.after_model_response(
            context,
            PluginEvent(
                name="after_model_response",
                conversation_id="qq:sticker-compression",
                metadata={"sticker_category": "happy"},
            ),
        )

        assert Path(result.actions[0].payload["asset_ref"]) == asset
        assert asset.stat().st_size <= round(0.1 * 1024 * 1024)
        assert asset.stat().st_size < original_size
        with Image.open(asset) as compressed:
            compressed.verify()

        gif_asset = asset_dir / "large.gif"
        frames = [
            Image.effect_noise((256, 256), 100 + index).convert("L")
            for index in range(12)
        ]
        frames[0].save(
            gif_asset,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=80,
            loop=0,
        )
        gif_original_size = gif_asset.stat().st_size
        assert gif_original_size > 0.1 * 1024 * 1024

        assert record.instance._prepare_asset(context, gif_asset) == gif_asset
        assert gif_asset.stat().st_size <= round(0.1 * 1024 * 1024)
        assert gif_asset.stat().st_size < gif_original_size
        with Image.open(gif_asset) as compressed_gif:
            compressed_gif.verify()


def test_sticker_admin_action_opens_only_its_assets_folder(
    tmp_path: Path,
    monkeypatch,
) -> None:
    opened: list[str] = []
    if os.name == "nt":
        monkeypatch.setattr(os, "startfile", lambda path: opened.append(str(path)))
    else:
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda command: opened.append(str(command[1])),
        )
    with make_client(tmp_path) as client:
        manager = client.app.state.plugin_manager
        client.portal.call(manager.configure, "sticker_reply", True)
        result = client.portal.call(
            manager.admin_action,
            "sticker_reply",
            "open-assets-folder",
            {},
        )
        expected = (manager.records["sticker_reply"].path / "assets").resolve()
        assert result == {"opened": True}
        assert opened == [str(expected)]
        assert manager.records["sticker_reply"].path in expected.parents


def test_time_awareness_injects_only_real_time_context(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        manager = client.app.state.plugin_manager
        listed = next(item for item in client.get("/api/plugins").json() if item["id"] == "time_awareness")
        prompt_definition = listed["settings_schema"]["properties"]["prompt"]
        assert prompt_definition["title"] == "时间感知提示词"
        assert prompt_definition["format"] == "textarea"
        response = client.put(
            "/api/plugins/time_awareness",
            json={"settings": {"prompt": "自定义时间感知规则。"}},
        )
        assert response.status_code == 200
        record = manager.records["time_awareness"]
        context = PluginContext(manager, "time_awareness", record.path)
        result = record.instance.before_prompt_compile(
            context,
            PluginEvent(
                name="before_prompt_compile",
                conversation_id="qq:time",
                created_at=datetime(2026, 7, 26, 12, 34, tzinfo=timezone.utc),
            ),
        )

        assert record.manifest.hooks == ["before_prompt_compile"]
        assert set(record.manifest.permissions) == {"prompt.inject", "message.history.read"}
        assert result.actions[0].kind == "prompt_addition"
        content = result.actions[0].payload["content"]
        assert content.count("<Time_Awareness>") == 0
        assert "<real_time_context>" not in content
        assert "当前现实时间" in content
        assert "搜索" not in content
        assert "自定义时间感知规则。" in content
        assert "长期记忆必须按本轮现实时间记录" not in content
        assert result.metadata["time_awareness"]["display"] in content
        assert "UTC" in result.metadata["time_awareness"]["memory_time"]


def test_web_search_has_requested_sources_and_encrypts_api_keys(tmp_path: Path) -> None:
    serp_secret = "serp-secret-value"
    model_secret = "search-model-secret-value"
    with make_client(tmp_path) as client:
        plugin = next(item for item in client.get("/api/plugins").json() if item["id"] == "web_search")
        engine = plugin["settings_schema"]["properties"]["engine"]
        prompt_definition = plugin["settings_schema"]["properties"]["prompt"]
        search_prompt_definition = plugin["settings_schema"]["properties"]["search_model_prompt"]
        assert engine["enum"] == ["model", "sear", "duckduckgo", "google", "serp", "bing"]
        assert prompt_definition["title"] == "网络搜索提示词"
        assert prompt_definition["format"] == "textarea"
        assert search_prompt_definition["title"] == "搜索模型内置提示词"
        assert search_prompt_definition["format"] == "textarea"
        assert search_prompt_definition["default"].startswith("<RealTime_Search>\n")
        assert search_prompt_definition["default"].endswith("\n</RealTime_Search>")
        assert "{{current_time}}" in search_prompt_definition["default"]
        assert "{{result_count}}" not in search_prompt_definition["default"]
        assert "报道发布时间、事件发生时间和数据发布时间" in search_prompt_definition["default"]
        assert "交叉核验" not in search_prompt_definition["default"]
        assert "不代入个人理解、主观判断" in search_prompt_definition["default"]
        model_name_definition = plugin["settings_schema"]["properties"]["search_model_name"]
        assert model_name_definition["description"] == (
            "填写接入的搜索模型名称。（如 gemini-3.1-pro-preview-search 等自带搜索工具的模型）"
        )
        result_count = plugin["settings_schema"]["properties"]["result_count"]
        timeout = plugin["settings_schema"]["properties"]["timeout_seconds"]
        assert result_count["description"] == (
            "交给模型参考的搜索结果数量（使用自定义搜索时不生效）。"
        )
        assert timeout["default"] == 60
        assert timeout["maximum"] == 120
        assert plugin["hooks"] == ["before_prompt_compile", "transform_model_response"]
        assert plugin["enabled"] is False

        updated = client.put(
            "/api/plugins/web_search",
            json={
                "enabled": True,
                "settings": {
                    "prompt": "只在需要最新资料时输出搜索标签。",
                    "engine": "model",
                    "serp_api_key": serp_secret,
                    "search_model_api_key": model_secret,
                }
            },
        ).json()
        assert updated["settings"]["serp_api_key"] == ""
        assert updated["settings"]["search_model_api_key"] == ""
        assert updated["secret_settings_configured"]["serp_api_key"] is True
        assert updated["secret_settings_configured"]["search_model_api_key"] is True
        assert serp_secret not in json.dumps(updated, ensure_ascii=False)
        assert model_secret not in json.dumps(updated, ensure_ascii=False)
        settings = client.app.state.plugin_manager.get_settings("web_search")
        assert settings["prompt"] == "只在需要最新资料时输出搜索标签。"
        assert settings["serp_api_key"] == serp_secret
        assert settings["search_model_api_key"] == model_secret
        manager = client.app.state.plugin_manager
        record = manager.records["web_search"]
        context = PluginContext(manager, "web_search", record.path)
        prompt_result = record.instance.before_prompt_compile(
            context,
            PluginEvent(name="before_prompt_compile", conversation_id="qq:search"),
        )
        assert prompt_result.actions[0].payload["content"] == "只在需要最新资料时输出搜索标签。"
        with client.app.state.database.session_factory() as session:
            stored = session.get(PluginInstallation, "web_search")
            assert stored.settings["serp_api_key"] != serp_secret
            assert stored.settings["search_model_api_key"] != model_secret

        client.put("/api/plugins/web_search", json={"settings": {"engine": "bing"}})
        settings = client.app.state.plugin_manager.get_settings("web_search")
        assert settings["serp_api_key"] == serp_secret
        assert settings["search_model_api_key"] == model_secret


def test_web_search_fetches_model_list_with_saved_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "search-z", "name": "Search Z"},
                    {"id": "search-a", "name": "Search A"},
                    {"id": "search-a", "name": "Duplicate"},
                ]
            },
        )

    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "catgirl.api.httpx.Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    with make_client(tmp_path) as client:
        response = client.put(
            "/api/plugins/web_search",
            json={
                "settings": {
                    "search_model_base_url": "https://saved-search.test/v1",
                    "search_model_api_key": "saved-search-key",
                }
            },
        )
        assert response.status_code == 200, response.text
        models = client.post(
            "/api/plugins/web_search/models",
            json={"payload": {"base_url": "https://temporary-search.test/v1", "api_key": ""}},
        )
        assert models.status_code == 200, models.text
        assert models.json() == [
            {"id": "search-a", "name": "Search A"},
            {"id": "search-z", "name": "Search Z"},
        ]
        assert captured == {
            "url": "https://temporary-search.test/v1/models",
            "authorization": "Bearer saved-search-key",
        }


def test_web_search_parses_supported_html_engines(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        manager = client.app.state.plugin_manager
        client.portal.call(manager.configure, "web_search", True)
        record = manager.records["web_search"]
        module = sys.modules[record.instance.__class__.__module__]
        samples = {
            "duckduckgo": '<div class="result"><a class="result__a" href="https://one.test">Duck</a><a class="result__snippet">Duck text</a></div>',
            "google": '<div class="MjjYud"><a href="https://two.test"><h3>Google</h3></a><div class="VwiC3b">Google text</div></div>',
            "bing": '<li class="b_algo"><h2><a href="https://three.test">Bing</a></h2><p>Bing text</p></li>',
            "sear": '<article class="result"><h3><a href="https://four.test">Sear</a></h3><p class="content">Sear text</p></article>',
        }
        for engine, source in samples.items():
            results = module._parse_html_results(source, engine, 5)
            assert len(results) == 1
            assert results[0].url.startswith("https://")
            assert results[0].snippet.endswith("text")


def test_web_search_uses_its_own_model_connection(tmp_path: Path) -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["calls"] = captured.get("calls", 0) + 1
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "项目的近期信息摘要。\n"
                                "来源：https://source.test/project\n"
                                "补充：https://second.test/news"
                            )
                        }
                    }
                ]
            },
        )

    with make_client(tmp_path) as client:
        response = client.put(
            "/api/plugins/web_search",
            json={
                "enabled": True,
                "settings": {
                    "engine": "model",
                    "search_model_base_url": "https://search-model.test/api",
                    "search_model_api_key": "search-only-secret",
                    "search_model_name": "search-model",
                    "result_count": 1,
                },
            },
        )
        assert response.status_code == 200
        manager = client.app.state.plugin_manager
        record = manager.records["web_search"]
        module = sys.modules[record.instance.__class__.__module__]
        assert module.DEFAULT_SEARCH_MODEL_PROMPT == (
            record.manifest.default_settings()["search_model_prompt"]
        )
        record.instance.transport = httpx.MockTransport(handler)

        results = client.portal.call(
            record.instance._search,
            manager.get_settings("web_search"),
            "A Cat Girl 最新信息",
        )

        assert captured["url"] == "https://search-model.test/api/chat/completions"
        assert captured["api_key"] == "Bearer search-only-secret"
        assert captured["payload"]["model"] == "search-model"
        assert captured["payload"]["stream"] is False
        assert "result_count" not in captured["payload"]
        search_system = captured["payload"]["messages"][0]["content"]
        assert "A Cat Girl 最新信息" in search_system
        assert "{{current_time}}" not in search_system
        assert datetime.now().astimezone().date().isoformat() in search_system
        assert "报道发布时间、事件发生时间和数据发布时间" in search_system
        assert search_system.startswith("<RealTime_Search>\n")
        assert "交叉核验" not in search_system
        assert "A Cat Girl 最新信息" in captured["payload"]["messages"][1]["content"]
        assert results[0].title == "搜索模型检索摘要"
        assert results[0].url == ""
        assert results[1].url == "https://source.test/project"
        assert results[2].url == "https://second.test/news"

        cached_results = client.portal.call(
            record.instance._search,
            {**manager.get_settings("web_search"), "result_count": 10},
            "A Cat Girl 最新信息",
        )
        assert captured["calls"] == 1
        assert cached_results == results


def test_segmented_reply_limits_segments_and_carries_typing_settings(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        manager = client.app.state.plugin_manager
        client.portal.call(manager.configure, "segmented_reply", True)
        record = manager.records["segmented_reply"]
        context = PluginContext(manager, "segmented_reply", record.path)
        prompt = record.instance.before_prompt_compile(
            context,
            PluginEvent(name="before_prompt_compile", conversation_id="qq:segments", text="测试"),
        )
        prompt_content = prompt.actions[0].payload["content"]
        assert prompt_content == record.manifest.default_settings()["prompt"]
        assert prompt_content.count("<Segmented_Reply>") == 1
        assert prompt_content.count("</Segmented_Reply>") == 1

        client.portal.call(
            manager.configure,
            "segmented_reply",
            True,
            {**context.settings, "prompt": "使用 ||| 分隔自定义回复。"},
        )
        record = manager.records["segmented_reply"]
        custom_prompt = record.instance.before_prompt_compile(
            context,
            PluginEvent(name="before_prompt_compile", conversation_id="qq:segments", text="测试"),
        )
        assert custom_prompt.actions[0].payload["content"] == "使用 ||| 分隔自定义回复。"

        parsed = record.instance.after_model_response(
            context,
            PluginEvent(
                name="after_model_response",
                conversation_id="qq:segments",
                response_text="一|||二|||三|||四|||五|||六",
            ),
        )
        action = parsed.actions[0]
        assert action.kind == "replace_response"
        assert action.payload["text_segments"] == ["一", "二", "三", "四", "五 六"]
        assert action.payload["segment_reply"]["max_segments"] == 5

        protected_text = "<SiWeiLian>不能使用|||分隔符</SiWeiLian>第一段|||第二段"
        protected = record.instance.after_model_response(
            context,
            PluginEvent(
                name="after_model_response",
                conversation_id="qq:segments",
                response_text=protected_text,
                metadata={
                    "response_split_metadata": {
                        "regex_filter": {
                            "protected_delimiter_offsets": [protected_text.index("|||")]
                        }
                    }
                },
            ),
        )
        assert protected.actions[0].payload["text_segments"] == [
            "<SiWeiLian>不能使用|||分隔符</SiWeiLian>第一段",
            "第二段",
        ]
