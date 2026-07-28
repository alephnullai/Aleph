"""Generate Aleph README diagrams."""
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.patheffects as pe
import numpy as np

os.makedirs('docs/images', exist_ok=True)

# ─────────────────────────────────────────────
# 1. PIPELINE DIAGRAM
# ─────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(14, 7))
ax.set_xlim(0, 14)
ax.set_ylim(0, 7)
ax.axis('off')
fig.patch.set_facecolor('#0d1117')
ax.set_facecolor('#0d1117')

BLUE   = '#58a6ff'
GREEN  = '#3fb950'
PURPLE = '#bc8cff'
ORANGE = '#f0883e'
GRAY   = '#8b949e'
WHITE  = '#e6edf3'
DARK   = '#161b22'
DARKER = '#0d1117'

def box(ax, x, y, w, h, label, sublabel='', color=BLUE):
    rect = FancyBboxPatch((x, y), w, h,
        boxstyle="round,pad=0.08",
        facecolor=DARK, edgecolor=color, linewidth=1.8, zorder=3)
    ax.add_patch(rect)
    ax.text(x + w/2, y + h/2 + (0.12 if sublabel else 0), label,
        ha='center', va='center', fontsize=9, fontweight='bold',
        color=color, zorder=4)
    if sublabel:
        ax.text(x + w/2, y + h/2 - 0.22, sublabel,
            ha='center', va='center', fontsize=7, color=GRAY, zorder=4)

def arrow(ax, x1, y1, x2, y2, color=GRAY):
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(arrowstyle='->', color=color, lw=1.5),
        zorder=2)

# Source
box(ax, 0.3, 2.8, 1.8, 1.4, 'Source Files', 'C++ / Rust / Python', GRAY)
arrow(ax, 2.1, 3.5, 2.5, 3.5)

# Phase 1 - Ingest
box(ax, 2.5, 2.8, 1.8, 1.4, 'INGEST', 'tree-sitter parse', BLUE)
arrow(ax, 4.3, 3.5, 4.7, 3.5)

# Phase 2 - Symbolize
box(ax, 4.7, 2.8, 1.8, 1.4, 'SYMBOLIZE', 'IDs + fingerprints', BLUE)
arrow(ax, 6.5, 3.5, 6.9, 3.5)

# Phase 3a - Structure (top fork)
box(ax, 6.9, 4.5, 1.8, 1.2, 'STRUCTURE', 'sig / hier / calls', GREEN)
# Phase 3b - Temporal (bottom fork)
box(ax, 6.9, 1.5, 1.8, 1.2, 'FS + TEMPORAL', 'git history', PURPLE)

# Fork arrows from Symbolize
ax.annotate('', xy=(6.9, 5.1), xytext=(6.5, 3.9),
    arrowprops=dict(arrowstyle='->', color=GRAY, lw=1.2, connectionstyle='arc3,rad=-0.3'), zorder=2)
ax.annotate('', xy=(6.9, 2.1), xytext=(6.5, 3.1),
    arrowprops=dict(arrowstyle='->', color=GRAY, lw=1.2, connectionstyle='arc3,rad=0.3'), zorder=2)

# Phase 4 - Compress
box(ax, 9.0, 2.8, 1.8, 1.4, 'COMPRESS', 'FULL/SUMMARY/OMIT', ORANGE)
ax.annotate('', xy=(9.0, 5.1), xytext=(8.7, 5.1),
    arrowprops=dict(arrowstyle='->', color=GRAY, lw=1.2), zorder=2)
ax.annotate('', xy=(9.0, 2.1), xytext=(8.7, 2.1),
    arrowprops=dict(arrowstyle='->', color=GRAY, lw=1.2), zorder=2)
ax.plot([8.7, 8.7], [2.1, 5.1], color=GRAY, lw=1.2, zorder=2)
arrow(ax, 9.0, 3.5, 9.0, 4.2)
arrow(ax, 9.0, 3.5, 9.0, 2.9)
arrow(ax, 10.8, 3.5, 11.2, 3.5)

# Phase 5 - Emit
box(ax, 11.2, 2.8, 1.8, 1.4, 'EMIT', '.struct / .bodies', GREEN)
arrow(ax, 11.2, 2.8, 10.5, 1.2)

# Phase 6 - Link
box(ax, 9.4, 0.3, 2.5, 1.0, 'LINK', 'project map + salience', PURPLE)

ax.text(7.0, 6.7, 'Aleph Compression Pipeline', ha='center', va='top',
    fontsize=13, fontweight='bold', color=WHITE)

