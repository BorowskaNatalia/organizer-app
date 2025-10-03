from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from urllib.parse import urlparse

import streamlit as st


# ==================== MODELE ====================
@dataclass
class Subtask:
    title: str
    done: bool = False


@dataclass
class Task:
    title: str
    minutes: int
    priority: int = 2  # 1=wysoki, 2=średni, 3=niski
    energy: str = "medium"  # low/medium/high
    deadline: datetime | None = None
    tags: list[str] = field(default_factory=list)
    project: str | None = None
    depends_on: list[str] = field(default_factory=list)  # nazwy zadań
    subtasks: list[Subtask] = field(default_factory=list)
    url: str | None = None


@dataclass
class Block:
    start: datetime
    end: datetime
    kind: str = "work"  # work/break/fixed/habit
    task_title: str | None = None  # nazwa zadania lub habitu


@dataclass
class FixedBlock:
    start: datetime
    end: datetime
    title: str


@dataclass
class Habit:
    name: str
    with_time_block: bool = False
    minutes: int = 5  # jeśli with_time_block=True
    goal_is_binary: bool = True  # np. "dzień bez szluga"


# ==================== POMOCNICZE ====================
ENERGY_ORDER = {"low": 0, "medium": 1, "high": 2}


def safe_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        return url if parsed.scheme in {"http", "https"} else None
    except Exception:
        return None


# scoring: balans deadline/energia + premiowanie P1 i krótkich zadań


def score(task: Task, slot_index: int, slot_energy: str) -> float:
    prio = {1: 3.0, 2: 2.0, 3: 1.0}[task.priority]

    # deadline urgency (im bliżej, tym większy wpływ)
    if task.deadline:
        days = (task.deadline.date() - datetime.now().date()).days
        urg = max(0.0, 4.0 - (days / 2))
    else:
        urg = 0.8

    # energy fit — preferuj dopasowanie do energii slotu
    fit = 1.2 if ENERGY_ORDER.get(task.energy, 1) <= ENERGY_ORDER.get(slot_energy, 1) else 0.6

    # krótkie zadania (<= 60 min) delikatny bonus - chcesz kończyć rzeczy
    short_bonus = 1.2 if task.minutes <= 60 else 1.0

    # rano (pierwsze 3 sloty pracy) preferuj P1
    morning_boost = 1.15 if (slot_index <= 2 and task.priority == 1) else 1.0

    return prio * 1.6 + urg * 1.1 + fit * 1.0 + short_bonus * 0.5 + morning_boost * 0.4


# ==================== PLANOWANIE ====================


