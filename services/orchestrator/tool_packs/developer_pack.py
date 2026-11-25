"""V3.1 P8 — Software Developer Pack: Java Structural/Semantic Analyzer, TypeScript Analyzer, Maven/JUnit, Local Security Scan, Log Analyzer."""
from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

_JAVA_PKG_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;")
_JAVA_IMP_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;")
_JAVA_TYPE_RE = re.compile(r"^\s*(?:public|protected|private)?\s*(?:abstract|final|sealed)?\s*(class|interface|enum|record)\s+(\w+)(?:\s+extends\s+([\w<>.,\s]+))?(?:\s+implements\s+([\w<>.,\s]+))?")
_JAVA_METHOD_RE = re.compile(r"^\s*(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?([\w<>\[\]?,\s]+?)\s+(\w+)\s*\(([^)]*)\)")
_SPRING_ANNO_RE = re.compile(r"@(RestController|Controller|Service|Repository|Component|Entity|Configuration|SpringBootApplication|GetMapping|PostMapping|PutMapping|DeleteMapping|RequestMapping)\b")

_TS_EXPORT_RE = re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?(function|class|interface|type|const|enum)\s+(\w+)")
_TS_IMPORT_RE = re.compile(r"^\s*import\s+.+?\s+from\s+['\"]([^'\"]+)['\"]")

_SECRET_LEAK_RE = re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|password\s*=\s*['\"][^'\"]{4,}['\"])", re.IGNORECASE)
_CORR_ID_RE = re.compile(r"(?:correlation[_-]?id|trace[_-]?id|request[_-]?id)[=:\s]+([A-Za-z0-9\-]{6,64})", re.IGNORECASE)
_EXC_RE = re.compile(r"([A-Za-z0-9_.]*(?:Exception|Error))(?::\s*(.*))?$")


def java_semantic_analyze(args: dict[str, Any]) -> dict[str, Any]:
    """Inspect Java source files for packages, imports, types, inheritance, Spring annotations, and method definitions with exact line numbers."""
    root = Path(str(args["project_path"])).resolve()
    files = sorted(root.rglob("*.java"))[:100]
    types_out = []
    spring_components = []
    packages: set[str] = set()
    for f in files:
        rel = str(f.relative_to(root)).replace("\\", "/")
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        pkg = ""
        imports: list[str] = []
        pending_annos: list[str] = []
        for idx, line in enumerate(lines, start=1):
            m_pkg = _JAVA_PKG_RE.match(line)
            if m_pkg:
                pkg = m_pkg.group(1)
                packages.add(pkg)
            m_imp = _JAVA_IMP_RE.match(line)
            if m_imp:
                imports.append(m_imp.group(1))
            for a in _SPRING_ANNO_RE.findall(line):
                pending_annos.append(a)
            m_type = _JAVA_TYPE_RE.match(line)
            if m_type:
                kind, name, ext_t, impl_t = m_type.groups()
                methods = []
                for midx, mline in enumerate(lines[idx:], start=idx + 1):
                    mm = _JAVA_METHOD_RE.match(mline)
                    if mm and mm.group(2) not in ("if", "for", "while", "switch", "catch"):
                        methods.append({"name": mm.group(2), "return_type": mm.group(1).strip(), "line": midx})
                entry = {
                    "file": rel,
                    "line": idx,
                    "package": pkg,
                    "kind": kind,
                    "name": name,
                    "extends": (ext_t or "").strip() or None,
                    "implements": [x.strip() for x in (impl_t or "").split(",") if x.strip()],
                    "annotations": sorted(set(pending_annos)),
                    "imports_count": len(imports),
                    "methods": methods[:30],
                }
                types_out.append(entry)
                if pending_annos:
                    spring_components.append({"name": name, "file": rel, "annotations": sorted(set(pending_annos))})
                pending_annos = []
    return {
        "analysis_level": "structural_source_parser",
        "jdt_lsp_connected": False,
        "honest_note": "Structural Java parser (packages, types, Spring annotations, methods, line numbers); JDT Language Server not attached.",
        "files_scanned": len(files),
        "packages": sorted(packages),
        "spring_components": spring_components,
        "types": types_out,
    }


