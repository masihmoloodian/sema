"""Session store — including round-tripping the extension's on-disk format."""

import hashlib
import json


from sema.agent.session import (
    Attachment,
    ChatMessage,
    Session,
    SessionStore,
    SessionUsage,
)


def test_store_path_matches_extension_hash(tmp_path):
    """The directory name must match sessionStore.ts or sessions go missing."""
    store = SessionStore(tmp_path, "/Users/x/repo")
    expected = hashlib.sha1(b"/Users/x/repo").hexdigest()[:16]
    assert store.dir == tmp_path / "sessions" / expected


def test_empty_workspace_key_uses_sentinel(tmp_path):
    store = SessionStore(tmp_path, "")
    expected = hashlib.sha1(b"_noworkspace").hexdigest()[:16]
    assert store.dir.name == expected


def test_save_and_load_round_trip(tmp_path):
    store = SessionStore(tmp_path, "repo")
    session = Session.create("anthropic", "claude-opus-4-8")
    session.messages.append(ChatMessage("user", "hello"))
    session.messages.append(ChatMessage("assistant", "hi"))
    session.usage.add(input_tokens=10, output_tokens=5, cached=2, cost=0.01)
    store.save(session)

    loaded = store.load(session.id)
    assert loaded is not None
    assert [m.content for m in loaded.messages] == ["hello", "hi"]
    assert loaded.usage.input == 10
    assert loaded.usage.cost_known is True


def test_written_file_uses_camel_case_schema(tmp_path):
    """The extension reads these files; the key names are its contract."""
    store = SessionStore(tmp_path, "repo")
    session = Session.create("anthropic", "claude-opus-4-8")
    session.cli_session_id = "abc"
    session.cli_session_provider = "claude-code"
    session.plan_path = ".sema/plans/x.md"
    store.save(session)

    raw = json.loads((store.dir / f"{session.id}.json").read_text())
    assert raw["createdAt"] > 0
    assert raw["usage"]["costKnown"] is False
    assert raw["cliSessionId"] == "abc"
    assert raw["cliSessionProvider"] == "claude-code"
    assert raw["planPath"] == ".sema/plans/x.md"


def test_loads_a_file_written_by_the_extension(tmp_path):
    """A hand-written extension-shaped file must load without loss."""
    store = SessionStore(tmp_path, "repo")
    store.dir.mkdir(parents=True)
    payload = {
        "id": "ext-1",
        "title": "From VS Code",
        "createdAt": 111,
        "updatedAt": 222,
        "provider": "claude-code",
        "model": "opus",
        "cliSessionId": "sess-9",
        "cliSessionProvider": "claude-code",
        "cliSessionModel": "opus",
        "cliSessionMode": "agent",
        "cliSessionPermission": "ask",
        "planPath": ".sema/plans/ext-1.md",
        "usage": {"input": 3, "output": 4, "cached": 1, "cost": 0.5,
                  "costKnown": True, "turns": 2},
        "messages": [
            {"role": "user", "content": "q",
             "attachments": [{"id": "a1", "name": "x.png", "kind": "image",
                              "mime": "image/png", "size": 12}]},
            {"role": "assistant", "content": "a"},
        ],
    }
    (store.dir / "ext-1.json").write_text(json.dumps(payload))

    loaded = store.load("ext-1")
    assert loaded is not None
    assert loaded.cli_session_id == "sess-9"
    assert loaded.cli_session_mode == "agent"
    assert loaded.usage.cost == 0.5
    assert loaded.messages[0].attachments[0].name == "x.png"
    # And it must survive a rewrite unchanged in every extension-owned field.
    store.save(loaded)
    rewritten = json.loads((store.dir / "ext-1.json").read_text())
    for key in ("cliSessionId", "cliSessionMode", "planPath", "provider"):
        assert rewritten[key] == payload[key]


def test_list_is_newest_first_and_skips_junk(tmp_path):
    store = SessionStore(tmp_path, "repo")
    store.dir.mkdir(parents=True)
    (store.dir / "broken.json").write_text("{not json")
    (store.dir / "nofields.json").write_text("{}")
    for index, updated in enumerate([100, 300, 200]):
        (store.dir / f"s{index}.json").write_text(json.dumps({
            "id": f"s{index}", "title": f"t{index}", "createdAt": 1,
            "updatedAt": updated, "provider": "p", "model": "m", "messages": [],
        }))
    rows = store.list()
    assert [r.id for r in rows] == ["s1", "s2", "s0"]


def test_title_derives_from_first_user_message():
    session = Session.create("anthropic", "m")
    session.messages.append(ChatMessage("user", "  \n"))
    session.messages.append(ChatMessage("user", "Fix the auth bug\nand tests"))
    assert session.title_from_messages() == "Fix the auth bug"


def test_title_falls_back_to_attachment_name():
    session = Session.create("anthropic", "m")
    session.messages.append(ChatMessage(
        "user", "", [Attachment("a", "diagram.png", "image", "image/png", 5)]
    ))
    assert session.title_from_messages() == "diagram.png"


def test_delete_removes_session_and_attachments(tmp_path):
    store = SessionStore(tmp_path, "repo")
    session = Session.create("anthropic", "m")
    store.save(session)
    attachments = store.attachments_dir(session.id)
    attachments.mkdir(parents=True)
    (attachments / "a1").write_bytes(b"x")

    store.delete(session.id)
    assert store.load(session.id) is None
    assert not attachments.exists()


def test_prune_drops_orphan_attachment_dirs(tmp_path):
    store = SessionStore(tmp_path, "repo")
    live = Session.create("anthropic", "m")
    store.save(live)
    store.attachments_dir(live.id).mkdir(parents=True)
    orphan = store.attachments_dir("ghost")
    orphan.mkdir(parents=True)

    store.prune_attachments()
    assert store.attachments_dir(live.id).exists()
    assert not orphan.exists()


def test_usage_add_tracks_cost_known():
    usage = SessionUsage()
    usage.add(input_tokens=1, output_tokens=2)
    assert usage.cost_known is False
    usage.add(input_tokens=1, output_tokens=2, cost=0.5)
    assert usage.cost_known is True
    assert usage.turns == 2
