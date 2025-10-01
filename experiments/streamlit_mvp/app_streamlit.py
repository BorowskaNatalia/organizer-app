from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

import streamlit as st

Energy = Literal["low", "medium", "high"]


@dataclass
class Task:
    title: str
    minutes: int
    priority: int = 2  # 1=wysoki, 2=średni, 3=niski
    deadline: datetime | None = None
    energy: Energy = "medium"


@dataclass
class Block:
    start: datetime
    end: datetime
    task: Task | None = None
    kind: Literal["work", "break", "fixed"] = "work"


def score(task: Task, slot_index: int, slot_energy: Energy) -> float:
    """Wylicza „atrakcyjność” zadania dla slotu."""
    prio_score = {1: 3.0, 2: 2.0, 3: 1.0}[task.priority]

    # deadline_urgency: im bliżej, tym większy score
    if task.deadline:
        days = (task.deadline.date() - datetime.now().date()).days
        urg = max(0.0, 3.0 - (days / 2))  # zgrubnie
    else:
        urg = 0.5

    # energy dopasowanie
    fit = 1.0
    if slot_energy == task.energy:
        fit = 1.5
    elif slot_energy == "low" and task.energy == "high":
        fit = 0.7
    elif slot_energy == "high" and task.energy == "low":
        fit = 0.8

    # lekka preferencja wcześniejszych slotów
    early_bias = max(0.7, 1.2 - slot_index * 0.03)

    return prio_score * fit * early_bias + urg


def generate_plan(
    tasks: list[Task],
    day_start: time,
    day_end: time,
    block_minutes: int,
    break_minutes: int,
    energy_profile: list[Energy],
    fixed_blocks: list[Block],
) -> list[Block]:
    # 1) wygeneruj sloty czasu z uwzględnieniem fixed i przerw
    today = datetime.now().date()
    start_dt = datetime.combine(today, day_start)
    end_dt = datetime.combine(today, day_end)

    slots: list[Block] = []
    t = start_dt
    i = 0

    while t < end_dt:
        # kolizje ze stałymi blokami
        collided = [
            b
            for b in fixed_blocks
            if not (t >= b.end or (t + timedelta(minutes=block_minutes)) <= b.start)
        ]
        if collided:
            fb = sorted(collided, key=lambda b: b.start)[0]
            slots.append(Block(start=fb.start, end=fb.end, task=None, kind="fixed"))
            t = fb.end
            continue

        # slot roboczy
        slot_end = min(t + timedelta(minutes=block_minutes), end_dt)
        slots.append(Block(start=t, end=slot_end, kind="work"))

        # przerwa po slocie (o ile jest czas)
        if break_minutes > 0:
            b_start = slot_end
            b_end = min(b_start + timedelta(minutes=break_minutes), end_dt)
            if b_start < b_end:
                slots.append(Block(start=b_start, end=b_end, kind="break"))
                t = b_end
            else:
                t = b_end
        else:
            t = slot_end

        i += 1

    # 2) przypisz zadania do slotów „work”
    remaining = tasks.copy()
    for idx, slot in enumerate(slots):
        if slot.kind != "work":
            continue
        if not remaining:
            continue

        slot_energy = (
            energy_profile[min(idx, len(energy_profile) - 1)] if energy_profile else "medium"
        )

        # wybierz najlepsze zadanie
        best = max(remaining, key=lambda tsk: score(tsk, idx, slot_energy))
        slot.task = best

        # zmniejsz pozostały czas zadania
        used = int((slot.end - slot.start).total_seconds() // 60)
        best.minutes -= used
        if best.minutes <= 0:
            remaining.remove(best)

    return slots


# ----------------------- UI (Streamlit) -----------------------

st.set_page_config(page_title="Plan dnia AI", layout="wide")
st.title("🗓️ Plan dnia z AI")

with st.sidebar:
    st.header("Ustawienia")
    col1, col2 = st.columns(2)
    start = col1.time_input("Start dnia", value=time(9, 0))
    end = col2.time_input("Koniec dnia", value=time(17, 0))
    block_len = st.number_input("Długość bloku (min)", 15, 240, 50, 5)
    break_len = st.number_input("Przerwa po bloku (min)", 0, 60, 10, 5)

    st.markdown("**Profil energii (na sloty)**")
    energy_map = {"Niski": "low", "Średni": "medium", "Wysoki": "high"}
    energy_labels = list(energy_map.keys())
    profile_len = int(
        (
            (
                datetime.combine(datetime.today(), end) - datetime.combine(datetime.today(), start)
            ).total_seconds()
            // 60
        )
        // (block_len + max(break_len, 0))
        + 1
    )
    chosen = st.multiselect(
        "Energia (kolejne sloty)",
        options=energy_labels,
        default=["Wysoki", "Średni", "Średni"][:profile_len],
    )
    energy_profile: list[Energy] = [energy_map[x] for x in chosen] if chosen else ["medium"]


st.subheader("Zadania")
default_tasks = [
    Task("Deep work: projekt", 120, priority=1, energy="high"),
    Task("E-maile", 30, priority=3, energy="low"),
    Task("Review PR", 45, priority=2, energy="medium"),
]
if "tasks" not in st.session_state:
    st.session_state.tasks: list[Task] = default_tasks

for i, tsk in enumerate(st.session_state.tasks):
    with st.expander(f"{tsk.title} ({tsk.minutes} min, prio {tsk.priority})", expanded=False):
        tsk.title = st.text_input(f"Tytuł #{i}", value=tsk.title, key=f"title_{i}")
        tsk.minutes = int(
            st.number_input(f"Minuty #{i}", 5, 600, value=tsk.minutes, step=5, key=f"min_{i}")
        )
        tsk.priority = int(
            st.selectbox(f"Priorytet #{i}", [1, 2, 3], index=tsk.priority - 1, key=f"prio_{i}")
        )
        t_energy = st.selectbox(
            f"Energia #{i}",
            ["low", "medium", "high"],
            index=["low", "medium", "high"].index(tsk.energy),
            key=f"eng_{i}",
        )
        tsk.energy = t_energy  # type: ignore[assignment]

col_add, col_plan = st.columns([1, 3])
with col_add:
    if st.button("Dodaj zadanie"):
        st.session_state.tasks.append(Task("Nowe zadanie", 30, 2, None, "medium"))

with col_plan:
    if st.button("🧠 Ułóż plan"):
        plan = generate_plan(
            tasks=[Task(**t.__dict__) for t in st.session_state.tasks],  # kopie
            day_start=start,
            day_end=end,
            block_minutes=int(block_len),
            break_minutes=int(break_len),
            energy_profile=energy_profile,  # można krótsze niż liczba slotów
            fixed_blocks=[],  # TODO: GUI do stałych bloków
        )
        st.success("Plan gotowy!")
        for b in plan:
            label = f"{b.start.strftime('%H:%M')}-{b.end.strftime('%H:%M')}"
            if b.kind == "fixed":
                st.info(f"{label} • 🔒 Blok stały")
            elif b.kind == "break":
                st.warning(f"{label} • ☕ Przerwa")
            else:
                title = b.task.title if b.task else "—"
                st.write(f"{label} • ✅ {title}")
