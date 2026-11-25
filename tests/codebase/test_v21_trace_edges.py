"""Synthetic explicit edge normalization only; no claim of real CBM compatibility."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"services"/"codebase"))
from app.explorer import trace_edges

def test_explicit_edges(tmp_path):
    (tmp_path/"demo.py").write_text("pass")
    a={"name":"a","path":"demo.py","line":1}
    payload={"edges":[{"caller":a,"callee":{"name":"b"}},{"caller":a,"callee":{"name":"b"}}]}
    edges=trace_edges(payload,tmp_path,[])
    assert len(edges)==1
    assert edges[0]["source"]["name"]=="a"

def test_no_invented_edges(tmp_path):
    assert trace_edges({"callers":["a","b"],"description":"a calls b"},tmp_path,[])==[]
