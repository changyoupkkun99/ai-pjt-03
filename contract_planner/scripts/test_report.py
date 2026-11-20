"""Run a named suite and save only case names, outcomes and time (no machine details)."""
import argparse,json,subprocess,sys,tempfile
from pathlib import Path
from xml.etree import ElementTree
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument("suite",choices=["planner","legacy"])
name=parser.parse_args().suite
work=ROOT/"legacy" if name=="legacy" else ROOT
with tempfile.TemporaryDirectory() as tmp:
    xml=Path(tmp)/"tests.xml"
    result=subprocess.run([sys.executable,"-m","pytest","tests","-q","-o","addopts=","--disable-warnings","--junitxml",str(xml)],cwd=work)
    cases=[]
    if xml.exists():
        for case in ElementTree.parse(xml).iter("testcase"):
            status=next((node.tag for node in case if node.tag in {"skipped","error","failure"}),"passed")
            cases.append(dict(name=case.get("name"),status=status,seconds=float(case.get("time"))))
    out={"suite":name,"exit_code":result.returncode,"cases":cases}
    (ROOT/f"docs/tests-{name}.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    sys.exit(result.returncode)
