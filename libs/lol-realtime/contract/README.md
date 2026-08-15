# Wire contract fixtures

`lobby-commands.golden.json` is the **byte-level** backend→desktop orchestration
contract: one entry per command `type`, holding exactly the JSON the desktop
receives as the `command` field of a `{"type":"command","command":…}` frame.

It exists because both sides were fully tested and still could not talk to each
other. The backend emitted `{"type":"create_lobby_command","match_id":…}`; the
desktop switched on `kind` and read `matchId`. Every command was dropped on the
controller's first line — no ack, no log — and no test in either language could
see it, because each side built its fixtures in its own idea of the shape.

Two tests consume this one file, which is what makes it a contract rather than a
pair of opinions:

- `services/backend/tests/test_wire_contract.py` — asserts the Pydantic models in
  `lol_realtime` still serialize to exactly this.
- `services/desktop/src/orchestration/wireContract.test.ts` — feeds these bytes
  through the real controller and asserts the LCU adapter is actually driven.

Change the models and the first test fails; change the client and the second
does. Regenerate deliberately, never to make a test pass.
