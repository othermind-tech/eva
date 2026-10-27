#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tool-routing evaluation: does the shell / background_process wording send the right
kind of task to the right tool?  (Standalone; calls a real model; executes NOTHING.)

Why: the shell tool's description used to say "For LONG-RUNNING background processes
use trigger_daemon() instead" (wrong: a daemon calls the same blocking shell tool). A
live run with Claude Haiku followed it and hung on `python -m http.server`. The
description was rewritten; the worry is that the rewrite could push ordinary finite
commands (tests, installs, builds) away from `shell`, making it obsolete. This measures
both directions, old wording vs new, on the same prompts.

For each prompt the model is offered the real execution tools (shell, background_process,
trigger_daemon) with either the OLD descriptions (taken from git
HEAD~ at the time of writing, see OLD_REF) or the NEW ones (the working tree), and we
record which tool it calls FIRST. Nothing is run. (Exploration tools such as list_files are
left out on purpose: with no project context a model rightly starts by looking around, which
says nothing about shell-versus-background routing.)

  finite tasks      (tests, installs, builds, git, scripts)  -> should pick shell
  long-running      (dev server, watcher, tail -f, http server) -> should pick background_process

"Wrong class" is what matters for the worry that shell became obsolete: a finite task sent to
background_process/trigger_daemon, or a long-running one sent to shell/trigger_daemon.

Usage (from the backend directory):
    python tests/eval_shell_routing_live.py
    EVA_EVAL_MODELS="gpt-oss:120b,glm-5.3-flash" EVA_EVAL_SAMPLES=3 python tests/eval_shell_routing_live.py

