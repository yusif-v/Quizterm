#!/usr/bin/env python3
"""Quizterm — exam-agnostic terminal quiz bot.  v0.2.0

Usage:
    quizterm                       # loads ./questions.json
    quizterm path/to/questions.json
    quizterm --help

Question file format (JSON):

    Either a bare array:
        [ {question_obj}, ... ]

    Or an object with metadata:
        {
          "title": "My Exam",
          "questions": [ {question_obj}, ... ]
        }

    Each question object:
        {
          "id":         "ch1-q1",                  # required, unique
          "chapter":    "ch1",                     # optional, groups questions
          "chapter_title": "Foundations",          # optional, display name
          "number":     1,                         # optional
          "question":   "...",                     # required
          "options":    {"A": "...", "B": "..."},  # required, 2+ keys
          "correct":    "A",                       # required, key of options
          "explanation": "..."                     # optional but recommended
        }

History (correct/wrong per question id) is saved next to the questions file
as `<basename>.history.json`.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.align import Align


MARGIN = 6
MAX_WIDTH = 100

_raw_console = Console()


class MarginConsole:
    def __init__(self, inner: Console):
        self.inner = inner

    def clear(self):
        self.inner.clear()

    def print(self, *args, **kwargs):
        if not args:
            self.inner.print(**kwargs)
            return
        for a in args:
            self.inner.print(Padding(a, (0, MARGIN)), **kwargs)


console = MarginConsole(_raw_console)


def ask(prompt_text: str, **kwargs):
    indent = " " * MARGIN
    return Prompt.ask(indent + prompt_text, console=_raw_console, **kwargs)


# ---------- Data loading ----------

def load_questions(path: Path) -> tuple[str, list[dict]]:
    """Return (title, questions). Accepts array or {title, questions} object."""
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return path.stem, data
    if isinstance(data, dict) and "questions" in data:
        return data.get("title") or path.stem, data["questions"]
    raise ValueError(
        f"Unrecognized JSON in {path}: expected array or object with 'questions'"
    )


def history_path_for(questions_path: Path) -> Path:
    return questions_path.with_name(questions_path.stem + ".history.json")


def load_history(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_history(path: Path, h: dict) -> None:
    path.write_text(json.dumps(h, indent=2))


def derive_chapters(questions: list[dict]) -> list[tuple[str, str]]:
    """Return ordered list of (chapter_id, chapter_title) preserving first-seen order."""
    seen: dict[str, str] = {}
    for q in questions:
        cid = q.get("chapter")
        if not cid or cid in seen:
            continue
        seen[cid] = q.get("chapter_title") or cid
    return list(seen.items())


def validate(questions: list[dict]) -> list[str]:
    """Return list of validation errors (empty if all good)."""
    errs: list[str] = []
    seen_ids: set[str] = set()
    for i, q in enumerate(questions):
        loc = f"#{i}" + (f" ({q.get('id')})" if q.get("id") else "")
        for k in ("id", "question", "options", "correct"):
            if k not in q:
                errs.append(f"{loc}: missing '{k}'")
        if q.get("id") in seen_ids:
            errs.append(f"{loc}: duplicate id")
        if q.get("id"):
            seen_ids.add(q["id"])
        opts = q.get("options")
        if isinstance(opts, dict):
            if len(opts) < 2:
                errs.append(f"{loc}: options must have ≥ 2 keys")
            if q.get("correct") and q["correct"] not in opts:
                errs.append(f"{loc}: correct '{q['correct']}' not in options")
    return errs


# ---------- UI ----------

def banner(title: str, n_questions: int):
    console.clear()
    t = Text(title, style="bold cyan")
    sub = Text(f"{n_questions} questions loaded", style="dim")
    _raw_console.print(Align.center(t))
    _raw_console.print(Align.center(sub))
    _raw_console.print()


def pick_mode(has_chapters: bool):
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold yellow")
    table.add_column()
    table.add_row("1.", "All questions")
    if has_chapters:
        table.add_row("2.", "By chapter / group")
    table.add_row("3.", "Random N questions")
    table.add_row("4.", "Wrong answers only (from history)")
    table.add_row("5.", "Show stats")
    table.add_row("q.", "Quit")
    console.print(Panel(table, title="[bold]Mode[/bold]", border_style="cyan"))
    choices = ["1", "3", "4", "5", "q"]
    if has_chapters:
        choices.insert(1, "2")
    return ask("Choose", choices=choices, default="1")


def pick_chapter(chapters: list[tuple[str, str]]):
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold yellow")
    table.add_column()
    for i, (_, title) in enumerate(chapters, 1):
        table.add_row(str(i), title)
    console.print(Panel(table, title="[bold]Chapter[/bold]", border_style="cyan"))
    choice = ask("Chapter", choices=[str(i) for i in range(1, len(chapters) + 1)])
    return chapters[int(choice) - 1][0]


def select_questions(mode: str, all_qs: list[dict], history: dict,
                     chapters: list[tuple[str, str]]):
    if mode == "1":
        return list(all_qs)
    if mode == "2":
        cid = pick_chapter(chapters)
        return [q for q in all_qs if q.get("chapter") == cid]
    if mode == "3":
        n_str = ask("How many", default="10")
        try:
            n = max(1, min(int(n_str), len(all_qs)))
        except ValueError:
            n = 10
        return random.sample(all_qs, n)
    if mode == "4":
        wrong_ids = {qid for qid, status in history.items() if status == "wrong"}
        unseen = [q for q in all_qs if q["id"] not in history]
        wrong = [q for q in all_qs if q["id"] in wrong_ids]
        pool = wrong + unseen
        if not pool:
            console.print("[green]No wrong answers in history. Take some quizzes first![/green]")
            return []
        console.print(f"[dim]{len(wrong)} previously wrong + {len(unseen)} unseen[/dim]")
        return pool
    return []


def show_stats(all_qs: list[dict], history: dict,
               chapters: list[tuple[str, str]]) -> None:
    by_chapter = {cid: {"total": 0, "correct": 0, "wrong": 0, "unseen": 0}
                  for cid, _ in chapters}
    other = {"total": 0, "correct": 0, "wrong": 0, "unseen": 0}

    for q in all_qs:
        cid = q.get("chapter")
        bucket = by_chapter.get(cid, other) if cid else other
        bucket["total"] += 1
        status = history.get(q.get("id"))
        if status == "correct":
            bucket["correct"] += 1
        elif status == "wrong":
            bucket["wrong"] += 1
        else:
            bucket["unseen"] += 1

    table = Table(title="History by chapter", border_style="cyan")
    table.add_column("Chapter", style="bold")
    table.add_column("Total", justify="right")
    table.add_column("✓", justify="right", style="green")
    table.add_column("✗", justify="right", style="red")
    table.add_column("?", justify="right", style="dim")
    table.add_column("Score", justify="right")

    grand = {"total": 0, "correct": 0, "wrong": 0, "unseen": 0}

    def row_for(name: str, s: dict):
        for k in grand: grand[k] += s[k]
        seen = s["correct"] + s["wrong"]
        pct = f"{int(100 * s['correct'] / seen)}%" if seen else "—"
        table.add_row(name, str(s["total"]), str(s["correct"]),
                      str(s["wrong"]), str(s["unseen"]), pct)

    for cid, title in chapters:
        row_for(title, by_chapter[cid])
    if other["total"]:
        row_for("(no chapter)", other)
    seen = grand["correct"] + grand["wrong"]
    pct = f"{int(100 * grand['correct'] / seen)}%" if seen else "—"
    table.add_section()
    table.add_row("[bold]TOTAL[/bold]", str(grand["total"]),
                  f"[bold]{grand['correct']}[/bold]",
                  f"[bold]{grand['wrong']}[/bold]",
                  str(grand["unseen"]), f"[bold]{pct}[/bold]")
    console.print(table)


def ask_question(q: dict, idx: int, total: int):
    """Render one question on a clean screen and return the answer key."""
    console.clear()
    header_parts = [f"[bold cyan]Q{idx}/{total}[/bold cyan]"]
    if q.get("chapter_title"):
        header_parts.append(f"[dim]{q['chapter_title']}[/dim]")
    if q.get("number") is not None:
        header_parts.append(f"[dim]#{q['number']}[/dim]")
    console.print(Rule("  ".join(header_parts), style="cyan"))
    console.print()
    console.print(Text(q["question"], style="bold white"))
    console.print()

    option_keys = sorted(q["options"].keys())
    for letter in option_keys:
        console.print(f"  [bold yellow]{letter}.[/bold yellow] {q['options'][letter]}")
    console.print()

    accepted = option_keys + [k.lower() for k in option_keys] + ["s", "S", "q", "Q"]
    accepted_keys = "/".join(option_keys)
    answer = ask(
        f"[bold]Your answer[/bold] ({accepted_keys}, s=skip, q=quit)",
        choices=accepted,
        show_choices=False,
    ).upper()
    return answer


def feedback(q: dict, answer: str) -> bool:
    """Show feedback panel. Returns True if correct."""
    correct = q["correct"]
    if answer == correct:
        console.print(Panel(
            f"[bold green]Correct![/bold green]  [dim]({correct}. {q['options'][correct]})[/dim]",
            border_style="green",
        ))
        return True
    correct_text = q["options"][correct]
    body = Text()
    body.append("Correct answer: ", style="dim")
    body.append(f"{correct}. {correct_text}\n", style="bold green")
    if answer != "S":
        your = q["options"].get(answer, "(invalid)")
        body.append("Your answer:    ", style="dim")
        body.append(f"{answer}. {your}\n", style="bold red")
    if q.get("explanation"):
        body.append("\n")
        body.append(q["explanation"], style="white")
    title = "[bold red]Skipped[/bold red]" if answer == "S" else "[bold red]Incorrect[/bold red]"
    console.print(Panel(body, title=title, border_style="red"))
    return False


def run_quiz(questions: list[dict], history: dict, history_path: Path):
    questions = list(questions)
    random.shuffle(questions)
    total = len(questions)
    correct_n = wrong_n = skipped_n = 0
    quit_early = False

    for i, q in enumerate(questions, 1):
        ans = ask_question(q, i, total)
        if ans == "Q":
            quit_early = True
            break
        if ans == "S":
            skipped_n += 1
            feedback(q, ans)
            ask("[dim]Press Enter for next question[/dim]", default="")
            continue
        if feedback(q, ans):
            correct_n += 1
            history[q["id"]] = "correct"
        else:
            wrong_n += 1
            history[q["id"]] = "wrong"
            ask("[dim]Press Enter for next question[/dim]", default="")
        save_history(history_path, history)

    answered = correct_n + wrong_n
    console.clear()
    console.print(Rule("Session complete", style="cyan"))
    pct = int(100 * correct_n / answered) if answered else 0
    summary = Text()
    summary.append("  Correct:  ", style="dim"); summary.append(f"{correct_n}\n", style="bold green")
    summary.append("  Wrong:    ", style="dim"); summary.append(f"{wrong_n}\n", style="bold red")
    if skipped_n:
        summary.append("  Skipped:  ", style="dim"); summary.append(f"{skipped_n}\n", style="bold yellow")
    summary.append("  Score:    ", style="dim"); summary.append(f"{pct}%", style="bold cyan")
    console.print(Panel(summary, border_style="cyan"))
    if quit_early:
        console.print("[dim]Quit early — progress saved.[/dim]")


# ---------- Entry point ----------

QUIZZES_DIR = Path(__file__).resolve().parent / "quizzes"


def resolve_questions_path(arg: str | None) -> Path:
    """Resolve the questions file path.

    Lookup order:
      1. If `arg` is a path containing '/' or ending in .json — use it directly.
      2. If `arg` is a bare name — try `quizzes/<arg>.json` first, then `quizzes/<arg>`.
      3. If `arg` is None — pick from `quizzes/` (single file → auto, multiple → prompt).
    """
    QUIZZES_DIR.mkdir(exist_ok=True)

    if arg:
        if "/" in arg or arg.endswith(".json"):
            return Path(arg).expanduser().resolve()
        named = QUIZZES_DIR / f"{arg}.json"
        if named.exists():
            return named
        named2 = QUIZZES_DIR / arg
        if named2.exists():
            return named2
        return named  # let caller report missing

    # No arg — scan quizzes/
    candidates = sorted(p for p in QUIZZES_DIR.glob("*.json")
                        if not p.name.endswith(".history.json"))
    if not candidates:
        _raw_console.print(
            f"[red]No quiz files found in[/red] {QUIZZES_DIR}\n"
            f"[dim]Drop a questions JSON in that folder, "
            f"or pass a path: quizterm path/to/questions.json[/dim]"
        )
        sys.exit(1)
    if len(candidates) == 1:
        return candidates[0]

    # Multiple — prompt
    _raw_console.print(f"[bold]Available quizzes in {QUIZZES_DIR}:[/bold]")
    for i, p in enumerate(candidates, 1):
        _raw_console.print(f"  [yellow]{i}.[/yellow] {p.stem}")
    choice = Prompt.ask("Pick one",
                        choices=[str(i) for i in range(1, len(candidates) + 1)],
                        console=_raw_console)
    return candidates[int(choice) - 1]


def parse_args(argv: list[str]):
    p = argparse.ArgumentParser(
        prog="quizterm",
        description="Exam-agnostic terminal quiz bot.",
    )
    p.add_argument("questions", nargs="?", default=None,
                   help="Quiz to load. Either a bare name (looked up in quizzes/) "
                        "or a path to a JSON file. Omit to auto-pick from quizzes/.")
    p.add_argument("--reset-history", action="store_true",
                   help="Wipe the history file before starting")
    return p.parse_args(argv)


def main(argv: list[str] | None = None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    qpath = resolve_questions_path(args.questions)
    if not qpath.exists():
        _raw_console.print(f"[red]Questions file not found:[/red] {qpath}")
        sys.exit(1)

    title, questions = load_questions(qpath)
    errs = validate(questions)
    if errs:
        _raw_console.print(f"[red]{len(errs)} validation error(s):[/red]")
        for e in errs[:10]:
            _raw_console.print(f"  • {e}")
        if len(errs) > 10:
            _raw_console.print(f"  ... and {len(errs) - 10} more")
        sys.exit(2)

    hpath = history_path_for(qpath)
    if args.reset_history and hpath.exists():
        hpath.unlink()
    history = load_history(hpath)

    chapters = derive_chapters(questions)
    has_chapters = bool(chapters)

    while True:
        banner(title, len(questions))
        mode = pick_mode(has_chapters)
        if mode == "q":
            console.print("[dim]Bye![/dim]")
            return
        if mode == "5":
            show_stats(questions, history, chapters)
            ask("\n[dim]Press Enter to continue[/dim]", default="")
            continue
        selected = select_questions(mode, questions, history, chapters)
        if not selected:
            ask("\n[dim]Press Enter to continue[/dim]", default="")
            continue
        run_quiz(selected, history, hpath)
        again = ask("\nPlay again", choices=["y", "n"], default="y")
        if again == "n":
            console.print("[dim]Bye![/dim]")
            return


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        _raw_console.print("\n[dim]Interrupted — progress saved.[/dim]")
        sys.exit(0)
