from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import time


class DialogConflict(Exception):
    def __init__(self, code):
        self.code = code


@dataclass
class DialogTurn:
    revision: int
    state: dict = field(default_factory=dict)
    replay: dict | None = None
    response: dict | None = None


def read_dialog(database, user_id):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT revision, state FROM assistant_dialogs WHERE user_id = ?", (user_id,),
        ).fetchone()
    return DialogTurn(row["revision"], json.loads(row["state"])) if row else DialogTurn(0)


@contextmanager
def dialog_turn(database, user_id, request):
    turn_id = str(request.turn_id)
    # A separate lease token fences off a worker that continues after expiration.
    lease = str(time.monotonic_ns()) + ":" + turn_id
    now = int(time.time())
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        saved = connection.execute(
            "SELECT message, response FROM assistant_turns WHERE user_id = ? AND turn_id = ?",
            (user_id, turn_id),
        ).fetchone()
        if saved and saved["message"] != request.message:
            raise DialogConflict("assistant_turn_reused")
        row = connection.execute("SELECT * FROM assistant_dialogs WHERE user_id = ?", (user_id,)).fetchone()
        revision = row["revision"] if row else 0
        if saved:
            turn = DialogTurn(revision, replay=json.loads(saved["response"]))
        else:
            if row and row["active_turn"] and row["active_until"] > now:
                raise DialogConflict("assistant_in_progress")
            if request.revision != revision:
                raise DialogConflict("assistant_revision_conflict")
            connection.execute(
                """INSERT INTO assistant_dialogs VALUES (?, 0, '{}', ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET active_turn = excluded.active_turn,
                   active_until = excluded.active_until""",
                (user_id, lease, now + 600),
            )
            turn = DialogTurn(revision, json.loads(row["state"]) if row else {})
    if turn.replay is not None:
        yield turn
        return

    try:
        yield turn
        with database.connect() as connection:
            revision = turn.revision + 1
            updated = connection.execute(
                """UPDATE assistant_dialogs SET revision = ?, state = ?, active_turn = NULL, active_until = NULL
                   WHERE user_id = ? AND active_turn = ?""",
                (revision, json.dumps(turn.state, ensure_ascii=False), user_id, lease),
            )
            if updated.rowcount != 1:
                raise DialogConflict("assistant_revision_conflict")
            connection.execute(
                "INSERT INTO assistant_turns VALUES (?, ?, ?, ?, ?)",
                (user_id, turn_id, request.message, revision, json.dumps(turn.response, ensure_ascii=False)),
            )
            connection.execute("DELETE FROM assistant_turns WHERE user_id = ? AND revision <= ?", (user_id, revision - 32))
    finally:
        with database.connect() as connection:
            connection.execute(
                "UPDATE assistant_dialogs SET active_turn = NULL, active_until = NULL WHERE user_id = ? AND active_turn = ?",
                (user_id, lease),
            )
