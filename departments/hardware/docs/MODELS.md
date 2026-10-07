# Model choices

Researched on 2 October 2026. Prices are from OpenRouter's public model list on that day, in US
dollars per 1 million tokens (input / output). Model line-ups and prices change monthly: use
`./dept models --refresh` and the "Compare models" table in the web interface for current data.

## The picks

| Role | Model | Price | Why |
|---|---|---|---|
| `hw-lead` | `anthropic/claude-opus-5.5` | 4 / 20 | Top of EEBench (75.0%) and of the agentic leaderboard (88.1). Planning is low volume, so the price matters less. |
| `hw-research` | `google/gemini-3.8-flash` | 0.75 / 3.75 | Best cheap model on EEBench (55.4%). 1M context and PDF input for long datasheets. |
| `hw-schematic` | `anthropic/claude-sonnet-5.5` | 2 / 10 | EEBench 67.2% at half the price of Opus. Reads PDFs and images. |
| `hw-layout` | `x-ai/grok-4.7` | 2 / 6 | EEBench 64.0%. Layout is a long tool loop, so cheap output tokens matter. Sees renders. |
| `hw-mech` | `anthropic/claude-sonnet-5.5` | 2 / 10 | Weakest evidence of all picks (see below). |
| `hw-firmware` | `z-ai/glm-5.3` | 1.40 / 4.40 | Agentic score 67.4, the best open-weight model. Text only: it cannot read images or PDFs. |
| `hw-review` | `openai/gpt-6.1-sol` | 2 / 10 | EEBench 63.6%. A different vendor from the schematic and layout engineers, so it does not share their blind spots. Released two days before this was written. |
| `hw-docs` | `deepseek/deepseek-v4.1-flash` | 0.30 / 1.20 | Clerical work. Agentic score 61.1. |

Every role also has a fallback model from a different vendor, used when the first is unavailable.

The "Budget" preset swaps the lead to Sonnet 5.5, layout and mechanics to Gemini 3.8 Flash, and
the reviewer to Grok 4.7. It is cheaper and scores lower on the two hardest jobs.

## The evidence

**EEBench** (atopile, snapshot 29 September 2026, via benchlm.ai): electrical-engineering design
tasks at professional difficulty, percent solved.

| Model | Score | | Model | Score |
|---|---|---|---|---|
| Claude Opus 5.5 | 75.0% | | Gemini 3.8 Flash | 55.4% |
| GPT-6 Astra | 69.3% | | Kimi K3 | 38.3% |
| Claude Sonnet 5.5 | 67.2% | | GPT-6 Luna | 31.1% |
| Grok 4.7 | 64.0% | | GLM-5.2 | 16.7% |
| GPT-6.1 Sol | 63.6% | | Kimi K2.7 Code | 12.8% |
| Grok 4.6 | 57.1% | | DeepSeek V4 Pro 0813 | 10.5% |

Two things follow. Cheap coding models that do well on software benchmarks do badly at
electronics, so hardware tasks should not be routed to them. And the gap between the top models
is smaller than the price gap: Sonnet 5.5 and Grok 4.7 are close to Opus at a fraction of the cost.

**Agentic leaderboard** (BenchLM, 1 October 2026): a composite of 23 agent benchmarks (tool
calling, terminal work, browsing), 0 to 100. Opus 5.5 88.1, Sonnet 5.5 68.0, Kimi K3 67.8,
GLM-5.3 67.4, Qwen3.8 Max 65.4, Gemini 3.8 Flash 65.3, DeepSeek V4.1 Flash 61.1, GPT-6 Sol 59.1.

The scores shown in the web interface are in `departments/hardware/benchmarks.json` (EEBench) and
`departments/engine/benchmarks.json` (agentic), with their sources and dates.

## How far to trust this

- **None of these models were tested on this department's tools.** EEBench measures circuit
  design, not KiCad layout and not FreeCAD.
- **No current benchmark covers PCB layout.** The layout pick is an inference from general
  electronics and agent scores. Spatial reasoning is a known weak spot of all current models.
- **No current CAD benchmark was readable.** The one that was (Text2CAD-Bench) tested models two
  generations old; GPT and Claude models led it, which is all the mechanical pick rests on.
- **The scores were read through a page summariser**, not copied by hand from the tables. Check
  the source before relying on a single number.
- **Several models are days or weeks old.** GPT-6.1 Sol appeared on OpenRouter two days before
  this research.

## Settling it properly: a bake-off

The honest way to choose the layout and mechanical models is to run the same small job through
each candidate and compare. Two jobs already exist with known-good results:

- `packages/kicad-mcp/examples/blinky.json` and `blinky_board.json`: a 555 blinker whose board
  passes DRC with zero errors and zero unconnected items.
- `packages/freecad-mcp/examples/`: a bracket and a flanged connector with known volumes.

Give each candidate the task in words (not the example files), then compare: did ERC/DRC come out
clean, how many tool calls, how long, what it cost. Change the model for one role in the web
interface, apply, run, and note the result.

## Sources

- OpenRouter models: https://openrouter.ai/api/v1/models
- OpenRouter rankings: https://openrouter.ai/rankings
- EEBench leaderboard: https://benchlm.ai/benchmarks/eebench
- Agentic leaderboard: https://benchlm.ai/agentic
- Hackaday, "Can AI Now Design PCBs That Just Work?": https://hackaday.com/2026/09/05/can-ai-now-design-pcbs-that-just-work/
- Quilter, "Can LLMs Design PCBs?": https://www.quilter.ai/blog/llm-pcb-layout-gpt-6-astra
- Text2CAD-Bench: https://arxiv.org/html/2605.18430v1
- D-Central LLM Benchmark Hub (KiCadBench preview): https://d-central.tech/llm-benchmarks/
