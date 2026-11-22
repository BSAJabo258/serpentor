"""The daily loop: plan -> script -> render -> draft. Publishing needs a human yes."""
import os
from pathlib import Path

from .. import config, db
from ..vault import audit
from . import episode, render


def episodes_dir() -> Path:
    d = Path(os.environ.get("BSI_EPISODES_DIR", config.DATA_DIR / "episodes"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_episode(claim_id: int | None = None, kind: str | None = None, *, captain_note: str | None = None,
                 make_short: bool = True) -> dict:
    with db.WRITE_LOCK:
        plan = _plan(claim_id, kind)
    if plan.get("status") == "nothing_to_make":
        return plan
    with db.WRITE_LOCK:     # compiling the research spec records questions + an audit event
        script = episode.build_script(plan["claim_id"], plan["kind"], plan.get("previous_status"), captain_note)
        ep_id = _create(script)
    ep_dir = episodes_dir() / f"ep{ep_id:04d}"
    out = render.render_episode(script, ep_dir, make_short=make_short)
    with db.WRITE_LOCK:
        _finish(ep_id, ep_dir, script, out)
    return {"episode_id": ep_id, "title": script["title"], "kind": script["kind"], "status": script["status"],
            "duration": out["duration"], "voice": out["voice"], "dir": str(ep_dir)}


def _plan(claim_id, kind):
    s = db.session()
    try:
        if claim_id is None:
            return episode.plan_next(s) or {"status": "nothing_to_make"}
        else:
            c = s.get(db.Claim, claim_id)
            if not c:
                raise ValueError("no such claim")
            prev = episode.last_aired_status(s, claim_id)
            from ..claims import compute_status
            now = compute_status(c)["status"]
            return {"claim_id": claim_id,
                    "kind": kind or ("update" if prev and prev != now else ("case" if c.evidence else "open_case")),
                    "previous_status": prev}
    finally:
        s.close()


def _create(script) -> int:
    s = db.session()
    try:
        ep = db.Episode(claim_id=script["claim_id"], kind=script["kind"], title=script["title"],
                        status_at_render=script["status"], previous_status=script.get("previous_status"),
                        script=script, state="draft")
        s.add(ep)
        s.flush()
        ep_id = ep.id
        s.commit()
        return ep_id
    finally:
        s.close()


def _finish(ep_id, ep_dir, script, out):
    s = db.session()
    try:
        ep = s.get(db.Episode, ep_id)
        ep.files = {k: str(ep_dir / v) for k, v in out["files"].items()}
        ep.voice = out["voice"]
        audit(s, "broadcast.render", {"episode_id": ep_id, "claim_id": script["claim_id"], "kind": script["kind"],
                                      "status": script["status"], "spec_hash": script["spec_hash"],
                                      "outputs": {k: v["sha256"] for k, v in out["manifest"]["files"].items()}})
        s.commit()
    finally:
        s.close()


def approve(ep_id: int, reviewer: str, note: str = "") -> None:
    with db.WRITE_LOCK:
        _set_state(ep_id, "approved", reviewer, note, require="draft")


def reject(ep_id: int, reviewer: str, note: str = "") -> None:
    with db.WRITE_LOCK:
        _set_state(ep_id, "rejected", reviewer, note)


def _set_state(ep_id, state, reviewer, note, require=None):
    s = db.session()
    try:
        ep = s.get(db.Episode, ep_id)
        if ep is None:
            raise ValueError("no such episode")
        if require and ep.state != require:
            raise ValueError(f"episode is {ep.state}")
        ep.state = state
        if state == "approved":
            ep.approved_by = reviewer
        audit(s, f"broadcast.{state}", {"episode_id": ep_id, "reviewer": reviewer, "note": note})
        s.commit()
    finally:
        s.close()


def publish(ep_id: int, platform: str = "youtube", **kw) -> dict:
    s = db.session()
    try:
        ep = s.get(db.Episode, ep_id)
        if ep.state not in ("approved", "published"):
            raise PermissionError("an episode must be approved by a person before it is published")
        files, script, ep_title = dict(ep.files), dict(ep.script), ep.title
    finally:
        s.close()
    from . import publish as pub
    res = pub.PUBLISHERS[platform](files, script, ep_title, **kw)
    with db.WRITE_LOCK:
        _record_publish(ep_id, platform, res)
    return res


def _record_publish(ep_id, platform, res):
    s = db.session()
    try:
        ep = s.get(db.Episode, ep_id)
        ep.published = {**(ep.published or {}), platform: res}
        if platform != "package":          # packaging for manual posting isn't publishing
            ep.state = "published"
        audit(s, "broadcast.publish", {"episode_id": ep_id, "platform": platform, "result": res})
        s.commit()
    finally:
        s.close()
