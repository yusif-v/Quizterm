#!/usr/bin/env python3
"""Quizterm — exam-agnostic terminal quiz bot.  v0.3.2

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
import os
import random
import re
import sys
import termios
import tty
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
    labels = ["All questions"]
    keys = ["1"]
    if has_chapters:
        labels.append("By chapter / group")
        keys.append("2")
    labels += ["Random N questions", "Wrong answers only (from history)", "Show stats", "Quit"]
    keys += ["3", "4", "5", "q"]

    console.print(Panel("[bold]Select mode[/bold]", border_style="cyan"))
    console.print("[dim]Up/Down to choose, Enter to confirm[/dim]")
    console.print()

    options = [(k, label) for k, label in zip(keys, labels)]
    sel = arrow_select(options)
    return keys[sel]


def pick_chapter(chapters: list[tuple[str, str]]):
    console.print(Panel("[bold]Select chapter[/bold]", border_style="cyan"))
    console.print("[dim]Up/Down to choose, Enter to confirm[/dim]")
    console.print()

    options = [(str(i), title) for i, (_, title) in enumerate(chapters, 1)]
    sel = arrow_select(options)
    return chapters[sel][0]


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


# ---------- Arrow-key selector ----------

def _read_key() -> str:
    """Read a single keypress. Returns 'up', 'down', 'enter', or the literal char."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                if ch3 == "A":
                    return "up"
                if ch3 == "B":
                    return "down"
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def arrow_select(options: list[tuple[str, str]]) -> int:
    """Interactive arrow-key menu. Returns index of chosen option.

    options: list of (key, label) pairs, e.g. [("A", "3"), ("B", "4"), ...]
    """
    selected = 0
    n = len(options)
    indent = " " * MARGIN

    # Figure out usable width for label truncation (avoid wrapping)
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80
    max_label = cols - MARGIN - 6  # indent + "  " + key + ". " + some slack

    def fmt_line(i: int) -> str:
        key, label = options[i]
        text = f"{key}. {label}"
        if len(text) > max_label:
            text = text[:max_label - 1] + "~"
        if i == selected:
            return f"{indent}  \033[7m {text} \033[0m"  # reverse video
        return f"{indent}    {text}"

    def render():
        """Clear the option block and reprint with current selection."""
        for _ in range(n):
            sys.stdout.write("\x1b[A\x1b[2K\r")
        sys.stdout.flush()
        for i in range(n):
            sys.stdout.write(fmt_line(i) + "\n")
        sys.stdout.flush()

    # Print initial options
    for i in range(n):
        sys.stdout.write(fmt_line(i) + "\n")
    sys.stdout.flush()

    while True:
        key = _read_key()
        if key == "up":
            selected = (selected - 1) % n
            render()
        elif key == "down":
            selected = (selected + 1) % n
            render()
        elif key == "enter":
            return selected
        elif key in ("q", "Q"):
            return -1  # quit sentinel