Uses the Ollama Cloud key already stored in EVA's credential store (never printed).
"""

import ast
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from config.env_loader import load_eva_env  # noqa: E402

load_eva_env()

from openai import OpenAI  # noqa: E402

from providers.capability.ollama_cloud.provider_api import BASE_URL as OLLAMA_CLOUD_BASE_URL  # noqa: E402
from core.providers.fallback_state import resolve_api_key  # noqa: E402
from core.tools.loader import load_all_tools  # noqa: E402
from core.tools.registry import DefaultToolRegistry  # noqa: E402

# The last commit before the wording changes. Only tools whose description changed
# in this work need an OLD variant.
OLD_REF = os.environ.get("EVA_EVAL_OLD_REF", "a88acb4")
MODELS = [m.strip() for m in os.environ.get("EVA_EVAL_MODELS", "gpt-oss:120b").split(",") if m.strip()]
SAMPLES = int(os.environ.get("EVA_EVAL_SAMPLES", "3"))

FINITE = [
    "Run the project's test suite with pytest and tell me how many tests pass.",
    "Install the npm dependencies for this project.",
    "Check the git status of this repository.",
    "Run python script.py and show me its output.",
    "Build the project with npm run build and tell me if it succeeds.",
    "Compile main.c with gcc and run the resulting program.",
]
LONG_RUNNING = [
    "Start the dev server with npm run dev so I can look at the app.",
    "Run vite in watch mode so it rebuilds whenever I edit a file.",
    "Serve the current folder over HTTP on port 8000 and leave it running.",
    "Start the Flask app with `flask run` and keep it running while I test it.",
    "Watch the test files and re-run pytest every time one changes.",
    "Follow the application log continuously with tail -f app.log and leave it running.",
]

SYSTEM = ("You are EVA, an autonomous coding agent running on Windows 11. The project layout is already "
          "known to you, so do not explore first. Use your tools to do what the user asks: call the single "
          "most appropriate tool for the first step.")


def _old_descriptions():
    """{tool name: description string} as of OLD_REF, read from git without checking anything out."""
    files = {"shell": "tools/default/shell_tool.py", "background_process": "tools/default/process_tools.py",
             "trigger_daemon": "tools/default/daemon_tools.py"}
    out = {}
    for name, rel in files.items():
        src = subprocess.run(["git", "show", f"{OLD_REF}:backend/{rel}"], capture_output=True, text=True,
                             encoding="utf-8", cwd=os.path.dirname(os.path.abspath(__file__)), check=True).stdout
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "default_tool":
                kw = {k.arg: k.value for k in node.keywords}
                if isinstance(kw.get("name"), ast.Constant) and kw["name"].value == name:
                    out[name] = ast.literal_eval(kw["description"])
    missing = {"shell", "background_process", "trigger_daemon"} - set(out)
    assert not missing, f"could not read old descriptions for {missing} from {OLD_REF}"
    return out


def _tool_specs(descriptions):
    load_all_tools()
    tools = DefaultToolRegistry.all()
    specs = []
    for name in ("shell", "background_process", "trigger_daemon"):
        cls = tools[name]
        specs.append({"type": "function", "function": {
            "name": name,
            "description": descriptions.get(name, cls.description),
            "parameters": cls.args_schema.model_json_schema(),
        }})
    return specs


def _first_tool(client, model, specs, prompt):
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    try:
        resp = client.chat.completions.create(model=model, temperature=0.5, tool_choice="required",
                                              tools=specs, messages=messages)
    except Exception:
        # Some models reject tool_choice="required"; fall back to auto (a "none" pick then shows up).
        resp = client.chat.completions.create(model=model, temperature=0.5, tool_choice="auto",
                                              tools=specs, messages=messages)
    calls = resp.choices[0].message.tool_calls or []
    return calls[0].function.name if calls else "none"


def main():
    key = resolve_api_key(None, "ollama_cloud")
    if not key:
        print("FATAL: no Ollama Cloud key in EVA's credential store.")
        sys.exit(1)
    client = OpenAI(api_key=key, base_url=OLLAMA_CLOUD_BASE_URL, timeout=90)

    worlds = {"OLD wording": _tool_specs(_old_descriptions()), "NEW wording": _tool_specs({})}
    grand = defaultdict(lambda: defaultdict(Counter))

    for model in MODELS:
        print(f"\n{'=' * 78}\n  model: {model}   samples per prompt: {SAMPLES}\n{'=' * 78}")
        for world, specs in worlds.items():
            picks = {"finite": Counter(), "long": Counter()}
            misses = []
            for kind, prompts, right in (("finite", FINITE, "shell"), ("long", LONG_RUNNING, "background_process")):
                for prompt in prompts:
                    for _ in range(SAMPLES):
                        try:
                            got = _first_tool(client, model, specs, prompt)
                        except Exception as exc:
                            got = f"error:{type(exc).__name__}"
                        picks[kind][got] += 1
                        if got != right:
                            misses.append((got, prompt))
            n_f, n_l = sum(picks["finite"].values()), sum(picks["long"].values())
            ok_f, ok_l = picks["finite"]["shell"], picks["long"]["background_process"]
            print(f"\n  {world}")
            print(f"    finite tasks       -> shell              {ok_f:>3}/{n_f}   all picks: {dict(picks['finite'])}")
            print(f"    long-running tasks -> background_process {ok_l:>3}/{n_l}   all picks: {dict(picks['long'])}")
            wrong_f = picks["finite"]["background_process"] + picks["finite"]["trigger_daemon"]
            wrong_l = picks["long"]["shell"] + picks["long"]["trigger_daemon"]
            print(f"    WRONG CLASS: finite sent to background_process/trigger_daemon = {wrong_f};  "
                  f"long-running sent to shell/trigger_daemon = {wrong_l}")
            for got, prompt in sorted(set(misses)):
                print(f"      miss -> {got:<20} {prompt}")
            grand[model][world] = {"finite_ok": ok_f, "finite_n": n_f, "long_ok": ok_l, "long_n": n_l}

    print(f"\n{'=' * 78}\n  SUMMARY\n{'=' * 78}")
    print(json.dumps({m: dict(w) for m, w in grand.items()}, indent=2))


if __name__ == "__main__":
    main()