plt.tight_layout(pad=0.3)
plt.savefig('docs/images/pipeline.png', dpi=150, bbox_inches='tight',
    facecolor=DARKER, edgecolor='none')
plt.close()
print("pipeline.png done")

# ─────────────────────────────────────────────
# 2. COMPONENT ARCHITECTURE DIAGRAM
# ─────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(14, 8))
ax.set_xlim(0, 14)
ax.set_ylim(0, 8)
ax.axis('off')
fig.patch.set_facecolor('#0d1117')
ax.set_facecolor('#0d1117')

def label_box(ax, x, y, w, h, title, items, color):
    rect = FancyBboxPatch((x, y), w, h,
        boxstyle="round,pad=0.1",
        facecolor='#161b22', edgecolor=color, linewidth=2, zorder=3)
    ax.add_patch(rect)
    ax.text(x + w/2, y + h - 0.25, title,
        ha='center', va='top', fontsize=9, fontweight='bold', color=color, zorder=4)
    for i, item in enumerate(items):
        ax.text(x + 0.15, y + h - 0.55 - i*0.32, f'• {item}',
            ha='left', va='top', fontsize=7.5, color=WHITE, zorder=4)

# Project-level
label_box(ax, 0.3, 4.2, 4.0, 3.5, 'PROJECT COMPONENTS (source-derived)',
    ['project.aleph.map   — manifest + hashes',
     'project.aleph.fs    — directory layout',
     'project.aleph.struct — cross-file call graph',
     'project.aleph.dict  — global symbol dictionary',
     'project.aleph.salience — centrality scores',
     'project.aleph.temporal — git age + churn',
     'project.aleph.attention — LLM load order',
     'project.aleph.coverage — test gap surface'],
    BLUE)

# File-level
label_box(ax, 0.3, 0.3, 4.0, 3.6, 'FILE COMPONENTS (source-derived)',
    ['<file>.aleph.struct  — signatures + hierarchy',
     '<file>.aleph.bodies  — compressed bodies',
     '<file>.aleph.intents — why + invariants',
     '<file>.aleph.errors  — error flow layer',
     '<file>.aleph.tests   — coverage map'],
    GREEN)

# Agent-derived
label_box(ax, 4.8, 4.2, 4.0, 2.2, 'AGENT COMPONENTS (LLM-derived)',
    ['project.aleph.epistemic',
     '  — cached inferences',
     '  — confidence scores',
     '  — prior reasoning',
     '  — never overwritten by compiler'],
    PURPLE)

# Build cache
label_box(ax, 4.8, 0.3, 4.0, 2.0, 'BUILD CACHE',
    ['.aleph.build_cache.json',
     '  — mtime + content hash stamps',
     '  — skips unchanged files',
     '  — aleph build --full to reset'],
    ORANGE)

# LLM interaction
label_box(ax, 9.3, 0.3, 4.4, 7.4, 'LLM INTERACTION PROTOCOL',
    ['ALEPH:MAP       → entry point',
     'ALEPH:FS        → file layout',
     'ALEPH:STRUCT    → architecture',
     'ALEPH:BODIES    → compressed code',
     'ALEPH:ERRORS    → error flows',
     'ALEPH:INTENTS   → intent layer',
     'ALEPH:COVERAGE  → test gaps',
     'ALEPH:EXPAND    → full body',
     'ALEPH:RESOLVE   → dict lookup',
     'ALEPH:CALLERS   → who calls X',
     'ALEPH:ATTENTION → load order',
     'ALEPH:SALIENCE  → criticality',
     'ALEPH:TEMPORAL  → age + churn',
     'ALEPH:EPISTEMIC → prior state',
     'ALEPH:INFER     → write inference',
     'ALEPH:PATCH     → semantic diff'],
    BLUE)

ax.text(7.0, 7.85, 'Aleph Component Architecture', ha='center', va='top',
    fontsize=13, fontweight='bold', color=WHITE)

plt.tight_layout(pad=0.3)
plt.savefig('docs/images/architecture.png', dpi=150, bbox_inches='tight',
    facecolor=DARKER, edgecolor='none')
plt.close()
print("architecture.png done")

# ─────────────────────────────────────────────
# 3. ROADMAP / CHECKLIST DIAGRAM
# ─────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(13, 11))
ax.set_xlim(0, 13)
ax.set_ylim(0, 11)
ax.axis('off')
fig.patch.set_facecolor('#0d1117')
ax.set_facecolor('#0d1117')

