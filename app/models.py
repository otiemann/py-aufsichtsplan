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

    quota = relationship("TeacherQuota", back_populates="teacher", uselist=False, cascade="all, delete-orphan")
    assignments = relationship("Assignment", back_populates="teacher")
    preferred_floor = relationship("Floor")

    __table_args__ = (
        UniqueConstraint("first_name", "last_name", "abbreviation", name="uq_teacher_name_abbrev"),
    )


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
