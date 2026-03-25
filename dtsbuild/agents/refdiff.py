"""Normalize generated-vs-reference DTS differences into semantic candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .calibration import (
    RefDiffCandidate,
    RefDiffReport,
    make_candidate_id,
    write_refdiff_report,
)

_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_NODE_HEADER_RE = re.compile(r"^(?:(?P<label>[\w.-]+)\s*:\s*)?(?P<name>.+?)\s*\{$")
_RENDER_SURFACES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^/buttons(?:/|$)"), "_render_buttons"),
    (re.compile(r"^/&uart\d+(?:/|$)"), "_render_uart"),
    (re.compile(r"^/&wdt(?:/|$)"), "_render_wdt"),
    (re.compile(r"^/&hsspi(?:/|$)"), "_render_hsspi"),
    (re.compile(r"^/&led_ctrl(?:/|$)"), "_render_led_ctrl"),
    (re.compile(r"^/&xport(?:/|$)"), "_render_xport"),
    (re.compile(r"^/&ethphytop(?:/|$)"), "_render_ethphy"),
    (re.compile(r"^/&mdio(?:/|$)"), "_render_mdio"),
    (re.compile(r"^/&mdio_bus(?:/|$)"), "_render_mdio_bus"),
    (re.compile(r"^/&switch0(?:/|$)"), "_render_switch0"),
    (re.compile(r"^/&i2c\d+(?:/|$)"), "_render_i2c"),
    (re.compile(r"^/&usb_ctrl(?:/|$)"), "_render_usb"),
    (re.compile(r"^/&usb0_xhci(?:/|$)"), "_render_usb"),
    (re.compile(r"^/&pcie\d+(?:/|$)"), "_render_pcie"),
    (re.compile(r"^/&wan_serdes(?:/|$)"), "_render_serdes"),
    (re.compile(r"^/wan_sfp(?:/|$)"), "_render_wan_sfp"),
    (re.compile(r"^/&ext_pwr_ctrl(?:/|$)"), "_render_power_ctrl"),
    (re.compile(r"^/&cpufreq(?:/|$)"), "_render_cpufreq"),
)


def _strip_block_comments_preserving_lines(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return _COMMENT_BLOCK_RE.sub(_replace, text)


@dataclass(slots=True)
class DtsProperty:
    name: str
    value: str | None
    line: int


@dataclass(slots=True)
class DtsNode:
    path: str
    name: str
    label: str | None
    start_line: int
    end_line: int | None = None
    properties: dict[str, DtsProperty] = field(default_factory=dict)


@dataclass(slots=True)
class DtsDocument:
    path: Path
    nodes: list[DtsNode]

    def node_index(self) -> dict[str, list[DtsNode]]:
        index: dict[str, list[DtsNode]] = {}
        for node in self.nodes:
            index.setdefault(node.path, []).append(node)
        return index


def parse_dts_document(path: Path) -> DtsDocument:
    """Parse a DTS file into a lightweight node/property tree."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    text = _strip_block_comments_preserving_lines(text)
    total_lines = len(text.splitlines())
    nodes: list[DtsNode] = []
    stack: list[DtsNode] = []

    for lineno, raw_line in _iter_active_dts_lines(text):
        line = _strip_inline_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line == "/dts-v1/;":
            continue

        if line in {"};", "}"}:
            if stack:
                node = stack.pop()
                node.end_line = lineno
            continue

        if line.endswith("{"):
            match = _NODE_HEADER_RE.match(line)
            if not match:
                continue
            node_name = match.group("name").strip()
            label = match.group("label")
            node_path = _node_path(stack[-1].path if stack else "", node_name)
            node = DtsNode(
                path=node_path,
                name=node_name,
                label=label,
                start_line=lineno,
            )
            nodes.append(node)
            stack.append(node)
            continue

        if not stack:
            continue

        prop = _parse_property(line, lineno)
        if prop is None:
            continue
        stack[-1].properties[prop.name] = prop

    for node in stack:
        if node.end_line is None:
            node.end_line = total_lines
    return DtsDocument(path=path, nodes=nodes)