def build_minimap(questions: list[dict], history: dict, current_idx: int,
                  term_rows: int, term_cols: int) -> list[str]:
    """Build the minimap sidebar as a list of raw ANSI strings (one per visual line).

    Returns lines ready to write to stdout at a given column offset.
    Each question = one cell: correct=✓, wrong=✗, unseen=·, current=▸
    """
    n = len(questions)
    if n == 0:
        return []

    # Minimap dimensions — compact, fixed max height
    max_grid_rows = min(12, max(4, term_rows // 3))  # cap at 12 rows
    cols = max(1, int(n / max_grid_rows) + (1 if n % max_grid_rows else 0))  # ceil(n / rows)
    cols = min(cols, (term_cols // 4) - 2)  # don't exceed ~1/4 terminal width
    cols = max(8, cols)
    rows = max(1, int(n / cols) + (1 if n % cols else 0))  # actual rows needed
    # Trim rows to what we actually fill (no empty rows at bottom)
    rows = min(rows, max_grid_rows)

    # Calculate grid layout: fill columns first, then rows
    cells_per_page = rows * cols
    if cells_per_page == 0:
        return []

    # Find which page current question is on
    current_page = current_idx // cells_per_page
    page_start = current_page * cells_per_page
    page_end = min(page_start + cells_per_page, n)

    # Status lookup in shuffled order
    def status_char(qi: int) -> tuple[str, str]:
        """Return (char, ansi_style) for question at shuffled index qi."""
        is_current = (qi == current_idx)
        qid = questions[qi]["id"]
        if is_current:
            return "▸", "\033[1;36m"  # bold cyan
        st = history.get(qid)
        if st == "correct":
            return "✓", "\033[32m"   # green
        if st == "wrong":
            return "✗", "\033[31m"   # red
        return "·", "\033[90m"       # dim gray

    reset = "\033[0m"
    lines = []

    # Top border
    lines.append("\033[36m┌" + "─" * cols + "┐" + reset)

    # Grid rows
    for r in range(rows):
        row_str = "\033[36m│" + reset
        for c in range(cols):
            qi = page_start + r * cols + c
            if qi < page_end:
                ch, style = status_char(qi)
                row_str += f"{style}{ch}{reset}"
            else:
                row_str += " "
        row_str += "\033[36m│" + reset
        lines.append(row_str)

    # Bottom border
    lines.append("\033[36m└" + "─" * cols + "┘" + reset)

    # Progress bar
    answered = sum(1 for q in questions[:current_idx + 1] if history.get(q["id"]) in ("correct", "wrong"))
    correct_n = sum(1 for q in questions[:current_idx + 1] if history.get(q["id"]) == "correct")
    pct = int(100 * correct_n / n) if n else 0

    bar_full = cols
    filled = int(bar_full * (current_idx + 1) / n) if n else 0
    bar = "\033[32m" + "█" * filled + "\033[90m" + "░" * (bar_full - filled) + reset

    lines.append(f" {bar}")
    lines.append(f" \033[1m{current_idx + 1}\033[0m/\033[1m{n}\033[0m  \033[32m{pct}%\033[0m \033[90mcorrect\033[0m")

    return lines


def ask_question(q: dict, idx: int, total: int,
                 questions: list[dict] | None = None,
                 history: dict | None = None):
    """Render one question on a clean screen, arrow-select answer, return answer key."""
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
    console.print("[dim]Up/Down to choose, Enter to confirm, q to quit[/dim]")
    console.print()

    # Draw minimap on the right side
    if questions and history is not None:
        try:
            ts = os.get_terminal_size()
            term_rows, term_cols = ts.lines, ts.columns
        except OSError:
            term_rows, term_cols = 24, 80
        minimap = build_minimap(questions, history, idx - 1, term_rows, term_cols)
        if minimap:
            # Measure visual width by stripping ANSI escape sequences
            ansi_strip = re.compile(r'\x1b\[[0-9;]*m')
            map_width = max(len(ansi_strip.sub('', line)) for line in minimap)
            start_col = term_cols - map_width - 2
            if start_col > 40:  # only draw if enough room
                for i, line in enumerate(minimap):
                    # Move to row i+2 (below top border), column start_col
                    sys.stdout.write(f"\033[{i + 2};{start_col}H{line}")
                sys.stdout.flush()

    option_keys = sorted(q["options"].keys())
    options = [(k, q["options"][k]) for k in option_keys]

    sel = arrow_select(options)
    if sel == -1:
        return "QUIT"
    return option_keys[sel]


def press_enter():
    """Block until Enter is pressed."""
    _raw_console.print("[dim]Press Enter to continue[/dim]")
    _read_key()  # wait for enter


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
    correct_n = wrong_n = 0

    for i, q in enumerate(questions, 1):
        ans = ask_question(q, i, total, questions=questions, history=history)
        if ans == "QUIT":
            break
        if feedback(q, ans):
            correct_n += 1
            history[q["id"]] = "correct"
        else:
            wrong_n += 1
            history[q["id"]] = "wrong"
            press_enter()
        save_history(history_path, history)

    answered = correct_n + wrong_n
    console.clear()
    console.print(Rule("Session complete", style="cyan"))
    pct = int(100 * correct_n / answered) if answered else 0
    summary = Text()
    summary.append("  Correct:  ", style="dim"); summary.append(f"{correct_n}\n", style="bold green")
    summary.append("  Wrong:    ", style="dim"); summary.append(f"{wrong_n}\n", style="bold red")
    summary.append("  Score:    ", style="dim"); summary.append(f"{pct}%", style="bold cyan")
    console.print(Panel(summary, border_style="cyan"))


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
            press_enter()
            continue
        selected = select_questions(mode, questions, history, chapters)
        if not selected:
            press_enter()
            continue
        run_quiz(selected, history, hpath)
        console.print()
        sel = arrow_select([("y", "Play again"), ("n", "Quit")])
        if sel == 1:
            console.print("[dim]Bye![/dim]")
            return


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        _raw_console.print("\n[dim]Interrupted — progress saved.[/dim]")
        sys.exit(0)
