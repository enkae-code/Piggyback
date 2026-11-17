<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/piggyback-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/piggyback-logo-light.svg">
    <img src="assets/piggyback-logo-light.svg" alt="Piggyback" width="440">
  </picture>
</h1>

<p align="center"><strong>Show your AI what you mean.</strong></p>

<p align="center">
  Measure a site you admire, brief your coding agent, check its build.<br>
  Piggyback teaches you the design words along the way.<br>
  Local, no AI key, works with any agent.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2C3E50?style=flat" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-2C3E50?style=flat&logo=python&logoColor=white" alt="Python 3.11+">
  <a href="#status"><img src="https://img.shields.io/badge/status-pre--release-F39C12?style=flat" alt="Status: pre-release"></a>
</p>

## Why

Designing with AI breaks at the handoff. You can recognise what you want, but you can't put it into words an agent follows.

- "Premium" becomes "add a gradient".
- The agent copies a reference's layout and misses its type scale and density.
- "Not quite" carries no information.

Piggyback swaps the adjectives for measurements. It reads the numbers off a page you admire, hands them to your agent as a brief, then measures the agent's build against the same numbers.

## How it works

v0.1 is one local page with two slots: a **reference** (a URL you admire) and **your build** (localhost or a hosted preview link). What you get depends on which you fill.

| You give it | You get |
|---|---|
| Reference&nbsp;only | A **brief**: the measured type scale, palette and accent share, radius set, spacing rhythm, container width and fonts, plus your own Borrow and Avoid lines. |
| Reference&nbsp;+&nbsp;build | The same brief as **differences**, with numbered pins on both screenshots: "H1 36/44 → 56/58", "accent on 19% of the first screen (1440×900) → 2%". Generic defaults found on your build are listed under them, never pinned. |
| Build&nbsp;only | **Defaults detected**: marks on generic tells such as gradient text, one font with a flat scale, or the same radius everywhere. |

Every pin and brief line names the concept in plain words and says why it matters. For example: *Radius (how rounded the corners of a box are)*, with its reason *Corner radius is one of the shape cues a reader sees across boxes.*

Type, palette, radius, anchors and container width are measured on the first screen (1440×900); container width is weighted by visible area. Spacing and motion are measured over the whole document.

Copy the brief to your agent as `BRIEF.md`, let it revise, then re-run. Fixed pins tick and open ones stay. "Copy DESIGN.md" writes the first reference as a `DESIGN.md` that works with Impeccable.

## Status

Pre-release. v0.1 is in progress and nothing is installable yet. This README describes what v0.1 will do, not something you can run today.

## Install

Coming with v0.1. The planned command:

```sh
uvx "piggyback-design[measure]"
```

This does not work yet. It will need Python 3.11 or newer. `pip install piggyback` installs a different project; this one is `piggyback-design`.

## Measure a list

`piggyback measure-list` measures one page after another, so only one browser runs at a time. List one URL per line in a text file; `#` lines and blank lines are ignored:

```sh
piggyback measure-list pages.txt --out measured/
```

Each URL gets a folder under `measured/` with `fingerprint.json` and `screenshot.png`. A folder whose `fingerprint.json` records the same URL is kept, not measured again; one that records another URL, or cannot be read, fails with a message. Folder names include the port. `measured/batch.json` lists every URL with its status (`measured`, `kept` or `failed`) and error. The command exits 1 if any URL failed; the other URLs are still measured. A symbolic link where a file or folder would be written fails that URL. It refuses the whole list up front on a duplicate URL, two URLs that give the same folder name, or more than 500 URLs.

## Use with your agent

Piggyback can run as a read-only MCP server, so your agent can read the brief and re-check your build without you pasting numbers. You set the URLs in the MCP config. The agent cannot pass a URL, a path or any text that reaches the network or disk. The `get_design_md` tool returns the first reference as a `DESIGN.md` that works with Impeccable.

Add this to your MCP client config (Claude Desktop, or `.mcp.json` for Claude Code):

```json
{
  "mcpServers": {
    "piggyback": {
      "command": "uvx",
      "args": [
        "piggyback-design[measure]",
        "mcp",
        "--ref", "https://example.com/",
        "--build", "http://localhost:3000/"
      ]
    }
  }
}
```

- `--ref` takes up to three public reference pages; differences use reference 1 (the first one listed). `--build` takes your localhost or a public preview. Give at least one.
- `get_brief` returns the brief. Its `refresh` flag measures again. `check` measures the build and returns numbered differences, marking the ones now within tolerance as fixed, plus any generic defaults on the build.
- The Claude Code skill in `skills/piggyback/SKILL.md` tells the agent when to call these tools.
- This is a config you edit. Your agent does not install or run it. Like the install command above, it works once v0.1 is installable.

## Works with design skills

Piggyback measures. A design skill judges. Piggyback does not score taste; it gives a skill the numbers to check against.

- [Impeccable](https://github.com/pbakaus/impeccable) (Apache-2.0) checks a project against its `DESIGN.md`.
- [Hallmark](https://github.com/Nutlope/hallmark) (MIT) is a design skill for agents, with numbered checks.
- [taste-skill](https://github.com/Leonxlnx/taste-skill) (MIT) is a design skill for agents.

Export the measured values as a `DESIGN.md` for Impeccable, then scan your project:

```sh
piggyback brief reference.json --design-md DESIGN.md
impeccable detect src/
```

`impeccable detect` reads the `DESIGN.md` at your project root for local file scans. It ignores `DESIGN.md` for http(s) URL scans.

Impeccable enforces the fonts in `DESIGN.md` as warnings (exit 2); colour, radius and size drift are advisory. Piggyback makes no Hallmark compatibility claim: Hallmark expects its own `DESIGN.md` format.

These are measurements of the reference pages, not a target. If you use a design skill such as Impeccable, Hallmark or taste-skill, use it for the method and check its output against these measurements.

Piggyback's wording was edited with [no-ai-slop](https://github.com/petergyang/no-ai-slop) (MIT).

## Principles

- **No taste score.** Piggyback reports measurements and differences. You decide what is good.
- **Local-first.** It runs on your machine and opens a local page.
- **Screenshots stay on your machine.** A brief carries links, measured values, your notes and the capture receipt (final URL, OS and kernel release, browser version).
- **No AI key.** Piggyback holds none. Your own agent does the judging.

## Develop

Run `scripts/check.sh` for the gates. To run them before every push, enable the hook once: `git config core.hooksPath .githooks`.

## License

MIT. See [LICENSE](LICENSE).
