from pathlib import Path
import os
import re
import tempfile
from datetime import date, datetime, timedelta

from fastapi import FastAPI, Request, Form, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from starlette.background import BackgroundTask
from dotenv import load_dotenv

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

load_dotenv()

from database import SessionLocal, User, OlympiadResult, CourseVisit, Order, init_db
from auth import (
    hash_password, verify_password, create_token, decode_token,
    COOKIE_NAME, COOKIE_MAX_AGE,
)
from certificate import generate_certificate, get_category

import pymupdf


BASE_DIR = Path(__file__).parent
OLYMPIADS_DIR = BASE_DIR / "olympiads"
COURSES_DIR = BASE_DIR / "courses"

FONT_PATH = BASE_DIR / "DejaVuSans.ttf"
if FONT_PATH.exists():
    pdfmetrics.registerFont(TTFont("DejaVu", str(FONT_PATH)))

app = FastAPI(title="Интеллектуальный жирафик")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

init_db()

SUBJECT_NAMES = {
    "math": "Математика",
    "russian": "Русский язык",
    "english": "Английский язык",
    "physics": "Физика",
    "informatika": "Информатика",
}

# ===================== Ближайшее событие =====================
EVENT_START = date(2026, 11, 12)
EVENT_APPLICATIONS_UNTIL = date(2026, 11, 26)
EVENT_END = date(2026, 12, 8)
EVENT_RESULTS = date(2026, 12, 10)

MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def format_date_ru(d: date) -> str:
    return f"{d.day} {MONTHS_RU[d.month - 1]} {d.year}"


def get_event_info():
    today = date.today()
    stages = [
        {"label": "Начало", "date": EVENT_START},
        {"label": "Приём заявок", "date": EVENT_APPLICATIONS_UNTIL},
        {"label": "Завершение", "date": EVENT_END},
        {"label": "Итоги", "date": EVENT_RESULTS},
    ]
    active_idx = 0
    for i, s in enumerate(stages):
        if today >= s["date"]:
            active_idx = i
    if today > EVENT_RESULTS:
        active_idx = len(stages)

    items = [
        {
            "label": s["label"],
            "date_short": s["date"].strftime("%d.%m.%Y"),
            "date_long": format_date_ru(s["date"]),
            "is_active": i == active_idx,
            "is_passed": i < active_idx,
        }
        for i, s in enumerate(stages)
    ]
    return {"items": items, "all_done": today > EVENT_RESULTS}


# ---------- Зависимости ----------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    uid = decode_token(token)
    if not uid:
        return None
    return db.query(User).filter(User.id == uid).first()


def render(request, template_name, ctx=None, status_code=200):
    context = {"request": request}
    if ctx:
        context.update(ctx)
    return templates.TemplateResponse(
        request=request, name=template_name, context=context, status_code=status_code
    )


def safe_next(url: str) -> str:
    if url and url.startswith("/") and not url.startswith("//"):
        return url
    return "/"


def parse_olympiad_filename(filename: str):
    stem = Path(filename).stem
    m = re.match(r'^([a-z]+)_(\d+)$', stem)
    if m:
        subject_slug = m.group(1)
        grade = int(m.group(2))
        subject = SUBJECT_NAMES.get(subject_slug, subject_slug.title())
        return {
            "filename": filename,
            "subject_slug": subject_slug,
            "subject": subject,
            "grade": grade,
            "title": f"{subject} — {grade} класс",
        }
    return {
        "filename": filename,
        "subject_slug": None,
        "subject": None,
        "grade": None,
        "title": stem.replace("_", " ").strip().title(),
    }


def list_olympiads(user: User | None):
    result = []
    for f in sorted(OLYMPIADS_DIR.iterdir()):
        if not (f.is_file() and f.suffix.lower() == ".html"):
            continue
        info = parse_olympiad_filename(f.name)
        info["available"] = True
        info["reason"] = None
        if user and info["grade"] is not None and info["grade"] < user.grade:
            info["available"] = False
            info["reason"] = f"Только для {info['grade']} класса и младше"
        result.append(info)
    return result


