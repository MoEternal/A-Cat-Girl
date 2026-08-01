A Cat Girl v1.1.0 Linux web edition

1. Extract the entire tar.gz archive to a local directory.
2. Run: bash start.sh
3. The first launch installs uv, Python 3.12, and locked runtime dependencies.
4. Open http://127.0.0.1:8732/ and create the administrator account.
5. This public package contains built-in plugins only and no private runtime data.

The default listener is local-only. For remote access, use an SSH tunnel or edit
CATGIRL_HOST in .env after creating the administrator account. Do not expose the
management port directly to the public Internet.
