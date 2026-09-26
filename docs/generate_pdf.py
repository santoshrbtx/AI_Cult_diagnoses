"""Generate the AICult hackathon presentation PDF.

Style influenced by ai-field-guide.santosh-rbtx.chatgpt.site: Inter-family
sans-serif body (Segoe UI on Windows as the nearest match), tight-tracked
bold headings, near-black on near-white, muted secondary text, single blue
accent, JetBrains-Mono-style monospace (Consolas on Windows).

Run:
    python docs/generate_pdf.py

Output: docs/AICult_Presentation.pdf
"""

from __future__ import annotations

import os
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    Spacer,
    SimpleDocTemplate,
    Table,
    TableStyle,
    KeepTogether,
    PageBreak,
    HRFlowable,
)


HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "AICult_Presentation.pdf")


# ---------- Fonts ----------

def _register_fonts() -> dict:
    """Register nice typefaces if available; fall back to built-ins."""
    fonts = {"body": "Helvetica", "bold": "Helvetica-Bold", "italic": "Helvetica-Oblique", "mono": "Courier"}

    candidates = {
        "body":   ["C:/Windows/Fonts/segoeui.ttf"],
        "bold":   ["C:/Windows/Fonts/segoeuib.ttf"],
        "italic": ["C:/Windows/Fonts/segoeuii.ttf"],
        "mono":   ["C:/Windows/Fonts/consola.ttf"],
    }
    aliases = {"body": "AIC-Sans", "bold": "AIC-Sans-Bold", "italic": "AIC-Sans-Italic", "mono": "AIC-Mono"}

    for key, paths in candidates.items():
        for p in paths:
            if os.path.exists(p):
                try:
                    pdfmetrics.registerFont(TTFont(aliases[key], p))
                    fonts[key] = aliases[key]
                    break
                except Exception:
                    pass
    try:
        pdfmetrics.registerFontFamily(
            fonts["body"], normal=fonts["body"], bold=fonts["bold"], italic=fonts["italic"]
        )
    except Exception:
        pass
    return fonts


# ---------- Palette ----------

BG = colors.HexColor("#ffffff")
TEXT = colors.HexColor("#111111")
MUTED = colors.HexColor("#6b7280")
BORDER = colors.HexColor("#e5e7eb")
ACCENT = colors.HexColor("#2563eb")
SOFT = colors.HexColor("#f5f5f7")
OK = colors.HexColor("#16a34a")
WARN = colors.HexColor("#d97706")


# ---------- Styles ----------

def _styles(fonts: dict) -> dict:
    def _s(name, size, leading=None, bold=False, italic=False, mono=False, color=TEXT, space_after=6, space_before=0, tracking=0):
        font = fonts["mono"] if mono else (fonts["bold"] if bold else (fonts["italic"] if italic else fonts["body"]))
        return ParagraphStyle(
            name=name,
            fontName=font,
            fontSize=size,
            leading=leading or size * 1.45,
            textColor=color,
            spaceAfter=space_after,
            spaceBefore=space_before,
            charSpace=tracking,
        )

    return {
        "cover_title": _s("cover_title", 38, leading=44, bold=True, tracking=-0.6, space_after=6),
        "cover_subtitle": _s("cover_subtitle", 15, color=MUTED, space_after=18),
        "cover_meta": _s("cover_meta", 10, color=MUTED, space_after=4),
        "h1": _s("h1", 22, bold=True, tracking=-0.3, space_before=16, space_after=8),
        "h2": _s("h2", 15, bold=True, tracking=-0.2, space_before=12, space_after=6),
        "eyebrow": _s("eyebrow", 9, color=ACCENT, bold=True, tracking=0.6, space_after=2),
        "body": _s("body", 10.5, leading=16),
        "muted": _s("muted", 9.5, color=MUTED, leading=14, space_after=4),
        "code": _s("code", 9.5, mono=True, leading=14, color=colors.HexColor("#1f2937")),
        "table_th": _s("table_th", 9, bold=True, color=MUTED, tracking=0.6),
        "table_td": _s("table_td", 10, color=TEXT),
        "table_td_mono": _s("table_td_mono", 10, mono=True, color=TEXT),
        "kbd": _s("kbd", 9, mono=True, color=colors.HexColor("#374151")),
        "footer": _s("footer", 8.5, color=MUTED),
    }