def collides(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return not (a_end <= b_start or a_start >= b_end)


def generate_plan(
    tasks: list[Task],
    habits: list[Habit],
    day_start: time,
    day_end: time,
    block_minutes: int,
    break_minutes: int,
    energy_profile: list[str],
    fixed_blocks: list[FixedBlock],
) -> list[Block]:
    today = datetime.now().date()
    cur = datetime.combine(today, day_start)
    end_dt = datetime.combine(today, day_end)

    slots: list[Block] = []
    fixed_sorted = sorted(fixed_blocks, key=lambda b: b.start)

    while cur < end_dt:
        next_end = cur + timedelta(minutes=block_minutes)
        # jeśli kolizja z blokiem stałym — wstaw i przesuń czas
        collided = None
        for fb in fixed_sorted:
            if collides(cur, next_end, fb.start, fb.end):
                collided = fb
                break
        if collided:
            slots.append(
                Block(
                    start=collided.start, end=collided.end, kind="fixed", task_title=collided.title
                )
            )
            cur = collided.end
            continue

        # blok pracy
        slots.append(Block(start=cur, end=next_end, kind="work"))
        cur = next_end
        # przerwa
        if break_minutes > 0 and cur < end_dt:
            slots.append(Block(start=cur, end=cur + timedelta(minutes=break_minutes), kind="break"))
            cur += timedelta(minutes=break_minutes)

    # wstaw habity czasowe na pierwsze wolne sloty pracy
    time_habits = [h for h in habits if h.with_time_block and h.minutes > 0]
    for h in time_habits:
        for i, s in enumerate(slots):
            if (
                s.kind == "work"
                and (s.end - s.start) >= timedelta(minutes=h.minutes)
                and s.task_title is None
            ):
                habit_block = Block(
                    start=s.start,
                    end=s.start + timedelta(minutes=h.minutes),
                    kind="habit",
                    task_title=h.name,
                )
                s.start = habit_block.end  # skróć slot pracy
                slots.insert(i, habit_block)
                break

    # przypisz zadania do slotów pracy
    remaining = tasks.copy()
    for idx, slot in enumerate(slots):
        if slot.kind != "work" or not remaining:
            continue
        slot_energy = (
            energy_profile[min(idx, len(energy_profile) - 1)] if energy_profile else "medium"
        )
        # pominąć zadania z niespełnionymi zależnościami
        candidates = [
            t
            for t in remaining
            if all(dep not in [x.title for x in remaining] for dep in t.depends_on)
        ]
        if not candidates:
            candidates = remaining
        best = max(candidates, key=lambda t: score(t, idx, slot_energy))
        slot.task_title = best.title
        # odlicz czas
        minutes_in_slot = int((slot.end - slot.start).seconds / 60)
        best.minutes -= minutes_in_slot
        if best.minutes <= 0:
            remaining.remove(best)

    return slots


# ==================== HABITY - HISTORIA/STREAK ====================


def get_today_key() -> str:
    return datetime.now().date().isoformat()


def mark_habit_done(habit_name: str, done: bool):
    key = get_today_key()
    hist = st.session_state.setdefault("habit_history", {})  # type: ignore[assignment]
    day = hist.setdefault(key, {})
    day[habit_name] = done


def habit_streak(habit_name: str) -> int:
    hist = st.session_state.get("habit_history", {})  # type: ignore[assignment]
    days = sorted(hist.keys(), reverse=True)
    streak = 0
    for d in days:
        if hist[d].get(habit_name):
            streak += 1
        else:
            if d == get_today_key():
                # dzisiaj niezaznaczony nie zrywa ciurkiem, o ile wczoraj było OK
                continue
            break
    return streak


# ==================== UI ====================
st.set_page_config(page_title="Organizer AI - MVP", page_icon="🗓️", layout="wide")
st.title("🗓️ Organizer AI - MVP")

# --- SIDEBAR USTAWIENIA ---
with st.sidebar:
    st.header("Ustawienia dnia")
    day_start = st.time_input("Start dnia", value=time(8, 0))
    day_end = st.time_input("Koniec dnia", value=time(20, 0))
    block_minutes = st.number_input("Długość bloku (min)", 15, 120, 60, 5)
    break_minutes = st.number_input("Przerwa (min)", 0, 60, 10, 5)

    st.subheader("Profil energii (edytowalny)")
    default_profile = ["high", "high", "medium", "medium", "low"] * 8
    profile_text = st.text_area(
        "Sloty energii (np. high,high,medium...)",
        value=",".join(default_profile),
        height=120,
    )
    energy_profile = [s.strip() for s in profile_text.split(",") if s.strip()]

    st.subheader("Tryb planera")
    ai_mode = st.radio("Planer", ["Heurystyka (offline)", "LLM (wkrótce)"])

# --- STAN SESJI ---
if "tasks" not in st.session_state:
    st.session_state.tasks = []  # list[Task]
if "fixed" not in st.session_state:
    st.session_state.fixed = []  # list[FixedBlock]
if "habits" not in st.session_state:
    st.session_state.habits = [Habit(name="Dni bez szluga", with_time_block=False)]
if "habit_history" not in st.session_state:
    st.session_state.habit_history = {}

# --- INBOX ZADAŃ ---
st.header("Inbox zadań")
with st.form("add_task"):
    c1, c2, c3, c4 = st.columns([3, 1, 1, 2])
    title = c1.text_input("Nazwa")
    minutes = c2.number_input("Minuty", 5, 600, 60, 5)
    priority = c3.selectbox("Priorytet", [1, 2, 3], index=1)
    energy = c4.selectbox("Energia", ["low", "medium", "high"], index=1)
    deadline = st.date_input("Deadline (opcjonalnie)", value=None)
    tags = st.text_input("Tagi (comma)")
    project = st.text_input("Projekt")
    depends_on = st.text_input("Zależności (nazwy zadań, comma)")
    url = st.text_input("Link (opcjonalnie)")

    with st.expander("Podzadania"):
        sub_titles = st.text_area("Podaj podzadania (jedno na linię)")

    submitted = st.form_submit_button("Dodaj zadanie")

    if submitted and title:
        dl = datetime.combine(deadline, time(23, 59)) if isinstance(deadline, date) else None
        st.session_state.tasks.append(
            Task(
                title=title,
                minutes=int(minutes),
                priority=int(priority),
                energy=energy,
                deadline=dl,
                tags=[t.strip() for t in tags.split(",") if t.strip()],
                project=project or None,
                depends_on=[d.strip() for d in depends_on.split(",") if d.strip()],
                subtasks=[Subtask(s.strip()) for s in sub_titles.splitlines() if s.strip()],
                url=safe_url(url),
            )
        )
        st.success(f"Dodano: {title}")

# --- BLOKI STAŁE ---
st.subheader("Bloki stałe (spotkania, trening, itp.)")
with st.form("add_fixed"):
    c1, c2, c3 = st.columns([1, 1, 2])
    fx_title = c3.text_input("Tytuł", value="Spotkanie")
    fx_start = c1.time_input("Start", value=time(12, 0), key="fx_start")
    fx_end = c2.time_input("Koniec", value=time(13, 0), key="fx_end")
    submitted_fx = st.form_submit_button("Dodaj blok stały")
    if submitted_fx:
        today = datetime.now().date()
        st.session_state.fixed.append(
            FixedBlock(
                start=datetime.combine(today, fx_start),
                end=datetime.combine(today, fx_end),
                title=fx_title,
            )
        )
        st.success("Dodano blok stały")

# --- HABIT TRACKER ---
st.header("Habit Tracker")
col_h1, col_h2 = st.columns(2)
with col_h1:
    with st.form("add_habit"):
        h_name = st.text_input("Nazwa habitu", value="Dni bez szluga")
        with_time = st.checkbox("Dodaj jako blok czasu w planie", value=False)
        h_min = st.number_input("Minuty bloku (jeśli wyżej zaznaczone)", 0, 120, 5, 5)
        submitted_h = st.form_submit_button("Dodaj habit")
        if submitted_h and h_name:
            st.session_state.habits.append(
                Habit(name=h_name, with_time_block=with_time, minutes=int(h_min))
            )
            st.success("Dodano habit")

with col_h2:
    today_key = get_today_key()
    for h in st.session_state.habits:
        done_today = st.checkbox(
            f"Dzisiejszy: {h.name}",
            value=st.session_state.get("habit_history", {}).get(today_key, {}).get(h.name, False),
        )
        mark_habit_done(h.name, done_today)
        st.caption(f"Streak: {habit_streak(h.name)} 🔥")

# --- PLAN DNIA ---
st.header("Plan dnia")
if st.button("⚡ Wygeneruj plan"):
    plan = generate_plan(
        tasks=st.session_state.tasks,
        habits=st.session_state.habits,
        day_start=day_start,
        day_end=day_end,
        block_minutes=int(block_minutes),
        break_minutes=int(break_minutes),
        energy_profile=energy_profile,
        fixed_blocks=st.session_state.fixed,
    )
    st.session_state.plan = plan

if plan := st.session_state.get("plan"):
    for b in plan:
        label = f"{b.start.strftime('%H:%M')}-{b.end.strftime('%H:%M')}"
        if b.kind == "break":
            st.info(f"Przerwa: {label}")
        elif b.kind == "fixed":
            st.warning(f"{label} — {b.task_title}")
        elif b.kind == "habit":
            st.success(f"{label} — Habit: {b.task_title}")
        else:
            st.write(f"**{label}** — {b.task_title or '(wolne)'}")

# --- TRYB FOCUS ---
st.header("Tryb Focus")
if "timer_until" not in st.session_state:
    st.session_state.timer_until = None

fc1, fc2 = st.columns(2)
with fc1:
    focus_minutes = st.number_input("Czas focus (min)", 5, 120, 25, 5)
    if st.button("Start"):
        st.session_state.timer_until = datetime.now() + timedelta(minutes=int(focus_minutes))
with fc2:
    if st.button("Stop"):
        st.session_state.timer_until = None

if st.session_state.timer_until:
    remaining = st.session_state.timer_until - datetime.now()
    secs = max(0, int(remaining.total_seconds()))
    st.metric("Pozostało (s)", secs)

# --- RAPORT DNIA ---
st.header("Raport dzienny")
planned = (
    sum(
        [
            (b.end - b.start).seconds
            for b in st.session_state.get("plan", [])
            if b.kind in {"work", "habit"}
        ]
    )
    // 60
)
st.write(f"Zaplanowane minuty (work+habit): **{planned}**")

# --- PRZEGLĄD TYGODNIA (placeholder) ---
st.header("Przegląd tygodnia")
st.caption("(placeholder) Wersja z SQLite pokaże wykres wykonania i streaki w czasie.")

# --- LLM PLACEHOLDER ---
if ai_mode == "LLM (wkrótce)":
    st.info("Tryb LLM będzie dostępny po dodaniu klucza i adaptera — gotowe miejsce w kodzie.")