def build_refdiff_report(
    project: str,
    generated_dts_path: Path,
    reference_dts_path: Path,
    schema_path: Path | None = None,
    validation_path: Path | None = None,
    unresolved_path: Path | None = None,
) -> RefDiffReport:
    """Compare generated and reference DTS files as semantic candidates."""
    generated_doc = parse_dts_document(generated_dts_path)
    reference_doc = parse_dts_document(reference_dts_path)
    generated_index = generated_doc.node_index()
    reference_index = reference_doc.node_index()
    candidates: list[RefDiffCandidate] = []

    # Binding mismatches caused by duplicate overlays / different multiplicity.
    all_paths = sorted(set(generated_index) | set(reference_index))
    for path in all_paths:
        gen_nodes = generated_index.get(path, [])
        ref_nodes = reference_index.get(path, [])
        if gen_nodes and ref_nodes and len(gen_nodes) != len(ref_nodes):
            candidates.append(
                RefDiffCandidate(
                    id=make_candidate_id("binding_mismatch", path),
                    candidate_type="binding_mismatch",
                    target=path,
                    project=project,
                    summary=(
                        f"Node '{path}' appears {len(gen_nodes)} time(s) in generated DTS "
                        f"and {len(ref_nodes)} time(s) in reference DTS."
                    ),
                    route_hint="renderer",
                    subsystem=_derive_subsystem(path),
                    dts_relevant=True,
                    generated_locator=_locator(generated_doc.path.name, gen_nodes[0].start_line),
                    reference_locator=_locator(reference_doc.path.name, ref_nodes[0].start_line),
                    reason="Overlay multiplicity differs and may indicate a binding aggregation mismatch.",
                    compiler_surface=_infer_compiler_surface(path),
                )
            )

    # Missing / unsupported nodes and shared-node property diffs.
    for path, ref_nodes in sorted(reference_index.items()):
        if path == "/":
            continue
        ref_node = ref_nodes[0]
        gen_nodes = generated_index.get(path, [])
        if not gen_nodes:
            surface = _infer_compiler_surface(path)
            candidate_type = "missing_node" if surface else "unsupported_surface"
            route_hint = "renderer" if surface else "capability"
            candidates.append(
                RefDiffCandidate(
                    id=make_candidate_id(candidate_type, path),
                    candidate_type=candidate_type,
                    target=path,
                    project=project,
                    summary=f"Reference DTS defines node '{path}' but generated DTS does not.",
                    route_hint=route_hint,
                    subsystem=_derive_subsystem(path),
                    dts_relevant=True,
                    reference_locator=_locator(reference_doc.path.name, ref_node.start_line),
                    reason=(
                        "Reference-only node is missing from generated output."
                        if surface
                        else "Reference-only node has no known compiler surface yet."
                    ),
                    compiler_surface=surface,
                )
            )
            continue

        gen_node = gen_nodes[0]
        candidates.extend(
            _compare_node_properties(
                project=project,
                path=path,
                generated_doc=generated_doc,
                reference_doc=reference_doc,
                generated_node=gen_node,
                reference_node=ref_node,
            )
        )

    # Generated-only nodes.
    for path, gen_nodes in sorted(generated_index.items()):
        if path == "/" or path in reference_index:
            continue
        gen_node = gen_nodes[0]
        candidates.append(
            RefDiffCandidate(
                id=make_candidate_id("extra_generated_node", path),
                candidate_type="extra_generated_node",
                target=path,
                project=project,
                summary=f"Generated DTS defines node '{path}' but reference DTS does not.",
                route_hint="reject",
                subsystem=_derive_subsystem(path),
                dts_relevant=True,
                generated_locator=_locator(generated_doc.path.name, gen_node.start_line),
                reason="Generated-only node should be reviewed before being trusted as an intentional board fact.",
                compiler_surface=_infer_compiler_surface(path),
            )
        )

    return RefDiffReport(
        project=project,
        generated_dts_path=str(generated_dts_path),
        reference_dts_path=str(reference_dts_path),
        schema_path=str(schema_path) if schema_path else None,
        validation_path=str(validation_path) if validation_path else None,
        unresolved_path=str(unresolved_path) if unresolved_path else None,
        candidates=candidates,
    )


def build_and_write_refdiff_report(
    project: str,
    generated_dts_path: Path,
    reference_dts_path: Path,
    output_path: Path,
    schema_path: Path | None = None,
    validation_path: Path | None = None,
    unresolved_path: Path | None = None,
) -> RefDiffReport:
    """Convenience helper for refdiff generation + write."""
    report = build_refdiff_report(
        project=project,
        generated_dts_path=generated_dts_path,
        reference_dts_path=reference_dts_path,
        schema_path=schema_path,
        validation_path=validation_path,
        unresolved_path=unresolved_path,
    )
    write_refdiff_report(report, output_path)
    return report


