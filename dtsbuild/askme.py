"""
Ask-Me CLI 互動處理器

基於 Copilot SDK 的 on_user_input_request 機制。
當 Ambiguity Resolver agent 遇到無法從電路圖確認的項目時，
透過此 handler 向使用者提問。

SDK 整合方式：
    resolver_session = await client.create_session({
        ...
        "on_user_input_request": cli_input_handler,
    })
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_QUESTION_SPACE_RE = re.compile(r"\s+")


def _normalize_question_text(question: str) -> str:
    """Normalize question text so stored answers survive formatting drift."""
    return _QUESTION_SPACE_RE.sub("", question.strip())


class AnswerLog:
    """記錄使用者的所有回答，附帶時間戳和 provenance。"""

    def __init__(self, log_path: Path | None = None):
        self._log_path = log_path
        self._answers: list[dict[str, Any]] = []
        if log_path and log_path.exists():
            self._answers = json.loads(log_path.read_text(encoding="utf-8"))

    def record(
        self,
        question: str,
        answer: str,
        was_freeform: bool,
        choices: list[str] | None = None,
    ) -> None:
        """記錄一筆回答。"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "question": question,
            "answer": answer,
            "was_freeform": was_freeform,
            "choices": choices or [],
        }
        self._answers.append(entry)
        if self._log_path:
            self._log_path.write_text(
                json.dumps(self._answers, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    @property
    def answers(self) -> list[dict[str, Any]]:
        return list(self._answers)

    def find_entry_for(self, question: str) -> dict[str, Any] | None:
        """Return the newest matching answer entry for *question*."""
        normalized = _normalize_question_text(question)
        if not normalized:
            return None

        partial_match: dict[str, Any] | None = None
        for entry in reversed(self._answers):
            entry_question = _normalize_question_text(str(entry.get("question", "")))
            if not entry_question:
                continue
            if entry_question == normalized:
                return dict(entry)
            if partial_match is None and (
                normalized in entry_question or entry_question in normalized
            ):
                partial_match = dict(entry)

        return partial_match

    def get_answer_for(self, question_substring: str) -> str | None:
        """查找包含特定文字的問題的回答。"""
        entry = self.find_entry_for(question_substring)
        if entry is None:
            return None
        answer = entry.get("answer")
        return str(answer) if answer is not None else None


# Global answer log (set by create_cli_handler)
_answer_log: AnswerLog | None = None


def create_cli_handler(
    log_path: Path | None = None,
    auto_answers: dict[str, str] | None = None,
) -> tuple[Any, AnswerLog]:
    """
    建立 CLI input handler 和對應的 AnswerLog。

    Args:
        log_path: 回答記錄檔路徑（JSON 格式）
        auto_answers: 預設回答對照表（question substring → answer），
                      用於非互動模式或自動化測試

    Returns:
        (handler_function, answer_log) tuple

    Usage:
        handler, log = create_cli_handler(log_path=Path("answers.json"))
        session = await client.create_session({
            "on_user_input_request": handler,
        })
    """
    answer_log = AnswerLog(log_path)

    def cli_input_handler(
        request: dict[str, Any],
        context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        CLI handler：印出問題，讀取 stdin，回傳回答。

        SDK 會把 UserInputRequest 序列化為 dict 傳入：
          {"question": "...", "choices": [...], "allowFreeform": true/false}

        回傳 UserInputResponse dict：
          {"answer": "...", "wasFreeform": true/false}
        """
        question = request.get("question", "")
        choices = request.get("choices", [])
        allow_freeform = request.get("allowFreeform", True)

        # Check auto_answers first (for testing / non-interactive)
        if auto_answers:
            for substring, auto_answer in auto_answers.items():
                if substring in question:
                    answer_log.record(
                        question=question,
                        answer=auto_answer,
                        was_freeform=False,
                        choices=choices,
                    )
                    print(f"\n🤖 自動回答：{question}")
                    print(f"   → {auto_answer}")
                    return {"answer": auto_answer, "wasFreeform": False}

        # Interactive CLI prompt
        print(f"\n{'='*60}")
        print(f"🔍 Agent 需要你的輸入：")
        print(f"   {question}")
        print(f"{'─'*60}")

        if choices:
            for i, choice in enumerate(choices, 1):
                print(f"   [{i}] {choice}")
            if allow_freeform:
                print(f"   或直接輸入自由文字")
            print()

        try:
            raw = input("   你的回答 → ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n   （跳過此問題）")
            answer_log.record(
                question=question,
                answer="SKIPPED",
                was_freeform=True,
                choices=choices,
            )
            return {"answer": "SKIPPED", "wasFreeform": True}

        # Parse numbered choice
        if choices and raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(choices):
                selected = choices[idx - 1]
                answer_log.record(
                    question=question,
                    answer=selected,
                    was_freeform=False,
                    choices=choices,
                )
                print(f"   ✓ 已選擇：{selected}")
                return {"answer": selected, "wasFreeform": False}

        # Freeform answer
        answer_log.record(
            question=question,
            answer=raw,
            was_freeform=True,
            choices=choices,
        )
        print(f"   ✓ 已記錄")
        return {"answer": raw, "wasFreeform": True}

    return cli_input_handler, answer_log


def create_non_interactive_handler(
    default_action: str = "skip",
    log_path: Path | None = None,
) -> tuple[Any, AnswerLog]:
    """
    建立非互動模式的 handler。

    所有問題都自動回答 SKIPPED 或使用預設值。
    用於 CI/CD 或非互動環境。

    Args:
        default_action: "skip" 或 "fail"

    Returns:
        (handler_function, answer_log) tuple
    """
    answer_log = AnswerLog(log_path)

    def non_interactive_handler(
        request: dict[str, Any],
        context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        question = request.get("question", "")
        choices = request.get("choices", [])
        replay = answer_log.find_entry_for(question)

        if replay and replay.get("answer") is not None:
            answer = str(replay["answer"])
            was_freeform = bool(replay.get("was_freeform", True))
            print(f"\n🤖 重播既有回答：{question}")
            print(f"   → {answer}")
            return {"answer": answer, "wasFreeform": was_freeform}

        if default_action == "fail":
            raise RuntimeError(
                f"非互動模式下收到使用者提問（設為 fail）：{question}"
            )

        print(f"\n⏭️  跳過（非互動模式）：{question}")
        answer_log.record(
            question=question,
            answer="SKIPPED",
            was_freeform=True,
            choices=choices,
        )
        return {"answer": "SKIPPED", "wasFreeform": True}

    return non_interactive_handler, answer_log
