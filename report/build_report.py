"""Build report/project_report.pdf from the outputs of the pipeline run.

Run (after `python main.py`):   python report/build_report.py

Every number, table, figure, log excerpt and code excerpt in the PDF is read from the
files produced by the pipeline or from the source files of this repository, so the
report always matches the submitted code and results. Nothing is typed in by hand
except the explanatory text.
"""
import ast
import json
import re
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate, Paragraph,
                                Preformatted, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

from src import config, validator   # noqa: E402  (needs sys.path above)

OUTPUT_PDF = config.REPORT_DIR / "project_report.pdf"

# ------------------------------------------------------------------ fonts / styles ----
FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
pdfmetrics.registerFont(TTFont("DejaVu", str(FONT_DIR / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Italic", str(FONT_DIR / "DejaVuSans-Oblique.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-BoldItalic", str(FONT_DIR / "DejaVuSans-BoldOblique.ttf")))
pdfmetrics.registerFont(TTFont("DejaVuMono", str(FONT_DIR / "DejaVuSansMono.ttf")))
pdfmetrics.registerFont(TTFont("DejaVuSerif", str(FONT_DIR / "DejaVuSerif.ttf")))
pdfmetrics.registerFont(TTFont("DejaVuSerif-Bold", str(FONT_DIR / "DejaVuSerif-Bold.ttf")))
pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold", italic="DejaVu-Italic", boldItalic="DejaVu-BoldItalic")

base = getSampleStyleSheet()
BODY = ParagraphStyle("Body", parent=base["BodyText"], fontName="DejaVu", fontSize=9.5, leading=13.5, spaceAfter=6)
BULLET = ParagraphStyle("Bullet", parent=BODY, leftIndent=14, bulletIndent=3, spaceAfter=2)
H1 = ParagraphStyle("H1", parent=base["Heading1"], fontName="DejaVu-Bold", fontSize=16, leading=20, spaceBefore=6, spaceAfter=10, textColor=colors.HexColor("#12355b"))
H2 = ParagraphStyle("H2", parent=base["Heading2"], fontName="DejaVu-Bold", fontSize=11.5, leading=15, spaceBefore=10, spaceAfter=5, textColor=colors.HexColor("#1f4e79"))
CAPTION = ParagraphStyle("Caption", parent=BODY, fontName="DejaVu-Italic", fontSize=8.5, leading=11, alignment=TA_CENTER, spaceAfter=10)
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=7.8, leading=10, spaceAfter=0)
CELL_B = ParagraphStyle("CellB", parent=CELL, fontName="DejaVu-Bold", textColor=colors.white)
CODE = ParagraphStyle("Code", fontName="DejaVuMono", fontSize=6.6, leading=8.2, backColor=colors.HexColor("#f3f5f7"),
                      borderColor=colors.HexColor("#d0d7de"), borderWidth=0.5, borderPadding=4, spaceBefore=3, spaceAfter=9)
TITLE = ParagraphStyle("Title", parent=H1, fontSize=24, leading=30, alignment=TA_CENTER, spaceAfter=18)
SUBTITLE = ParagraphStyle("Sub", parent=BODY, fontSize=12, leading=17, alignment=TA_CENTER, spaceAfter=6)

counter = {"figure": 0, "table": 0}


def esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def P(text, style=BODY):
    return Paragraph(text, style)


def bullets(items):
    return [Paragraph(item, BULLET, bulletText="•") for item in items]


def fmt(value, digits=2):
    if value is None or (isinstance(value, float) and value != value):
        return "–"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def table(rows, header, widths=None, title=None, align_right_from=None):
    """rows: list of lists. Cells are wrapped in Paragraphs so long text breaks lines."""
    counter["table"] += 1
    data = [[Paragraph(esc(h), CELL_B) for h in header]]
    for row in rows:
        data.append([Paragraph(esc(fmt(c)), CELL) for c in row])
    tbl = Table(data, colWidths=widths, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
             ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8c4d0")),
             ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7fa")]),
             ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
             ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    tbl.setStyle(TableStyle(style))
    parts = []
    if title:
        parts.append(P(f"<b>Table {counter['table']}.</b> {esc(title)}", ParagraphStyle("TT", parent=BODY, fontSize=8.5, spaceAfter=3, spaceBefore=4)))
    parts.append(tbl)
    parts.append(Spacer(1, 8))
    return parts


def code(text: str, width=116):
    """Code block; lines longer than the page width are wrapped (continuation lines are indented)."""
    lines = []
    for line in text.rstrip("\n").splitlines():
        while len(line) > width:
            lines.append(line[:width])
            line = "        " + line[width:]
        lines.append(line)
    return Preformatted("\n".join(lines), CODE)


def figure(path: Path, caption: str, width_cm=15.0):
    counter["figure"] += 1
    if not Path(path).exists():
        return [P(f"<i>[figure {esc(Path(path).name)} not found - run the pipeline first]</i>")]
    w, h = ImageReader(str(path)).getSize()
    width = width_cm * cm
    image = Image(str(path), width=width, height=width * h / w)
    return [KeepTogether([image, P(f"<b>Figure {counter['figure']}.</b> {esc(caption)}", CAPTION)])]


# ------------------------------------------------------------------ data access ----
def jload(path):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def csv_load(path, **kw):
    path = Path(path)
    return pd.read_csv(path, **kw) if path.exists() else pd.DataFrame()


def source_of(rel_path: str, name: str) -> str:
    """Source code of a function or module-level assignment, taken from the repository file."""
    text = (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return ast.get_source_segment(text, node)
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.get_source_segment(text, node)
    return f"# {name} not found in {rel_path}"


def lines_between(rel_path: str, start: str, end: str) -> str:
    lines = (PROJECT_ROOT / rel_path).read_text(encoding="utf-8").splitlines()
    begin = next((i for i, line in enumerate(lines) if start in line), None)
    if begin is None:
        return f"# '{start}' not found in {rel_path}"
    stop = next((i for i in range(begin + 1, len(lines)) if end in lines[i]), len(lines) - 1)
    indent = min(len(l) - len(l.lstrip()) for l in lines[begin:stop + 1] if l.strip())
    return "\n".join(l[indent:] for l in lines[begin:stop + 1])


def log_excerpt(name: str, max_lines=14) -> str:
    path = config.LOGS_DIR / name
    if not path.exists():
        return f"(log {name} not found - run `python main.py`)"
    lines = path.read_text(encoding="utf-8").splitlines()
    shown = lines[:max_lines] + (["..."] + lines[-3:] if len(lines) > max_lines + 3 else [])
    return "\n".join(l[:118] for l in shown)


# ------------------------------------------------------------------ architecture diagram ----
def make_architecture_diagram() -> Path:
    config.FIGURES_DIR.mkdir(exist_ok=True)
    path = config.FIGURES_DIR / "architecture.png"
    boxes = [
        ("Open-Meteo\nAir Quality API", 0.5, 4.2, "#d9e8fb"),
        ("api_client.py\nraw_data.csv", 3.2, 4.2, "#e8f3e0"),
        ("Kafka Producer\nproducer.py", 5.9, 4.2, "#fde9cf"),
        ("Kafka Broker\ntopic air_quality_raw", 8.6, 4.2, "#f8d3d0"),
        ("Kafka Consumer\nconsumer.py", 8.6, 2.2, "#fde9cf"),
        ("Pandera validation\nvalidator.py", 5.9, 2.2, "#e8f3e0"),
        ("pandas + NumPy\ncleaner.py", 3.2, 2.2, "#e8f3e0"),
        ("SQLite3 output.db\nloader.py", 0.5, 2.2, "#e1dcf3"),
        ("EDA + figures\nanalysis.py", 0.5, 0.4, "#e8f3e0"),
        ("Verification\nverification.py", 3.2, 0.4, "#e8f3e0"),
        ("PDF report\nbuild_report.py", 5.9, 0.4, "#d9e8fb"),
    ]
    fig, ax = plt.subplots(figsize=(11, 5.6))
    ax.set_xlim(0, 11.3)
    ax.set_ylim(0, 5.8)
    ax.axis("off")
    centers = {}
    for label, x, y, color in boxes:
        ax.add_patch(FancyBboxPatch((x, y), 2.2, 1.1, boxstyle="round,pad=0.05", fc=color, ec="#35506b", lw=1.2))
        ax.text(x + 1.1, y + 0.55, label, ha="center", va="center", fontsize=9)
        centers[label.split("\n")[0]] = (x, y)

    def arrow(a, b, text="", side="h"):
        (ax1, ay1), (bx1, by1) = centers[a], centers[b]
        if side == "h":
            start = (ax1 + 2.25 if bx1 > ax1 else ax1 - 0.05, ay1 + 0.55)
            end = (bx1 - 0.05 if bx1 > ax1 else bx1 + 2.25, by1 + 0.55)
        else:
            start = (ax1 + 1.1, ay1 - 0.05 if by1 < ay1 else ay1 + 1.15)
            end = (bx1 + 1.1, by1 + 1.15 if by1 < ay1 else by1 - 0.05)
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=14, color="#35506b", lw=1.4))
        if text:
            ax.text((start[0] + end[0]) / 2, (start[1] + end[1]) / 2 + 0.18, text, ha="center", fontsize=7.5, color="#35506b")

    arrow("Open-Meteo", "api_client.py", "HTTP GET JSON")
    arrow("api_client.py", "Kafka Producer", "raw_data.csv")
    arrow("Kafka Producer", "Kafka Broker", "JSON messages")
    arrow("Kafka Broker", "Kafka Consumer", "poll", side="v")
    arrow("Kafka Consumer", "Pandera validation", "consumed_data.csv")
    arrow("Pandera validation", "pandas + NumPy", "valid_records.csv")
    arrow("pandas + NumPy", "SQLite3 output.db", "cleaned_data.csv")
    arrow("SQLite3 output.db", "EDA + figures", "SQL query", side="v")
    arrow("EDA + figures", "Verification", "")
    arrow("Verification", "PDF report", "")
    ax.set_title("SIS2 pipeline architecture", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# ------------------------------------------------------------------ document template ----
class ReportDoc(BaseDocTemplate):
    def __init__(self, filename, **kw):
        super().__init__(filename, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm, **kw)
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="main")
        self.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=self.decorate)])

    title_info = {}

    def decorate(self, canvas, doc):
        if doc.page == 1:
            self.draw_title_page(canvas)
            return
        canvas.saveState()
        canvas.setFont("DejaVu", 7.5)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(2 * cm, 1.1 * cm, "SIS2 - Air-quality streaming and validation pipeline")
        canvas.drawRightString(A4[0] - 2 * cm, 1.1 * cm, f"Page {doc.page}")
        canvas.restoreState()

    def draw_title_page(self, canvas):
        """Title page in the layout of the KBTU SIS title-page template."""
        info = self.title_info
        width, height = A4
        canvas.saveState()
        canvas.setFillColor(colors.black)
        canvas.setFont("DejaVuSerif", 20)
        canvas.drawCentredString(width / 2, height - 4.6 * cm, "Kazakh-British Technical University")
        canvas.setFont("DejaVuSerif", 15)
        canvas.drawCentredString(width / 2, height - 6.0 * cm, info.get("course_short", "Data collection and preparation"))
        canvas.setFont("DejaVuSerif", 12)
        y = height - 11.6 * cm
        canvas.drawCentredString(width / 2, y, f"STUDENTS' INDEPENDENT STUDY (SIS) \u2116{info.get('sis_number', 2)}")
        canvas.setFont("DejaVuSerif", 11)
        for line in simpleSplit(info.get("project_title", ""), "DejaVuSerif", 11, width - 6 * cm):
            y -= 0.65 * cm
            canvas.drawCentredString(width / 2, y, line)
        y = height - 15.2 * cm
        canvas.setFont("DejaVuSerif-Bold", 10)
        canvas.drawString(2.4 * cm, y, f"Full Name: {info.get('student_name', '')}")
        canvas.drawString(2.4 * cm, y - 0.8 * cm, f"ID: {info.get('student_id', '')}")
        if info.get("group_number"):
            canvas.drawString(2.4 * cm, y - 1.6 * cm, f"Group: {info['group_number']}")
        canvas.setFont("DejaVuSerif-Bold", 10)
        canvas.drawCentredString(width / 2, 3.4 * cm, info.get("date", ""))
        canvas.drawCentredString(width / 2, 2.6 * cm, "Almaty")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name in ("H1", "H2"):
            level = 0 if flowable.style.name == "H1" else 1
            text = flowable.getPlainText()
            key = f"h{self.seq.nextf('heading')}"
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (level, text, self.page, key))


