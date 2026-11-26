from pathlib import Path
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BASE_DIR = Path(__file__).parent
IMG_DIR = BASE_DIR / "static" / "img"

FONT_PATH = BASE_DIR / "static" / "fonts" / "DejaVuSans.ttf"
FONT_BOLD_PATH = BASE_DIR / "static" / "fonts" / "DejaVuSans.ttf"

pdfmetrics.registerFont(TTFont("DejaVu", str(FONT_PATH)))
FONT_BOLD = "DejaVu"
if FONT_BOLD_PATH.exists():
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONT_BOLD_PATH)))
    FONT_BOLD = "DejaVu-Bold"


def get_category(correct: int, total: int) -> dict:
    pct = (correct / total * 100) if total else 0
    if correct == total and total > 0:
        return {"key": "diploma_1", "title": "Диплом I степени", "price": 150, "template": "diploma_template.png"}
    if pct >= 90:
        return {"key": "diploma_2", "title": "Диплом II степени", "price": 150, "template": "diploma_template.png"}
    if pct >= 80:
        return {"key": "diploma_3", "title": "Диплом III степени", "price": 150, "template": "diploma_template.png"}
    return {"key": "certificate", "title": "Сертификат участника", "price": 100, "template": "cert_template.png"}


def split_name(full_name: str):
    """'Карпов Тимофей Александрович' -> ('Карпов', 'Тимофей')"""
    parts = (full_name or "").strip().split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    return (parts[0] if parts else ""), ""


def generate_certificate(
        username: str,
        olympiad_name: str,
        correct: int,
        total: int,
        output_path: Path,
        grade: str = "",
) -> dict:
    cat = get_category(correct, total)
    template_path = IMG_DIR / cat["template"]

    width, height = A4   # 595 x 842
    c = canvas.Canvas(str(output_path), pagesize=(width, height))

    # 1. Фон
    if template_path.exists():
        c.drawImage(str(template_path), 0, 0, width=width, height=height)

    dark = (0.09, 0.14, 0.25)

    # ====== ВЕРТИКАЛЬНЫЕ ПОЗИЦИИ ======
    Y_DEGREE   = height - 320
    Y_OLYMP    = height - 375
    Y_OLYMP2   = height - 410
    Y_NAME     = height - 510
    Y_GRADE    = height - 580

    center_x = width / 2

    # 2. Степень
    degree_text = cat["title"].replace("Диплом ", "").replace("Сертификат участника", "участника")
    c.setFillColorRGB(*dark)
    c.setFont(FONT_BOLD, 26)
    c.drawCentredString(center_x, Y_DEGREE, degree_text)

    # 3. «Всероссийской олимпиады школьников»
    c.setFont("DejaVu", 16)
    c.setFillColorRGB(0.15, 0.15, 0.15)
    c.drawCentredString(center_x, Y_OLYMP, "Всероссийской олимпиады школьников")

    # 4. Название олимпиады
    c.setFont(FONT_BOLD, 20)
    c.setFillColorRGB(*dark)
    c.drawCentredString(center_x, Y_OLYMP2, "«Интеллектуальный жирафик»")

    # 5. Фамилия Имя
    surname, name = split_name(username)
    display_name = f"{surname} {name}".strip()

    MAX_W = width - 140
    size = 30
    while size > 16 and pdfmetrics.stringWidth(display_name, FONT_BOLD, size) > MAX_W:
        size -= 1

    c.setFont(FONT_BOLD, size)
    c.setFillColorRGB(*dark)
    c.drawCentredString(center_x, Y_NAME, display_name)

    # 6. Класс
    if grade:
        c.setFont(FONT_BOLD, 20)
        c.setFillColorRGB(*dark)
        c.drawCentredString(center_x, Y_GRADE, grade)

    c.save()
    return cat