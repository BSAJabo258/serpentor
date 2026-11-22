"""Broadcast: honest scripts, approval gate, rights gate, small real renders (silent voice)."""
import json
from pathlib import Path

import pytest

from bsi import db, registry, vault
from bsi.broadcast import episode, publish, render, studio, visuals


@pytest.fixture(autouse=True)
def fast(monkeypatch, tmp_path):
    monkeypatch.setenv("BSI_VOICE", "silent")
    monkeypatch.setenv("BSI_EPISODES_DIR", str(tmp_path / "eps"))
    monkeypatch.delenv("BSI_MUSIC_DIR", raising=False)
    monkeypatch.setattr(render, "FPS", 4)
    real = render.render_episode
    monkeypatch.setattr(render, "render_episode",
                        lambda script, d, **kw: real(script, d, landscape=(320, 180), vertical=(180, 320), **kw))


def all_narration(script):
    return " ".join(s["narration"] for s in script["scenes"])


def test_open_case_never_claims_proof():
    p = registry.add_person("Broadcast Open Case")
    c = registry.add_claim(p.id, "ancestry", "Born abroad.", alternatives=["Born locally"])
    sc = episode.build_script(c.id, "open_case")
    text = all_narration(sc)
    assert "open case" in text.lower()
    assert not any(line in text for line in episode.STATUS_LINE["documented"])
    assert "Born locally" in text                     # the alternative is stated up front


def test_documented_language_only_with_human_primary(make_pdf):
    p = registry.add_person("Broadcast Documented", birth_year=1800, death_year=1870)
    c = registry.add_claim(p.id, "contribution", "Built the mill.")
    src = vault.register(make_pdf("mill.pdf", ["Deed: the mill built by the subject in 1830"]), tier="primary",
                         title="Mill deed", rights="public domain")
    registry.link_evidence(c.id, src.id, "supports", quote="the mill built by the subject")
    sc = episode.build_script(c.id)
    assert sc["status"] == "documented"
    scale = next(s for s in sc["scenes"] if s["type"] == "scale")
    assert any(line in scale["narration"] for line in episode.STATUS_LINE["documented"])
    assert "human reviewer still has to sign off" in scale["narration"]


def test_counter_evidence_is_narrated(make_pdf):
    p = registry.add_person("Broadcast Contested")
    c = registry.add_claim(p.id, "ancestry", "Born in X.")
    a = vault.register(make_pdf("bx.pdf", ["born in X per register"]), tier="primary")
    b = vault.register(make_pdf("by.pdf", ["study says born in Y"]), tier="secondary")
    registry.link_evidence(c.id, a.id, "supports")
    registry.link_evidence(c.id, b.id, "contradicts")
    sc = episode.build_script(c.id)
    stances = [s["evidence"]["stance"] for s in sc["scenes"] if s["type"] == "evidence"]
    assert "contradicts" in stances and sc["status"] == "contested"


def test_full_render_outputs_and_manifest(make_pdf):
    p = registry.add_person("Broadcast Render", birth_year=1750, death_year=1800)
    c = registry.add_claim(p.id, "depiction", "Later portrayals changed.")
    a = vault.register(make_pdf("r1.pdf", ["portrait made 1790"]), tier="primary", created_year=1790,
                       rights="public domain")
    b = vault.register(make_pdf("r2.pdf", ["engraving 1880"]), tier="tertiary", created_year=1880)
    registry.link_evidence(c.id, a.id, "supports", depiction_descriptor="dark complexion")
    registry.link_evidence(c.id, b.id, "supports", depiction_descriptor="lightened")
    r = studio.make_episode(c.id, captain_note="Our own words go here.")
    s = db.session()
    ep = s.get(db.Episode, r["episode_id"])
    files = {k: Path(v) for k, v in ep.files.items()}
    s.close()
    for role in ("episode", "short", "thumbnail", "captions", "description", "post", "manifest"):
        assert files[role].exists() and files[role].stat().st_size > 0, role
    assert any(k.startswith("carousel") for k in files)
    m = json.loads(files["manifest"].read_text())
    assert m["files"]["episode"]["sha256"] == render.sha(files["episode"])
    assert m["synthetic_voice"] is False                         # silent voice -> no AI-voice disclosure
    d = files["description"].read_text()
    assert "SOURCES" in d and "CHAPTERS" in d and "Disclosure" not in d
    types = [s["type"] for s in ep.script["scenes"]]
    assert "timeline" in types and "note" in types
    assert ep.state == "draft"