def ts_semantic_analyze(args: dict[str, Any]) -> dict[str, Any]:
    """Inspect TypeScript project: exported symbols, import graph, and optional real `tsc --noEmit` diagnostics."""
    root = Path(str(args["project_path"])).resolve()
    files = [f for f in sorted(root.rglob("*.ts")) if "node_modules" not in f.parts and "dist" not in f.parts][:150]
    exports = []
    imports_map: dict[str, list[str]] = {}
    for f in files:
        rel = str(f.relative_to(root)).replace("\\", "/")
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        file_imps = []
        for idx, line in enumerate(lines, start=1):
            me = _TS_EXPORT_RE.match(line)
            if me:
                exports.append({"file": rel, "line": idx, "kind": me.group(1), "name": me.group(2)})
            mi = _TS_IMPORT_RE.match(line)
            if mi:
                file_imps.append(mi.group(1))
        if file_imps:
            imports_map[rel] = file_imps

    tsc_diag: dict[str, Any] = {"ran": False, "exit_code": None, "output_excerpt": ""}
    if args.get("run_tsc", False) and (root / "tsconfig.json").is_file():
        tsc_bin = root / "node_modules" / ".bin" / ("tsc.cmd" if shutil.which("cmd") else "tsc")
        cmd = [str(tsc_bin), "--noEmit"] if tsc_bin.is_file() else (["npx", "tsc", "--noEmit"] if shutil.which("npx") else [])
        if cmd:
            proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45, shell=False)
            tsc_diag = {"ran": True, "exit_code": proc.returncode, "output_excerpt": ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[:2000]}

    return {
        "files_scanned": len(files),
        "exported_symbols_count": len(exports),
        "exported_symbols": exports[:60],
        "import_graph_files": len(imports_map),
        "tsc_diagnostics": tsc_diag,
    }


def maven_inspect_pom(args: dict[str, Any]) -> dict[str, Any]:
    """Parse pom.xml for coordinates, parent, dependencies, and check if local `mvn` is available."""
    pom = Path(str(args["pom_path"])).resolve()
    if not pom.is_file():
        raise FileNotFoundError(f"pom.xml not found: {pom}")
    tree = ET.parse(pom)
    root = tree.getroot()
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"
    deps = []
    for d in root.findall(f".//{ns}dependencies/{ns}dependency"):
        deps.append(
            {
                "groupId": (d.findtext(f"{ns}groupId") or "").strip(),
                "artifactId": (d.findtext(f"{ns}artifactId") or "").strip(),
                "version": (d.findtext(f"{ns}version") or "managed").strip(),
                "scope": (d.findtext(f"{ns}scope") or "compile").strip(),
            }
        )
    return {
        "pom_path": str(pom),
        "groupId": (root.findtext(f"{ns}groupId") or "").strip(),
        "artifactId": (root.findtext(f"{ns}artifactId") or "").strip(),
        "version": (root.findtext(f"{ns}version") or "").strip(),
        "dependency_count": len(deps),
        "dependencies": deps,
        "mvn_available": bool(shutil.which("mvn")),
    }


def junit_parse_reports(args: dict[str, Any]) -> dict[str, Any]:
    """Parse JUnit XML reports (target/surefire-reports/TEST-*.xml) into structured pass/fail/skip evidence."""
    report_dir = Path(str(args["report_dir"])).resolve()
    xml_files = sorted(report_dir.glob("TEST-*.xml"))
    total = failures = errors = skipped = 0
    failed_cases = []
    for xf in xml_files:
        root = ET.parse(xf).getroot()
        total += int(root.attrib.get("tests", 0))
        failures += int(root.attrib.get("failures", 0))
        errors += int(root.attrib.get("errors", 0))
        skipped += int(root.attrib.get("skipped", 0))
        for tc in root.findall("testcase"):
            fail_node = tc.find("failure") or tc.find("error")
            if fail_node is not None:
                failed_cases.append(
                    {
                        "classname": tc.attrib.get("classname"),
                        "name": tc.attrib.get("name"),
                        "message": (fail_node.attrib.get("message") or "")[:300],
                    }
                )
    passed = max(0, total - failures - errors - skipped)
    return {
        "report_files": len(xml_files),
        "tests": total,
        "passed": passed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "failed_cases": failed_cases,
        "all_passed": total > 0 and failures == 0 and errors == 0,
    }