# ------------------------------------------------------------------ report content ----
def build() -> int:
    cfg = jload(config.REPORT_DIR / "report_config.json")
    s = jload(config.PIPELINE_SUMMARY)
    if not s:
        print("[report] ERROR data/pipeline_summary.json not found - run `python main.py` first")
        return 1
    coll, prod, cons = s.get("collection", {}), jload(config.PRODUCER_STATS), jload(config.CONSUMER_STATS)
    val, clean = jload(config.VALIDATION_REPORT), jload(config.CLEANING_REPORT)
    idem, sqlres = jload(config.IDEMPOTENCY_REPORT), jload(config.SQL_VERIFICATION_RESULTS)
    eda, ver = jload(config.EDA_SUMMARY), jload(config.VERIFICATION_REPORT)
    raw = csv_load(config.RAW_CSV)
    valid_df, invalid_df = csv_load(config.VALID_CSV), csv_load(config.INVALID_CSV)
    test_df = csv_load(config.VALIDATION_TEST_CSV)
    architecture = make_architecture_diagram()
    submitted = cfg.get("submission_date") or date.today().strftime("%d.%m.%Y")
    n_cities, n_days = len(config.CITIES), (pd.Timestamp(coll.get("end_date", config.END_DATE)) - pd.Timestamp(coll.get("start_date", config.START_DATE))).days + 1

    story = []
    # ---------------- title page (drawn on the canvas, see ReportDoc.draw_title_page) ----
    story.append(PageBreak())

    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("t1", fontName="DejaVu-Bold", fontSize=9.5, leading=14, leftIndent=0),
                       ParagraphStyle("t2", fontName="DejaVu", fontSize=8.5, leading=12, leftIndent=14)]
    story += [P("Contents", ParagraphStyle("TocHead", parent=H1)), toc, PageBreak()]

    # ---------------- 1 introduction ----
    story += [P("1. Introduction", H1), P("1.1 Project background", H2),
              P("Air pollution by fine particles (PM2.5, PM10) and gases (NO2, SO2, CO, O3) is one of the main environmental health risks in cities. "
                "The World Health Organization publishes guideline values for these pollutants, and the European Air Quality Index (EAQI) summarises them in six bands from "
                "'Good' to 'Extremely poor'. Large Kazakhstani cities differ in climate, size and energy use, so a comparison of their air quality is a meaningful "
                "real-world data problem for a data-engineering project."),
              P("1.2 Problem description", H2),
              P("Hourly air-quality data are available from a public API, but they arrive as nested JSON and may contain gaps. The task is to build a complete, reproducible "
                "pipeline that collects the data, transports it through a real Apache Kafka broker, validates it with Pandera, cleans and transforms it with pandas and NumPy, "
                "stores it in SQLite3 without duplicates and analyses it. The research question is: <b>how does air quality compare between the four cities and at which "
                "time of the day is it worst?</b>"),
              P("1.3 Objectives", H2)]
    story += bullets(["Collect at least 200 real records (numerical, categorical and date/time fields) from the Open-Meteo Air Quality API and keep the raw data.",
                      "Publish and consume every record as a JSON message through a real Kafka broker, with a producer and a consumer implemented as separate scripts.",
                      "Validate the received data with a Pandera schema (at least five rules), quarantine invalid rows and demonstrate detection with deliberately invalid test records.",
                      "Clean and transform the data (missing values, duplicates, types, text, NumPy features, filtering, GroupBy, reshaping).",
                      "Load the data into SQLite3 with keys and constraints and prove that repeated loading is idempotent.",
                      "Perform EDA with at least three Matplotlib/Seaborn visualisations and verify record counts across all stages."])
    story += [P("1.4 Expected outcomes", H2),
              P("A working pipeline that is started with one command (<font name='DejaVuMono'>python main.py</font>), consistent record counts at every stage, a SQLite3 database "
                "with the cleaned data, six figures with interpretation, and this report whose numbers are generated from the real run.")]

    # ---------------- 2 data source ----
    story += [PageBreak(), P("2. Data Source and Collection", H1), P("2.1 API description and endpoint", H2),
              P(f"The <b>Open-Meteo Air Quality API</b> provides hourly air-quality model data (CAMS) for any coordinates and requires no API key for non-commercial use. "
                f"Endpoint: <font name='DejaVuMono'>{esc(config.API_URL)}</font>. The response is JSON with an <i>hourly</i> object that contains a <i>time</i> array and one array per requested variable.")]
    params = [["latitude / longitude", "Coordinates of the city (one request per city)"],
              ["hourly", ", ".join(config.HOURLY_VARIABLES)],
              ["start_date / end_date", f"{coll.get('start_date', config.START_DATE)} / {coll.get('end_date', config.END_DATE)} ({n_days} days)"],
              ["timezone", config.TIMEZONE]]
    story += table(params, ["Request parameter", "Value"], [4.5 * cm, 12.5 * cm], "Request parameters")
    story += [P("2.2 Data collection procedure", H2)]
    story += bullets(["<b>src/api_client.py</b> sends one HTTP GET request per city using the <i>requests</i> library with a timeout of "
                      f"{config.REQUEST_TIMEOUT_S} s.",
                      f"Errors are handled: connection errors, timeouts, HTTP 429 and 5xx are retried up to {config.MAX_RETRIES} times with exponential back-off; "
                      "HTTP 4xx errors (the API returns <i>{\"error\": true, \"reason\": ...}</i>) are reported and not retried; the JSON structure and array lengths are checked.",
                      "The column-oriented hourly arrays are flattened into one record per city and hour. <i>record_id</i> = city key + timestamp, <i>collected_at</i> = download time (UTC).",
                      "The original responses are preserved unchanged in <i>data/raw_api_responses.json</i> and the flattened records in <i>data/raw_data.csv</i> before any validation or cleaning."])
    log_rows = [[r.get("city"), r.get("attempt", "-"), r.get("status_code", "-"), r.get("result", "")] for r in coll.get("requests", [])]
    story += table(log_rows, ["City", "Attempt", "HTTP status", "Result"], [3 * cm, 2 * cm, 2.5 * cm, 9.5 * cm], "Request log of this run")
    story += [P("2.3 Dataset size and structure", H2),
              P(f"<b>{fmt(coll.get('collected_records'))}</b> records were collected ({n_cities} cities x {n_days} days x 24 hours = {n_cities * n_days * 24} expected), "
                f"{len(raw.columns)} columns per record. The assignment minimum is 200 records.")]
    story += table([[c, fmt(n)] for c, n in coll.get("records_per_city", {}).items()], ["City", "Records"], [6 * cm, 4 * cm], "Records per city")
    field_rows = [["record_id", "identifier (text)", "Unique key: city key + timestamp"], ["city, region", "categorical", "Four cities, four regions"],
                  ["latitude, longitude", "numerical", "Centre of the model grid cell"], ["time", "date/time", "Local hourly timestamp (Asia/Almaty)"],
                  ["pm10, pm2_5, carbon_monoxide, nitrogen_dioxide, sulphur_dioxide, ozone", "numerical", "Concentrations in µg/m³"],
                  ["european_aqi", "numerical", "European Air Quality Index"], ["collected_at", "date/time", "Download time (UTC)"]]
    story += table(field_rows, ["Field", "Kind", "Description"], [6.2 * cm, 3 * cm, 7.8 * cm], "Dataset fields (full list in docs/data_dictionary.md)")
    if not raw.empty:
        sample = raw[["record_id", "city", "time", "pm10", "pm2_5", "ozone", "european_aqi"]].head(5).values.tolist()
        story += [P("2.4 Examples of collected records", H2)]
        story += table(sample, ["record_id", "city", "time", "pm10", "pm2_5", "ozone", "european_aqi"],
                       [4.2 * cm, 2 * cm, 3.2 * cm, 1.8 * cm, 1.8 * cm, 1.8 * cm, 2.2 * cm], "First five collected records (data/raw_data.csv)")

    # ---------------- 3 architecture ----
    story += [PageBreak(), P("3. Pipeline Architecture", H1)]
    story += figure(architecture, "Complete pipeline architecture and the files exchanged between the components", 16.5)
    comp = [["API client", "src/api_client.py", "Open-Meteo API", "data/raw_data.csv, raw_api_responses.json"],
            ["Kafka Producer", "src/producer.py", "data/raw_data.csv", "JSON messages in topic, data/producer_stats.json"],
            ["Kafka Consumer", "src/consumer.py", "topic + producer_stats.json", "data/consumed_data.csv, consumer_stats.json"],
            ["Pandera validator", "src/validator.py", "data/consumed_data.csv", "valid_records.csv, invalid_records.csv, validation_report.json"],
            ["Cleaner", "src/cleaner.py", "data/valid_records.csv", "cleaned_data.csv, data/aggregates/*.csv"],
            ["Loader", "src/loader.py", "data/cleaned_data.csv", "data/output.db, idempotency_report.json"],
            ["Analysis", "src/analysis.py", "data/output.db (SQL)", "figures/*.png, data/eda_summary.json"],
            ["Verification", "src/verification.py", "data/pipeline_summary.json, output.db", "data/verification_report.json"]]
    story += table(comp, ["Component", "Script", "Input", "Output"], [2.8 * cm, 3.4 * cm, 4.4 * cm, 6.4 * cm], "Pipeline components and their connected outputs")
    story += [P("Data flow and interaction", H2),
              P("Each stage reads the file written by the previous stage, so no stage uses a manually prepared dataset. The orchestrator <i>main.py</i> starts every stage as a "
                "separate process, streams its output to the console and stores it in <i>logs/</i>. The producer and the consumer only communicate through the Kafka broker: "
                "the producer tags every message with a <i>run_id</i> header and stores the number of confirmed messages in <i>producer_stats.json</i>; the consumer reads this file "
                "to know how many messages to expect and which run to accept. The analysis reads from the SQLite database, not from CSV files, which proves that the stored data is usable.")]

    # ---------------- 4 kafka ----
    story += [PageBreak(), P("4. Apache Kafka Implementation", H1), P("4.1 Kafka broker configuration", H2),
              P("A real single-node Apache Kafka broker (<font name='DejaVuMono'>apache/kafka:3.9.0</font>, KRaft mode, no ZooKeeper) is started with Docker Compose "
                f"and is reachable at <font name='DejaVuMono'>{esc(prod.get('bootstrap_servers', config.KAFKA_BOOTSTRAP_SERVERS))}</font>."),
              code((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")),
              P("4.2 Topic configuration", H2)]
    story += table([["Topic name", prod.get("topic", config.KAFKA_TOPIC)], ["Partitions", prod.get("partitions", config.KAFKA_PARTITIONS)],
                    ["Replication factor", config.KAFKA_REPLICATION_FACTOR], ["Message key", "city name in lower case (messages of one city go to one partition)"],
                    ["Message value", "one API record as a JSON object (UTF-8)"], ["Message header", "run_id - identifies the producer run"]],
                   ["Setting", "Value"], [4.5 * cm, 12.5 * cm], "Topic configuration (created by src/kafka_utils.ensure_topic)")
    story += [P("4.3 Producer implementation", H2),
              P("The producer reads <i>data/raw_data.csv</i>, replaces NaN by <i>None</i> (NaN is not valid JSON), serialises each record to JSON with the value serializer, "
                "sends it with <i>acks='all'</i> and counts broker confirmations in callbacks."),
              code(source_of("src/producer.py", "create_producer")), code(lines_between("src/producer.py", "sent = 0", "producer.flush")),
              P("4.4 Consumer implementation", H2),
              P("The consumer subscribes with a new consumer group and <i>auto_offset_reset='earliest'</i>, filters messages by the <i>run_id</i> header, deserialises JSON and counts bad messages. "
                "<b>Termination condition:</b> it stops when the expected number of messages has been consumed, after an idle timeout, or after a maximum run time."),
              code(lines_between("src/consumer.py", "while True:", "except Exception as exc:"))]
    story += [P("4.5 JSON serialization and deserialization", H2), code(source_of("src/kafka_utils.py", "serialize_value")),
              code(source_of("src/kafka_utils.py", "deserialize_value")),
              P("<i>allow_nan=False</i> guarantees strictly valid JSON; deserialization rejects anything that is not a JSON object.")]
    story += [P("4.6 Message transmission results", H2)]
    story += table([["Records loaded from raw_data.csv", prod.get("records_loaded")], ["Messages sent", prod.get("sent")],
                    ["Messages published (confirmed by broker)", prod.get("published")], ["Failed", prod.get("failed")],
                    ["Published per partition", json.dumps(prod.get("published_per_partition", {}))],
                    ["Messages consumed", cons.get("consumed")], ["Expected by consumer", cons.get("expected")],
                    ["Skipped (other runs in the topic)", cons.get("skipped_other_runs")], ["Undecodable messages", cons.get("failed_deserialization")],
                    ["Consumed per partition", json.dumps(cons.get("consumed_per_partition", {}))], ["Consumer stop reason", cons.get("stop_reason")],
                    ["Consumer run time (s)", cons.get("duration_s")]], ["Measure", "Value"], [8 * cm, 9 * cm],
                   "Producer and consumer statistics (data/producer_stats.json, data/consumer_stats.json)")
    story += [P("4.7 Error handling", H2)]
    story += bullets(["<b>Connection:</b> a failure to reach the broker is caught, a clear message is printed and the script exits with a non-zero code, which stops the pipeline.",
                      "<b>Transmission:</b> every send has success and error callbacks; failed messages are counted and the first errors are stored in producer_stats.json; <i>retries=5</i>, <i>acks='all'</i>.",
                      "<b>Message processing:</b> undecodable or non-object messages are counted and skipped instead of crashing the consumer; messages of other runs are skipped.",
                      "<b>Termination:</b> the consumer cannot wait forever (expected count, idle timeout, maximum run time)."])
    story += [P("4.8 Evidence of successful publishing and consumption", H2),
              P("Console output of the producer (logs/02_produce.log):"), code(log_excerpt("02_produce.log")),
              P("Console output of the consumer (logs/03_consume.log):"), code(log_excerpt("03_consume.log"))]

    # ---------------- 5 pandera ----
    story += [PageBreak(), P("5. Pandera Validation", H1), P("5.1 Validation schema", H2),
              P("The schema is defined once in <i>src/validator.py</i> and is applied to the records received from Kafka, before any cleaning. "
                "<i>lazy=True</i> makes Pandera collect all errors instead of stopping at the first one."),
              code(source_of("src/validator.py", "SCHEMA"))]
    story += [P("5.2 Validation rules", H2)]
    story += table([[n, d] for n, d in validator.RULES], ["Rule", "Description"], [4 * cm, 13 * cm], f"The {len(validator.RULES)} validation rules")
    story += [P("5.3 Validation implementation and error handling", H2),
              P("Failure cases returned by Pandera contain the row index, the column, the failed check and the offending value. They are converted into one readable message per row. "
                "<b>Handling strategy:</b> invalid rows are <b>quarantined</b> in <i>data/invalid_records.csv</i> together with their error messages and are excluded from cleaning; valid rows continue in "
                "<i>data/valid_records.csv</i>."),
              code(lines_between("src/validator.py", "row_failures[\"index\"] = row_failures", "invalid[\"validation_errors\"]"))]
    story += [P("5.4 Summary of validation results", H2)]
    story += table([["Records received from Kafka", val.get("total_records")], ["Valid records", val.get("valid_records")],
                    ["Invalid records", val.get("invalid_records")]], ["Measure", "Count"], [8 * cm, 4 * cm], "Validation result of the real data")
    if val.get("failures_by_rule"):
        story += table([[r["rule"], r["count"]] for r in val["failures_by_rule"]], ["Failed rule", "Failure cases"], [13 * cm, 4 * cm], "Failures by rule (real data)")
    story += [P("5.5 Examples of valid and invalid records", H2)]
    cols = ["record_id", "city", "time", "pm10", "pm2_5", "european_aqi"]
    if not valid_df.empty:
        story += table(valid_df[cols].head(3).values.tolist(), cols, [4.4 * cm, 2 * cm, 3.4 * cm, 2 * cm, 2 * cm, 2.6 * cm], "Examples of valid records")
    if invalid_df.empty:
        story += [P("<b>The real dataset contained no invalid records.</b> The schema was therefore demonstrated with deliberately invalid test records (section 5.6).")]
    else:
        story += table(invalid_df[cols + ["validation_errors"]].head(5).values.tolist(), cols + ["validation_errors"],
                       [3.6 * cm, 1.6 * cm, 2.6 * cm, 1.2 * cm, 1.2 * cm, 1.4 * cm, 5.4 * cm], "Examples of invalid records with their errors")
    vt = val.get("validation_test", {})
    story += [P("5.6 Demonstration with intentionally invalid test records", H2),
              P("Six test records with ids starting with <i>test</i> are appended to five real valid rows and validated with the same schema. Each one violates a different rule. "
                "They exist only in this demonstration (<i>data/validation_test_results.csv</i>); they are never written to <i>valid_records.csv</i>, and the cleaner removes any record whose id starts with 'test' as a second safeguard."),
              code(source_of("src/validator.py", "make_invalid_test_records"))]
    if not test_df.empty:
        rows = [[r["record_id"] if pd.notna(r["record_id"]) else "test6 (record_id missing)", str(r["validation_errors"])[:170]] for _, r in test_df.iterrows()]
        story += table(rows, ["Test record", "Errors reported by Pandera"], [4.6 * cm, 12.4 * cm], "Detection of the invalid test records")
    story += [P(f"Result: <b>{fmt(vt.get('test_records_detected'))} of {fmt(vt.get('test_records_injected'))}</b> invalid test records were detected; "
                f"{fmt(vt.get('real_rows_wrongly_rejected'))} real rows were wrongly rejected.")]

    # ---------------- 6 cleaning ----
    story += [PageBreak(), P("6. Data Cleaning and Transformation", H1), P("6.1 Missing-value analysis", H2)]
    mv = clean.get("missing_values", {})
    nb, na = mv.get("nulls_before", {}), mv.get("nulls_after_interpolation", {})
    story += table([[c, nb.get(c), na.get(c)] for c in nb], ["Column", "Missing before", "Missing after interpolation"], [6 * cm, 4 * cm, 6 * cm],
                   "Missing values in the validated data")
    story += [P(f"{fmt(mv.get('values_filled'))} values were filled by linear interpolation inside each city (maximum gap {mv.get('max_interpolation_gap_hours')} hours); "
                f"{fmt(mv.get('rows_dropped_without_pm'))} rows without any PM10/PM2.5 value were dropped. <i>Why:</i> {esc(clean.get('rationale', {}).get('missing_values', ''))}"),
              code(lines_between("src/cleaner.py", "for column in config.NUMERIC_COLUMNS:", "df = df.dropna"))]
    dup = clean.get("duplicates", {})
    story += [P("6.2 Duplicate detection and removal", H2),
              P(f"Exact duplicate rows: {fmt(dup.get('exact_duplicate_rows'))}; duplicate record_id: {fmt(dup.get('duplicate_record_id'))}; duplicate (city, time): "
                f"{fmt(dup.get('duplicate_city_time'))}. Rows after removal: {fmt(dup.get('rows_after_removal'))}. <i>Why:</i> {esc(clean.get('rationale', {}).get('duplicates', ''))}"),
              P("6.3 Data type conversion", H2)]
    before, after = clean.get("dtypes_before", {}), clean.get("dtypes_after", {})
    story += table([[c, before.get(c), after.get(c)] for c in after if c in before and before.get(c) != after.get(c)] or [["(no change)", "", ""]],
                   ["Column", "Type before", "Type after"], [6 * cm, 5 * cm, 6 * cm], "Columns whose type was converted")
    story += [P(f"<i>Why:</i> {esc(clean.get('rationale', {}).get('dtypes', ''))}"), P("6.4 Text normalization", H2),
              P(f"City and region names are stripped, repeated spaces collapsed and converted to title case; record ids to lower case. <i>Why:</i> {esc(clean.get('rationale', {}).get('text', ''))}"),
              P("6.5 NumPy operations", H2), code(lines_between("src/cleaner.py", "df[\"hour\"] = hour", "df[\"pm2_5_zscore\"]"))]
    npr = clean.get("numpy", {})
    story += table([["Mean PM2.5 (np.mean)", npr.get("pm2_5_mean")], ["Median PM2.5 (np.median)", npr.get("pm2_5_median")], ["Std PM2.5 (np.std)", npr.get("pm2_5_std")],
                    ["90th percentile (np.percentile)", npr.get("pm2_5_p90")], ["IQR upper fence", npr.get("iqr_upper_fence")], ["Outliers above fence", npr.get("iqr_outliers")]],
                   ["NumPy statistic", "Value"], [8 * cm, 4 * cm], "Statistics computed with NumPy")
    story += [P(f"New features: {esc(', '.join(npr.get('features_created', [])))}. <i>Why:</i> {esc(clean.get('rationale', {}).get('numpy', ''))}")]
    flt = clean.get("filtering", {})
    story += [P("6.6 Filtering with multiple conditions", H2),
              P(f"Condition: <font name='DejaVuMono'>{esc(flt.get('condition'))}</font>. Result: <b>{fmt(flt.get('rows_matching'))}</b> rows "
                f"({fmt(flt.get('share_of_dataset_percent'))} % of the dataset); per city: {esc(json.dumps(flt.get('rows_per_city', {})))}. <i>Why:</i> {esc(clean.get('rationale', {}).get('filtering', ''))}"),
              code(lines_between("src/cleaner.py", "high_events = df[", "high_events.to_csv"))]
    story += [P("6.7 GroupBy and aggregation", H2)]
    cs = clean.get("groupby", {}).get("city_summary", [])
    if cs:
        story += table([[r["city"], r["records"], r["pm2_5_mean"], r["pm2_5_median"], r["pm2_5_std"], r["pm2_5_max"], r["pm10_mean"], r["aqi_mean"], r["who_exceedance_share"]] for r in cs],
                       ["City", "Records", "PM2.5 mean", "median", "std", "max", "PM10 mean", "AQI mean", "Share > WHO"],
                       [2.3 * cm, 1.6 * cm, 2 * cm, 1.7 * cm, 1.5 * cm, 1.5 * cm, 2 * cm, 1.8 * cm, 2.3 * cm], "GroupBy city with several aggregation functions (data/aggregates/city_summary.csv)")
    story += [code(lines_between("src/cleaner.py", "city_summary = df.groupby", ").round(2).reset_index()")),
              P(f"<i>Why:</i> {esc(clean.get('rationale', {}).get('groupby', ''))}"), P("6.8 Reshaping operations", H2)]
    pivot = csv_load(config.AGG_DIR / "pivot_city_hour.csv")
    if not pivot.empty:
        picked = [c for c in ["city", "0", "6", "12", "18", "21"] if c in pivot.columns]
        story += table(pivot[picked].values.tolist(), ["City" if c == "city" else f"hour {c}" for c in picked], None,
                       "pivot_table: mean PM2.5 by city (rows) and hour (columns, selection of hours)")
    rs = clean.get("reshaping", {})
    story += [code(lines_between("src/cleaner.py", "pivot_city_hour = df.pivot_table", "var_name=\"pollutant\"")),
              P(f"pivot_table shape {rs.get('pivot_table_shape')}; unstack shape {rs.get('unstack_shape')}; melt shape {rs.get('melt_shape')} (six pollutant columns melted into long format). "
                f"<i>Why:</i> {esc(clean.get('rationale', {}).get('reshape', ''))}"),
              P(f"Records after cleaning: <b>{fmt(clean.get('records_after_cleaning'))}</b>.")]

    # ---------------- 7 sqlite ----
    story += [PageBreak(), P("7. SQLite3 Database Implementation", H1), P("7.1 Database and table schema", H2),
              code(source_of("src/loader.py", "SCHEMA_SQL").replace('SCHEMA_SQL = """', "").replace('"""', "")),
              P("7.2 Primary key and uniqueness constraints", H2)]
    story += bullets(["<i>cities.city_id</i> PRIMARY KEY, <i>cities.city_name</i> UNIQUE.", "<i>measurements.record_id</i> PRIMARY KEY (text, e.g. almaty_2026-11-10T00:00).",
                      "<i>UNIQUE (city_id, observed_at)</i> - a city can have only one value per hour even if the record id were different.",
                      "FOREIGN KEY <i>city_id</i> -> cities; CHECK constraints (hour 0-23, non-negative PM, 0/1 flags); foreign keys are enforced with <i>PRAGMA foreign_keys = ON</i>."])
    story += [P("7.3 Data insertion process", H2),
              P("<i>INSERT OR IGNORE</i> inside one transaction skips rows whose key already exists, which prevents duplicates during repeated execution. NaN values are converted to NULL."),
              code(lines_between("src/loader.py", "placeholders = ", "after = connection.execute"))]
    story += [P("7.4 SQL verification queries and results", H2)]
    for name, result in sqlres.get("queries", {}).items():
        story += [KeepTogether([P(f"<b>{esc(name)}</b>"), code(result["sql"])])]
        story += table(result["rows"][:8] or [["(no rows - expected for this check)"] + [""] * (len(result["columns"]) - 1)], result["columns"], None, f"Result of {name}")
    lo = s.get("loading", {})
    story += [P("7.5 Number of stored records and idempotency test", H2),
              P(f"Records processed (cleaned): <b>{fmt(lo.get('processed_records'))}</b>; records stored in the table: <b>{fmt(lo.get('stored_records'))}</b>; "
                f"cleaned records found in the database: <b>{fmt(lo.get('cleaned_records_found_in_db'))}</b>. SQLite integrity check: {esc(sqlres.get('integrity_check'))}; "
                f"foreign-key violations: {fmt(sqlres.get('foreign_key_violations'))}.")]
    fl, sl = idem.get("first_load", {}), idem.get("second_load", {})
    story += table([["Rows offered", fl.get("rows_offered"), sl.get("rows_offered")], ["Rows inserted", fl.get("rows_inserted"), sl.get("rows_inserted")],
                    ["Rows ignored (already present)", fl.get("rows_ignored_as_existing"), sl.get("rows_ignored_as_existing")],
                    ["Rows in table afterwards", fl.get("rows_in_table_after"), sl.get("rows_in_table_after")],
                    ["SHA-256 of all stored values", str(idem.get("checksum_after_first", ""))[:16] + "...", str(idem.get("checksum_after_second", ""))[:16] + "..."]],
                   ["Measure", "Load #1", "Load #2 (same dataset)"], [6.5 * cm, 5 * cm, 5.5 * cm], "Idempotency test: the loading stage executed twice")
    story += [P(f"Result: idempotent = <b>{idem.get('idempotent')}</b>. The second load inserted no rows and the checksum of all stored values did not change, so repeated execution neither creates duplicates "
                "nor changes the stored data. (If the database already existed from an earlier run, load #1 also inserts 0 rows for the same reason.)")]

    # ---------------- 8 EDA ----
    story += [PageBreak(), P("8. Exploratory Data Analysis", H1), P("8.1 Dimensions and data types", H2),
              P(f"The analysis dataset (read from SQLite with a JOIN of the two tables) has <b>{eda.get('shape', ['?', '?'])[0]}</b> rows and <b>{eda.get('shape', ['?', '?'])[1]}</b> columns.")]
    dt = eda.get("dtypes", {})
    story += table([[k, v] for k, v in dt.items()], ["Column", "Type"], [7 * cm, 5 * cm], "Data types of the analysis dataset")
    story += [P("8.2 Descriptive statistics (numerical variables)", H2)]
    desc = csv_load(config.DATA_DIR / "eda_describe.csv", index_col=0)
    if not desc.empty:
        d = desc.T.reset_index()
        story += table(d[["index", "count", "mean", "std", "min", "50%", "max"]].values.tolist(), ["Variable", "count", "mean", "std", "min", "median", "max"],
                       [3.6 * cm, 1.8 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm, 2.6 * cm], "describe() of the numerical variables")
    story += [P("8.3 Categorical analysis", H2)]
    cat = eda.get("categorical_counts", {})
    story += table([[k, ", ".join(f"{a}: {b}" for a, b in v.items())] for k, v in cat.items()], ["Variable", "Value counts"], [3 * cm, 14 * cm], "Counts of categorical variables")
    story += [P("8.4 Grouped analysis", H2)]
    gm = eda.get("grouped_means_by_city", [])
    if gm:
        keys = ["city"] + config.NUMERIC_COLUMNS
        story += table([[r[k] for k in keys] for r in gm], ["City", "PM10", "PM2.5", "CO", "NO2", "SO2", "O3", "EAQI"], None, "Mean of every pollutant by city (µg/m³)")
    findings = eda.get("findings", {})
    plots = [("fig1_mean_pm25_by_city.png", "fig1", "Mean hourly PM2.5 by city compared with the WHO guideline", 12.5),
             ("fig2_daily_pm25_trend.png", "fig2", "Daily mean PM2.5 of the four cities", 15.5),
             ("fig3_heatmap_city_hour.png", "fig3", "Mean PM2.5 by city and hour of the day", 15.5),
             ("fig4_pm25_boxplot.png", "fig4", "Distribution of hourly PM2.5 by city", 12.5),
             ("fig5_aqi_category_share.png", "fig5", "Share of hours per European AQI category", 13.5),
             ("fig6_correlation.png", "fig6", "Correlation matrix of pollutants and AQI", 12)]
    story += [P("8.5 Visualizations and interpretation", H2)]
    for file, key, caption, width in plots:
        story += figure(config.FIGURES_DIR / file, caption, width)
        story += [P(f"<b>Interpretation.</b> {esc(findings.get(key, ''))}")]
    story += [P("8.6 Main findings", H2)] + bullets([esc(x) for x in findings.get("conclusions", [])])

    # ---------------- 9 testing ----
    story += [PageBreak(), P("9. Pipeline Testing and Verification", H1), P("9.1 Record counts at every stage", H2)]
    story += table([[r["stage"], r["count"]] for r in ver.get("counts", [])], ["Stage", "Records"], [9 * cm, 4 * cm], "Comparison of record counts (data/verification_report.json)")
    story += [P("Explanation of the differences", H2)] + bullets([esc(x) for x in ver.get("explanations", [])])
    story += [P("9.2 Verification checks", H2)]
    story += table([[c["check"], "PASS" if c["passed"] else "FAIL", c["detail"]] for c in ver.get("checks", [])], ["Check", "Result", "Detail"], [8.5 * cm, 1.8 * cm, 6.7 * cm],
                   "Automated verification checks (src/verification.py)")
    story += [P("9.3 Evidence of data transmission, validation and repeated execution", H2),
              P("Validation test (logs/04_validate.log):"), code(log_excerpt("04_validate.log", 12)),
              P("Repeated loading and SQL checks (logs/06_load.log):"), code(log_excerpt("06_load.log", 12)),
              P("Pipeline can also be executed again without cleaning: every run has its own <i>run_id</i>, so the consumer skips messages of older runs that remain in the topic, and the database ignores rows that already exist."),
              P("9.4 Encountered errors and how they were handled", H2)]
    problems = []
    for log in sorted(config.LOGS_DIR.glob("*.log")):
        for line in log.read_text(encoding="utf-8").splitlines():
            if re.search(r"\b(ERROR|WARNING)\b", line):
                problems.append([log.name, line[:150]])
    if problems:
        story += table(problems[:20], ["Log", "Message"], [3.5 * cm, 13.5 * cm], "Warnings and errors found in the logs of this run")
    else:
        story += [P("No warnings or errors were recorded in the logs of the final run.")]
    story += table([["Broker not running", "Producer/consumer print the error and exit with code 2; main.py stops the pipeline."],
                    ["API timeout / HTTP 429 / 5xx", "Retried with exponential back-off, then the city is reported as failed."],
                    ["Missing values (null)", "JSON null in messages, interpolation or removal in the cleaner, NULL in SQLite."],
                    ["Invalid records", "Quarantined by the validator with the reason; pipeline continues with the valid rows."],
                    ["Re-running the pipeline", "run_id filter in the consumer, INSERT OR IGNORE in the loader."]],
                   ["Situation", "Handling"], [5 * cm, 12 * cm], "Error situations handled by the implementation")

    # ---------------- 10 conclusions ----
    story += [P("10. Conclusions", H1), P("10.1 Summary of implemented functionality", H2),
              P(f"The pipeline collected {fmt(coll.get('collected_records'))} hourly records, streamed them through a real Kafka broker "
                f"({fmt(prod.get('published'))} published, {fmt(cons.get('consumed'))} consumed), validated them with Pandera ({fmt(val.get('valid_records'))} valid, "
                f"{fmt(val.get('invalid_records'))} invalid), cleaned them to {fmt(clean.get('records_after_cleaning'))} records, stored {fmt(lo.get('stored_records'))} rows in SQLite3 "
                "with demonstrated idempotency and produced six figures.")]
    story += [P("10.2 Main analytical findings", H2)] + bullets([esc(x) for x in findings.get("conclusions", [])])
    story += [P("10.3 Technical challenges and solutions", H2)]
    story += bullets(["<b>Several runs share one topic.</b> Re-running the producer adds messages to the same topic. Solution: a <i>run_id</i> header and a fresh consumer group per run.",
                      "<b>NaN is not valid JSON.</b> Missing values are converted to <i>null</i> before serialization and <i>allow_nan=False</i> enforces valid JSON.",
                      "<b>Pandera reports.</b> Lazy validation returns failure cases per cell; they are grouped per row to quarantine whole records with readable reasons.",
                      "<b>Idempotent loading.</b> Primary key + UNIQUE constraint + INSERT OR IGNORE, verified by a checksum of all stored values."])
    story += [P("10.4 Limitations", H2)]
    story += bullets(["The data are model values (CAMS) on a coarse grid, not measurements of a city monitoring station, so local pollution differences are smoothed.",
                      f"Only {n_days} days were analysed; seasonal conclusions cannot be drawn.",
                      "The WHO guideline values are defined for 24-hour means; they are used here as an indicative threshold for hourly values and as a real 24-hour comparison for daily means.",
                      "Interpolation of short gaps assumes smooth change between neighbouring hours.",
                      "Kafka runs as a single node without replication, which is sufficient for the assignment but not for production."])

    # ---------------- 11 contributions ----
    story += [P("11. Individual Contributions", H1),
              P(f"This project was completed individually by <b>{esc(cfg.get('student_name', ''))}</b>.")]
    story += table([[cfg.get("student_name", ""), "API collection, Kafka producer/consumer, Pandera validation, cleaning and transformation, SQLite3 loading, EDA and visualisation, verification, report"]],
                   ["Student", "Components implemented and responsibilities"], [4 * cm, 13 * cm], "Individual contribution")

    # ---------------- 12 references ----
    story += [P("12. References", H1)]
    story += bullets(["Open-Meteo. Air Quality API documentation. https://open-meteo.com/en/docs/air-quality-api",
                      "Apache Kafka documentation. https://kafka.apache.org/documentation/",
                      "Apache Kafka Docker image (apache/kafka). https://hub.docker.com/r/apache/kafka",
                      "kafka-python documentation. https://kafka-python.readthedocs.io/",
                      "Pandera documentation. https://pandera.readthedocs.io/",
                      "pandas documentation. https://pandas.pydata.org/docs/",
                      "NumPy documentation. https://numpy.org/doc/",
                      "SQLite documentation. https://www.sqlite.org/docs.html and Python sqlite3 module. https://docs.python.org/3/library/sqlite3.html",
                      "Matplotlib documentation. https://matplotlib.org/stable/ ; Seaborn documentation. https://seaborn.pydata.org/",
                      "WHO global air quality guidelines 2021. https://www.who.int/publications/i/item/9789240034228",
                      "European Environment Agency. European Air Quality Index. https://airindex.eea.europa.eu/",
                      "Copernicus Atmosphere Monitoring Service (CAMS) data, via Open-Meteo."])

    # ---------------- appendix: screenshots ----
    shots = sorted((config.REPORT_DIR / "screenshots").glob("*.png")) if (config.REPORT_DIR / "screenshots").exists() else []
    if shots:
        story += [PageBreak(), P("Appendix A. Screenshots", H1)]
        for shot in shots:
            story += figure(shot, shot.stem.replace("_", " "), 15.5)

    ReportDoc.title_info = {
        "course_short": cfg.get("course_short", "Data collection and preparation"),
        "sis_number": cfg.get("sis_number", 2),
        "project_title": cfg.get("project_title", "Data Streaming and Validation Pipeline with Apache Kafka and Pandera: "
                                                  "Air Quality in Almaty, Astana, Shymkent and Karaganda"),
        "student_name": cfg.get("student_name", ""), "student_id": cfg.get("student_id", ""),
        "group_number": cfg.get("group_number", ""), "date": submitted,
    }
    doc = ReportDoc(str(OUTPUT_PDF), title="SIS2 - Air-quality streaming and validation pipeline", author=cfg.get("student_name", ""))
    doc.multiBuild(story)
    print(f"[report] written {OUTPUT_PDF.relative_to(PROJECT_ROOT)} ({OUTPUT_PDF.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(build())
