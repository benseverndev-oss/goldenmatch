import json
import os
import subprocess
import sys
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
RUN_GM = HERE / "run_goldenmatch.py"

def _tiny_parquet(tmp_path):
    df = pl.DataFrame({
        "record_id": ["r0","r1","r2","r3","r4","r5"],
        "first_name": ["ann","ann","bob","bob","cara","dan"],
        "surname":    ["lee","lee","kim","kim","ng","ono"],
        "dob":        ["1990-01-01","1990-01-01","1985-02-02","1985-02-02","1972-03-03","1965-04-04"],
        "postcode":   ["AA1","AA1","BB2","BB2","CC3","DD4"],
    })
    p = tmp_path / "tiny.parquet"; df.write_parquet(p); return p, df

def _run(p, mode, tmp_path):
    out = tmp_path / "res.json"; pred = tmp_path / "pred.parquet"
    proc = subprocess.run(
        [sys.executable, str(RUN_GM), "--input", str(p), "--rows", "6",
         "--mode", mode, "--out", str(out), "--pred-out", str(pred),
         "--allow-pure-python", "--threshold", "0.85"],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "POLARS_SKIP_CPU_CHECK": "1", "PYTHONIOENCODING": "utf-8"},
    )
    return proc, out, pred

def test_zeroconfig_emits_string_record_id_preds(tmp_path):
    p, df = _tiny_parquet(tmp_path)
    proc, out, pred = _run(p, "zeroconfig", tmp_path)
    assert out.exists(), proc.stderr
    res = json.loads(out.read_text())
    assert res["mode"] == "zeroconfig"
    assert res["status"] in ("ok", "refused")
    if res["status"] == "ok":
        t = pq.read_table(pred)
        assert t.column_names == ["record_id", "pred_cluster_id"]
        rids = set(t.column("record_id").to_pylist())
        assert rids <= set(df["record_id"].to_list())
        assert all(isinstance(x, str) for x in rids)

def test_probabilistic_mode_runs_and_string_preds(tmp_path):
    p, df = _tiny_parquet(tmp_path)
    proc, out, pred = _run(p, "probabilistic", tmp_path)
    assert out.exists(), proc.stderr
    res = json.loads(out.read_text())
    assert res["mode"] == "probabilistic" and res["status"] == "ok", res.get("error")
    t = pq.read_table(pred)
    assert all(isinstance(x, str) for x in t.column("record_id").to_pylist())

def test_hand_built_mode_unchanged_int_ids(tmp_path):
    p, df = _tiny_parquet(tmp_path)
    proc, out, pred = _run(p, "hand_built", tmp_path)
    res = json.loads(out.read_text())
    assert res["mode"] == "hand_built"
    if res["status"] == "ok":
        t = pq.read_table(pred)
        assert pa.types.is_integer(t.schema.field("record_id").type)