# ---------- Page frame ----------

def _draw_page(canvas, doc, footer_text: str):
    canvas.saveState()
    canvas.setFillColor(MUTED)
    canvas.setFont(doc.fonts["body"], 8.5)
    page_num = f"AICult · Hackathon Presentation · p{canvas.getPageNumber()}"
    canvas.drawRightString(LETTER[0] - 0.75 * inch, 0.55 * inch, page_num)
    canvas.drawString(0.75 * inch, 0.55 * inch, footer_text)
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.4)
    canvas.line(0.75 * inch, 0.75 * inch, LETTER[0] - 0.75 * inch, 0.75 * inch)
    canvas.restoreState()


# ---------- Content pieces ----------

def _pill(text: str, color, styles) -> Table:
    tbl = Table([[Paragraph(f"<font color='{color.hexval()}'><b>{text}</b></font>", styles["muted"])]], colWidths=[None])
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, color),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return tbl


def _cover(styles) -> list:
    story = []
    story.append(Spacer(1, 1.4 * inch))
    story.append(Paragraph("AICult", styles["cover_title"]))
    story.append(Paragraph(
        "Autonomous, human-in-the-loop incident diagnosis and remediation for "
        "resource-pool exhaustion.",
        styles["cover_subtitle"],
    ))
    story.append(HRFlowable(width="20%", color=ACCENT, thickness=2, spaceAfter=18, spaceBefore=4, hAlign="LEFT"))

    labels = [
        ("Domain", "Reliability / SRE"),
        ("Stack", "Python 3.11 · TrueFoundry SDK · sqlite-vec · Bedrock Titan · FastAPI · Angular 17 · Streamlit"),
        ("Pattern", "Multi-agent RAG · Decision-tree runbooks · HITL approval loop"),
        ("Status", "Runnable end-to-end · Local-only · Hackathon submission"),
    ]
    rows = [[Paragraph(f"<b>{k}</b>", styles["cover_meta"]),
             Paragraph(v, styles["cover_meta"])] for k, v in labels]
    tbl = Table(rows, colWidths=[0.9 * inch, 5.3 * inch])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(tbl)
    story.append(PageBreak())
    return story


def _problem(styles) -> list:
    return [
        Paragraph("PROBLEM", styles["eyebrow"]),
        Paragraph("Resource-pool incidents are the hardest kind of production fire.", styles["h1"]),
        Paragraph(
            "A service starts timing out. Is the database connection pool exhausted? Are "
            "application worker threads saturated? Both? Enlarging the wrong pool wastes an "
            "incident window and can make things worse — for example, giving the app more "
            "workers when the real bottleneck is downstream DB connections just increases queue "
            "depth on the DB side.",
            styles["body"],
        ),
        Paragraph(
            "AICult is a local, model-driven playbook that diagnoses these incidents end-to-end, "
            "explains its reasoning, asks a human to approve every change, and verifies whether "
            "the change actually helped.",
            styles["body"],
        ),
        Spacer(1, 8),
    ]


def _what_we_built(styles) -> list:
    bullets = [
        ("DB pool management", "A bounded async DB pool (asyncio.Semaphore) is stressed by 100 concurrent virtual users. When acquire waits blow past the timeout, requests fail with db_timeout — the classic pool-exhaustion signal."),
        ("Thread pool management", "A bounded ThreadPoolExecutor(max_workers=N) is stressed with blocking work. When submit waits blow past the timeout, requests fail with thread_timeout."),
        ("Two failure modes, one loop", "AICult runs both stress scenarios simultaneously (`--scenario combined`), so the agents see a mixed signal — the same shape as real production incidents."),
        ("Human-in-the-loop by design", "Every configuration change is presented to the operator BEFORE it is applied. Approve → apply → retest. Reject → stop. The LLM cannot execute anything; it can only propose."),
        ("Extensible", "The same pattern generalizes to memory pool, HTTP client pool, cache pool, disk I/O queue depth — any bounded shared resource with a saturation-friendly failure mode."),
    ]
    story = [
        Paragraph("WHAT IT DOES", styles["eyebrow"]),
        Paragraph("DB pool + thread pool management with a diagnosis loop.", styles["h1"]),
    ]
    for title, text in bullets:
        story.append(Paragraph(f"<b>{title}.</b> {text}", styles["body"]))
    story.append(Spacer(1, 8))
    return story


