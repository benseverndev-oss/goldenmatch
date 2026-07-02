"""End-to-end smoke: register scorer -> build frame -> dedupe_df -> score, on a
tiny synthetic name-block. No network, no native kernel, no LLM. Proves the whole
wiring (plugin scorer resolves through the schema validator, the co-author signal
clusters the right papers) without touching the real corpus."""
import scorers
from configs import relational_config
from score import pairwise_score_one
from to_frame import build_name_frame, clusters_to_pid_lists

# Person A: p1,p2,p3 chained by co-authors (p1&p2 share "ning zeng", p2&p3 share
# "bo li") -- transitive clustering must pull all three together even though p1&p3
# share NO co-author. Person B: q1,q2 share "amy king". No cross-person overlap.
_PUB = {
    "p1": {"id": "p1", "title": "t", "authors": [
        {"name": "Wei Wang"}, {"name": "Ning Zeng"}], "year": 2019},
    "p2": {"id": "p2", "title": "t", "authors": [
        {"name": "Wei Wang"}, {"name": "Ning Zeng"}, {"name": "Bo Li"}], "year": 2020},
    "p3": {"id": "p3", "title": "t", "authors": [
        {"name": "Wei Wang"}, {"name": "Bo Li"}], "year": 2021},
    "q1": {"id": "q1", "title": "t", "authors": [
        {"name": "Wei Wang"}, {"name": "Amy King"}], "year": 2005},
    "q2": {"id": "q2", "title": "t", "authors": [
        {"name": "Wei Wang"}, {"name": "Amy King"}], "year": 2006},
}
_TRUTH = [["p1", "p2", "p3"], ["q1", "q2"]]


def test_relational_clusters_the_coauthor_graph():
    import goldenmatch as gm

    scorers.register(force=True)
    df = build_name_frame("wei_wang", list(_PUB), _PUB)
    cfg = relational_config(coauthor_threshold=0.15)
    result = gm.dedupe_df(df, config=cfg, confidence_required=False)
    pred = clusters_to_pid_lists(result.clusters, df)

    # transitive closure gives the two true people; perfect on this clean block
    s = pairwise_score_one(pred, _TRUTH)
    assert s.f1 == 1.0, f"pred={sorted(map(sorted, pred))} f1={s.f1}"
