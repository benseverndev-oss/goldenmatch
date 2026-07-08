from types import SimpleNamespace

from goldenpipe.compiler.compiled_runner import compile_and_run
from goldenpipe.engine.runner import Runner
from goldenpipe.models.context import PipeContext, StageResult, StageStatus


class _Registry:  # Router.apply is never called (no stage emits a decision)
    pass


def _stage(name, produces, consumes, run_fn):
    info = SimpleNamespace(name=name, produces=produces, consumes=consumes, location="local")
    return SimpleNamespace(info=info, run=run_fn, remote_capable=False)


def _planned(name, stage, config=None):
    spec = SimpleNamespace(skip_if=None, on_error="continue", config=config or {})
    return SimpleNamespace(name=name, stage=stage, spec=spec, config=config or {})


def _plan(planned_list):
    return SimpleNamespace(stages=planned_list)


def _load():
    return _stage("load", ["df"], [], lambda ctx: StageResult(status=StageStatus.SUCCESS))


def _flow():
    def run(ctx):
        ctx.artifacts["manifest"] = SimpleNamespace(
            records=[SimpleNamespace(column="email", transform="email_normalize")]
        )
        return StageResult(status=StageStatus.SUCCESS)
    return _stage("goldenflow.transform", ["df", "manifest"], ["df"], run)


def test_runner_hook_none_runs_stages_unchanged():
    ctx = PipeContext(df="DF")
    results = Runner(_Registry()).run(_plan([_planned("load", _load())]), ctx)
    assert results["load"].status == StageStatus.SUCCESS


def test_compile_and_run_records_source_map_and_edge():
    ctx = PipeContext(df="DF")
    plan = _plan([
        _planned("load", _load()),
        _planned("goldenflow.transform", _flow(),
                 config={"config": {"transforms": [{"column": "email", "ops": ["email_normalize"]}]}}),
    ])
    results, compiled = compile_and_run(plan, ctx, _Registry())
    assert results["load"].status == StageStatus.SUCCESS
    assert results["goldenflow.transform"].status == StageStatus.SUCCESS
    assert [n["kind"] for n in compiled["nodes"]] == ["Source", "Map"]
    assert compiled["nodes"][1]["op"] == "email_normalize"
    # load produces df (Source id 0); flow consumes df (Map id 1) -> one df edge
    assert compiled["edges"] == [[0, 1, "df"]]
