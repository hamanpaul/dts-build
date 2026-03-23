"""
4-Agent 電路分析架構

Agent A: Vision Indexer — PDF 索引，建立 tag/refdes/connector mapping
Agent B: Connectivity Auditor — 跟線追蹤，跨頁/跨 PDF，lane swap 偵測
Agent C: Ambiguity Resolver — 歧義解決，唯一會 ask-me 的 agent
Agent D: DTS Compiler — 確定性 DTS 產出，只讀 VERIFIED schema record
"""

from .orchestrator import run_pipeline, run_pipeline_sync

__all__ = ["run_pipeline", "run_pipeline_sync"]
