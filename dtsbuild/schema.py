"""Hardware Connectivity Schema — semantic intermediate layer.

Bridges raw circuit evidence (CSV / PDF text) and DTS generation.
Every inferred fact carries provenance (source pages, refs, method, confidence).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Provenance(BaseModel):
    """Source tracking attached to every inferred fact."""

    model_config = ConfigDict(extra="forbid")

    pdfs: list[str] = Field(
        description='Source PDF filenames (supports cross-PDF tracing, '
                    'e.g. ["mainboard.pdf", "daughter.pdf"])',
    )
    pages: list[int] = Field(description="Page numbers within the PDFs")
    refs: list[str] = Field(
        description='Reference designators involved (e.g. ["U1E", "HN2436G", "J3"])',
    )
    method: str = Field(
        description="How the fact was derived "
                    '(e.g. "net_trace", "differential_pair_trace", "bom_lookup", '
                    '"user_answer", "gpio_table")',
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0.0 to 1.0")
    bom_line: Optional[int] = Field(default=None, description="BOM line number if applicable")


class Signal(BaseModel):
    """A traced signal/net from SoC to destination."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description='Signal name (e.g. "UART0_TX", "GPHY1_DP0")')
    soc_pin: str = Field(description="SoC pin designation")
    traced_path: str = Field(
        description='Human-readable path description '
                    '(e.g. "U1.Pin45 → R23(0R) → J1.Pin3")',
    )
    role: str = Field(
        description='Semantic role (e.g. "DEBUG_UART_TX", "ETHERNET_PHY_LANE", '
                    '"RESET_BUTTON", "LED_CONTROL")',
    )
    status: Literal["VERIFIED", "INCOMPLETE", "AMBIGUOUS"]
    swap_detected: Optional[bool] = Field(
        default=None, description="For differential pair lane swap detection",
    )
    swap_detail: Optional[str] = Field(
        default=None,
        description='e.g. "DP0↔DP1 at RJ45 connector J3"',
    )
    provenance: Provenance


class Device(BaseModel):
    """A component on the board."""

    model_config = ConfigDict(extra="forbid")

    refdes: str = Field(description='Reference designator (e.g. "U8", "J5")')
    part_number: str = Field(description='Actual part number (e.g. "TCA9555PWR")')
    compatible: Optional[str] = Field(
        default=None,
        description='Linux DT compatible string (e.g. "nxp,pca9555")',
    )
    bus: Optional[str] = Field(
        default=None, description='Bus type (e.g. "i2c0", "spi0", "pcie")',
    )
    address: Optional[str] = Field(
        default=None, description='Bus address (e.g. "0x27")',
    )
    status: Literal["VERIFIED", "INCOMPLETE", "AMBIGUOUS"]
    dnp: bool = Field(default=False, description="Do Not Populate")
    provenance: Provenance


class TracedPath(BaseModel):
    """A complete trace from source to destination."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique path identifier")
    source: str = Field(description='Starting point (e.g. "U1E.GPHY1_DP0_P")')
    destination: str = Field(description='Ending point (e.g. "J3.Pin4")')
    segments: list[str] = Field(description="Ordered list of trace segments")
    crosses_pdf: bool = Field(
        default=False, description="Whether trace crosses PDF boundaries",
    )
    pdf_sequence: list[str] = Field(description="Ordered list of PDFs traversed")
    passive_components: list[str] = Field(
        description="Passive components penetrated (0R resistors, etc.)",
    )
    provenance: Provenance


class ClarificationRequest(BaseModel):
    """A question for the user when evidence is incomplete or ambiguous."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description='Unique identifier (e.g. "cr-wdt-001")')
    blocking: bool = Field(description="Whether this blocks DTS generation")
    domain: str = Field(
        description='Category (e.g. "chip_features", "gpio_assignment", '
                    '"led_polarity", "lane_swap")',
    )
    question: str = Field(description="The question text")
    choices: list[str] = Field(description="Predefined answer choices")
    evidence_context: str = Field(description="What evidence exists")
    missing_evidence: str = Field(description="What evidence is needed")
    status: Literal["pending", "answered", "skipped"]
    answer: Optional[str] = Field(default=None, description="User's answer")
    answer_provenance: Optional[str] = Field(
        default=None,
        description='How/when answered (e.g. "user_input:2024-01-15", "default_assumed")',
    )


class DtsHint(BaseModel):
    """A hint for DTS generation derived from traced evidence."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(
        description='DTS node target (e.g. "&uart0", "ethphytop", "&mdio_bus/xphy1")'
    )
    property: Optional[str] = Field(
        default=None,
        description='Specific property (e.g. "enet-phy-lane-swap")',
    )
    value: Optional[str] = Field(
        default=None, description="Property value if applicable",
    )
    reason: str = Field(description="Why this hint exists")
    provenance: Provenance


class HardwareSchema(BaseModel):
    """Top-level container aggregating all hardware connectivity evidence."""

    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    project: str = Field(description='Project name (e.g. "BGW720")')
    chip: str = Field(description='Chip identifier (e.g. "BCM68575")')
    signals: list[Signal] = []
    devices: list[Device] = []
    paths: list[TracedPath] = []
    clarification_requests: list[ClarificationRequest] = []
    dts_hints: list[DtsHint] = []
    user_answers: dict[str, str] = Field(
        default_factory=dict, description="cr_id → answer mapping",
    )

    # -- helper methods ------------------------------------------------

    def verified_signals(self) -> list[Signal]:
        """Return signals whose status is VERIFIED."""
        return [s for s in self.signals if s.status == "VERIFIED"]

    def verified_devices(self) -> list[Device]:
        """Return devices whose status is VERIFIED."""
        return [d for d in self.devices if d.status == "VERIFIED"]

    def pending_clarifications(self) -> list[ClarificationRequest]:
        """Return clarification requests still awaiting an answer."""
        return [c for c in self.clarification_requests if c.status == "pending"]

    def has_lane_swap(self, prefix: str) -> bool:
        """Check if any signal whose name starts with *prefix* has swap_detected."""
        return any(
            s.swap_detected
            for s in self.signals
            if s.name.startswith(prefix) and s.swap_detected is not None
        )

    def get_dts_hints_for(self, target: str) -> list[DtsHint]:
        """Return DTS hints that apply to the given target node."""
        return [h for h in self.dts_hints if h.target == target]