def security_scan_local(args: dict[str, Any]) -> dict[str, Any]:
    """Local offline static security check (never uploads repo to SaaS)."""
    root = Path(str(args["project_path"])).resolve()
    findings = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in (".py", ".java", ".ts", ".js", ".yml", ".yaml", ".properties", ".xml"):
            continue
        if any(p in f.parts for p in ("node_modules", ".git", "target", "dist", "__pycache__")):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for idx, line in enumerate(text.splitlines(), start=1):
            if _SECRET_LEAK_RE.search(line):
                findings.append({"file": str(f.relative_to(root)), "line": idx, "rule": "hardcoded_secret_pattern", "severity": "high"})
    return {
        "offline_only": True,
        "findings_count": len(findings),
        "findings": findings[:50],
        "optional_scanners": {"trivy": bool(shutil.which("trivy")), "semgrep": bool(shutil.which("semgrep"))},
    }


def log_analyze(args: dict[str, Any]) -> dict[str, Any]:
    """Analyze log text or file: group exceptions, extract correlation IDs, redact secrets, bound excerpts."""
    raw = str(args.get("log_text") or "")
    if not raw and args.get("file_path"):
        raw = Path(str(args["file_path"])).resolve().read_text(encoding="utf-8", errors="replace")
    redacted = _SECRET_LEAK_RE.sub("[REDACTED_SECRET]", raw)
    exc_counter: Counter[str] = Counter()
    corr_ids: set[str] = set()
    error_lines: list[str] = []
    for line in redacted.splitlines():
        for cid in _CORR_ID_RE.findall(line):
            corr_ids.add(cid)
        m = _EXC_RE.search(line)
        if m:
            exc_counter[m.group(1)] += 1
        if any(lvl in line.upper() for lvl in ("ERROR", "EXCEPTION", "FATAL", "CAUSED BY")):
            error_lines.append(line[:240])
    return {
        "total_lines": len(redacted.splitlines()),
        "exception_groups": dict(exc_counter.most_common(15)),
        "correlation_ids": sorted(corr_ids)[:30],
        "error_excerpts": error_lines[:25],
        "secrets_redacted": True,
    }


DEVELOPER_TOOLS: dict[str, Any] = {
    "java_semantic_analyze": {
        "meta": {
            "name": "java_semantic_analyze",
            "description": "Analyze Java/Spring source files (packages, types, hierarchy, Spring annotations, methods with exact lines).",
            "capability_tags": ["java_semantic", "developer"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "required": ["project_path"], "properties": {"project_path": {"type": "string"}}},
        },
        "handler": java_semantic_analyze,
    },
    "ts_semantic_analyze": {
        "meta": {
            "name": "ts_semantic_analyze",
            "description": "Analyze TypeScript exports, imports, and optional real tsc --noEmit diagnostics.",
            "capability_tags": ["ts_semantic", "developer"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "required": ["project_path"], "properties": {"project_path": {"type": "string"}, "run_tsc": {"type": "boolean"}}},
        },
        "handler": ts_semantic_analyze,
    },
    "maven_inspect_pom": {
        "meta": {
            "name": "maven_inspect_pom",
            "description": "Inspect Maven pom.xml dependencies and local mvn availability.",
            "capability_tags": ["maven_build", "developer"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "required": ["pom_path"], "properties": {"pom_path": {"type": "string"}}},
        },
        "handler": maven_inspect_pom,
    },
    "junit_parse_reports": {
        "meta": {
            "name": "junit_parse_reports",
            "description": "Parse JUnit XML test reports into structured pass/fail/skip evidence.",
            "capability_tags": ["junit_test", "developer"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "required": ["report_dir"], "properties": {"report_dir": {"type": "string"}}},
        },
        "handler": junit_parse_reports,
    },
    "security_scan_local": {
        "meta": {
            "name": "security_scan_local",
            "description": "Offline local security & secret-leak scan (never uploads code to SaaS).",
            "capability_tags": ["security_scan", "developer"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "required": ["project_path"], "properties": {"project_path": {"type": "string"}}},
        },
        "handler": security_scan_local,
    },
    "log_analyze": {
        "meta": {
            "name": "log_analyze",
            "description": "Group exceptions, extract correlation IDs, redact secrets, and return bounded log excerpts.",
            "capability_tags": ["log_analyze", "observability"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "properties": {"log_text": {"type": "string"}, "file_path": {"type": "string"}}},
        },
        "handler": log_analyze,
    },
}
