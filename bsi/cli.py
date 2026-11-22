"""Command line.  python -m bsi.cli <command> ..."""
import argparse
import json

from . import claims, compiler, db, registry, vault


def main():
    ap = argparse.ArgumentParser(prog="bsi")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("seed")
    sub.add_parser("verify")
    r = sub.add_parser("ingest"); r.add_argument("path"); r.add_argument("--tier", default="unverified")
    r.add_argument("--title"); r.add_argument("--year", type=int); r.add_argument("--holder")
    r.add_argument("--sensitive", action="store_true")
    f = sub.add_parser("find"); f.add_argument("text")
    c = sub.add_parser("compile"); c.add_argument("claim_id", type=int)
    st = sub.add_parser("status"); st.add_argument("claim_id", type=int)
    sub.add_parser("board")
    b = sub.add_parser("broadcast", help="documentary series: daily | make CLAIM | approve EP NAME | publish EP")
    b.add_argument("action", choices=["daily", "plan", "make", "approve", "reject", "publish", "list"])
    b.add_argument("target", nargs="?", type=int)
    b.add_argument("--by", help="reviewer name for approve/reject")
    b.add_argument("--note", help="producer's note read in the episode, or review note")
    b.add_argument("--platform", default="youtube")
    b.add_argument("--publish-at", help="RFC 3339 time to schedule the YouTube release")
    b.add_argument("--short", action="store_true", help="publish the vertical Short instead of the episode")
    b.add_argument("--no-short", action="store_true", help="skip rendering the vertical cut")
    a = ap.parse_args()
    db.init()

    if a.cmd == "init":
        print("database ready")
    elif a.cmd == "seed":
        print(f"added {registry.load_seed()} research targets")
    elif a.cmd == "verify":
        s = db.session(); print(json.dumps(vault.verify_audit(s), indent=2)); s.close()
    elif a.cmd == "ingest":
        src = vault.register(a.path, tier=a.tier, title=a.title, created_year=a.year,
                             holder=a.holder, sensitive=a.sensitive)
        print(json.dumps({"source_id": src.id, "sha256": src.sha256,
                          "injection_flags": src.injection_flags}, indent=2))
    elif a.cmd == "find":
        print(json.dumps(vault.find_passage(a.text), indent=2))
    elif a.cmd == "compile":
        print(json.dumps(compiler.compile_claim(a.claim_id), indent=2))
    elif a.cmd == "status":
        s = db.session(); print(json.dumps(claims.compute_status(s.get(db.Claim, a.claim_id)), indent=2)); s.close()
    elif a.cmd == "broadcast":
        from .broadcast import episode as bep, studio
        if a.action == "plan":
            s = db.session(); print(json.dumps(bep.plan_next(s), indent=2)); s.close()
        elif a.action in ("daily", "make"):
            print(json.dumps(studio.make_episode(a.target if a.action == "make" else None, captain_note=a.note,
                                                 make_short=not a.no_short), indent=2))
        elif a.action == "approve":
            if not a.by:
                raise SystemExit("--by NAME is required: a person approves every episode")
            studio.approve(a.target, a.by, a.note or ""); print("approved")
        elif a.action == "reject":
            studio.reject(a.target, a.by or "unknown", a.note or ""); print("rejected")
        elif a.action == "publish":
            kw = {} if a.platform == "package" else {"publish_at": a.publish_at, "short": a.short}
            print(json.dumps(studio.publish(a.target, a.platform, **kw), indent=2))
        elif a.action == "list":
            s = db.session()
            for e in s.query(db.Episode).order_by(db.Episode.id.desc()).limit(30):
                print(f"{e.id:>4}  {e.state:<9} {e.status_at_render:<20} {e.title}")
            s.close()
    elif a.cmd == "board":
        s = db.session()
        for p in s.query(db.Person).order_by(db.Person.display_name):
            for c in p.claims:
                st_ = claims.compute_status(c)["status"]
                print(f"{c.id:>4}  {st_:<20} {p.display_name:<40} [{c.claim_type}] {c.statement[:70]}")
        s.close()


if __name__ == "__main__":
    main()
