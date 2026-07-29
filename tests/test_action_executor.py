import asyncio

from catgirl.action_executor import ActionExecutor
from catgirl.database import Database, RuntimeAction
from catgirl.plugins import PluginAction


def test_history_filter_is_completed_as_an_internal_action(tmp_path) -> None:
    database = Database(tmp_path / "catgirl.db")
    database.initialize()
    executor = ActionExecutor(database)

    async def run_action() -> str:
        await executor.startup()
        try:
            action_id = await executor.submit(
                "history_filter_test",
                PluginAction(
                    kind="history_filter",
                    payload={"exclude_through_position": 12},
                ),
            )
            await asyncio.wait_for(executor.queue.join(), timeout=1)
            return action_id
        finally:
            await executor.shutdown()

    action_id = asyncio.run(run_action())
    with database.session_factory() as session:
        stored = session.get(RuntimeAction, action_id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.error == ""
