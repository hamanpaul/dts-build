"""
Agent A: Vision Indexer — 讀取 schematic PDF 文字，建立索引

兩種執行模式：
- direct: 直接呼叫 indexing tools（快速、確定性）
- agent: 透過 Copilot SDK agent session（未來擴充用）
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .tools.indexing import index_all_pdfs

logger = logging.getLogger(__name__)


async def run_indexer(
    analysis_dir: Path,
    *,
    mode: str = "direct",
    model: str = "gpt-4.1",
    cli_url: str | None = None,
) -> dict[str, Any]:
    """
    讀取 .analysis/ 目錄下所有 *.txt，建立索引。

    Args:
        analysis_dir: .analysis/ 目錄路徑
        mode: "direct" (直接呼叫 tools) 或 "agent" (Copilot SDK)
        model: agent mode 使用的模型
        cli_url: agent mode 連接的 CLI server URL

    Returns:
        dict with keys:
        - page_indices: {pdf_id: {page_num: content}}
        - tag_index: {tag_name: [{pdf_id, page, context}]}
        - refdes_index: {refdes: [{pdf_id, page, part_number, context}]}
        - connector_index: {connector_refdes: {pdf_id, pins: {name: number}}}
    """
    if not analysis_dir.exists():
        raise FileNotFoundError(f"Analysis directory not found: {analysis_dir}")

    txt_files = list(analysis_dir.glob("*.txt"))
    if not txt_files:
        raise FileNotFoundError(f"No .txt files found in {analysis_dir}")

    logger.info(f"Indexing {len(txt_files)} files from {analysis_dir}")

    if mode == "direct":
        return _run_direct(analysis_dir)
    elif mode == "agent":
        return await _run_with_agent(analysis_dir, model=model, cli_url=cli_url)
    else:
        raise ValueError(f"Unknown mode: {mode}. Use 'direct' or 'agent'.")


def _run_direct(analysis_dir: Path) -> dict[str, Any]:
    """直接呼叫 indexing tools，快速且確定性。"""
    result = index_all_pdfs(analysis_dir)

    # Log summary
    n_pdfs = len(result.get("page_indices", {}))
    n_tags = len(result.get("tag_index", {}))
    n_refs = len(result.get("refdes_index", {}))
    n_conns = len(result.get("connector_index", {}))
    logger.info(
        f"Indexing complete: {n_pdfs} PDFs, {n_tags} tags, "
        f"{n_refs} refdes, {n_conns} connectors"
    )

    return result


async def _run_with_agent(
    analysis_dir: Path,
    *,
    model: str,
    cli_url: str | None,
) -> dict[str, Any]:
    """透過 Copilot SDK agent session 執行索引（未來擴充）。"""
    # For now, fall back to direct mode
    # Future: use Copilot SDK with custom tools for intelligent indexing
    logger.warning("Agent mode not yet fully implemented, falling back to direct mode")
    return _run_direct(analysis_dir)


def run_indexer_sync(
    analysis_dir: Path,
    **kwargs: Any,
) -> dict[str, Any]:
    """同步版本的 run_indexer，供 CLI 使用。"""
    return asyncio.run(run_indexer(analysis_dir, **kwargs))