def list_html_files(directory: Path):
    result = []
    for f in sorted(directory.iterdir()):
        if f.is_file() and f.suffix.lower() == ".html":
            title = f.stem.replace("_", " ").replace("-", " ").strip().title()
            result.append({"filename": f.name, "title": title})
    return result


def serve_file(base_dir: Path, file_path: str):
    base = base_dir.resolve()
    target = (base / file_path).resolve()
    if base != target and not str(target).startswith(str(base) + os.sep):
        raise HTTPException(status_code=404)
    if not target.is_file():
        raise HTTPException(status_code=404)
    media = "text/html" if target.suffix.lower() == ".html" else None
    return FileResponse(target, media_type=media)


def touch_streak(user: User, db: Session):
    today = date.today()
    last = user.last_activity_date

    if last is None:
        user.streak_days = 1
    elif last == today:
        pass
    elif last == today - timedelta(days=1):
        user.streak_days = (user.streak_days or 0) + 1
    else:
        user.streak_days = 1

    user.last_activity_date = today
    user.last_login = datetime.utcnow()


# ---------- Главная ----------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return render(request, "index.html", {
        "current_user": user,
        "page_title": "Главная",
        "event": get_event_info(),
    })


# ---------- Регистрация ----------

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/", status_code=303)
    return render(request, "register.html", {"current_user": None, "next": next})


@app.post("/register", response_class=HTMLResponse)
def register_post(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    email: str = Form(...),
    birth_date: str = Form(...),
    grade: int = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    errors = []

    full_name = (full_name or "").strip()
    if not full_name:
        errors.append("Укажите ФИО.")
    elif len(full_name) < 2:
        errors.append("ФИО — минимум 2 символа.")
    elif len(full_name) > 100:
        errors.append("ФИО — не больше 100 символов.")

    username = (username or "").strip()
    if not username:
        errors.append("Укажите ник.")
    elif len(username) < 3:
        errors.append("Ник — минимум 3 символа.")
    elif len(username) > 30:
        errors.append("Ник — не больше 30 символов.")
    elif not re.fullmatch(r"[A-Za-z0-9_]+", username):
        errors.append("Ник может содержать только латиницу, цифры и подчёркивание.")

    email = (email or "").strip().lower()
    if not email:
        errors.append("Укажите e-mail.")
    elif "@" not in email or "." not in email.split("@")[-1]:
        errors.append("Введите корректный e-mail.")

    bdate = None
    raw_date = (birth_date or "").strip()
    if not raw_date:
        errors.append("Укажите дату рождения.")
    else:
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                bdate = datetime.strptime(raw_date, fmt).date()
                break
            except ValueError:
                continue
        if bdate is None:
            errors.append("Не удалось распознать дату рождения.")
        elif bdate > date.today():
            errors.append("Дата рождения не может быть в будущем.")
        elif bdate.year < 1900:
            errors.append("Проверьте дату рождения.")

    if not (1 <= grade <= 11):
        errors.append("Класс должен быть от 1 до 11.")

    if not password:
        errors.append("Укажите пароль.")
    elif len(password) < 6:
        errors.append("Пароль — минимум 6 символов.")
    if password != password_confirm:
        errors.append("Пароли не совпадают.")

    if not errors:
        if db.query(User).filter(User.username == username).first():
            errors.append("Этот ник уже занят. Выберите другой.")
        if db.query(User).filter(User.email == email).first():
            errors.append("Пользователь с таким e-mail уже существует.")

    if errors:
        return render(request, "register.html", {
            "current_user": None, "errors": errors,
            "full_name": full_name, "username": username, "email": email,
            "birth_date": birth_date, "grade": grade, "next": next,
        }, status_code=400)

    user = User(
        full_name=full_name,
        username=username,
        email=email,
        password_hash=hash_password(password),
        grade=grade,
        birth_date=bdate,
        created_at=datetime.utcnow(),
        last_login=datetime.utcnow(),
        last_activity_date=date.today(),
        streak_days=1,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_token(user.id)
    response = RedirectResponse(safe_next(next), status_code=303)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=True,          # Vercel работает по HTTPS, это обязательно
        samesite="none",      # Разрешает кросс-сайтовые запросы (если фронт и бэк на разных поддоменах)
        # domain=...          # УДАЛИТЕ или закомментируйте эту строку, если она есть
    )
    return response


# ---------- Вход ----------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", {"current_user": None, "next": next})


@app.post("/login", response_class=HTMLResponse)
def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    email = (email or "").strip().lower()
    user = db.query(User).filter(User.email == email).first()
    print("DEBUG: LOGIN ENDPOINT CALLED")

    if not user or not verify_password(password, user.password_hash):
        return render(request, "login.html", {
            "current_user": None,
            "error": "Неверный e-mail или пароль.",
            "email": email, "next": next,
        }, status_code=400)

    touch_streak(user, db)
    db.commit()

    token = create_token(user.id)
    response = RedirectResponse(safe_next(next), status_code=303)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=True,          # Vercel работает по HTTPS, это обязательно
        samesite="none",      # Разрешает кросс-сайтовые запросы (если фронт и бэк на разных поддоменах)
        # domain=...          # УДАЛИТЕ или закомментируйте эту строку, если она есть
    )
    return response


