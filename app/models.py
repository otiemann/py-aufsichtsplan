from sqlalchemy import Column, Integer, String, UniqueConstraint, Date, ForeignKey, Index, Boolean
from sqlalchemy.orm import relationship
from .database import Base


class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=True, unique=True)
    abbreviation = Column(String(50), nullable=True, unique=True)
    department = Column(String(100), nullable=True)
    exempt = Column(Boolean, nullable=False, default=False)
    preferred_floor_id = Column(Integer, ForeignKey("floors.id", ondelete="SET NULL"), nullable=True)
    # Anwesenheitstage: Bitflags für Wochentage Mo=1, Di=2, Mi=4, Do=8, Fr=16
    # Standard: 31 = Mo+Di+Mi+Do+Fr = alle Wochentage (binär: 11111)
    attendance_days = Column(Integer, nullable=False, default=31)

    quota = relationship("TeacherQuota", back_populates="teacher", uselist=False, cascade="all, delete-orphan")
    assignments = relationship("Assignment", back_populates="teacher")
    preferred_floor = relationship("Floor")

    __table_args__ = (
        UniqueConstraint("first_name", "last_name", "abbreviation", name="uq_teacher_name_abbrev"),
    )
    
    def is_available_on_weekday(self, weekday: int) -> bool:
        """Prüft ob Lehrkraft an einem Wochentag verfügbar ist (0=Montag, 4=Freitag)"""
        if weekday < 0 or weekday > 4:
            return False
        return bool(self.attendance_days & (1 << weekday))
    
    def get_attendance_days_list(self) -> list:
        """Gibt Liste der Anwesenheitstage zurück ['Mo', 'Di', ...]"""
        days = ['Mo', 'Di', 'Mi', 'Do', 'Fr']
        return [days[i] for i in range(5) if self.is_available_on_weekday(i)]
    
    def set_attendance_days(self, days_list: list) -> None:
        """Setzt Anwesenheitstage aus Liste ['Mo', 'Di', ...]"""
        day_map = {'Mo': 0, 'Di': 1, 'Mi': 2, 'Do': 3, 'Fr': 4}
        self.attendance_days = 0
        for day in days_list:
            if day in day_map:
                self.attendance_days |= (1 << day_map[day])


class Floor(Base):
    __tablename__ = "floors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    required_per_break = Column(Integer, nullable=False, default=1)
    order_index = Column(Integer, nullable=False, default=0, index=True)

    duty_slots = relationship("DutySlot", back_populates="floor")


class TeacherQuota(Base):
    __tablename__ = "teacher_quotas"

    id = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False, unique=True)
    target_duties = Column(Integer, nullable=False, default=0)

    teacher = relationship("Teacher", back_populates="quota")


class DutySlot(Base):
    __tablename__ = "duty_slots"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    break_index = Column(Integer, nullable=False)  # 1, 2, ...
    floor_id = Column(Integer, ForeignKey("floors.id", ondelete="CASCADE"), nullable=False)

    floor = relationship("Floor", back_populates="duty_slots")
    assignments = relationship("Assignment", back_populates="duty_slot", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_duty_unique", "date", "break_index", "floor_id", unique=True),
    )


class Assignment(Base):
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True, index=True)
    duty_slot_id = Column(Integer, ForeignKey("duty_slots.id", ondelete="CASCADE"), nullable=False)
    teacher_id = Column(Integer, ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False)

    duty_slot = relationship("DutySlot", back_populates="assignments")
    teacher = relationship("Teacher", back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("duty_slot_id", "teacher_id", name="uq_assignment_slot_teacher"),
    )