def _compare_node_properties(
    project: str,
    path: str,
    generated_doc: DtsDocument,
    reference_doc: DtsDocument,
    generated_node: DtsNode,
    reference_node: DtsNode,
) -> list[RefDiffCandidate]:
    candidates: list[RefDiffCandidate] = []
    surface = _infer_compiler_surface(path)

    for prop_name, ref_prop in reference_node.properties.items():
        gen_prop = generated_node.properties.get(prop_name)
        if gen_prop is None:
            candidates.append(
                RefDiffCandidate(
                    id=make_candidate_id("missing_property", f"{path}:{prop_name}"),
                    candidate_type="missing_property",
                    target=f"{path}:{prop_name}",
                    project=project,
                    summary=(
                        f"Reference DTS defines property '{prop_name}' in node '{path}' "
                        "but generated DTS does not."
                    ),
                    route_hint="renderer",
                    subsystem=_derive_subsystem(path),
                    dts_relevant=True,
                    reference_value=ref_prop.value,
                    reference_locator=_locator(reference_doc.path.name, ref_prop.line),
                    reason="Shared node exists, but the reference property is absent from generated output.",
                    compiler_surface=surface,
                )
            )
            continue

        if gen_prop.value != ref_prop.value:
            candidates.append(
                RefDiffCandidate(
                    id=make_candidate_id("value_mismatch", f"{path}:{prop_name}"),
                    candidate_type="value_mismatch",
                    target=f"{path}:{prop_name}",
                    project=project,
                    summary=(
                        f"Property '{prop_name}' in node '{path}' differs between generated "
                        "and reference DTS."
                    ),
                    route_hint="renderer",
                    subsystem=_derive_subsystem(path),
                    dts_relevant=True,
                    generated_value=gen_prop.value,
                    reference_value=ref_prop.value,
                    generated_locator=_locator(generated_doc.path.name, gen_prop.line),
                    reference_locator=_locator(reference_doc.path.name, ref_prop.line),
                    reason="Shared property has different values and needs evidence-backed review.",
                    compiler_surface=surface,
                )
            )

    return candidates


def _parse_property(line: str, lineno: int) -> DtsProperty | None:
    if line.startswith("/delete-property/"):
        name = line[len("/delete-property/"):].strip().rstrip(";")
        return DtsProperty(name=f"/delete-property/ {name}", value=None, line=lineno)

    if not line.endswith(";"):
        return None
    body = line[:-1].strip()
    if not body:
        return None
    if "=" in body:
        name, value = body.split("=", 1)
        return DtsProperty(name=name.strip(), value=value.strip(), line=lineno)
    return DtsProperty(name=body, value=None, line=lineno)


def _strip_inline_comment(line: str) -> str:
    if "//" not in line:
        return line
    prefix, _comment = line.split("//", 1)
    return prefix


def _iter_active_dts_lines(text: str):
    """Yield active DTS lines, skipping inactive preprocessor branches.

    Reference DTS files often contain mutually-exclusive ``#if/#else`` blocks.
    Refdiff uses the reference only as a diff oracle, so parsing both branches
    creates false positives (for example ``buttons`` and ``gpio-keys`` at once).
    When we cannot evaluate macros here, prefer the first branch and suppress
    later ``#elif``/``#else`` alternatives.
    """

    stack: list[tuple[bool, bool, bool]] = []
    active = True

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.lstrip()
        if stripped.startswith(("#if", "#ifdef", "#ifndef")):
            parent_active = active
            current_active = parent_active
            branch_taken = current_active
            stack.append((parent_active, current_active, branch_taken))
            active = current_active
            continue
        if stripped.startswith("#elif"):
            if stack:
                parent_active, _current_active, branch_taken = stack[-1]
                current_active = parent_active and not branch_taken
                stack[-1] = (parent_active, current_active, branch_taken or current_active)
                active = current_active
            continue
        if stripped.startswith("#else"):
            if stack:
                parent_active, _current_active, branch_taken = stack[-1]
                current_active = parent_active and not branch_taken
                stack[-1] = (parent_active, current_active, True)
                active = current_active
            continue
        if stripped.startswith("#endif"):
            if stack:
                stack.pop()
            active = stack[-1][1] if stack else True
            continue
        if active:
            yield lineno, raw_line


def _node_path(parent: str, name: str) -> str:
    if name == "/":
        return "/"
    base = parent.rstrip("/") if parent and parent != "/" else ""
    return f"{base}/{name}"


def _derive_subsystem(path: str) -> str:
    path_lower = path.lower()
    if "phy_wan_serdes" in path_lower:
        return "serdes"

    tokens = [
        token.lstrip("&")
        for token in re.split(r"[/@:.-]+", path_lower)
        if token and token != "&"
    ]
    for token in tokens:
        if token in {"ext_pwr_ctrl", "ext_pwr", "pwr_ctrl"}:
            return "power"
        if token.startswith("pcie"):
            return "pcie"
        if token.startswith("i2c"):
            return "i2c"
        if token.startswith("uart"):
            return "uart"
        if token.startswith("led"):
            return "led"
        if token.startswith("eth") or token.startswith("xphy") or token.startswith("mdio"):
            return "ethernet"
        if token.startswith("usb"):
            return "usb"
        if token.startswith("serdes") or token.startswith("sfp"):
            return "serdes"
        if token.startswith("power") or token.startswith("pwr") or "cpufreq" in token:
            return "power"
        if token.startswith("button") or token == "buttons":
            return "buttons"
        if token in {"pincontroller", "pinctrl"}:
            return "pinctrl"
    return "general"


def _infer_compiler_surface(path: str) -> str | None:
    for pattern, surface in _RENDER_SURFACES:
        if pattern.match(path):
            return surface
    return None


def _locator(filename: str, line: int) -> str:
    return f"{filename}:{line}"