def _rag_workflow(styles) -> list:
    story = [
        Paragraph("HOW IT DECIDES", styles["eyebrow"]),
        Paragraph("A RAG-driven decision-tree workflow — not a monolithic LLM prompt.", styles["h1"]),
        Paragraph(
            "The agents do NOT freeform-diagnose the incident. Instead the system embeds every "
            "log line and two Markdown runbooks (one for the DB pool, one for the thread pool) "
            "into a local <b>sqlite-vec</b> vector store. Each specialist agent retrieves only "
            "the log entries and runbook chunks relevant to its area, then follows the runbook "
            "like a decision tree.",
            styles["body"],
        ),
        Spacer(1, 4),
        Paragraph("Decision tree — DB pool runbook (paraphrased):", styles["h2"]),
    ]
    tree = [
        ["Baseline observed", "→", "Tier 1: recommendedDbPoolSize = ceil(0.30 · N)"],
        ["Tier 1 still has db_timeout > 0", "→", "Tier 2: ceil(0.60 · N)"],
        ["Tier 2 still has db_timeout > 0", "→", "Tier 3: ceil(0.90 · N)"],
        ["Tier 3 still has db_timeout > 0", "→", "Ceiling: N (one connection per user)"],
        ["db_call_ms elevated w/ pool spare capacity", "→", "NO_ACTION (query is the real problem)"],
    ]
    t = Table(
        [[Paragraph(a, styles["table_td"]),
          Paragraph(f"<font color='{ACCENT.hexval()}'>{b}</font>", styles["table_td"]),
          Paragraph(c, styles["table_td_mono"])] for a, b, c in tree],
        colWidths=[2.6 * inch, 0.3 * inch, 3.3 * inch],
    )
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))

    story.append(Paragraph("Three cooperating agents:", styles["h2"]))
    for role, desc in [
        ("ThreadPoolAgent", "Retrieves thread-only + combined logs and the thread pool runbook. Emits a JSON AgentReport."),
        ("DatabasePoolAgent", "Retrieves db-only + combined logs and the DB pool runbook. Emits a JSON AgentReport."),
        ("DecisionAgent", "Reads both peer reports plus the run history and picks the escalation tier. Emits a Decision (HYBRID / APPLICATION / DATABASE_POOL / NO_ACTION)."),
    ]:
        story.append(Paragraph(f"<b>{role}.</b> {desc}", styles["body"]))
    return story


def _results_table(styles) -> list:
    header = [
        Paragraph("Step", styles["table_th"]),
        Paragraph("DB pool", styles["table_th"]),
        Paragraph("Thread pool", styles["table_th"]),
        Paragraph("Success", styles["table_th"]),
        Paragraph("DB timeouts", styles["table_th"]),
        Paragraph("Thread timeouts", styles["table_th"]),
        Paragraph("p50 (ms)", styles["table_th"]),
        Paragraph("p95 (ms)", styles["table_th"]),
    ]
    rows = [
        ("baseline", 5,  5,   "15.0%",  85, 0,  1027, 2781),
        ("iter 1",   30, 5,   "60.0%",  40, 0,  4225, 5826),
        ("iter 2",   60, 5,   "83.0%",  0,  17, 6992, 7243),
        ("iter 3",   60, 30, "100.0%",  0,  0,  4095, 7526),
    ]

    def cell(val, bold=False, color=None):
        style = styles["table_td_mono"]
        s = f"<b>{val}</b>" if bold else str(val)
        if color is not None:
            s = f"<font color='{color.hexval()}'>{s}</font>"
        return Paragraph(s, style)

    body_rows = []
    for (step, db, th, succ, dbt, tht, p50, p95) in rows:
        color = OK if succ == "100.0%" else (WARN if succ.rstrip("%") and float(succ.rstrip("%")) >= 50 else None)
        body_rows.append([
            Paragraph(f"<b>{step}</b>", styles["table_td"]),
            cell(db), cell(th),
            cell(succ, bold=True, color=color),
            cell(dbt), cell(tht),
            cell(p50), cell(p95),
        ])

    data = [header] + body_rows
    t = Table(data, colWidths=[0.7 * inch, 0.6 * inch, 0.8 * inch, 0.8 * inch,
                               0.9 * inch, 1.1 * inch, 0.75 * inch, 0.75 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), SOFT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, BORDER),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return [t]


