from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text

from catgirl.database import Conversation, ConversationTurn, Database


def test_existing_conversation_turn_table_adds_batch_trigger_ids(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "catgirl.db")
    database.initialize()
    with database.session_factory() as session:
        session.add(Conversation(id="existing-record", external_id="qq:existing"))
        session.add(
            ConversationTurn(
                id="existing-turn",
                conversation_id="existing-record",
                route_id="qq:existing",
                trigger_message_id="old-message",
                trigger_message_ids=["old-message"],
                trigger_user_id="old-user",
            )
        )
        session.commit()
    with database.engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE conversation_turns DROP COLUMN trigger_message_ids")
        )

    database.initialize()

    columns = {
        column["name"] for column in inspect(database.engine).get_columns("conversation_turns")
    }
    assert "trigger_message_ids" in columns
    with database.session_factory() as session:
        turn = session.get(ConversationTurn, "existing-turn")
        assert turn is not None
        assert turn.trigger_message_id == "old-message"
        assert turn.trigger_message_ids == []
