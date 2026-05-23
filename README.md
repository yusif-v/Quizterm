# Quizterm

Exam-agnostic terminal quiz bot. Loads any compatible JSON file of
multiple-choice questions and drills you on them with a colored TUI.

Works with any certification, exam, or self-study question set.

## Features

- Five modes — all / by chapter / random N / wrong-answers-only / stats
- Per-chapter performance table (correct/wrong/unseen + score %)
- Persistent history per question file — quit anytime, resume later
- Color-coded feedback panels with explanations on wrong answers
- Margins + max-width capping for readability on wide terminals
- Validates question files before starting, with helpful errors

## Install

```bash
cd /Users/lizard/Development/Projects/Quizterm
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Quiz library — `quizzes/`

Quizterm looks for quiz files in `Quizterm/quizzes/` by default. The
folder is auto-created on first run and is **gitignored** — your quiz data
and progress never get committed.

Drop one or more `<name>.json` files into `quizzes/`, then:

```bash
./quizterm                  # auto-picks if one file, otherwise prompts
./quizterm my-exam          # loads quizzes/my-exam.json
./quizterm /abs/path.json   # any external path works too
```

History for each quiz is saved alongside it as
`quizzes/<name>.history.json`.

Add the launcher to your PATH for the cleanest workflow:

```bash
ln -s /Users/lizard/Development/Projects/Quizterm/quizterm ~/bin/quizterm
quizterm                    # now usable from anywhere
```

Flags:

| Flag | Effect |
|------|--------|
| `--reset-history` | Wipe the history file before starting this session |
| `--help`          | Show argparse help |

## Question file format

The JSON file is either a bare array of question objects, or an object
with a title and a `questions` array:

```json
{
  "title": "My Exam",
  "questions": [
    {
      "id": "ch1-q1",
      "chapter": "ch1",
      "chapter_title": "Foundations",
      "number": 1,
      "question": "What is 2 + 2?",
      "options": {"A": "3", "B": "4", "C": "5", "D": "22"},
      "correct": "B",
      "explanation": "Basic arithmetic."
    }
  ]
}
```

### Field reference

| Field | Required | Notes |
|-------|----------|-------|
| `id` | yes | Must be unique across the whole file |
| `question` | yes | The prompt shown to the user |
| `options` | yes | Object — keys are answer letters (A, B, ...), 2+ required |
| `correct` | yes | One of the keys in `options` |
| `chapter` | no | Group key — questions sharing this are quizzable together |
| `chapter_title` | no | Display name for the chapter (defaults to chapter id) |
| `number` | no | Question number within the chapter, shown in headers |
| `explanation` | no | Shown on wrong answers; highly recommended |

A single example file lives at `examples/sample.json`.

## Layout

```
Quizterm/
├── quizterm.py        # main program
├── quizterm           # bash launcher
├── requirements.txt   # rich
├── README.md
├── .gitignore
├── examples/
│   └── sample.json    # tiny demo set (tracked)
└── quizzes/           # YOUR quiz files + history (gitignored)
    ├── my-exam.json
    └── my-exam.history.json
```