def _results(styles) -> list:
    story = [
        Paragraph("EVIDENCE", styles["eyebrow"]),
        Paragraph("From 15% success to 100% in three approved retests.", styles["h1"]),
        Paragraph(
            "Below is a real end-to-end run on the <b>combined</b> scenario with 100 virtual users, "
            "seed=42. Baseline pools are 5/5. Every iteration was proposed by the DecisionAgent, "
            "shown to the operator for approval, then applied and retested with identical seed and "
            "user count so the numbers are directly comparable.",
            styles["body"],
        ),
        Spacer(1, 6),
    ]
    story.extend(_results_table(styles))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "<b>Read the trajectory.</b> Iter 1 (Tier-1 DB bump to 30) took success from 15% to 60% "
        "and cleared more than half the DB timeouts. Iter 2 (Tier-2 DB bump to 60) eliminated "
        "DB timeouts entirely, but a bottleneck shift appeared — 17 thread timeouts emerged. "
        "Iter 3 (HYBRID: DB stays at 60, thread jumps to 30 per its own tier-1) resolved the "
        "shift and reached 100%.",
        styles["body"],
    ))
    story.append(Paragraph(
        "The escalation ladder and bottleneck-shift rule are encoded in the Markdown "
        "runbooks — not in Python. Editing the runbook changes agent behavior; the agents "
        "re-embed and re-read the new guidance automatically.",
        styles["body"],
    ))
    return story


def _architecture(styles) -> list:
    diagram = (
        "                +----- ThreadPoolAgent -----+\n"
        "                |          (RAG)            |\n"
        "  workload --> logs+runbooks (sqlite-vec) --+--> DecisionAgent --> HITL --> apply --> retest\n"
        "                |          (RAG)            |\n"
        "                +----- DatabasePoolAgent ---+\n"
    )
    story = [
        Paragraph("ARCHITECTURE", styles["eyebrow"]),
        Paragraph("Four phases, one loop, everything local.", styles["h1"]),
        Paragraph(
            "Phase 1 <b>Workload</b> — 100 concurrent virtual users hit a bounded DB pool "
            "(asyncio.Semaphore) and a bounded ThreadPoolExecutor. A single asyncio queue "
            "funnels every LogEntry to SQLite so there is only ever one SQLite writer.",
            styles["body"],
        ),
        Paragraph(
            "Phase 2 <b>Embedding</b> — Bedrock Titan v2 embeds every log entry and the two "
            "Markdown runbooks; vectors go into sqlite-vec virtual tables (vec_logs, vec_docs).",
            styles["body"],
        ),
        Paragraph(
            "Phase 3 <b>Agents</b> — Three LLM calls via the TrueFoundry SDK (TrueForge). Each "
            "agent retrieves top-k logs + runbook chunks scoped to its area, then produces a "
            "schema-enforced JSON report.",
            styles["body"],
        ),
        Paragraph(
            "Phase 4 <b>HITL &amp; Retest</b> — Python validates the LLM's numbers, clamps them, "
            "shows the plan to the operator, applies the approved change, reruns the same "
            "workload with the same seed, and compares. Loop until success = 100%, both pools "
            "reach the ceiling, operator declines, or safety cap is hit.",
            styles["body"],
        ),
        Spacer(1, 6),
        Paragraph("Data flow (simplified):", styles["h2"]),
        Paragraph(diagram, styles["code"]),
    ]
    return story