def test_publish_requires_approval_and_discloses_synthetic_voice(make_pdf, tmp_path):
    p = registry.add_person("Broadcast Publish")
    c = registry.add_claim(p.id, "biographical", "Lived.")
    r = studio.make_episode(c.id, make_short=False)
    with pytest.raises(PermissionError):
        studio.publish(r["episode_id"], "package")
    studio.approve(r["episode_id"], "Captain")
    s = db.session()
    files = dict(s.get(db.Episode, r["episode_id"]).files)
    s.close()
    m = json.loads(Path(files["manifest"]).read_text())
    m["synthetic_voice"] = True
    Path(files["manifest"]).write_text(json.dumps(m))

    sent = {}

    class Req:
        def next_chunk(self):
            return None, {"id": "abc123"}

    class Videos:
        def insert(self, part, body, media_body):
            sent["body"] = body
            return Req()

    class Exec:
        def execute(self):
            return {}

    class Fake:
        def videos(self):
            return Videos()

        def thumbnails(self):
            return type("T", (), {"set": lambda *a, **k: Exec()})()

        def captions(self):
            return type("C", (), {"insert": lambda *a, **k: Exec()})()

    import sys
    import types
    fake_http = types.ModuleType("googleapiclient.http")
    fake_http.MediaFileUpload = lambda *a, **k: None
    sys.modules.setdefault("googleapiclient", types.ModuleType("googleapiclient"))
    sys.modules["googleapiclient.http"] = fake_http
    res = publish.youtube(files, {}, "T", client=Fake())
    assert res["video_id"] == "abc123" and res["privacy"] == "private"
    assert sent["body"]["status"]["containsSyntheticMedia"] is True
    pkg = studio.publish(r["episode_id"], "package")
    assert "caption" in pkg


def test_planner_prefers_status_changes(make_pdf):
    p = registry.add_person("Broadcast Planner")
    c = registry.add_claim(p.id, "contribution", "Invented Q.")
    r = studio.make_episode(c.id, make_short=False)
    studio.approve(r["episode_id"], "Captain")
    src = vault.register(make_pdf("q.pdf", ["patent for Q to the subject"]), tier="primary")
    registry.link_evidence(c.id, src.id, "supports")
    s = db.session()
    # clear other drafts so the planner sees this case
    for e in s.query(db.Episode).filter(db.Episode.state == "draft"):
        e.state = "rejected"
    s.commit()
    plan = episode.plan_next(s)
    s.close()
    assert plan["claim_id"] == c.id and plan["kind"] == "update" and plan["previous_status"] == "unexamined"


def test_rights_gate_hides_unlicensed_images():
    assert not visuals.rights_ok(None) and not visuals.rights_ok("all rights reserved")
    assert visuals.rights_ok("Public domain (published before 1929)") and visuals.rights_ok("CC BY-SA 4.0")
    assert visuals.page_image({"rights": "unknown", "sha256": "0" * 64}, Path("/tmp")) is None


def test_caption_sentences_keep_initials():
    assert render.sentences("Held by Martha E. Tyson. Next.") == ["Held by Martha E. Tyson.", "Next."]


def test_file_endpoint_serves_only_recorded_roles():
    from fastapi.testclient import TestClient
    from bsi.api import app
    with TestClient(app) as cl:
        eps = cl.get("/broadcast/episodes").json()
        assert eps, "an episode should exist from earlier tests"
        assert cl.get(f"/broadcast/episodes/{eps[0]['id']}/files/..%2F..%2Fetc%2Fpasswd").status_code == 404
        assert cl.get(f"/broadcast/episodes/{eps[0]['id']}/files/manifest").status_code == 200