# ---------- Выход ----------

@app.post("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# ---------- Профиль ----------

def compute_stats(user: User, db: Session):
    results = db.query(OlympiadResult).filter(OlympiadResult.user_id == user.id).all()
    visits = db.query(CourseVisit).filter(CourseVisit.user_id == user.id).all()

    unique_files = {r.filename for r in results}
    olympiads_done = len(unique_files)
    tasks_solved = sum(r.correct for r in results)
    courses_started = len({v.filename for v in visits})

    today = date.today()
    streak = user.streak_days or 0
    if user.last_activity_date and user.last_activity_date < today - timedelta(days=1):
        streak = 0

    return {
        "olympiads_done": olympiads_done,
        "tasks_solved": tasks_solved,
        "streak_days": streak,
        "courses_started": courses_started,
        "results_count": len(results),
    }


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, saved: str = None, error: str = None,
                 db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login?next=/profile", status_code=303)

    birth_str = user.birth_date.strftime("%d.%m.%Y") if user.birth_date else "—"
    days_with_us = (datetime.utcnow() - user.created_at).days if user.created_at else 0

    all_olympiads = list_olympiads(user)
    available_count = sum(1 for o in all_olympiads if o["available"])

    stats = compute_stats(user, db)

    best_by_file = {}
    for r in (db.query(OlympiadResult)
              .filter(OlympiadResult.user_id == user.id)
              .order_by(OlympiadResult.completed_at.desc())
              .all()):
        if r.filename not in best_by_file:
            best_by_file[r.filename] = r
        else:
            old = best_by_file[r.filename]
            old_pct = (old.correct / old.total) if old.total else 0
            new_pct = (r.correct / r.total) if r.total else 0
            if new_pct > old_pct:
                best_by_file[r.filename] = r

    passed_olympiads = []
    for filename, r in best_by_file.items():
        pct = round((r.correct / r.total) * 100) if r.total else 0
        passed_olympiads.append({
            "filename": filename,
            "subject": r.subject or filename,
            "grade": r.grade,
            "correct": r.correct,
            "total": r.total,
            "percent": pct,
            "completed_at": r.completed_at,
            "passed": pct >= 50,
        })
    passed_olympiads.sort(key=lambda x: x["completed_at"], reverse=True)

    certificates = [
        {
            "filename": p["filename"],
            "subject": p["subject"],
            "grade": p["grade"],
            "percent": p["percent"],
            "date": p["completed_at"],
        }
        for p in passed_olympiads if p["passed"]
    ]

    visits = (db.query(CourseVisit)
              .filter(CourseVisit.user_id == user.id)
              .order_by(CourseVisit.last_visited_at.desc())
              .all())
    courses_passed = [
        {
            "filename": v.filename,
            "title": v.title or v.filename,
            "first_visited_at": v.first_visited_at,
            "last_visited_at": v.last_visited_at,
            "visits": v.visits,
        }
        for v in visits
    ]

    achievements = []
    if stats["olympiads_done"] >= 1:
        achievements.append({"icon": "🎉", "title": "Первый шаг", "desc": "Пройдена первая олимпиада"})
    if stats["olympiads_done"] >= 5:
        achievements.append({"icon": "🥉", "title": "Первые шаги", "desc": "Пройдено 5 олимпиад"})
    if stats["olympiads_done"] >= 10:
        achievements.append({"icon": "🥈", "title": "Уверенный старт", "desc": "Пройдено 10 олимпиад"})
    if stats["olympiads_done"] >= 15:
        achievements.append({"icon": "🥇", "title": "Опытный боец", "desc": "Пройдено 15 олимпиад"})
    if stats["tasks_solved"] >= 50:
        achievements.append({"icon": "💯", "title": "Полтинник", "desc": "Решено 50 задач"})
    if stats["tasks_solved"] >= 100:
        achievements.append({"icon": "🏅", "title": "Сотка", "desc": "Решено 100 задач"})
    if stats["streak_days"] >= 3:
        achievements.append({"icon": "🔥", "title": "Три дня подряд", "desc": "3 дня активности подряд"})
    if stats["streak_days"] >= 7:
        achievements.append({"icon": "⚡", "title": "Неделя подряд", "desc": "7 дней подряд"})
    if stats["courses_started"] >= 1:
        achievements.append({"icon": "📘", "title": "Первый курс", "desc": "Начат первый курс"})
    if len(certificates) >= 1:
        achievements.append({"icon": "🎖️", "title": "Первый сертификат", "desc": "Получен первый сертификат"})
    if len(certificates) >= 5:
        achievements.append({"icon": "🏛️", "title": "Коллекционер", "desc": "5 сертификатов"})

    success_message = "Изменения сохранены!" if saved else None

    recent = (db.query(OlympiadResult)
              .filter(OlympiadResult.user_id == user.id)
              .order_by(OlympiadResult.completed_at.desc())
              .limit(5).all())

    return render(request, "profile.html", {
        "current_user": user,
        "page_title": "Профиль",
        "birth_str": birth_str,
        "days_with_us": days_with_us,
        "available_count": available_count,
        "total_olympiads": len(all_olympiads),
        "stats": stats,
        "recent_results": recent,
        "passed_olympiads": passed_olympiads,
        "courses_passed": courses_passed,
        "certificates": certificates,
        "achievements": achievements,
        "success_message": success_message,
        "error_message": error,
    })


@app.post("/profile/update")
def profile_update(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    email: str = Form(...),
    grade: int = Form(...),
    birth_date: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login?next=/profile", status_code=303)

    errors = []
    full_name = (full_name or "").strip()
    username = (username or "").strip()
    email = (email or "").strip().lower()

    if not full_name:
        errors.append("ФИО не может быть пустым.")
    if not username or not re.fullmatch(r"[A-Za-z0-9_]{3,30}", username):
        errors.append("Ник: 3–30 символов, только латиница, цифры, _.")
    if "@" not in email or "." not in email.split("@")[-1]:
        errors.append("Введите корректный e-mail.")
    if not (1 <= grade <= 11):
        errors.append("Класс 1–11.")

    bdate = None
    raw = (birth_date or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            bdate = datetime.strptime(raw, fmt).date()
            break
        except ValueError:
            continue
    if bdate is None:
        errors.append("Некорректная дата рождения.")

    if not errors:
        if username != user.username and db.query(User).filter(User.username == username).first():
            errors.append("Этот ник уже занят.")
        if email != user.email and db.query(User).filter(User.email == email).first():
            errors.append("Этот e-mail уже используется.")

    if errors:
        return RedirectResponse("/profile?error=" + "%20".join(errors[:1]), status_code=303)

    user.full_name = full_name
    user.username = username
    user.email = email
    user.grade = grade
    user.birth_date = bdate
    db.commit()

    return RedirectResponse("/profile?saved=1", status_code=303)


@app.post("/profile/password")
def profile_password(
    request: Request,
    old_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login?next=/profile", status_code=303)

    if not verify_password(old_password, user.password_hash):
        return RedirectResponse("/profile?error=Текущий%20пароль%20неверен", status_code=303)
    if len(new_password) < 6:
        return RedirectResponse("/profile?error=Новый%20пароль%20—%20минимум%206%20символов", status_code=303)
    if new_password != new_password_confirm:
        return RedirectResponse("/profile?error=Пароли%20не%20совпадают", status_code=303)

    user.password_hash = hash_password(new_password)
    db.commit()
    return RedirectResponse("/profile?saved=1", status_code=303)


# ---------- Олимпиады ----------

@app.get("/olympiads", response_class=HTMLResponse)
def olympiads_page(request: Request, error: str = None,
                   db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    items = list_olympiads(user)

    # Какие олимпиады пользователь уже проходил
    passed_files = set()
    if user:
        rows = (db.query(OlympiadResult.filename)
                .filter(OlympiadResult.user_id == user.id)
                .all())
        passed_files = {r[0] for r in rows}

    for it in items:
        it["passed"] = it["filename"] in passed_files

    error_message = None
    if error == "grade":
        error_message = (
            "Эта олимпиада предназначена для более младших классов. "
            "Вы можете участвовать только в олимпиадах своего класса и старше."
        )
    elif error == "login":
        error_message = "Чтобы открыть олимпиаду, войдите в аккаунт."
    elif error == "already":
        error_message = (
            "Вы уже проходили эту олимпиаду. Повторное прохождение недоступно. "
            "Результат сохранён в профиле."
        )

    return render(request, "list_items.html", {
        "current_user": user,
        "items": items,
        "section": "olympiads",
        "section_title": "Олимпиады",
        "section_description": "Выберите олимпиаду для прохождения. Доступны олимпиады для вашего класса и старше.",
        "page_title": "Олимпиады",
        "error_message": error_message,
        "event": get_event_info(),
        "passed_files": passed_files,
    })


@app.get("/olympiads/view/{file_path:path}")
def olympiad_view(file_path: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(f"/login?next=/olympiads/view/{file_path}", status_code=303)

    info = parse_olympiad_filename(Path(file_path).name)
    if info["grade"] is not None and info["grade"] < user.grade:
        return RedirectResponse("/olympiads?error=grade", status_code=303)

    # Проверка: уже проходил?
    filename = Path(file_path).name
    already = (db.query(OlympiadResult)
               .filter(OlympiadResult.user_id == user.id,
                       OlympiadResult.filename == filename)
               .first())
    if already:
        return RedirectResponse(
            f"/olympiads?error=already&file={filename}",
            status_code=303
        )

    return serve_file(OLYMPIADS_DIR, file_path)


# ---------- Приём результата олимпиады ----------

@app.post("/olympiads/submit")
def olympiad_submit(
    request: Request,
    filename: str = Form(...),
    correct: int = Form(...),
    total: int = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")

    if not (OLYMPIADS_DIR / filename).is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")

    info = parse_olympiad_filename(filename)
    touch_streak(user, db)

    result = OlympiadResult(
        user_id=user.id,
        filename=filename,
        subject=info["subject"],
        grade=info["grade"],
        correct=correct,
        total=total,
        completed_at=datetime.utcnow(),
    )
    db.add(result)
    db.commit()

    return {"status": "ok", "saved": True}


# ---------- Покупка награды ----------

@app.get("/olympiads/buy", response_class=HTMLResponse)
def olympiad_buy(
    request: Request,
    filename: str,
    correct: int,
    total: int,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(
            f"/login?next=/olympiads/buy?filename={filename}&correct={correct}&total={total}",
            status_code=303,
        )

    if not (OLYMPIADS_DIR / filename).is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")

    cat = get_category(correct, total)
    info = parse_olympiad_filename(filename)

    # ФИО из профиля
    participant_name = user.full_name or user.username

    # Класс из названия олимпиады
    olympiad_name = info["title"] if info["subject"] else filename.replace(".html", "")
    m = re.search(r"(\d+)\s*класс", str(olympiad_name or ""))
    grade = f"{m.group(1)} класс" if m else ""

    # Степень без слова «Диплом»
    degree_text = cat["title"].replace("Диплом ", "").replace("Сертификат участника", "участника")

    return render(request, "buy.html", {
        "current_user": user,
        "page_title": "Покупка награды",
        "filename": filename,
        "olympiad_title": olympiad_name,
        "correct": correct,
        "total": total,
        "percent": round(correct / total * 100) if total else 0,
        "reward_title": cat["title"],
        "reward_kind": cat["key"],
        "price": cat["price"],
        "participant_name": participant_name,
        "grade": grade,
        "degree_text": degree_text,
    })


@app.post("/olympiads/pay")
def olympiad_pay(
    request: Request,
    filename: str = Form(...),
    correct: int = Form(...),
    total: int = Form(...),
    db: Session = Depends(get_db),
):
    """Пока без реальной оплаты: сохраняет заказ и сразу отдаёт PDF.
    Когда подключите ЮKassa — уберите генерацию PDF, ставьте status='pending'
    и редиректьте на payment_url. PDF генерируйте в вебхуке."""
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    # ФИО берём из профиля (не из формы)
    participant_name = user.full_name or user.username

    cat = get_category(correct, total)
    info = parse_olympiad_filename(filename)

    # 1. Сохраняем результат
    result = OlympiadResult(
        user_id=user.id,
        filename=filename,
        subject=info["subject"],
        grade=info["grade"],
        correct=correct,
        total=total,
        completed_at=datetime.utcnow(),
    )
    db.add(result)
    db.commit()
    db.refresh(result)

    # 2. Сохраняем заказ
    order = Order(
        user_id=user.id,
        result_id=result.id,
        award_key=cat["key"],
        price=cat["price"],
        participant_name=participant_name,
        status="paid",  # TODO: для реальной оплаты — "pending"
        paid_at=datetime.utcnow(),
    )
    db.add(order)
    db.commit()

    touch_streak(user, db)
    db.commit()

    # 3. Генерируем PDF
    olympiad_name = info["title"] if info["subject"] else filename.replace(".html", "")
    m = re.search(r"(\d+)\s*класс", str(olympiad_name or ""))
    grade = f"{m.group(1)} класс" if m else ""

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()

    cat = generate_certificate(
        username=participant_name,
        olympiad_name=olympiad_name,
        grade=grade,
        correct=correct,
        total=total,
        output_path=Path(tmp.name),
    )

    # 4. Отдаём файл
    return FileResponse(
        tmp.name,
        media_type="application/pdf",
        filename=f"{cat['key']}_{user.username}.pdf",
        background=BackgroundTask(lambda: os.unlink(tmp.name)),
    )


@app.get("/olympiads/preview.png")
def olympiad_preview_png(
    request: Request,
    filename: str,
    correct: int,
    total: int,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")

    if not (OLYMPIADS_DIR / filename).is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")

    info = parse_olympiad_filename(filename)
    olympiad_name = info["title"] if info["subject"] else filename.replace(".html", "")
    m = re.search(r"(\d+)\s*класс", str(olympiad_name or ""))
    grade = f"{m.group(1)} класс" if m else ""

    participant_name = user.full_name or user.username

    # Генерируем PDF во временный файл
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()

    generate_certificate(
        username=participant_name,
        olympiad_name=olympiad_name,
        grade=grade,
        correct=correct,
        total=total,
        output_path=Path(tmp.name),
    )

    # PDF -> PNG через PyMuPDF (без Poppler)
    doc = pymupdf.open(tmp.name)
    page = doc[0]
    pix = page.get_pixmap(dpi=120)   # 120 DPI достаточно для превью
    png_bytes = pix.tobytes("png")
    doc.close()
    os.unlink(tmp.name)

    return Response(content=png_bytes, media_type="image/png")


@app.get("/courses", response_class=HTMLResponse)
def courses_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return render(request, "list_items.html", {
        "current_user": user,
        "items": [{"filename": f["filename"], "title": f["title"],
                   "grade": None, "subject": None,
                   "available": True, "reason": None, "passed": False}
                  for f in list_html_files(COURSES_DIR)],
        "section": "courses",
        "section_title": "Курсы",
        "section_description": "Выберите курс для прохождения.",
        "page_title": "Курсы",
        "error_message": None,
        "event": None,
    })


@app.get("/courses/view/{file_path:path}")
def course_view(file_path: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(f"/login?next=/courses/view/{file_path}", status_code=303)

    filename = Path(file_path).name
    visit = db.query(CourseVisit).filter(
        CourseVisit.user_id == user.id,
        CourseVisit.filename == filename,
    ).first()
    title = Path(filename).stem.replace("_", " ").replace("-", " ").strip().title()

    if visit:
        visit.last_visited_at = datetime.utcnow()
        visit.visits = (visit.visits or 1) + 1
    else:
        visit = CourseVisit(
            user_id=user.id,
            filename=filename,
            title=title,
        )
        db.add(visit)

    touch_streak(user, db)
    db.commit()

    return serve_file(COURSES_DIR, file_path)


@app.get("/certificate/{filename}", response_class=HTMLResponse)
def certificate_page(filename: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(f"/login?next=/certificate/{filename}", status_code=303)

    result = (db.query(OlympiadResult)
              .filter(OlympiadResult.user_id == user.id,
                      OlympiadResult.filename == filename)
              .order_by(OlympiadResult.correct.desc())
              .first())

    if not result:
        raise HTTPException(status_code=404, detail="Сертификат не найден")

    info = parse_olympiad_filename(filename)
    pct = round((result.correct / result.total) * 100) if result.total else 0
    if pct < 50:
        raise HTTPException(status_code=403, detail="Олимпиада не пройдена (нужно 50%+)")

    if pct >= 90:
        level, level_label = "gold", "Победитель"
    elif pct >= 70:
        level, level_label = "silver", "Призёр"
    else:
        level, level_label = "bronze", "Участник"

    return render(request, "certificate.html", {
        "current_user": user,
        "page_title": "Сертификат",
        "filename": filename,
        "subject": info["subject"] or filename,
        "grade": info["grade"],
        "correct": result.correct,
        "total": result.total,
        "percent": pct,
        "level": level,
        "level_label": level_label,
        "date": result.completed_at.strftime("%d.%m.%Y"),
    })


# ---------- Информационные страницы ----------

def render_info(request, db, title, description, key):
    user = get_current_user(request, db)
    return render(request, "static_page.html", {
        "current_user": user,
        "page_title": title,
        "page_heading": title,
        "page_description": description,
        "page_key": key,
    })


@app.get("/publications", response_class=HTMLResponse)
def publications(request: Request, db: Session = Depends(get_db)):
    return render_info(request, db, "Публикации",
                       "Научные и методические публикации.", "publications")


@app.get("/certificates", response_class=HTMLResponse)
def certificates(request: Request, db: Session = Depends(get_db)):
    return render_info(request, db, "Сертификаты",
                       "Сертификаты участника.", "certificates")


@app.get("/preparation", response_class=HTMLResponse)
def preparation(request: Request, db: Session = Depends(get_db)):
    return render_info(request, db, "Подготовка к олимпиадам",
                       "Материалы для подготовки.", "preparation")


@app.get("/regulations", response_class=HTMLResponse)
def regulations(request: Request, db: Session = Depends(get_db)):
    return render_info(request, db, "Положения мероприятий",
                       "Официальные положения.", "regulations")


@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return render(request, "about.html", {
        "current_user": user,
        "page_title": "О нас",
    })

# https://chat.deepseek.com/share/22jvdiogf2xiy2v8jf
