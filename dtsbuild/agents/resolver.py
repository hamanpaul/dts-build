"""
Agent C: Ambiguity Resolver — 歧義解決，唯一會 ask-me 的 agent

直接模式：系統化處理所有 INCOMPLETE/AMBIGUOUS record
Agent 模式：透過 Copilot SDK + on_user_input_request（未來擴充）
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dtsbuild.schema import HardwareSchema, ClarificationRequest, Provenance, Signal, Device
from dtsbuild.schema_io import save_schema, load_schema
from .issue_register import build_device_issue_item, build_signal_issue_item
from .tools.schema_ops import record_answer, emit_clarification

logger = logging.getLogger(__name__)

_UNRESOLVED_STATUSES = ("INCOMPLETE", "AMBIGUOUS")

# ── Role → question templates ────────────────────────────────────────

_SIGNAL_QUESTION_MAP: dict[str, tuple[str, list[str], str]] = {
    "WATCHDOG": (
        "此板是否啟用 WDT（Watchdog Timer）？",
        ["是，啟用 WDT", "否，不啟用 WDT"],
        "chip_features",
    ),
    "HSSPI": (
        "此板是否使用 HSSPI（High Speed SPI）？",
        ["是，啟用 HSSPI", "否，不使用 HSSPI"],
        "chip_features",
    ),
    "CPU_FREQ": (
        "是否啟用 CPU frequency scaling？",
        ["是，啟用 cpufreq", "否，不啟用 cpufreq"],
        "chip_features",
    ),
    "UART": (
        "此 UART 信號用途為何？",
        ["Debug console UART", "外接設備 UART", "未使用"],
        "gpio_assignment",
    ),
    "LED_CONTROL": (
        "此 LED 控制信號的極性為何？",
        ["Active Low", "Active High", "無法確認"],
        "led_polarity",
    ),
    "RESET_BUTTON": (
        "此 Reset Button 是否確認連接？GPIO 配置是否正確？",
        ["是，確認連接", "否，未連接或 DNP", "需要更多資訊"],
        "gpio_assignment",
    ),
    "ETHERNET_PHY": (
        "此 Ethernet PHY lane 是否有 lane swap？",
        ["無 swap", "有 swap，需要 DTS 設定", "無法確認"],
        "lane_swap",
    ),
    "I2C": (
        "此 I2C 信號連接的裝置資訊是否完整？",
        ["是，裝置資訊完整", "否，缺少裝置位址或型號", "此 I2C bus 未使用"],
        "gpio_assignment",
    ),
    "SFP": (
        "此 SFP 模組的 GPIO 配置是否完整？",
        ["是，所有 GPIO 已確認", "否，部分 GPIO 缺失", "此 SFP cage 未使用"],
        "gpio_assignment",
    ),
    "PCIE_WIFI": (
        "此 PCIe slot 是否安裝 WiFi 模組？",
        ["是，已安裝 WiFi 模組", "否，此 slot 未使用", "無法確認"],
        "gpio_assignment",
    ),
    "USB": (
        "此 USB port 是否啟用？",
        ["是，啟用", "否，未使用", "無法確認"],
        "gpio_assignment",
    ),
    "POWER_CONTROL": (
        "此電源控制信號的用途為何？",
        ["SLIC 電源控制", "WiFi 電源控制", "其他外設電源", "無法確認"],
        "gpio_assignment",
    ),
}

_DEVICE_QUESTION_MAP: dict[str, tuple[str, list[str], str]] = {
    "TCA9555": (
        "TCA9555 I2C GPIO expander 的 bus 和 address 是否已確認？",
        ["是，bus 和 address 已確認", "否，需要更多資訊"],
        "gpio_assignment",
    ),
    "PCA9555": (
        "PCA9555 I2C GPIO expander 的 bus 和 address 是否已確認？",
        ["是，bus 和 address 已確認", "否，需要更多資訊"],
        "gpio_assignment",
    ),
    "PCA9557": (
        "PCA9557 I2C GPIO expander 的 bus 和 address 是否已確認？",
        ["是，bus 和 address 已確認", "否，需要更多資訊"],
        "gpio_assignment",
    ),
    "74HC595": (
        "74HC595 serial LED shift register 的配置是否已確認？",
        ["是，已確認", "否，需要更多資訊"],
        "led_polarity",
    ),
}

_I2C_BUS_TOKEN_RE = re.compile(r"\b(i2c[0-9a-z_/-]*)\b", re.IGNORECASE)
_I2C_ADDRESS_TOKEN_RE = re.compile(r"\b0x[0-9a-f]{1,2}\b", re.IGNORECASE)


def _clarification_id_for_signal(signal: Signal) -> str:
    return (
        f"cr-{signal.role.lower().replace('_', '-')}-"
        f"{signal.name.lower().replace('_', '-')}"
    )


def _clarification_id_for_device(device: Device) -> str:
    return f"cr-dev-{device.refdes.lower()}"


def _device_is_i2c_expander(device: Device) -> bool:
    part_upper = device.part_number.upper()
    compatible_upper = (device.compatible or "").upper()
    return any(token in part_upper for token in ("TCA9555", "PCA9555", "PCA9557")) or any(
        token in compatible_upper for token in ("PCA9555", "PCA9557")
    )


def _device_missing_fields(device: Device) -> list[str]:
    missing: list[str] = []
    if not device.compatible:
        missing.append("compatible")
    if _device_is_i2c_expander(device):
        if not device.bus:
            missing.append("bus")
        if not device.address:
            missing.append("address")
    return missing


def _build_device_evidence_context(device: Device) -> str:
    evidence_parts = []
    if device.provenance.pdfs:
        evidence_parts.append(f"來源 PDF: {', '.join(device.provenance.pdfs)}")
    if device.compatible:
        evidence_parts.append(f"Compatible: {device.compatible} (已確認)")
    if _device_is_i2c_expander(device):
        evidence_parts.append(
            f"Bus: {device.bus} (已確認)" if device.bus else "Bus: 未確認"
        )
        evidence_parts.append(
            f"Address: {device.address} (已確認)"
            if device.address
            else "Address: 未確認"
        )
    else:
        if device.bus:
            evidence_parts.append(f"Bus: {device.bus}")
        if device.address:
            evidence_parts.append(f"Address: {device.address}")
    return "; ".join(evidence_parts) if evidence_parts else "無額外證據"


def _build_device_missing_evidence(device: Device, missing_fields: list[str]) -> str:
    if _device_is_i2c_expander(device) and missing_fields == ["bus"]:
        known = []
        if device.compatible:
            known.append(f"compatible={device.compatible}")
        if device.address:
            known.append(f"address={device.address}")
        known_text = f"；目前已知 {'、'.join(known)}" if known else ""
        return (
            f"請確認 Device '{device.refdes}' ({device.part_number}) 的 I2C bus 名稱"
            f"{known_text}"
        )

    if missing_fields:
        field_labels = {
            "compatible": "compatible",
            "bus": "bus",
            "address": "address",
        }
        joined = "、".join(field_labels.get(field, field) for field in missing_fields)
        return f"請補齊 Device '{device.refdes}' ({device.part_number}) 的 {joined} metadata"

    return f"Device '{device.refdes}' ({device.part_number}) 的 {device.status} 狀態需要確認"


def _device_ready_for_verification(device: Device) -> bool:
    if _device_is_i2c_expander(device):
        return bool(device.compatible and device.bus and device.address)
    return bool(device.compatible or device.part_number)


def _extract_bus_and_address_from_answer(answer: str) -> tuple[str | None, str | None]:
    bus_match = _I2C_BUS_TOKEN_RE.search(answer)
    address_match = _I2C_ADDRESS_TOKEN_RE.search(answer)
    bus = bus_match.group(1).lower() if bus_match else None
    address = address_match.group(0).lower() if address_match else None
    return bus, address


def _merge_user_provenance(existing: Provenance, user_prov: Provenance) -> Provenance:
    methods: list[str] = []
    for method in (existing.method, user_prov.method):
        if method and method not in methods:
            methods.append(method)

    return Provenance(
        pdfs=list(dict.fromkeys([*existing.pdfs, *user_prov.pdfs])),
        pages=list(dict.fromkeys([*existing.pages, *user_prov.pages])),
        refs=list(dict.fromkeys([*existing.refs, *user_prov.refs])),
        method="+".join(methods),
        confidence=max(existing.confidence, user_prov.confidence),
        bom_line=existing.bom_line if existing.bom_line is not None else user_prov.bom_line,
    )


# ── Question generation ──────────────────────────────────────────────

def _generate_question_for_signal(signal: Signal) -> ClarificationRequest:
    """Based on signal role and status, generate an appropriate ClarificationRequest."""
    role_upper = signal.role.upper()

    # Try exact match first, then prefix match
    question_text, choices, domain = _SIGNAL_QUESTION_MAP.get(
        role_upper,
        (None, None, None),
    )

    if question_text is None:
        for key, (q, c, d) in _SIGNAL_QUESTION_MAP.items():
            if key in role_upper or role_upper in key:
                question_text, choices, domain = q, c, d
                break

    if question_text is None:
        question_text = f"信號 '{signal.name}' (role={signal.role}) 狀態為 {signal.status}，是否需要納入 DTS？"
        choices = ["是，納入 DTS", "否，不納入", "需要更多資訊"]
        domain = "gpio_assignment"

    evidence_parts = []
    if signal.provenance.pdfs:
        evidence_parts.append(f"來源 PDF: {', '.join(signal.provenance.pdfs)}")
    if signal.provenance.pages:
        evidence_parts.append(f"頁碼: {signal.provenance.pages}")
    if signal.provenance.refs:
        evidence_parts.append(f"元件: {', '.join(signal.provenance.refs)}")
    if signal.traced_path:
        evidence_parts.append(f"追蹤路徑: {signal.traced_path}")
    evidence_context = "; ".join(evidence_parts) if evidence_parts else "無額外證據"

    missing = f"Signal '{signal.name}' (pin={signal.soc_pin}) 的 {signal.status} 狀態需要確認"

    cr_id = _clarification_id_for_signal(signal)

    return ClarificationRequest(
        id=cr_id,
        blocking=(signal.status == "AMBIGUOUS"),
        domain=domain,
        question=question_text,
        choices=choices,
        evidence_context=evidence_context,
        missing_evidence=missing,
        status="pending",
    )


def _generate_question_for_device(device: Device) -> ClarificationRequest:
    """Generate a ClarificationRequest for an INCOMPLETE/AMBIGUOUS device."""
    part_upper = device.part_number.upper()
    missing_fields = _device_missing_fields(device)

    question_text, choices, domain = None, None, None
    matched_key = None
    for key, (q, c, d) in _DEVICE_QUESTION_MAP.items():
        if key.upper() in part_upper:
            question_text, choices, domain = q, c, d
            matched_key = key
            break

    if question_text is None:
        question_text = (
            f"裝置 '{device.refdes}' ({device.part_number}) 狀態為 {device.status}，"
            f"是否需要納入 DTS？"
        )
        choices = ["是，納入 DTS", "否，不納入", "需要更多資訊"]
        domain = "gpio_assignment"

    if _device_is_i2c_expander(device) and missing_fields == ["bus"]:
        label = matched_key or device.part_number
        question_text = (
            f"{label} I2C GPIO expander 的 I2C bus 是哪一條？"
            "請直接提供 bus 名稱（例如 i2c0 / i2c1）。"
        )
        choices = [
            "i2c0",
            "i2c1",
            "其他（請直接填 bus 名稱）",
            "需要更多資訊",
        ]
        domain = "gpio_assignment"

    evidence_context = _build_device_evidence_context(device)
    missing = _build_device_missing_evidence(device, missing_fields)

    cr_id = _clarification_id_for_device(device)

    return ClarificationRequest(
        id=cr_id,
        blocking=(device.status == "AMBIGUOUS"),
        domain=domain,
        question=question_text,
        choices=choices,
        evidence_context=evidence_context,
        missing_evidence=missing,
        status="pending",
    )


# ── Answer application ───────────────────────────────────────────────

def _apply_answer(schema: HardwareSchema, cr: ClarificationRequest) -> None:
    """Update the corresponding signal/device based on the answered CR."""
    answer = (cr.answer or "").strip()
    answer_lower = answer.lower()

    cr_id = cr.id

    # Determine affirmative / negative
    is_yes = any(k in answer_lower for k in ("是", "yes", "啟用", "確認", "okay", "納入"))
    is_no = any(k in answer_lower for k in ("否", "no", "不", "未使用", "未連接", "dnp"))
    is_skip = answer_lower in ("skipped", "skip", "跳過")

    if is_skip:
        return

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    user_prov = Provenance(
        pdfs=[], pages=[], refs=[],
        method=f"user_answer:{now_str}",
        confidence=1.0,
    )

    # Try to match CR id to a signal
    for sig in schema.signals:
        expected_id = _clarification_id_for_signal(sig)
        if expected_id == cr_id:
            if is_yes:
                sig.status = "VERIFIED"
                sig.provenance = _merge_user_provenance(sig.provenance, user_prov)
            elif is_no:
                sig.status = "VERIFIED"
                sig.provenance = _merge_user_provenance(sig.provenance, user_prov)
                sig.traced_path = f"[NOT_APPLICABLE per user: {answer}]"
            else:
                # Free-form: record as-is, keep status but add provenance note
                sig.provenance = _merge_user_provenance(sig.provenance, user_prov)
                sig.traced_path = (
                    f"{sig.traced_path} [user note: {answer}]"
                    if sig.traced_path
                    else f"[user note: {answer}]"
                )
            return

    # Try to match CR id to a device
    for dev in schema.devices:
        expected_id = _clarification_id_for_device(dev)
        if expected_id == cr_id:
            bus_answer, address_answer = _extract_bus_and_address_from_answer(answer)
            needs_more_info = any(
                token in answer_lower for token in ("需要更多資訊", "無法確認", "more info")
            )
            exclude_from_dts = any(
                token in answer_lower for token in ("不納入", "未使用", "未連接", "dnp")
            )

            if bus_answer or address_answer:
                if bus_answer:
                    dev.bus = bus_answer
                if address_answer:
                    dev.address = address_answer
                if _device_ready_for_verification(dev):
                    dev.status = "VERIFIED"
                dev.provenance = _merge_user_provenance(dev.provenance, user_prov)
            elif needs_more_info:
                dev.provenance = _merge_user_provenance(dev.provenance, user_prov)
            elif exclude_from_dts:
                dev.status = "VERIFIED"
                dev.dnp = True
                dev.provenance = _merge_user_provenance(dev.provenance, user_prov)
            elif is_yes:
                if _device_ready_for_verification(dev):
                    dev.status = "VERIFIED"
                dev.provenance = _merge_user_provenance(dev.provenance, user_prov)
            else:
                dev.provenance = _merge_user_provenance(dev.provenance, user_prov)
            return


def _iter_actionable_signal_candidates(schema: HardwareSchema) -> list[Signal]:
    return [
        sig
        for sig in schema.signals
        if sig.status in _UNRESOLVED_STATUSES and build_signal_issue_item(sig).dts_relevant
    ]


def _iter_actionable_device_candidates(schema: HardwareSchema) -> list[Device]:
    return [
        dev
        for dev in schema.devices
        if dev.status in _UNRESOLVED_STATUSES and build_device_issue_item(dev).dts_relevant
    ]


def count_actionable_unresolved(schema_path: Path) -> int:
    """Return the number of unresolved items that still deserve ask-me attention."""
    schema = load_schema(schema_path)
    return len(_iter_actionable_signal_candidates(schema)) + len(
        _iter_actionable_device_candidates(schema)
    )


def _normalize_clarification_statuses(
    schema: HardwareSchema,
    actionable_cr_ids: set[str],
) -> int:
    """Retire non-DTS-relevant clarifications and reopen skipped asks for later reruns."""
    normalized = 0
    for cr in schema.clarification_requests:
        answer = (cr.answer or "").strip().upper()
        if cr.id in actionable_cr_ids:
            if cr.status == "answered" and answer == "SKIPPED":
                cr.status = "skipped"
                normalized += 1
            continue

        if cr.status == "pending":
            cr.status = "skipped"
            cr.answer = "AUTO_SKIPPED_NON_DTS_RELEVANT"
            cr.answer_provenance = "resolver:auto_suppressed"
            normalized += 1
        elif cr.status == "answered" and answer == "SKIPPED":
            cr.status = "skipped"
            normalized += 1
    return normalized


# ── Direct resolution loop ──────────────────────────────────────────

async def _resolve_direct(
    schema_path: Path,
    input_handler: Callable | None,
) -> dict[str, int]:
    """Iterate through DTS-relevant ambiguities and resolve via input_handler."""
    schema_path = Path(schema_path)
    sp_str = str(schema_path)

    schema = load_schema(schema_path)
    actionable_signals = _iter_actionable_signal_candidates(schema)
    actionable_devices = _iter_actionable_device_candidates(schema)
    actionable_cr_ids = {
        _clarification_id_for_signal(sig) for sig in actionable_signals
    } | {
        _clarification_id_for_device(dev) for dev in actionable_devices
    }

    unresolved_total = sum(
        1 for sig in schema.signals if sig.status in _UNRESOLVED_STATUSES
    ) + sum(
        1 for dev in schema.devices if dev.status in _UNRESOLVED_STATUSES
    )
    actionable_total = len(actionable_cr_ids)
    informational_suppressed = max(unresolved_total - actionable_total, 0)

    normalized = _normalize_clarification_statuses(schema, actionable_cr_ids)
    if normalized:
        save_schema(schema, schema_path)

    if actionable_total == 0:
        logger.info(
            "No DTS-relevant ambiguities found — suppressed %d informational unresolved item(s).",
            informational_suppressed,
        )
        return {
            "resolved": 0,
            "pending": 0,
            "total": 0,
            "suppressed": informational_suppressed,
        }

    logger.info(
        "Resolver focus: %d DTS-relevant unresolved (%d signals, %d devices); "
        "suppressed %d informational unresolved item(s)",
        actionable_total,
        len(actionable_signals),
        len(actionable_devices),
        informational_suppressed,
    )

    existing_cr_ids = {cr.id for cr in schema.clarification_requests}

    for sig in actionable_signals:
        cr = _generate_question_for_signal(sig)
        if cr.id not in existing_cr_ids:
            emit_clarification(
                schema_path=sp_str,
                id=cr.id,
                blocking=cr.blocking,
                domain=cr.domain,
                question=cr.question,
                choices=cr.choices,
                evidence_context=cr.evidence_context,
                missing_evidence=cr.missing_evidence,
            )
            existing_cr_ids.add(cr.id)
            logger.debug("Emitted CR: %s — %s", cr.id, cr.question)

    for dev in actionable_devices:
        cr = _generate_question_for_device(dev)
        if cr.id not in existing_cr_ids:
            emit_clarification(
                schema_path=sp_str,
                id=cr.id,
                blocking=cr.blocking,
                domain=cr.domain,
                question=cr.question,
                choices=cr.choices,
                evidence_context=cr.evidence_context,
                missing_evidence=cr.missing_evidence,
            )
            existing_cr_ids.add(cr.id)
            logger.debug("Emitted CR: %s — %s", cr.id, cr.question)

    schema = load_schema(schema_path)

    resolved = 0
    still_pending = 0

    for cr in schema.clarification_requests:
        if cr.id not in actionable_cr_ids or cr.status not in ("pending", "skipped"):
            continue

        if input_handler is None:
            logger.info("No input_handler — deferring: %s", cr.question)
            still_pending += 1
            continue

        # Call the input handler
        request = {
            "question": cr.question,
            "choices": cr.choices,
            "allowFreeform": True,
        }
        response = input_handler(request)
        answer = response.get("answer", "SKIPPED")
        was_freeform = response.get("wasFreeform", False)

        # Record the answer
        record_answer(
            schema_path=sp_str,
            cr_id=cr.id,
            answer=answer,
            was_freeform=was_freeform,
        )

        # Apply the answer to the corresponding record
        schema = load_schema(schema_path)
        # Find the now-answered CR
        for updated_cr in schema.clarification_requests:
            if updated_cr.id == cr.id:
                _apply_answer(schema, updated_cr)
                break
        save_schema(schema, schema_path)

        if answer.upper() != "SKIPPED":
            resolved += 1
        else:
            still_pending += 1

    logger.info(
        "Resolution complete: %d resolved, %d still pending (of %d DTS-relevant total)",
        resolved, still_pending, actionable_total,
    )

    return {
        "resolved": resolved,
        "pending": still_pending,
        "total": actionable_total,
        "suppressed": informational_suppressed,
    }


# ── Public entry point ───────────────────────────────────────────────

async def run_resolver(
    schema_path: Path,
    input_handler: Callable | None = None,
    mode: str = "direct",
) -> dict[str, int]:
    """
    處理 schema 中的 INCOMPLETE/AMBIGUOUS record，透過 ask-me 向使用者提問。

    Args:
        schema_path: Hardware schema YAML 路徑（讀寫）
        input_handler: CLI input handler（None = non-interactive, just log）
        mode: "direct" (default) — future: "agent" for Copilot SDK session

    Returns:
        Summary dict with resolved / pending / total counts.
    """
    schema_path = Path(schema_path)

    if mode == "direct":
        return await _resolve_direct(schema_path, input_handler)
    else:
        raise ValueError(f"Unsupported resolver mode: {mode!r}")
