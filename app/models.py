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
    attendance_days = Column(Integer, nullable=True, default=31)

    quota = relationship("TeacherQuota", back_populates="teacher", uselist=False, cascade="all, delete-orphan")
    assignments = relationship("Assignment", back_populates="teacher")
    preferred_floor = relationship("Floor")
    lessons = relationship("TeacherLesson", back_populates="teacher", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("first_name", "last_name", "abbreviation", name="uq_teacher_name_abbrev"),
    )
    
    def is_available_on_weekday(self, weekday: int) -> bool:
        """Prüft ob Lehrkraft an einem Wochentag verfügbar ist (0=Montag, 4=Freitag)"""
        if weekday < 0 or weekday > 4:
            return False
        actual_attendance = self.get_actual_attendance_days()
        return bool(actual_attendance & (1 << weekday))
    
    def set_attendance_days(self, days_list: list) -> None:
        """Setzt Anwesenheitstage aus Liste ['Mo', 'Di', ...]"""
        day_map = {'Mo': 0, 'Di': 1, 'Mi': 2, 'Do': 3, 'Fr': 4}
        self.attendance_days = 0
        for day in days_list:
            if day in day_map:
                self.attendance_days |= (1 << day_map[day])
    
    def get_attendance_days_display(self) -> str:
        """Gibt Anwesenheitstage als lesbaren String zurück"""
        if not self.attendance_days:
            return "Keine"
        
        days = []
        if self.attendance_days & 1: days.append("Mo")
        if self.attendance_days & 2: days.append("Di")
        if self.attendance_days & 4: days.append("Mi")
        if self.attendance_days & 8: days.append("Do")
        if self.attendance_days & 16: days.append("Fr")
        
        if len(days) == 5:
            return "Mo-Fr"
        elif len(days) == 0:
            return "Keine"
        else:
            return " ".join(days)
    
    def get_attendance_days_list(self) -> list:
        """Gibt Liste der Anwesenheitstage zurück ['Mo', 'Di', ...]"""
        days = []
        if self.attendance_days & 1: days.append("Mo")
        if self.attendance_days & 2: days.append("Di")
        if self.attendance_days & 4: days.append("Mi")
        if self.attendance_days & 8: days.append("Do")
        if self.attendance_days & 16: days.append("Fr")
        return days
    
    def get_actual_attendance_days(self) -> int:
        """Berechnet tatsächliche Anwesenheitstage basierend auf Unterrichtsstunden
        
        Returns:
            Bitflags für tatsächliche Anwesenheitstage oder gespeicherte Werte wenn manuell gesetzt
        """
        # Wenn manuell gesetzt (nicht Default-Werte), verwende gespeicherte Werte
        if self.attendance_days is not None and self.attendance_days != 31 and self.attendance_days != 0:
            return self.attendance_days
        
        # Berechne aus Unterrichtsstunden
        if not self.lessons:
            return 0  # Keine Stunden = keine Anwesenheit
        
        lesson_weekdays = set()
        for lesson in self.lessons:
            if 0 <= lesson.weekday <= 4:  # 0=Montag bis 4=Freitag
                lesson_weekdays.add(lesson.weekday)
        
        attendance_bits = 0
        for weekday in lesson_weekdays:
            attendance_bits |= (1 << weekday)
        
        return attendance_bits
    
    def get_actual_attendance_days_display(self) -> str:
        """Gibt tatsächliche Anwesenheitstage als lesbaren String zurück"""
        actual_attendance = self.get_actual_attendance_days()
        
        if not actual_attendance:
            return "Keine"
        
        days = []
        if actual_attendance & 1: days.append("Mo")
        if actual_attendance & 2: days.append("Di")
        if actual_attendance & 4: days.append("Mi")
        if actual_attendance & 8: days.append("Do")
        if actual_attendance & 16: days.append("Fr")
        
        if len(days) == 5:
            return "Mo-Fr"
        elif len(days) == 0:
            return "Keine"
        else:
            return " ".join(days)
    
    def get_actual_attendance_days_list(self) -> list:
        """Gibt Liste der tatsächlichen Anwesenheitstage zurück ['Mo', 'Di', ...]"""
        actual_attendance = self.get_actual_attendance_days()
        
        days = []
        if actual_attendance & 1: days.append("Mo")
        if actual_attendance & 2: days.append("Di")
        if actual_attendance & 4: days.append("Mi")
        if actual_attendance & 8: days.append("Do")
        if actual_attendance & 16: days.append("Fr")
        return days
    
    def is_attendance_manually_set(self) -> bool:
        """Prüft ob Anwesenheitstage manuell gesetzt wurden (nicht Default-Werte)"""
        return (self.attendance_days is not None and 
                self.attendance_days != 31 and  # Nicht Mo-Fr Default
                self.attendance_days != 0)      # Nicht "keine Tage" Default
    
    def is_available_for_supervision(self, weekday: int, break_index: int) -> bool:
        """Prüft ob Lehrkraft für eine Pausenaufsicht verfügbar ist
        
        Args:
            weekday: 0=Montag, 1=Dienstag, ..., 4=Freitag
            break_index: 1=0.Stunde Aufsicht, 2=zwischen 2./3.Stunde, 3=zwischen 4./5.Stunde, 4=zwischen 6./7.Stunde
        
        Returns:
            True wenn verfügbar (HAT Unterricht in relevanten Zeiten und ist daher sowieso anwesend)
        """
        # Bestimme welche Unterrichtsstunden für diese Pause relevant sind
        # Lehrkräfte können Aufsicht machen, WENN sie in diesen Stunden unterrichten
        relevant_hours = []
        if break_index == 1:  # 0. Stunde Aufsicht
            relevant_hours = [1]  # Lehrkräfte mit 1. Stunde können 0. Stunde Aufsicht machen
        elif break_index == 2:  # Pause zwischen 2./3. Stunde
            relevant_hours = [2, 3]  # Lehrkräfte mit 2. oder 3. Stunde
        elif break_index == 3:  # Pause zwischen 4./5. Stunde
            relevant_hours = [4, 5]  # Lehrkräfte mit 4. oder 5. Stunde
        elif break_index == 4:  # Pause zwischen 6./7. Stunde
            relevant_hours = [6, 7]  # Lehrkräfte mit 6. oder 7. Stunde
        else:
            return False  # Unbekannter break_index, keine Aufsicht möglich
        
        # Prüfe ob Lehrkraft in einer der relevanten Stunden Unterricht hat
        for lesson in self.lessons:
            if lesson.weekday == weekday and lesson.hour in relevant_hours:
                return True  # Hat Unterricht, kann Aufsicht machen (ist sowieso da)
        
        return False  # Kein Unterricht in relevanten Stunden, nicht verfügbar
    



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
        Index("ix_assignment_duty_slot_id", "duty_slot_id"),
        Index("ix_assignment_teacher_id", "teacher_id"),
    )


class TeacherLesson(Base):
    __tablename__ = "teacher_lessons"

    id = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False)
    weekday = Column(Integer, nullable=False)  # 0=Montag, 1=Dienstag, ..., 4=Freitag
    hour = Column(Integer, nullable=False)     # 1-8 Unterrichtsstunden
    subject = Column(String(100), nullable=True)
    class_name = Column(String(100), nullable=True)
    room = Column(String(50), nullable=True)

    teacher = relationship("Teacher", back_populates="lessons")

    __table_args__ = (
        Index("ix_teacher_lesson_weekday_hour", "teacher_id", "weekday", "hour"),
    )
