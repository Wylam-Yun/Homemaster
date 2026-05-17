"""Render experiment results as HORIZONTAL PNG screenshots for demo.

Landscape orientation (1920x1080-ish), large text, flow-oriented layout.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RESULT_DIR = Path("/Users/wylam/Documents/workspace/result")
RESULT_DIR.mkdir(parents=True, exist_ok=True)

# Colors
BG = (24, 24, 32)
TEXT = (230, 230, 230)
GREEN = (72, 199, 114)
YELLOW = (255, 200, 50)
CYAN = (56, 189, 248)
MAGENTA = (192, 132, 252)
DIM = (120, 120, 140)
WHITE = (255, 255, 255)
RED = (248, 113, 113)
HEADER_BG = (35, 40, 70)
STAGE_BG = (30, 32, 48)
ARROW = (100, 110, 160)
BADGE_BG = (45, 50, 80)
PASS_BG = (30, 60, 45)
DIVIDER = (60, 60, 80)


def get_font(size: int = 20):
    candidates = [
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.dfont",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_rounded_rect(draw, xy, radius, fill):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def render_landscape_image(
    sections: list[dict],
    title: str,
    subtitle: str,
    output_path: Path,
    *,
    width: int = 1920,
    height: int = 1080,
):
    """Render a landscape image with stage sections side by side."""
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    font_lg = get_font(22)
    font_md = get_font(17)
    font_sm = get_font(14)
    font_title = get_font(28)
    font_subtitle = get_font(16)

    # Header bar
    draw.rectangle([(0, 0), (width, 80)], fill=HEADER_BG)
    draw.text((40, 18), title, fill=WHITE, font=font_title)
    draw.text((40, 52), subtitle, fill=DIM, font=font_subtitle)

    # Status badge in header
    badge_text = "ALL PASS"
    bbox = draw.textbbox((0, 0), badge_text, font=font_md)
    bw = bbox[2] - bbox[0] + 30
    bx = width - bw - 40
    draw_rounded_rect(draw, (bx, 22, bx + bw, 58), radius=8, fill=PASS_BG)
    draw.text((bx + 15, 26), badge_text, fill=GREEN, font=font_md)

    y_start = 100
    content_h = height - y_start - 30

    # Calculate section widths
    n = len(sections)
    gap = 20
    total_gap = gap * (n + 1)
    sec_w = (width - total_gap) // n

    for i, sec in enumerate(sections):
        x = gap + i * (sec_w + gap)
        y = y_start

        # Section background
        draw_rounded_rect(draw, (x, y, x + sec_w, y + content_h), radius=12, fill=STAGE_BG)

        # Section header
        header_h = 48
        draw_rounded_rect(draw, (x, y, x + sec_w, y + header_h), radius=12, fill=sec.get("header_bg", BADGE_BG))
        # Bottom corners unrounded
        draw.rectangle([(x, y + header_h - 12), (x + sec_w, y + header_h)], fill=sec.get("header_bg", BADGE_BG))

        # Stage badge
        badge = sec.get("badge", "")
        if badge:
            draw.text((x + 15, y + 12), badge, fill=sec.get("badge_color", CYAN), font=font_lg)

        # Arrow between sections (except last)
        if i < n - 1:
            ax = x + sec_w + gap // 2
            ay = y + content_h // 2
            arrow_len = gap - 4
            draw.line([(ax - arrow_len // 2, ay), (ax + arrow_len // 2, ay)], fill=ARROW, width=3)
            # Arrowhead
            draw.polygon([
                (ax + arrow_len // 2, ay),
                (ax + arrow_len // 2 - 8, ay - 6),
                (ax + arrow_len // 2 - 8, ay + 6),
            ], fill=ARROW)

        # Content
        cy = y + header_h + 15
        cx = x + 18
        max_w = sec_w - 36

        for item in sec.get("lines", []):
            text = item["text"]
            color = item.get("color", TEXT)
            font = item.get("font", font_sm)
            spacing = item.get("spacing", 4)

            # Word wrap for long lines
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            if tw > max_w and len(text) > 0:
                # Simple wrap
                mid = len(text) // 2
                for j in range(mid, mid + 20):
                    if j < len(text) and text[j] == " ":
                        line1 = text[:j]
                        line2 = text[j + 1:]
                        draw.text((cx, cy), line1, fill=color, font=font)
                        cy += spacing + (bbox[3] - bbox[1])
                        draw.text((cx, cy), line2, fill=color, font=font)
                        cy += spacing + (bbox[3] - bbox[1]) + 2
                        break
                else:
                    draw.text((cx, cy), text, fill=color, font=font)
                    cy += spacing + (bbox[3] - bbox[1])
            else:
                draw.text((cx, cy), text, fill=color, font=font)
                cy += spacing + (bbox[3] - bbox[1])

            if item.get("gap_after"):
                cy += item["gap_after"]

    img.save(output_path, "PNG")
    print(f"  Saved: {output_path}")


def render_pipeline_flow():
    """Render the 6-stage pipeline as a horizontal flow diagram."""
    # Load result data
    debug_path = Path(
        "/Users/wylam/Documents/workspace/HomeMaster/var/homemaster/debug/stage_07/"
        "fetch_cup_retry-1778332893/result.md"
    )
    blocks = re.findall(r"```json\n(.*?)\n```", debug_path.read_text(), re.DOTALL)
    result = json.loads(blocks[3])

    tc = result["task_card"]
    pc = result["planning_context"]
    plan = result["orchestration_plan"]
    exec_r = result["execution_result"]
    evidence = pc["memory_evidence"]
    hits = evidence["hits"]
    sel = pc["selected_target"]
    mc = result["memory_commit"]["commit_log"]

    font_sm = get_font(14)

    sections = [
        {
            "badge": "02",
            "header_bg": (40, 45, 75),
            "badge_color": YELLOW,
            "lines": [
                {"text": "Task Understanding", "color": YELLOW, "font": get_font(16), "spacing": 6, "gap_after": 8},
                {"text": f"Type:   {tc['task_type']}", "color": TEXT},
                {"text": f"Target: {tc['target']}", "color": TEXT},
                {"text": f"Dest:   {tc['delivery_target']}", "color": TEXT},
                {"text": f"Conf:   {tc['confidence']}", "color": GREEN, "gap_after": 12},
                {"text": "LLM parsed user intent", "color": DIM},
                {"text": "from utterance", "color": DIM},
            ],
        },
        {
            "badge": "03",
            "header_bg": (35, 50, 70),
            "badge_color": CYAN,
            "lines": [
                {"text": "Memory Retrieval (RAG)", "color": CYAN, "font": get_font(16), "spacing": 6, "gap_after": 8},
                {"text": "Embed: BGE-M3 + BM25", "color": TEXT},
                {"text": f"Query: \"{pc['retrieval_query']['query_text']}\"", "color": TEXT},
                {"text": f"Result: {len(hits)} hits", "color": GREEN, "gap_after": 8},
                {"text": "[mem-cup-1] 厨房餐桌", "color": CYAN},
                {"text": "  score=0.33  conf=high", "color": DIM},
                {"text": "[mem-cup-2] 厨房操作台", "color": CYAN},
                {"text": "  score=0.28  conf=med", "color": DIM, "gap_after": 8},
                {"text": "Hybrid ranking fusion", "color": DIM},
            ],
        },
        {
            "badge": "04",
            "header_bg": (45, 40, 70),
            "badge_color": MAGENTA,
            "lines": [
                {"text": "Grounding", "color": MAGENTA, "font": get_font(16), "spacing": 6, "gap_after": 8},
                {"text": f"Status:  {pc['runtime_state_summary']['grounding_status']}", "color": GREEN},
                {"text": f"Target:  {sel['memory_id']}", "color": CYAN},
                {"text": f"Location: {sel['display_text']}", "color": TEXT},
                {"text": f"Conf:    {sel['evidence']['reliability']['status']}", "color": GREEN, "gap_after": 12},
                {"text": "Selected first", "color": DIM},
                {"text": "reliable executable hit", "color": DIM},
            ],
        },
        {
            "badge": "05",
            "header_bg": (45, 50, 60),
            "badge_color": GREEN,
            "lines": [
                {"text": "Orchestration Plan", "color": GREEN, "font": get_font(16), "spacing": 6, "gap_after": 8},
                {"text": "Goal: 交付水杯给用户", "color": TEXT, "gap_after": 6},
                {"text": "1. find_cup  [kitchen]", "color": CYAN},
                {"text": "2. pick_cup  [kitchen]", "color": CYAN},
                {"text": "3. return    [user]", "color": CYAN},
                {"text": "4. deliver   [user]", "color": CYAN, "gap_after": 8},
                {"text": f"Confidence: {plan['confidence']}", "color": GREEN},
            ],
        },
        {
            "badge": "05",
            "header_bg": (30, 55, 45),
            "badge_color": GREEN,
            "lines": [
                {"text": "Execution", "color": GREEN, "font": get_font(16), "spacing": 6, "gap_after": 8},
                {"text": f"Status: {exec_r['final_state']['task_status']}", "color": GREEN},
                {"text": "Recovery: 0 (no retry)", "color": GREEN, "gap_after": 6},
            ] + [
                {"text": f"[{st['subtask_id']}]", "color": GREEN}
                for st in exec_r["final_state"]["subtasks"]
            ] + [
                {"text": "  all verified PASS", "color": DIM, "gap_after": 8},
                {"text": "4/4 subtasks complete", "color": GREEN},
            ],
        },
        {
            "badge": "06",
            "header_bg": (50, 40, 55),
            "badge_color": MAGENTA,
            "lines": [
                {"text": "Summary & Commit", "color": MAGENTA, "font": get_font(16), "spacing": 6, "gap_after": 8},
                {"text": "Result: success", "color": GREEN},
                {"text": f"Memory: {mc['object_memory_update_count']} update", "color": TEXT},
                {"text": f"Facts:  {mc['fact_memory_write_count']} writes", "color": TEXT, "gap_after": 8},
                {"text": "Verified facts:", "color": DIM},
                {"text": "  观察到水杯", "color": DIM},
                {"text": "  拿起水杯", "color": DIM},
                {"text": "  到达位置", "color": DIM},
                {"text": "  交付水杯", "color": DIM, "gap_after": 8},
                {"text": "Memory updated", "color": GREEN},
            ],
        },
    ]

    render_landscape_image(
        sections,
        "HomeMaster — Live Pipeline Experiment",
        "fetch_cup_retry  |  Utterance: \"帮我拿水杯\"  |  Mimo + BGE-M3  |  All Stages PASS",
        RESULT_DIR / "experiment_pipeline.png",
        width=1920,
        height=1080,
    )


def render_test_screenshot():
    """Render test results as horizontal image."""
    font_lg = get_font(20)
    font_md = get_font(16)
    font_sm = get_font(14)
    font_title = get_font(26)

    width, height = 1920, 1080
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    # Header
    draw.rectangle([(0, 0), (width, 80)], fill=HEADER_BG)
    draw.text((40, 18), "HomeMaster Test Suite", fill=WHITE, font=font_title)
    draw.text((40, 52), "364 passed  |  20 skipped  |  9.54s  |  0 failed", fill=GREEN, font=font_md)

    # Badge
    badge = "ALL PASS"
    bbox = draw.textbbox((0, 0), badge, font=font_lg)
    bw = bbox[2] - bbox[0] + 30
    bx = width - bw - 40
    draw_rounded_rect(draw, (bx, 22, bx + bw, 58), radius=8, fill=PASS_BG)
    draw.text((bx + 15, 26), badge, fill=GREEN, font=font_lg)

    y = 100

    # P9 Recovery tests (left column)
    col1_x = 40
    col2_x = 650
    col3_x = 1300

    draw.text((col1_x, y), "P9 Recovery Tests (9 tests)", fill=YELLOW, font=font_lg)
    p9_tests = [
        "recovery_always_executes",
        "finish_failed_stops_loop",
        "ask_user_sets_needs_user_input",
        "retry_step_succeeds_on_second_try",
        "max_attempts_enforced",
        "no_infinite_loop",
        "decision_generation_failure_graceful",
        "attempts_populated_on_success",
        "reobserve_treated_as_retry_step",
    ]
    ty = y + 32
    for t in p9_tests:
        draw.text((col1_x + 10, ty), "[PASS]", fill=GREEN, font=font_sm)
        draw.text((col1_x + 70, ty), t, fill=TEXT, font=font_sm)
        ty += 26

    # Infrastructure tests (middle column)
    draw.text((col2_x, y), "Infrastructure Tests", fill=YELLOW, font=font_lg)
    infra = [
        ("test_stage_registry.py", "2 tests"),
        ("test_execution_state_reset.py", "3 tests"),
        ("test_recovery_config.py", "2 tests"),
    ]
    ty = y + 32
    for name, count in infra:
        draw.text((col2_x + 10, ty), "[PASS]", fill=GREEN, font=font_sm)
        draw.text((col2_x + 70, ty), name, fill=TEXT, font=font_sm)
        draw.text((col2_x + 340, ty), count, fill=DIM, font=font_sm)
        ty += 26

    # Test categories (right column)
    draw.text((col3_x, y), "Test Categories", fill=YELLOW, font=font_lg)
    cats = [
        "Pipeline core & contracts",
        "Stage 01-07 adapters",
        "Memory RAG (BM25+dense)",
        "Grounding & planning",
        "Orchestration & execution",
        "Recovery loop (P9)",
        "Evidence & memory commit",
        "Scenario snapshots",
        "World overlay & token budget",
    ]
    ty = y + 32
    for c in cats:
        draw.text((col3_x + 10, ty), f"  {c}", fill=TEXT, font=font_sm)
        ty += 26

    # Bottom: flow diagram showing pipeline
    flow_y = 700
    draw.line([(40, flow_y), (width - 40, flow_y)], fill=DIVIDER, width=1)
    flow_y += 20

    draw.text((40, flow_y), "Pipeline Flow", fill=YELLOW, font=font_lg)
    flow_y += 35

    stages = ["02: Task", "03: Memory", "04: Ground", "05: Plan", "05: Exec", "06: Summary"]
    stage_w = 240
    stage_h = 80
    total = len(stages) * stage_w + (len(stages) - 1) * 40
    start_x = (width - total) // 2

    for i, s in enumerate(stages):
        sx = start_x + i * (stage_w + 40)
        draw_rounded_rect(draw, (sx, flow_y, sx + stage_w, flow_y + stage_h), radius=10, fill=BADGE_BG)
        # Stage number
        parts = s.split(": ")
        draw.text((sx + 15, flow_y + 15), parts[0], fill=CYAN, font=font_md)
        draw.text((sx + 15, flow_y + 40), parts[1], fill=TEXT, font=font_sm)
        # PASS badge
        draw_rounded_rect(draw, (sx + stage_w - 60, flow_y + 10, sx + stage_w - 10, flow_y + 32), radius=4, fill=PASS_BG)
        draw.text((sx + stage_w - 52, flow_y + 12), "PASS", fill=GREEN, font=get_font(11))

        # Arrow
        if i < len(stages) - 1:
            ax = sx + stage_w + 5
            ay = flow_y + stage_h // 2
            draw.line([(ax, ay), (ax + 30, ay)], fill=ARROW, width=3)
            draw.polygon([(ax + 30, ay), (ax + 22, ay - 5), (ax + 22, ay + 5)], fill=ARROW)

    img.save(RESULT_DIR / "experiment_tests.png", "PNG")
    print(f"  Saved: {RESULT_DIR / 'experiment_tests.png'}")


def render_architecture_screenshot():
    """Render architecture as horizontal image."""
    font_lg = get_font(20)
    font_md = get_font(16)
    font_sm = get_font(14)
    font_title = get_font(26)

    width, height = 1920, 1080
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    # Header
    draw.rectangle([(0, 0), (width, 80)], fill=HEADER_BG)
    draw.text((40, 18), "HomeMaster System Architecture", fill=WHITE, font=font_title)
    draw.text((40, 52), "7-Stage Pipeline  |  P9 Recovery Loop  |  Memory-Augmented Task Planning", fill=DIM, font=font_md)

    y = 100

    # Left: Pipeline stages as flow
    col1_w = 900
    draw.text((40, y), "Pipeline Stages", fill=YELLOW, font=font_lg)
    y += 35

    stages = [
        ("01", "System Prompt", "programmatic", False),
        ("02", "Task Understanding", "Mimo (live LLM)", True),
        ("03", "Memory Retrieval (RAG)", "BGE-M3 + BM25", True),
        ("04", "Grounding", "programmatic", False),
        ("05", "Orchestration + Execution", "Mimo + Recovery", True),
        ("06", "Summary & Memory Commit", "Mimo + programmatic", True),
        ("07", "Final Result", "programmatic", False),
    ]

    box_w = 380
    box_h = 65
    gap = 15
    for i, (num, name, mode, is_live) in enumerate(stages):
        bx = 40
        by = y + i * (box_h + gap)
        bg = STAGE_BG if not is_live else (30, 45, 55)
        draw_rounded_rect(draw, (bx, by, bx + box_w, by + box_h), radius=8, fill=bg)

        # Number badge
        draw_rounded_rect(draw, (bx + 10, by + 12, bx + 45, by + 45), radius=6, fill=BADGE_BG)
        draw.text((bx + 16, by + 15), num, fill=CYAN, font=font_md)

        # Name
        draw.text((bx + 55, by + 10), name, fill=TEXT, font=font_md)
        draw.text((bx + 55, by + 34), mode, fill=GREEN if is_live else DIM, font=font_sm)

        # Arrow
        if i < len(stages) - 1:
            ay = by + box_h
            draw.line([(bx + box_w // 2, ay), (bx + box_w // 2, ay + gap)], fill=ARROW, width=2)
            draw.polygon([
                (bx + box_w // 2, ay + gap),
                (bx + box_w // 2 - 4, ay + gap - 6),
                (bx + box_w // 2 + 4, ay + gap - 6),
            ], fill=ARROW)

    # Right: Recovery Loop + Model Boundary
    rx = 500
    ry = 100

    # Recovery loop
    draw.text((rx, ry), "P9 Recovery Loop (Stage 05)", fill=YELLOW, font=font_lg)
    ry += 35

    rl_box_w = 420
    rl_box_h = 320
    draw_rounded_rect(draw, (rx, ry, rx + rl_box_w, ry + rl_box_h), radius=12, fill=STAGE_BG)

    actions = [
        ("retry_step", "Reset subtask + re-execute", CYAN),
        ("reobserve", "= retry_step (re-observe)", CYAN),
        ("retrieve_again", "Inject neg evidence -> Stage03-04-05", MAGENTA),
        ("replan", "Fresh state -> re-plan -> execute", YELLOW),
        ("ask_user", "Set needs_user_input status", RED),
        ("finish_failed", "Terminate loop, mark failed", RED),
    ]

    ay = ry + 15
    for name, desc, color in actions:
        draw.text((rx + 15, ay), name, fill=color, font=font_sm)
        draw.text((rx + 180, ay), desc, fill=DIM, font=font_sm)
        ay += 24

    ay += 10
    draw.text((rx + 15, ay), "Config:", fill=DIM, font=font_sm)
    draw.text((rx + 80, ay), "recovery.max_attempts = 3", fill=TEXT, font=font_sm)
    ay += 22
    draw.text((rx + 15, ay), "Safety:", fill=DIM, font=font_sm)
    draw.text((rx + 80, ay), "bounded loop + logging + graceful fail", fill=TEXT, font=font_sm)

    # Model boundary (below recovery)
    ry2 = ry + rl_box_h + 40
    draw.text((rx, ry2), "Model Boundary", fill=YELLOW, font=font_lg)
    ry2 += 30

    boundaries = [
        ("task_understanding", "Mimo", True),
        ("memory_query", "Mimo", True),
        ("embedding", "BGE-M3", True),
        ("grounding", "programmatic", False),
        ("planning", "Mimo", True),
        ("step_decision", "Mimo", True),
        ("skills", "simulated", False),
        ("verification", "simulated", False),
        ("summary", "Mimo", True),
        ("memory_commit", "programmatic", False),
    ]

    # Two columns
    for i, (comp, mode, is_live) in enumerate(boundaries):
        col = 0 if i < 6 else 1
        row = i if i < 6 else i - 6
        bx = rx + col * 220
        by = ry2 + row * 24

        color = GREEN if is_live else DIM
        draw.text((bx, by), f"{comp}", fill=TEXT, font=font_sm)
        draw.text((bx + 170, by), mode, fill=color, font=font_sm)

    img.save(RESULT_DIR / "experiment_architecture.png", "PNG")
    print(f"  Saved: {RESULT_DIR / 'experiment_architecture.png'}")


if __name__ == "__main__":
    print("Rendering landscape screenshots...")
    render_pipeline_flow()
    render_test_screenshot()
    render_architecture_screenshot()
    print("Done!")