DONE   = '#3fb950'
IN_PROGRESS = '#f0883e'
PLANNED = '#58a6ff'
FUTURE  = '#8b949e'

phases = [
    ('Phase 0 — Prototype',         DONE,   0.75, [
        (True,  'Data model (Symbol, Graph, enums)'),
        (True,  'Hashing utilities (symbol_id, semantic, byte)'),
        (True,  'Token counter (tiktoken)'),
        (True,  'Tree-sitter parser (C++, Rust, Python)'),
        (True,  'Symbol extraction + qualified names'),
        (True,  'Symbol ID collision detection + auto-extend'),
        (True,  'Structure extraction (signatures, hierarchy, callgraph)'),
        (True,  'Body compression (FULL / SUMMARY / OMIT)'),
        (True,  'Serializer + emit (.aleph.struct, .aleph.bodies)'),
        (True,  'Semantic fingerprint'),
        (True,  'Single-file salience (fan-in)'),
        (True,  'End-to-end pipeline + CLI (aleph compress <file>)'),
        (True,  'Self-application test (405 tests passing)'),
    ]),
    ('Phase 1 — Core CLI',          DONE,   0.75, [
        (True,  'Full file-level components (.intents, .errors, .tests)'),
        (True,  'Intent + invariant auto-inference'),
        (True,  'Error flow extraction'),
        (True,  'Test coverage mapping (symbol ↔ test bidirectional)'),
        (True,  'aleph compress + expand CLI'),
        (True,  'Semantic diff (aleph diff)'),
        (True,  '490 tests passing'),
    ]),
    ('Phase 2 — Project Pipeline',  IN_PROGRESS, 0.75, [
        (True,  '2.1  aleph build <dir> — project-level components'),
        (True,  '2.2  Cross-file salience + attention budget'),
        (True,  '2.3  Incremental recompilation (<build cache>)'),
        (False, '2.4  aleph query interface (EXPAND/RESOLVE/CALLERS)'),
        (False, '2.5  Temporal layer from git history (full)'),
        (False, '2.6  Full self-application (Aleph builds its own project)'),
        (False, '2.7  Semantic hash staleness detection in map'),
    ]),
    ('Phase 3 — LLM Integration',   PLANNED, 0.75, [
        (False, '3.1  MCP server exposing full ALEPH: protocol'),
        (False, '3.2  Memory compression (60%+ vs raw transcript)'),
        (False, '3.3  IDE plugin (Claude Code / Cursor / Windsurf)'),
        (False, '3.4  Semantic patching end-to-end'),
        (False, '3.5  90%+ session resume success rate'),
    ]),
    ('Future / Considered',         FUTURE,  0.65, [
        (False, 'Rust rewrite of hot paths (<100ms incremental)'),
        (False, 'Multi-agent epistemic versioning (shared vs personal)'),
        (False, 'Confidence-decay on stale inferences'),
        (False, 'Attention budget TUI editor'),
        (False, 'Token savings dashboard'),
        (False, 'Semantic patch marketplace / shared patch library'),
        (False, 'Cross-repo salience (monorepo support)'),
        (False, 'Natural language roundtrip benchmarking suite'),
    ]),
]

y = 10.5
for (phase_name, color, fontsize, items) in phases:
    ax.text(0.4, y, phase_name, ha='left', va='top',
        fontsize=fontsize*1.3, fontweight='bold', color=color)
    y -= 0.38
    for done, item in items:
        mark = '✓' if done else '○'
        mcol = DONE if done else color
        ax.text(0.6, y, mark, ha='left', va='top', fontsize=8, color=mcol, fontweight='bold')
        ax.text(1.05, y, item, ha='left', va='top', fontsize=7.8,
            color=WHITE if done else GRAY)
        y -= 0.30
    y -= 0.18

ax.text(6.5, 10.9, 'Aleph Roadmap', ha='center', va='top',
    fontsize=14, fontweight='bold', color=WHITE)

# Legend
for i, (label, color) in enumerate([('Done','#3fb950'),('In Progress','#f0883e'),('Planned','#58a6ff'),('Future','#8b949e')]):
    ax.add_patch(mpatches.Rectangle((0.4 + i*2.8, 0.05), 0.25, 0.18, color=color))
    ax.text(0.72 + i*2.8, 0.14, label, va='center', fontsize=7.5, color=WHITE)

plt.tight_layout(pad=0.3)
plt.savefig('docs/images/roadmap.png', dpi=150, bbox_inches='tight',
    facecolor=DARKER, edgecolor='none')
plt.close()
print("roadmap.png done")

print("All diagrams generated in docs/images/")
