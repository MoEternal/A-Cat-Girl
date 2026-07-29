"""Public plugin API for A CAT GIRL.

Third-party plugins may import the symbols re-exported here. Python plugins run
inside the server process and therefore have the same operating-system access
as the service account.
"""

from .context import PluginContext
from .file_memory import FileMemoryStore
from .types import PluginAction, PluginEvent, PluginManifest, PluginResult

__all__ = [
    "FileMemoryStore",
    "PluginAction",
    "PluginContext",
    "PluginEvent",
    "PluginManifest",
    "PluginResult",
]