def _extensibility(styles) -> list:
    return [
        Paragraph("EXTENSIBILITY", styles["eyebrow"]),
        Paragraph("This is a pattern, not just a demo.", styles["h1"]),
        Paragraph(
            "The trio of <i>specialist RAG agent × runbook × HITL-gated Python remediation</i> "
            "generalizes to any bounded shared resource where saturation produces a distinctive "
            "failure signature. To add a new resource type, add three files:",
            styles["body"],
        ),
        Paragraph("<b>1.</b> A new runbook Markdown file under <font face='{}'>Data/</font> describing the escalation ladder and the anti-patterns.".format("AIC-Mono"), styles["body"]),
        Paragraph("<b>2.</b> A new agent module under <font face='{}'>agents/</font> that filters logs by scenario and points at the new runbook.".format("AIC-Mono"), styles["body"]),
        Paragraph("<b>3.</b> A new remediation clamp in <font face='{}'>remediation.py</font> that maps the LLM's proposed number onto a real config knob.".format("AIC-Mono"), styles["body"]),
        Spacer(1, 4),
        Paragraph("Concrete next steps we could ship in a follow-on hackathon:", styles["h2"]),
        Paragraph("<b>Memory pool</b> — trigger on OOMKilled / GC-pressure signatures; escalate heap size.", styles["body"]),
        Paragraph("<b>HTTP client pool</b> — outbound connection saturation, gateway 503 patterns.", styles["body"]),
        Paragraph("<b>Cache pool</b> — Redis eviction storms, cache-stampede detection, capacity resize.", styles["body"]),
        Paragraph("<b>Auto-rollback</b> — if the retest shows regression, revert automatically and flag for review.", styles["body"]),
    ]


def _why_it_matters(styles) -> list:
    return [
        Paragraph("WHY THIS MATTERS", styles["eyebrow"]),
        Paragraph("Judges: this is what SRE-grade LLM assistance looks like.", styles["h1"]),
        Paragraph(
            "<b>Grounded, not guessed.</b> Every recommendation cites specific log IDs and runbook "
            "sections. Operators can trace exactly why the agent proposed a change.",
            styles["body"],
        ),
        Paragraph(
            "<b>Deterministic execution boundary.</b> The LLM proposes; Python validates, clamps, "
            "applies. The remediation module is the only thing that can mutate config, and it "
            "will refuse anything the LLM invents.",
            styles["body"],
        ),
        Paragraph(
            "<b>Empirical validation loop.</b> Every change is retested with identical seed and "
            "user count. Improvement is measured, not asserted. Bottleneck shifts are detected "
            "and handled by the ladder.",
            styles["body"],
        ),
        Paragraph(
            "<b>Human in the loop by construction.</b> The workflow physically blocks on a "
            "threading.Event until an operator clicks Approve or Reject in the UI. There is no "
            "\"auto-apply\" mode. This is aligned with SRE practice, not against it.",
            styles["body"],
        ),
    ]


# ---------- Assemble ----------

class _DocWithFonts(SimpleDocTemplate):
    fonts: dict


def build_pdf(output_path: str = OUTPUT) -> str:
    fonts = _register_fonts()
    styles = _styles(fonts)

    doc = _DocWithFonts(
        output_path,
        pagesize=LETTER,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.85 * inch,
        title="AICult — Incident Diagnosis & Remediation",
        author="AICult Hackathon Team",
    )
    doc.fonts = fonts

    story: List = []
    story.extend(_cover(styles))
    story.extend(_problem(styles))
    story.extend(_what_we_built(styles))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", color=BORDER, thickness=0.5, spaceBefore=4, spaceAfter=10))
    story.extend(_rag_workflow(styles))
    story.append(PageBreak())
    story.extend(_results(styles))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", color=BORDER, thickness=0.5, spaceBefore=4, spaceAfter=10))
    story.extend(_architecture(styles))
    story.append(PageBreak())
    story.extend(_extensibility(styles))
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", color=BORDER, thickness=0.5, spaceBefore=4, spaceAfter=10))
    story.extend(_why_it_matters(styles))

    def on_page(canvas, d):
        _draw_page(canvas, d, "AICult · Local-first · RAG · HITL · TrueFoundry SDK")

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return output_path


if __name__ == "__main__":
    path = build_pdf()
    print(f"Wrote {path}")
